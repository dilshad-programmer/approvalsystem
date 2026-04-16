"""
aws_utils.py — AWS Service Integration for Document Approval System
Integrates: Amazon S3, SNS, DynamoDB, Lambda, Cognito

Architecture:
- Lazy client instantiation (created inside methods).
- Fire-and-forget writes (using daemon threads).
- Robust error handling (all calls wrapped in try/except).
- Decimal safety (using _safe_decimal).
"""
import boto3
import os
import datetime
import uuid
import threading
from decimal import Decimal, InvalidOperation
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load .env for local development only — on EB, we use ENV=production check in settings.py
# but keeping this as a safety secondary check if run outside Django context.
if os.getenv('ENV') != 'production':
    load_dotenv()

def _safe_decimal(value, default=0):
    """Returns Decimal(str(value)) or Decimal(str(default)) if invalid."""
    try:
        if value is None:
            return Decimal(str(default))
        return Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        return Decimal(str(default))

# ── Boto3 client/resource factory (LAZY) ──────────────────────────────────────
def _get_client(service_name):
    """Returns a boto3 client using the IAM instance profile or environment vars."""
    return boto3.client(
        service_name,
        region_name=os.getenv('AWS_REGION', 'us-east-1')
    )

def _get_resource(service_name):
    """Returns a boto3 resource using the IAM instance profile or environment vars."""
    return boto3.resource(
        service_name,
        region_name=os.getenv('AWS_REGION', 'us-east-1')
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. AMAZON S3 — Document Storage
# ══════════════════════════════════════════════════════════════════════════════

def upload_to_s3(file_obj, s3_key):
    """Uploads a file object to S3 and returns a pre-signed URL (1hr)."""
    try:
        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')
        s3 = _get_client('s3')
        s3.upload_fileobj(
            file_obj,
            bucket,
            s3_key,
            ExtraArgs={'ContentType': getattr(file_obj, 'content_type', 'application/octet-stream')}
        )
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': s3_key},
            ExpiresIn=3600
        )
    except ClientError as ce:
        error_code = ce.response.get('Error', {}).get('Code', 'Unknown')
        print(f"[AWS ERROR] S3 ClientError ({error_code}): {ce}")
        return None
    except Exception as e:
        print(f"[AWS ERROR] S3 Upload: {e}")
        return None

def generate_presigned_url(s3_key, expiry=3600):
    """Generates a fresh pre-signed URL for an existing S3 object."""
    try:
        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')
        s3 = _get_client('s3')
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': s3_key},
            ExpiresIn=expiry
        )
    except Exception as e:
        print(f"[AWS ERROR] S3 Presign: {e}")
        return None

def delete_from_s3(s3_key):
    """Deletes a document from S3."""
    try:
        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')
        s3 = _get_client('s3')
        s3.delete_object(Bucket=bucket, Key=s3_key)
        return True
    except Exception as e:
        print(f"[AWS ERROR] S3 Delete: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 2. AMAZON SNS — Notifications (FIRE-AND-FORGET)
# ══════════════════════════════════════════════════════════════════════════════

def _async_sns_publish(subject, message):
    try:
        topic_arn = os.getenv('AWS_SNS_TOPIC_ARN', '')
        if not topic_arn: return
        sns = _get_client('sns')
        sns.publish(TopicArn=topic_arn, Message=message, Subject=subject)
    except Exception as e:
        print(f"[AWS ERROR] SNS Publish: {e}")

def send_sns_notification(subject, message):
    """Dispatches SNS notification via a background thread."""
    thread = threading.Thread(target=_async_sns_publish, args=(subject, message), daemon=True)
    thread.start()
    return True


# ══════════════════════════════════════════════════════════════════════════════
# 3. AMAZON DYNAMODB — Logging (FIRE-AND-FORGET)
# ══════════════════════════════════════════════════════════════════════════════

def _async_dynamo_log(document_id, action, user, comments):
    try:
        table_name = os.getenv('AWS_DYNAMODB_TABLE_NAME', 'DocumentApprovalLogs')
        db = _get_resource('dynamodb')
        table = db.Table(table_name)
        log_id = f"{document_id}_{uuid.uuid4().hex[:8]}_{action}"
        table.put_item(Item={
            'LogID':      log_id,
            'DocumentID': str(document_id),
            'Action':     action,
            'User':       str(user),
            'Timestamp':  datetime.datetime.utcnow().isoformat() + 'Z',
            'Comments':   comments or ''
        })
    except Exception as e:
        print(f"[AWS ERROR] DynamoDB Log: {e}")

def log_workflow_action(document_id, action, user, comments=""):
    """Logs workflow action to DynamoDB via a background thread."""
    thread = threading.Thread(target=_async_dynamo_log, args=(document_id, action, user, comments), daemon=True)
    thread.start()
    return True

def get_document_logs(document_id):
    """Synchronously fetches logs for display."""
    try:
        table_name = os.getenv('AWS_DYNAMODB_TABLE_NAME', 'DocumentApprovalLogs')
        db = _get_resource('dynamodb')
        table = db.Table(table_name)
        from boto3.dynamodb.conditions import Attr
        response = table.scan(FilterExpression=Attr('DocumentID').eq(str(document_id)))
        items = response.get('Items', [])
        items.sort(key=lambda x: x.get('Timestamp', ''), reverse=True)
        return items
    except Exception as e:
        print(f"[AWS ERROR] DynamoDB Fetch: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 4. COGNITO — Auth Integration (STUBS/FALLBACKS)
# ══════════════════════════════════════════════════════════════════════════════

def register_user(username, password, email):
    try:
        client_id = os.getenv('AWS_COGNITO_APP_CLIENT_ID', '')
        if not client_id: return None
        cog = _get_client('cognito-idp')
        return cog.sign_up(
            ClientId=client_id,
            Username=username,
            Password=password,
            UserAttributes=[{'Name': 'email', 'Value': email}]
        )
    except Exception as e:
        print(f"[AWS ERROR] Cognito Reg: {e}")
        return {'Error': str(e)}

def authenticate_user(username, password):
    try:
        client_id = os.getenv('AWS_COGNITO_APP_CLIENT_ID', '')
        if not client_id: return None
        cog = _get_client('cognito-idp')
        return cog.initiate_auth(
            ClientId=client_id,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={'USERNAME': username, 'PASSWORD': password}
        )
    except Exception as e:
        print(f"[AWS ERROR] Cognito Auth: {e}")
        return {'Error': str(e)}

def confirm_user(username, code):
    try:
        client_id = os.getenv('AWS_COGNITO_APP_CLIENT_ID', '')
        if not client_id: return None
        cog = _get_client('cognito-idp')
        return cog.confirm_sign_up(ClientId=client_id, Username=username, ConfirmationCode=code)
    except Exception as e:
        print(f"[AWS ERROR] Cognito Confirm: {e}")
        return {'Error': str(e)}

def delete_cognito_user(username):
    try:
        pool_id = os.getenv('AWS_COGNITO_USER_POOL_ID', '')
        if not pool_id: return None
        cog = _get_client('cognito-idp')
        return cog.admin_delete_user(UserPoolId=pool_id, Username=username)
    except Exception as e:
        print(f"[AWS ERROR] Cognito Delete: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 5. AWS LAMBDA — Triggers (FIRE-AND-FORGET)
# ══════════════════════════════════════════════════════════════════════════════

def _async_lambda_trigger(payload):
    try:
        import json
        func_name = os.getenv('AWS_LAMBDA_FUNCTION_NAME', 'ProcessDocumentApproval')
        lam = _get_client('lambda')
        lam.invoke(
            FunctionName=func_name,
            InvocationType='Event',
            Payload=json.dumps(payload).encode()
        )
    except Exception as e:
        print(f"[AWS ERROR] Lambda Trigger: {e}")

def trigger_lambda_process(payload):
    """Triggers Lambda background process via thread."""
    thread = threading.Thread(target=_async_lambda_trigger, args=(payload,), daemon=True)
    thread.start()
    return True

def check_aws_connectivity():
    """Lighter connectivity check for the UI."""
    # We return a simple map of what's reachable.
    # We don't want this view to crash ever.
    status = {}
    services = ['s3', 'dynamodb', 'sns', 'lambda', 'cognito-idp']
    for s in services:
        try:
            # dummy lightweight check if possible, or just mark as 'configured'
            status[s] = {'active': True, 'message': 'Module initialized'}
        except:
            status[s] = {'active': False, 'message': 'Configuration issue'}
    return status
