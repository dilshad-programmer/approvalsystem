"""
aws_utils.py — AWS Service Integration for Document Approval System
Integrates: Amazon S3, SNS, DynamoDB
(Cognito not available in this AWS lab environment — Django auth is used instead)
"""
import boto3
import os
import datetime
import uuid
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ── Credentials ────────────────────────────────────────────────────────────────
AWS_ACCESS_KEY    = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_KEY    = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_SESSION_TOKEN = os.getenv('AWS_SESSION_TOKEN')   # Required for temporary STS creds
AWS_REGION        = os.getenv('AWS_REGION', 'us-west-2')

# ── Service config ─────────────────────────────────────────────────────────────
S3_BUCKET            = os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')
SNS_TOPIC_ARN        = os.getenv('AWS_SNS_TOPIC_ARN', '')
DYNAMODB_TABLE       = os.getenv('AWS_DYNAMODB_TABLE_NAME', 'DocumentApprovalLogs')
LAMBDA_FUNCTION_NAME = os.getenv('AWS_LAMBDA_FUNCTION_NAME', 'ProcessDocumentApproval')

# Cognito Configuration
COGNITO_USER_POOL_ID = os.getenv('AWS_COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID    = os.getenv('AWS_COGNITO_APP_CLIENT_ID', '')


# ── Boto3 client/resource factory ─────────────────────────────────────────────
def get_client(service_name):
    """Returns a boto3 client.
    - Uses explicit STS credentials if AWS_ACCESS_KEY_ID is set in environment.
    - Falls back to EC2 instance profile (IAM role) on Elastic Beanstalk.
    - Returns None gracefully if credentials are missing or invalid.
    """
    try:
        if AWS_ACCESS_KEY and AWS_SECRET_KEY:
            return boto3.client(
                service_name,
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                aws_session_token=AWS_SESSION_TOKEN,
                region_name=AWS_REGION
            )
        else:
            # On EB: use EC2 instance profile (IAM role)
            return boto3.client(service_name, region_name=AWS_REGION)
    except Exception as e:
        print(f"[AWS] Failed to create {service_name} client: {e}")
        return None

def get_resource(service_name):
    """Returns a boto3 resource.
    - Uses explicit STS credentials if AWS_ACCESS_KEY_ID is set in environment.
    - Falls back to EC2 instance profile (IAM role) on Elastic Beanstalk.
    - Returns None gracefully if credentials are missing or invalid.
    """
    try:
        if AWS_ACCESS_KEY and AWS_SECRET_KEY:
            return boto3.resource(
                service_name,
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                aws_session_token=AWS_SESSION_TOKEN,
                region_name=AWS_REGION
            )
        else:
            # On EB: use EC2 instance profile (IAM role)
            return boto3.resource(service_name, region_name=AWS_REGION)
    except Exception as e:
        print(f"[AWS] Failed to create {service_name} resource: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. AMAZON S3 — Document Storage
# ══════════════════════════════════════════════════════════════════════════════

def upload_to_s3(file_obj, s3_key):
    """
    Uploads a file object to S3.
    Returns a pre-signed URL (valid 1 hour) so files stay private and secure.
    """
    s3 = get_client('s3')
    if s3 is None:
        print("[S3 SKIPPED] No valid AWS client available.")
        return None
    try:
        s3.upload_fileobj(
            file_obj,
            S3_BUCKET,
            s3_key,
            ExtraArgs={'ContentType': getattr(file_obj, 'content_type', 'application/octet-stream')}
        )
        print(f"[S3] Uploaded: s3://{S3_BUCKET}/{s3_key}")
        # Return a pre-signed URL valid for 1 hour
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=3600
        )
        return url
    except ClientError as e:
        print(f"[S3 ERROR] Upload failed: {e}")
        return None


def generate_presigned_url(s3_key, expiry=3600):
    """
    Generates a fresh pre-signed URL for an existing S3 object.
    Use this to refresh download links that may have expired.
    """
    s3 = get_client('s3')
    if s3 is None:
        return None
    try:
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': s3_key},
            ExpiresIn=expiry
        )
        return url
    except ClientError as e:
        print(f"[S3 ERROR] Pre-signed URL generation failed: {e}")
        return None


def delete_from_s3(s3_key):
    """Deletes a document from S3."""
    s3 = get_client('s3')
    if s3 is None:
        return False
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        print(f"[S3] Deleted: s3://{S3_BUCKET}/{s3_key}")
        return True
    except ClientError as e:
        print(f"[S3 ERROR] Delete failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 2. AMAZON SNS — Notifications
# ══════════════════════════════════════════════════════════════════════════════

def send_sns_notification(subject, message):
    """
    Sends a notification via Amazon SNS Topic.
    Returns the response or None on failure.
    """
    if not SNS_TOPIC_ARN:
        print(f"[SNS SKIPPED] No Topic ARN configured. Would have sent:")
        print(f"  Subject: {subject} | Message: {message}")
        return None

    sns = get_client('sns')
    if sns is None:
        return None
    try:
        response = sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=subject
        )
        print(f"[SNS] Notification published — MessageId: {response['MessageId']}")
        return response
    except ClientError as e:
        print(f"[SNS ERROR] {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 3. AMAZON DYNAMODB — Audit / Workflow Logging
# ══════════════════════════════════════════════════════════════════════════════

def log_workflow_action(document_id, action, user, comments=""):
    """
    Writes an audit log entry to DynamoDB.
    Each log entry is immutable and provides a complete audit trail.
    """
    dynamodb = get_resource('dynamodb')
    if dynamodb is None:
        print(f"[DynamoDB SKIPPED] No valid AWS resource. Action={action}, User={user}")
        return
    table = dynamodb.Table(DYNAMODB_TABLE)
    log_id = f"{document_id}_{uuid.uuid4().hex[:8]}_{action}"
    try:
        table.put_item(Item={
            'LogID':      log_id,
            'DocumentID': str(document_id),
            'Action':     action,
            'User':       str(user),
            'Timestamp':  datetime.datetime.utcnow().isoformat() + 'Z',
            'Comments':   comments or ''
        })
        print(f"[DynamoDB] Logged: {action} on doc {document_id} by {user}")
    except ClientError as e:
        print(f"[DynamoDB ERROR] {e}")


def get_document_logs(document_id):
    """
    Fetches all audit log entries for a given document from DynamoDB.
    Returns a list of log dicts sorted by timestamp (newest first).
    """
    dynamodb = get_resource('dynamodb')
    if dynamodb is None:
        return []
    table = dynamodb.Table(DYNAMODB_TABLE)
    from boto3.dynamodb.conditions import Attr
    try:
        response = table.scan(
            FilterExpression=Attr('DocumentID').eq(str(document_id))
        )
        items = response.get('Items', [])
        # Sort by Timestamp descending
        items.sort(key=lambda x: x.get('Timestamp', ''), reverse=True)
        return items
    except ClientError as e:
        print(f"[DynamoDB ERROR] Fetch logs failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# 4. COGNITO STUBS — Not available in this AWS lab (Django auth is used)
# ══════════════════════════════════════════════════════════════════════════════

def register_user(username, password, email):
    """Real Cognito registration via boto3."""
    if not COGNITO_CLIENT_ID:
        print("[Cognito SKIPPED] No Client ID — using Django fallback.")
        return None
    
    cognito = get_client('cognito-idp')
    try:
        response = cognito.sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=username,
            Password=password,
            UserAttributes=[{'Name': 'email', 'Value': email}]
        )
        print(f"[Cognito] User {username} signed up.")
        return response
    except ClientError as e:
        print(f"[Cognito ERROR] SignUp Failed: {e.response['Error']['Message']}")
        return {'Error': e.response['Error']['Message']}

def authenticate_user(username, password):
    """Real Cognito authentication via boto3."""
    if not COGNITO_CLIENT_ID:
        print("[Cognito SKIPPED] No Client ID — using Django fallback.")
        return None
        
    cognito = get_client('cognito-idp')
    try:
        response = cognito.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={'USERNAME': username, 'PASSWORD': password}
        )
        print(f"[Cognito] User {username} authenticated.")
        return response
    except ClientError as e:
        print(f"[Cognito ERROR] Auth Failed: {e.response['Error']['Message']}")
        return {'Error': e.response['Error']['Message']}

def confirm_user(username, code):
    """Real Cognito confirmation via boto3."""
    if not COGNITO_CLIENT_ID:
        print("[Cognito SKIPPED] No Client ID — using Django fallback.")
        return None
        
    cognito = get_client('cognito-idp')
    try:
        response = cognito.confirm_sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=username,
            ConfirmationCode=code
        )
        print(f"[Cognito] User {username} confirmed.")
        return response
    except ClientError as e:
        print(f"[Cognito ERROR] Confirmation Failed: {e.response['Error']['Message']}")
        return {'Error': e.response['Error']['Message']}

def delete_cognito_user(username):
    """
    Deletes a user from Cognito permanently.
    Requires Admin privileges (AdminDeleteUser).
    """
    if not COGNITO_USER_POOL_ID:
        print("[Cognito SKIPPED] No User Pool ID — cannot delete from cloud.")
        return None
        
    cognito = get_client('cognito-idp')
    try:
        response = cognito.admin_delete_user(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=username
        )
        print(f"[Cognito] User {username} deleted from pool.")
        return response
    except ClientError as e:
        print(f"[Cognito ERROR] Delete Failed: {e.response['Error']['Message']}")
        return {'Error': e.response['Error']['Message']}


# ══════════════════════════════════════════════════════════════════════════════
# 5. AWS LAMBDA — Background automation trigger
# ══════════════════════════════════════════════════════════════════════════════

def trigger_lambda_process(payload):
    """
    Asynchronously triggers the ProcessDocumentApproval Lambda function.
    Used for background processing after workflow events.
    """
    import json
    lambda_client = get_client('lambda')
    if lambda_client is None:
        print("[Lambda SKIPPED] No valid AWS client.")
        return None
    try:
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType='Event',   # Async — fire and forget
            Payload=json.dumps(payload).encode()
        )
        print(f"[Lambda] Triggered {LAMBDA_FUNCTION_NAME} — StatusCode: {response['StatusCode']}")
        return response
    except ClientError as e:
        print(f"[Lambda SKIPPED] {e}")
        return None


def check_aws_connectivity():
    """Checks the status of all configured AWS services using lightweight API calls."""
    status = {
        's3':       {'active': False, 'message': 'Not Checked'},
        'dynamodb': {'active': False, 'message': 'Not Checked'},
        'sns':      {'active': False, 'message': 'Not Checked'},
        'lambda':   {'active': False, 'message': 'Not Checked'},
        'cognito':  {'active': False, 'message': 'Not Checked'},
    }

    # 1. S3 — Use list_objects_v2 (lighter than head_bucket)
    try:
        s3 = get_client('s3')
        s3.list_objects_v2(Bucket=S3_BUCKET, MaxKeys=1)
        status['s3'] = {'active': True, 'message': f'Connected to bucket: {S3_BUCKET}'}
    except Exception as e:
        status['s3'] = {'active': False, 'message': f"S3: {str(e)}"}

    # 2. DynamoDB — Use list_tables (lighter than describe_table)
    try:
        ddb = get_client('dynamodb')
        result = ddb.list_tables()
        tables = result.get('TableNames', [])
        if DYNAMODB_TABLE in tables:
            status['dynamodb'] = {'active': True, 'message': f'Table {DYNAMODB_TABLE} is active'}
        else:
            status['dynamodb'] = {'active': False, 'message': f'Table {DYNAMODB_TABLE} not found in account'}
    except Exception as e:
        status['dynamodb'] = {'active': False, 'message': f'DynamoDB: {str(e)[:120]}'}

    # 3. SNS — Use list_topics (lighter than get_topic_attributes)
    if not SNS_TOPIC_ARN:
        status['sns'] = {'active': False, 'message': 'SNS Topic ARN not configured in .env'}
    else:
        try:
            sns = get_client('sns')
            pages = sns.list_topics()
            found = any(t['TopicArn'] == SNS_TOPIC_ARN for t in pages.get('Topics', []))
            if found:
                status['sns'] = {'active': True, 'message': f'SNS Topic active: {SNS_TOPIC_ARN.split(":")[-1]}'}
            else:
                status['sns'] = {'active': False, 'message': 'SNS Topic ARN not found in account'}
        except Exception as e:
            status['sns'] = {'active': False, 'message': f'SNS: {str(e)[:120]}'}

    # 4. Lambda — Use list_functions (lighter than get_function)
    try:
        lam = get_client('lambda')
        result = lam.list_functions(MaxItems=50)
        names = [f['FunctionName'] for f in result.get('Functions', [])]
        if LAMBDA_FUNCTION_NAME in names:
            status['lambda'] = {'active': True, 'message': f'Function {LAMBDA_FUNCTION_NAME} is LIVE'}
        else:
            status['lambda'] = {'active': False, 'message': f'Function {LAMBDA_FUNCTION_NAME} not found'}
    except Exception as e:
        status['lambda'] = {'active': False, 'message': f'Lambda: {str(e)[:120]}'}

    # 5. Cognito — Use list_user_pools (lighter than describe_user_pool)
    if not COGNITO_USER_POOL_ID or not COGNITO_CLIENT_ID:
        status['cognito'] = {'active': False, 'message': 'Cognito IDs not configured in .env'}
    else:
        try:
            cog = get_client('cognito-idp')
            result = cog.list_user_pools(MaxResults=10)
            ids = [p['Id'] for p in result.get('UserPools', [])]
            if COGNITO_USER_POOL_ID in ids:
                status['cognito'] = {'active': True, 'message': f'Cognito pool {COGNITO_USER_POOL_ID} is reachable'}
            else:
                status['cognito'] = {'active': False, 'message': f'Pool {COGNITO_USER_POOL_ID} not found (may still be working)'}
        except Exception as e:
            status['cognito'] = {'active': False, 'message': f'Cognito: {str(e)[:120]}'}

    return status

