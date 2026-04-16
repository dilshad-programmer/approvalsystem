"""
aws_utils.py — AWS Service Integration for Document Approval System
Integrates: Amazon S3, SNS, DynamoDB, Lambda, Cognito

Design principles applied for EB / AWS Academy reliability:
  • Lazy boto3 clients  — created inside functions, NEVER at module level or __init__
  • IAM instance profile — on EB (ENV=production) no explicit credentials are passed;
    boto3 picks up the LabRole automatically via EC2 instance metadata.
  • Fire-and-forget     — DynamoDB writes and SNS publishes run in daemon threads so
    HTTP responses never wait for AWS.
  • Safe fallbacks      — every AWS call is wrapped in try/except Exception; returns
    None / False / [] on any failure — never raises to the caller.
  • _safe_decimal()     — converts any value safely to Decimal.
"""

import boto3
import os
import datetime
import uuid
import logging
import threading
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# ── Service config — read from environment (set in .ebextensions on EB) ────────
AWS_REGION        = os.getenv('AWS_REGION', 'us-east-1')
S3_BUCKET         = os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')
SNS_TOPIC_ARN     = os.getenv('AWS_SNS_TOPIC_ARN', '')
DYNAMODB_TABLE    = os.getenv('AWS_DYNAMODB_TABLE_NAME', 'DocumentApprovalLogs')
LAMBDA_FUNCTION   = os.getenv('AWS_LAMBDA_FUNCTION_NAME', 'ProcessDocumentApproval')
COGNITO_POOL_ID   = os.getenv('AWS_COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID = os.getenv('AWS_COGNITO_APP_CLIENT_ID', '')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_decimal(value, default=0):
    """
    Safely convert any value to Decimal.
    Returns Decimal(str(default)) if value is None, empty, or unconvertible.
    """
    if value is None or value == '':
        return Decimal(str(default))
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("[Decimal] Could not convert %r — using default %s", value, default)
        return Decimal(str(default))


def _get_client(service_name):
    """
    Returns a boto3 client.

    On EB (ENV=production):  no explicit credentials — IAM LabRole instance profile
                             is picked up automatically via EC2 instance metadata.
    In local dev:            boto3 reads credentials from environment variables
                             (set by load_dotenv in settings.py).
    """
    if os.getenv('ENV') == 'production':
        return boto3.client(service_name, region_name=AWS_REGION)
    # Local dev — boto3 reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
    # from the environment (loaded via load_dotenv in settings.py).
    return boto3.client(service_name, region_name=AWS_REGION)


def _get_resource(service_name):
    """Same pattern as _get_client but returns a boto3 resource."""
    if os.getenv('ENV') == 'production':
        return boto3.resource(service_name, region_name=AWS_REGION)
    return boto3.resource(service_name, region_name=AWS_REGION)


# ══════════════════════════════════════════════════════════════════════════════
# 1. AMAZON S3 — Document Storage
# ══════════════════════════════════════════════════════════════════════════════

def upload_to_s3(file_obj, s3_key):
    """
    Uploads a file object to S3.
    Returns a pre-signed URL (valid 1 hour) so files stay private and secure.
    Returns None on any failure — never raises.
    """
    try:
        s3 = _get_client('s3')
        s3.upload_fileobj(
            file_obj,
            S3_BUCKET,
            s3_key,
            ExtraArgs={'ContentType': getattr(file_obj, 'content_type', 'application/octet-stream')}
        )
        logger.info("[S3] Uploaded: s3://%s/%s", S3_BUCKET, s3_key)
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=3600
        )
        return url
    except Exception as e:
        logger.warning("[S3] Upload failed: %s", e)
        return None


def generate_presigned_url(s3_key, expiry=3600):
    """
    Generates a fresh pre-signed URL for an existing S3 object.
    Returns None on any failure — never raises.
    """
    if not s3_key:
        return None
    try:
        s3 = _get_client('s3')
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expiry
        )
        return url
    except Exception as e:
        logger.warning("[S3] Pre-signed URL failed: %s", e)
        return None


def delete_from_s3(s3_key):
    """Deletes a document from S3. Returns False on any failure — never raises."""
    try:
        s3 = _get_client('s3')
        s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.info("[S3] Deleted: s3://%s/%s", S3_BUCKET, s3_key)
        return True
    except Exception as e:
        logger.warning("[S3] Delete failed: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 2. AMAZON SNS — Notifications  (fire-and-forget)
# ══════════════════════════════════════════════════════════════════════════════

def _sns_publish_worker(subject, message):
    """Background worker — do NOT call directly."""
    if not SNS_TOPIC_ARN:
        logger.info("[SNS SKIPPED] No Topic ARN — would have sent: %s | %s", subject, message)
        return
    try:
        sns = _get_client('sns')
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=subject
        )
        logger.info("[SNS] Published — MessageId: %s", response.get('MessageId'))
    except Exception as e:
        logger.warning("[SNS] Publish failed: %s", e)


def send_sns_notification(subject, message):
    """
    Sends a notification via Amazon SNS Topic — fire and forget.
    Returns immediately; never blocks the HTTP response.
    """
    t = threading.Thread(target=_sns_publish_worker, args=(subject, message), daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
# 3. AMAZON DYNAMODB — Audit / Workflow Logging  (fire-and-forget)
# ══════════════════════════════════════════════════════════════════════════════

def _dynamodb_put_worker(log_id, document_id, action, user, comments):
    """Background worker — do NOT call directly."""
    try:
        dynamodb = _get_resource('dynamodb')
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.put_item(Item={
            'LogID':      log_id,
            'DocumentID': str(document_id),
            'Action':     action,
            'User':       str(user),
            'Timestamp':  datetime.datetime.utcnow().isoformat() + 'Z',
            'Comments':   comments or ''
        })
        logger.info("[DynamoDB] Logged: %s on doc %s by %s", action, document_id, user)
    except Exception as e:
        logger.warning("[DynamoDB] Log failed: %s", e)


def log_workflow_action(document_id, action, user, comments=""):
    """
    Writes an audit log entry to DynamoDB — fire and forget.
    Returns immediately; HTTP response is never delayed by this call.
    """
    log_id = f"{document_id}_{uuid.uuid4().hex[:8]}_{action}"
    t = threading.Thread(
        target=_dynamodb_put_worker,
        args=(log_id, document_id, action, user, comments),
        daemon=True
    )
    t.start()


def get_document_logs(document_id):
    """
    Fetches all audit log entries for a given document from DynamoDB.
    Returns a list of log dicts sorted by timestamp (newest first).
    Returns [] on any failure — never raises.
    """
    try:
        from boto3.dynamodb.conditions import Attr
        dynamodb = _get_resource('dynamodb')
        table = dynamodb.Table(DYNAMODB_TABLE)
        response = table.scan(
            FilterExpression=Attr('DocumentID').eq(str(document_id))
        )
        items = response.get('Items', [])
        items.sort(key=lambda x: x.get('Timestamp', ''), reverse=True)
        return items
    except Exception as e:
        logger.warning("[DynamoDB] Fetch logs failed: %s", e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 4. COGNITO — Auth helpers (stubs when not configured)
# ══════════════════════════════════════════════════════════════════════════════

def register_user(username, password, email):
    """Register a user in Cognito. Returns None if not configured or on error."""
    if not COGNITO_CLIENT_ID:
        logger.info("[Cognito SKIPPED] No Client ID — using Django fallback.")
        return None
    try:
        cognito = _get_client('cognito-idp')
        response = cognito.sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=username,
            Password=password,
            UserAttributes=[{'Name': 'email', 'Value': email}]
        )
        logger.info("[Cognito] User %s signed up.", username)
        return response
    except Exception as e:
        logger.warning("[Cognito] SignUp failed: %s", e)
        return {'Error': str(e)}


def authenticate_user(username, password):
    """Authenticate a user via Cognito. Returns None if not configured or on error."""
    if not COGNITO_CLIENT_ID:
        logger.info("[Cognito SKIPPED] No Client ID — using Django fallback.")
        return None
    try:
        cognito = _get_client('cognito-idp')
        response = cognito.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={'USERNAME': username, 'PASSWORD': password}
        )
        logger.info("[Cognito] User %s authenticated.", username)
        return response
    except Exception as e:
        logger.warning("[Cognito] Auth failed: %s", e)
        return {'Error': str(e)}


def confirm_user(username, code):
    """Confirm a Cognito sign-up. Returns None if not configured or on error."""
    if not COGNITO_CLIENT_ID:
        logger.info("[Cognito SKIPPED] No Client ID — using Django fallback.")
        return None
    try:
        cognito = _get_client('cognito-idp')
        response = cognito.confirm_sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=username,
            ConfirmationCode=code
        )
        logger.info("[Cognito] User %s confirmed.", username)
        return response
    except Exception as e:
        logger.warning("[Cognito] Confirmation failed: %s", e)
        return {'Error': str(e)}


def delete_cognito_user(username):
    """Delete a user from Cognito pool. Returns None if not configured or on error."""
    if not COGNITO_POOL_ID:
        logger.info("[Cognito SKIPPED] No User Pool ID — cannot delete from cloud.")
        return None
    try:
        cognito = _get_client('cognito-idp')
        response = cognito.admin_delete_user(
            UserPoolId=COGNITO_POOL_ID,
            Username=username
        )
        logger.info("[Cognito] User %s deleted from pool.", username)
        return response
    except Exception as e:
        logger.warning("[Cognito] Delete failed: %s", e)
        return {'Error': str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# 5. AWS LAMBDA — Background automation trigger
# ══════════════════════════════════════════════════════════════════════════════

def trigger_lambda_process(payload):
    """
    Asynchronously triggers the ProcessDocumentApproval Lambda function.
    Returns None on any failure — never raises.
    """
    import json
    try:
        lambda_client = _get_client('lambda')
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION,
            InvocationType='Event',   # Async — fire and forget
            Payload=json.dumps(payload).encode()
        )
        logger.info("[Lambda] Triggered %s — StatusCode: %s", LAMBDA_FUNCTION, response.get('StatusCode'))
        return response
    except Exception as e:
        logger.warning("[Lambda] Trigger failed (skipped): %s", e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 6. AWS Service Health Check
# ══════════════════════════════════════════════════════════════════════════════

def check_aws_connectivity():
    """Checks the status of all configured AWS services using lightweight API calls."""
    status = {
        's3':       {'active': False, 'message': 'Not Checked'},
        'dynamodb': {'active': False, 'message': 'Not Checked'},
        'sns':      {'active': False, 'message': 'Not Checked'},
        'lambda':   {'active': False, 'message': 'Not Checked'},
        'cognito':  {'active': False, 'message': 'Not Checked'},
    }

    # S3
    try:
        s3 = _get_client('s3')
        s3.list_objects_v2(Bucket=S3_BUCKET, MaxKeys=1)
        status['s3'] = {'active': True, 'message': f'Connected to bucket: {S3_BUCKET}'}
    except Exception as e:
        status['s3'] = {'active': False, 'message': f'S3: {str(e)[:120]}'}

    # DynamoDB
    try:
        ddb = _get_client('dynamodb')
        result = ddb.list_tables()
        tables = result.get('TableNames', [])
        if DYNAMODB_TABLE in tables:
            status['dynamodb'] = {'active': True, 'message': f'Table {DYNAMODB_TABLE} is active'}
        else:
            status['dynamodb'] = {'active': False, 'message': f'Table {DYNAMODB_TABLE} not found in account'}
    except Exception as e:
        status['dynamodb'] = {'active': False, 'message': f'DynamoDB: {str(e)[:120]}'}

    # SNS
    if not SNS_TOPIC_ARN:
        status['sns'] = {'active': False, 'message': 'SNS Topic ARN not configured'}
    else:
        try:
            sns = _get_client('sns')
            pages = sns.list_topics()
            found = any(t['TopicArn'] == SNS_TOPIC_ARN for t in pages.get('Topics', []))
            if found:
                status['sns'] = {'active': True, 'message': f'SNS Topic active: {SNS_TOPIC_ARN.split(":")[-1]}'}
            else:
                status['sns'] = {'active': False, 'message': 'SNS Topic ARN not found in account'}
        except Exception as e:
            status['sns'] = {'active': False, 'message': f'SNS: {str(e)[:120]}'}

    # Lambda
    try:
        lam = _get_client('lambda')
        result = lam.list_functions(MaxItems=50)
        names = [f['FunctionName'] for f in result.get('Functions', [])]
        if LAMBDA_FUNCTION in names:
            status['lambda'] = {'active': True, 'message': f'Function {LAMBDA_FUNCTION} is LIVE'}
        else:
            status['lambda'] = {'active': False, 'message': f'Function {LAMBDA_FUNCTION} not found'}
    except Exception as e:
        status['lambda'] = {'active': False, 'message': f'Lambda: {str(e)[:120]}'}

    # Cognito
    if not COGNITO_POOL_ID or not COGNITO_CLIENT_ID:
        status['cognito'] = {'active': False, 'message': 'Cognito IDs not configured'}
    else:
        try:
            cog = _get_client('cognito-idp')
            result = cog.list_user_pools(MaxResults=10)
            ids = [p['Id'] for p in result.get('UserPools', [])]
            if COGNITO_POOL_ID in ids:
                status['cognito'] = {'active': True, 'message': f'Cognito pool {COGNITO_POOL_ID} is reachable'}
            else:
                status['cognito'] = {'active': False, 'message': f'Pool {COGNITO_POOL_ID} not found (may still be working)'}
        except Exception as e:
            status['cognito'] = {'active': False, 'message': f'Cognito: {str(e)[:120]}'}

    return status
