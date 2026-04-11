"""
aws_utils.py — AWS Service Integration for Document Approval System
Integrates: Amazon S3, SES, DynamoDB
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
S3_BUCKET       = os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')
SES_SENDER      = os.getenv('AWS_SES_SOURCE_EMAIL', '')
DYNAMODB_TABLE  = os.getenv('AWS_DYNAMODB_TABLE_NAME', 'DocumentApprovalLogs')

# Cognito placeholders (not used — Django auth handles login/register)
COGNITO_USER_POOL_ID = os.getenv('AWS_COGNITO_USER_POOL_ID', '')
COGNITO_CLIENT_ID    = os.getenv('AWS_COGNITO_APP_CLIENT_ID', '')


# ── Boto3 client/resource factory ─────────────────────────────────────────────
def get_client(service_name):
    """Returns a boto3 client with STS temporary credentials support."""
    return boto3.client(
        service_name,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        aws_session_token=AWS_SESSION_TOKEN,
        region_name=AWS_REGION
    )

def get_resource(service_name):
    """Returns a boto3 resource with STS temporary credentials support."""
    return boto3.resource(
        service_name,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        aws_session_token=AWS_SESSION_TOKEN,
        region_name=AWS_REGION
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. AMAZON S3 — Document Storage
# ══════════════════════════════════════════════════════════════════════════════

def upload_to_s3(file_obj, s3_key):
    """
    Uploads a file object to S3.
    Returns a pre-signed URL (valid 1 hour) so files stay private and secure.
    """
    s3 = get_client('s3')
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
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        print(f"[S3] Deleted: s3://{S3_BUCKET}/{s3_key}")
        return True
    except ClientError as e:
        print(f"[S3 ERROR] Delete failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 2. AMAZON SES — Email Notifications
# ══════════════════════════════════════════════════════════════════════════════

def send_email_notification(recipient, subject, body_text):
    """
    Sends an email via Amazon SES.
    Gracefully falls back (prints to console) if SES is not configured.
    """
    if not SES_SENDER or SES_SENDER == 'your_verified_email@example.com':
        print(f"[SES SKIPPED] No verified sender configured. Would have sent:")
        print(f"  To: {recipient} | Subject: {subject}")
        return None

    ses = get_client('ses')
    try:
        response = ses.send_email(
            Source=SES_SENDER,
            Destination={'ToAddresses': [recipient]},
            Message={
                'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                'Body': {
                    'Text': {'Data': body_text, 'Charset': 'UTF-8'},
                    'Html': {
                        'Data': f"""
                        <html><body>
                        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
                          <div style="background:#2c3e50;padding:20px;color:white">
                            <h2>Document Approval System</h2>
                          </div>
                          <div style="padding:20px;background:#f9f9f9">
                            <p>{body_text.replace(chr(10), '<br>')}</p>
                          </div>
                          <div style="padding:10px;text-align:center;color:#888;font-size:12px">
                            Cloud Document Approval System
                          </div>
                        </div>
                        </body></html>
                        """,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )
        print(f"[SES] Email sent to {recipient} — MessageId: {response['MessageId']}")
        return response
    except ClientError as e:
        print(f"[SES ERROR] {e}")
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
    try:
        response = lambda_client.invoke(
            FunctionName='ProcessDocumentApproval',
            InvocationType='Event',   # Async — fire and forget
            Payload=json.dumps(payload).encode()
        )
        print(f"[Lambda] Triggered — StatusCode: {response['StatusCode']}")
        return response
    except ClientError as e:
        print(f"[Lambda SKIPPED] {e}")
        return None

def check_aws_connectivity():
    """Checks the status of all configured AWS services."""
    status = {
        's3': {'active': False, 'message': 'Not Checked'},
        'dynamodb': {'active': False, 'message': 'Not Checked'},
        'ses': {'active': False, 'message': 'Not Checked'},
        'lambda': {'active': False, 'message': 'Not Checked'},
        'cognito': {'active': False, 'message': 'Not Checked'}
    }

    # 1. S3 Check
    try:
        s3 = get_client('s3')
        s3.head_bucket(Bucket=S3_BUCKET)
        status['s3'] = {'active': True, 'message': f'Connected to bucket: {S3_BUCKET}'}
    except Exception as e:
        status['s3'] = {'active': False, 'message': f"S3: {str(e)}"}

    # 2. DynamoDB Check
    try:
        ddb = get_client('dynamodb')
        ddb.describe_table(TableName=DYNAMODB_TABLE)
        status['dynamodb'] = {'active': True, 'message': f'Table {DYNAMODB_TABLE} is active'}
    except Exception as e:
        status['dynamodb'] = {'active': False, 'message': f"DynamoDB: {str(e)}"}

    # 3. SES Check
    if not SES_SENDER or SES_SENDER == 'your_verified_email@example.com':
        status['ses'] = {'active': False, 'message': 'Sender email not configured properly in .env'}
    else:
        try:
            ses = get_client('ses')
            ses.get_send_quota()
            status['ses'] = {'active': True, 'message': f'SES is active with sender: {SES_SENDER}'}
        except Exception as e:
            status['ses'] = {'active': False, 'message': f"SES: {str(e)}"}

    # 4. Lambda Check
    try:
        lam = get_client('lambda')
        lam.get_function(FunctionName='ProcessDocumentApproval')
        status['lambda'] = {'active': True, 'message': 'Lambda function found and reachable'}
    except Exception as e:
        status['lambda'] = {'active': False, 'message': f'Lambda: {str(e)}'}

    # 5. Cognito Check
    if not COGNITO_USER_POOL_ID or not COGNITO_CLIENT_ID:
        status['cognito'] = {'active': False, 'message': 'Cognito IDs not provided in .env (Using Local Auth fallback)'}
    else:
        try:
            cog = get_client('cognito-idp')
            cog.describe_user_pool(UserPoolId=COGNITO_USER_POOL_ID)
            status['cognito'] = {'active': True, 'message': f'Cognito pool {COGNITO_USER_POOL_ID} is reachable'}
        except Exception as e:
            status['cognito'] = {'active': False, 'message': f"Cognito: {str(e)}"}

    return status
