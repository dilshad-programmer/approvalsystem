import boto3
import os
from dotenv import load_dotenv

# Load env variables for credentials
load_dotenv()

def provision_cognito():
    """
    Creates a Cognito User Pool and App Client, then updates the .env file.
    """
    pool_name = "DocumentApprovalPool"
    region = os.getenv('AWS_REGION', 'us-west-2')
    
    print(f"Starting Cognito Provisioning in {region}...")
    
    idp = boto3.client(
        'cognito-idp',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
        region_name=region
    )
    
    try:
        # 1. Create User Pool
        print(f"Creating User Pool: {pool_name}...")
        pool_resp = idp.create_user_pool(
            PoolName=pool_name,
            UsernameAttributes=['email'],
            AutoVerifiedAttributes=['email'],
            Policies={
                'PasswordPolicy': {
                    'MinimumLength': 8,
                    'RequireUppercase': True,
                    'RequireLowercase': True,
                    'RequireNumbers': True,
                    'RequireSymbols': False
                }
            }
        )
        pool_id = pool_resp['UserPool']['Id']
        print(f"[OK] User Pool Created: {pool_id}")
        
        # 2. Create App Client
        print("Creating App Client...")
        client_resp = idp.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName="DocApprovalAppClient",
            ExplicitAuthFlows=['USER_PASSWORD_AUTH']
        )
        client_id = client_resp['UserPoolClient']['ClientId']
        print(f"[OK] App Client Created: {client_id}")
        
        # 3. Update .env file
        update_env_file(pool_id, client_id)
        
    except Exception as e:
        print(f"[ERROR] Failed to provision Cognito: {str(e)}")

def update_env_file(pool_id, client_id):
    env_path = '.env'
    if not os.path.exists(env_path):
        print("❌ .env file not found. Skipping auto-update.")
        return
        
    with open(env_path, 'r') as f:
        lines = f.readlines()
        
    new_lines = []
    for line in lines:
        if line.startswith('AWS_COGNITO_USER_POOL_ID='):
            new_lines.append(f"AWS_COGNITO_USER_POOL_ID={pool_id}\n")
        elif line.startswith('AWS_COGNITO_APP_CLIENT_ID='):
            new_lines.append(f"AWS_COGNITO_APP_CLIENT_ID={client_id}\n")
        else:
            new_lines.append(line)
            
    with open(env_path, 'w') as f:
        f.writelines(new_lines)
    print("📝 .env file updated with Cognito IDs.")

if __name__ == "__main__":
    provision_cognito()
