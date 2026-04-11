import boto3
import zipfile
import io
import os
from dotenv import load_dotenv

# Load env variables
load_dotenv()

def deploy_lambda():
    """
    Zips the local Lambda source and updates the 'ProcessDocumentApproval' 
    function on AWS using credentials from .env.
    """
    function_name = 'ProcessDocumentApproval'
    lambda_src = 'aws_lambda/process_approval.py'
    
    print(f"Preparing to deploy {lambda_src} to {function_name}...")
    
    if not os.path.exists(lambda_src):
        print(f"[ERROR] {lambda_src} not found.")
        return

    # 1. Create Zip in Memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.write(lambda_src, arcname='process_approval.py')
    
    zip_data = zip_buffer.getvalue()
    
    # 2. Update Lambda Code
    try:
        client = boto3.client(
            'lambda',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
            region_name=os.getenv('AWS_REGION', 'us-west-2')
        )
        
        try:
            # Try to update existing function
            response = client.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_data
            )
            print(f"[OK] Lambda updated. Version: {response['Version']}")
        except client.exceptions.ResourceNotFoundException:
            print(f"[INFO] Function {function_name} not found. Attempting to CREATE...")
            # To create, we need a Role. In a lab, we can often guess the LabRole
            sts = boto3.client(
                'sts',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
                region_name=os.getenv('AWS_REGION', 'us-west-2')
            )
            account_id = sts.get_caller_identity()['Account']
            role_arn = f"arn:aws:iam::{account_id}:role/LabRole"
            
            response = client.create_function(
                FunctionName=function_name,
                Runtime='python3.9',
                Role=role_arn,
                Handler='process_approval.lambda_handler',
                Code={'ZipFile': zip_data},
                Description='Background processing for document approvals',
                Timeout=15,
                MemorySize=128
            )
            print(f"[OK] Lambda created. ARN: {response['FunctionArn']}")
        
    except Exception as e:
        print(f"[ERROR] Deployment failed: {str(e)}")
        print("\nTip: If you are in an AWS Lab, ensure the 'LabRole' exists or provide a valid IAM Role ARN.")

if __name__ == "__main__":
    deploy_lambda()
