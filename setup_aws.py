"""
Setup script: Creates all required AWS resources for the Document Approval System.
Run once: python setup_aws.py
"""
import boto3, os, sys, json, time
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

key    = os.getenv('AWS_ACCESS_KEY_ID')
secret = os.getenv('AWS_SECRET_ACCESS_KEY')
token  = os.getenv('AWS_SESSION_TOKEN')
region = os.getenv('AWS_REGION', 'us-west-2')

def client(svc):
    return boto3.client(
        svc,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        aws_session_token=token,
        region_name=region
    )

def resource(svc):
    return boto3.resource(
        svc,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        aws_session_token=token,
        region_name=region
    )

# ── 1. Ensure S3 bucket has public-access block & CORS ──────────────────────
print("=== Configuring S3 Bucket ===")
bucket_name = 'doc-approval-bucket'
s3 = client('s3')
try:
    # Block public access (documents are accessed via pre-signed URLs)
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True
        }
    )
    print(f"  [OK] S3 bucket '{bucket_name}' public access blocked.")
except Exception as e:
    print(f"  [WARN] S3 public access block: {e}")

try:
    s3.put_bucket_cors(
        Bucket=bucket_name,
        CORSConfiguration={
            'CORSRules': [{
                'AllowedHeaders': ['*'],
                'AllowedMethods': ['GET', 'PUT', 'POST'],
                'AllowedOrigins': ['http://127.0.0.1:8000', 'http://localhost:8000'],
                'MaxAgeSeconds': 3000
            }]
        }
    )
    print(f"  [OK] S3 CORS configured.")
except Exception as e:
    print(f"  [WARN] S3 CORS: {e}")

# ── 2. Create DynamoDB table ─────────────────────────────────────────────────
print("\n=== Creating DynamoDB Table ===")
table_name = 'DocumentApprovalLogs'
dynamodb = resource('dynamodb')
ddb_client = client('dynamodb')

# Check if table exists
existing = ddb_client.list_tables()['TableNames']
if table_name in existing:
    print(f"  [OK] Table '{table_name}' already exists.")
else:
    try:
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'LogID', 'KeyType': 'HASH'},
            ],
            AttributeDefinitions=[
                {'AttributeName': 'LogID', 'AttributeType': 'S'},
            ],
            BillingMode='PAY_PER_REQUEST'
        )
        # Wait for table to be active
        print(f"  Creating table '{table_name}'... waiting for it to be active...")
        table.wait_until_exists()
        print(f"  [OK] Table '{table_name}' created successfully.")
    except Exception as e:
        print(f"  [ERROR] DynamoDB table creation failed: {e}")

# ── 3. Verify S3 upload works ────────────────────────────────────────────────
print("\n=== Testing S3 Upload ===")
try:
    import io
    test_content = b"This is a test file from setup_aws.py"
    s3.upload_fileobj(io.BytesIO(test_content), bucket_name, 'test/setup_test.txt')
    print(f"  [OK] Test file uploaded to s3://{bucket_name}/test/setup_test.txt")
    
    # Generate a presigned URL to verify access
    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket_name, 'Key': 'test/setup_test.txt'},
        ExpiresIn=300
    )
    print(f"  [OK] Pre-signed URL generated (expires in 5 min)")
except Exception as e:
    print(f"  [ERROR] S3 test failed: {e}")

# ── 4. Test DynamoDB write ───────────────────────────────────────────────────
print("\n=== Testing DynamoDB Write ===")
try:
    import datetime
    table = dynamodb.Table(table_name)
    table.put_item(Item={
        'LogID': 'SETUP_TEST_001',
        'DocumentID': '0',
        'Action': 'SETUP_TEST',
        'User': 'system',
        'Timestamp': datetime.datetime.now().isoformat(),
        'Comments': 'Setup verification record'
    })
    print(f"  [OK] Test record written to DynamoDB table '{table_name}'")
except Exception as e:
    print(f"  [ERROR] DynamoDB write test failed: {e}")

print("\n=== Setup Complete ===")
print(f"  S3 Bucket  : {bucket_name}")
print(f"  DynamoDB   : {table_name}")
print(f"  Region     : {region}")
print("\nNote: Cognito is not available in this AWS lab environment.")
print("      Authentication uses Django's built-in auth system instead.")
print("      SES email sending will work if the sender email is verified in AWS console.")
