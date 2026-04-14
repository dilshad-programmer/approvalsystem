import boto3
import os
import time
from dotenv import load_dotenv

load_dotenv()

ROLE_ARN = "arn:aws:iam::590183817281:role/LabRole"
REGION = os.getenv('AWS_REGION', 'us-east-1')

def deploy_lambda():
    print("Deploying Lambda Function...")
    client = boto3.client(
        'lambda',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
        region_name=REGION
    )
    
    zip_path = os.path.join("aws_lambda", "lambda.zip")
    with open(zip_path, 'rb') as f:
        zip_content = f.read()

    func_name = "ProcessDocumentApproval"
    try:
        # Check if exists
        try:
            client.get_function(FunctionName=func_name)
            print(f"Function {func_name} already exists. Updating...")
            client.update_function_code(FunctionName=func_name, ZipFile=zip_content)
        except client.exceptions.ResourceNotFoundException:
            print(f"Creating new function {func_name}...")
            client.create_function(
                FunctionName=func_name,
                Runtime='python3.9',
                Role=ROLE_ARN,
                Handler='process_approval.lambda_handler',
                Code={'ZipFile': zip_content},
                Timeout=15,
                MemorySize=128,
                Publish=True
            )
        print("Lambda deployed successfully.")
    except Exception as e:
        print(f"Error deploying Lambda: {e}")

def deploy_cognito():
    print("Initializing Cognito User Pool...")
    client = boto3.client(
        'cognito-idp',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
        region_name=REGION
    )
    
    pool_name = "DocumentApprovalPool"
    try:
        # Create User Pool
        response = client.create_user_pool(
            PoolName=pool_name,
            Policies={
                'PasswordPolicy': {
                    'MinimumLength': 8,
                    'RequireUppercase': False,
                    'RequireLowercase': False,
                    'RequireNumbers': False,
                    'RequireSymbols': False
                }
            },
            AutoVerifiedAttributes=['email']
        )
        pool_id = response['UserPool']['Id']
        print(f"User Pool Created: {pool_id}")

        # Create Client
        client_response = client.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName="DjangoAppClient",
            ExplicitAuthFlows=['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH', 'ALLOW_CUSTOM_AUTH']
        )
        client_id = client_response['UserPoolClient']['ClientId']
        print(f"App Client Created: {client_id}")
        
        return pool_id, client_id
    except Exception as e:
        print(f"Error creating Cognito resources: {e}")
        return None, None

def update_env(pool_id, client_id):
    if not pool_id or not client_id:
        return
    
    with open('.env', 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        if line.startswith('AWS_COGNITO_USER_POOL_ID='):
            new_lines.append(f'AWS_COGNITO_USER_POOL_ID={pool_id}\n')
        elif line.startswith('AWS_COGNITO_APP_CLIENT_ID='):
            new_lines.append(f'AWS_COGNITO_APP_CLIENT_ID={client_id}\n')
        else:
            new_lines.append(line)
            
    with open('.env', 'w') as f:
        f.writelines(new_lines)
    print(".env updated with new Cognito IDs.")

if __name__ == "__main__":
    deploy_lambda()
    p_id, c_id = deploy_cognito()
    update_env(p_id, c_id)
