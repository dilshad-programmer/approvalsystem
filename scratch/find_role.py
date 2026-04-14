import boto3
import os
from dotenv import load_dotenv

load_dotenv()

def find_lab_role():
    try:
        iam = boto3.client(
            'iam',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            aws_session_token=os.getenv('AWS_SESSION_TOKEN'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        roles = iam.list_roles()['Roles']
        lab_roles = [role['Arn'] for role in roles if 'LabRole' in role['RoleName']]
        if lab_roles:
            print(f"LAB_ROLE_ARN={lab_roles[0]}")
        else:
            print("No LabRole found.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    find_lab_role()
