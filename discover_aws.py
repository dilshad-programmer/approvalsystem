import boto3, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

key    = os.getenv('AWS_ACCESS_KEY_ID')
secret = os.getenv('AWS_SECRET_ACCESS_KEY')
token  = os.getenv('AWS_SESSION_TOKEN')
region = os.getenv('AWS_REGION', 'us-west-2')

def client(svc, rgn=None):
    return boto3.client(
        svc,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        aws_session_token=token,
        region_name=rgn or region
    )

print('=== S3 BUCKETS ===')
try:
    r = client('s3').list_buckets()
    for b in r['Buckets']:
        print(' -', b['Name'])
    if not r['Buckets']:
        print('  (none found)')
except Exception as e:
    print('S3 Error:', e)

print()
print('=== COGNITO USER POOLS ===')
try:
    r = client('cognito-idp').list_user_pools(MaxResults=10)
    for p in r['UserPools']:
        print(f"  ID: {p['Id']}   Name: {p['Name']}")
    if not r['UserPools']:
        print('  (none found)')
except Exception as e:
    print('Cognito Error:', e)

print()
print('=== COGNITO APP CLIENTS (first pool) ===')
try:
    pools = client('cognito-idp').list_user_pools(MaxResults=10)['UserPools']
    if pools:
        pool_id = pools[0]['Id']
        clients = client('cognito-idp').list_user_pool_clients(UserPoolId=pool_id, MaxResults=10)
        for c in clients['UserPoolClients']:
            print(f"  ClientId: {c['ClientId']}   Name: {c['ClientName']}")
except Exception as e:
    print('Cognito Clients Error:', e)

print()
print('=== SES VERIFIED EMAILS ===')
try:
    r = client('ses').list_verified_email_addresses()
    for e in r['VerifiedEmailAddresses']:
        print(' -', e)
    if not r['VerifiedEmailAddresses']:
        print('  (none found)')
except Exception as e:
    print('SES Error:', e)

print()
print('=== SES VERIFIED IDENTITIES ===')
try:
    r = client('ses').list_identities(IdentityType='Domain')
    for i in r['Identities']:
        print(' - domain:', i)
except Exception as e:
    print('SES Identities Error:', e)

print()
print('=== DYNAMODB TABLES ===')
try:
    r = client('dynamodb').list_tables()
    for t in r['TableNames']:
        print(' -', t)
    if not r['TableNames']:
        print('  (none found)')
except Exception as e:
    print('DynamoDB Error:', e)

print()
print('=== LAMBDA FUNCTIONS ===')
try:
    r = client('lambda').list_functions()
    for f in r['Functions']:
        print(f"  - {f['FunctionName']}  ({f['Runtime']})")
    if not r['Functions']:
        print('  (none found)')
except Exception as e:
    print('Lambda Error:', e)
