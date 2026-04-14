# Setup Instructions: Cloud Document Approval System

This guide will help you set up the project and connect the required AWS services.

## 1. Prerequisites
- Python 3.8+
- Django 3.2+
- Boto3 (`pip install boto3`)
- An active AWS Account

## 2. AWS Service Setup

### A. Amazon S3 (Storage)
1. Go to the S3 Console.
2. Create a bucket named `doc-approval-bucket` (or your preferred name).
3. Ensure "Block all public access" is ON (the app uses pre-signed URLs for security).

### B. Amazon SNS (Notifications)
1. Go to the SNS Console.
2. Create a **Topic** (Standard).
3. Create a **Subscription** for your email address to this topic.
4. Confirm the subscription from your email inbox.

### C. Amazon DynamoDB (Audit Logs)
1. Go to the DynamoDB Console.
2. Create a table named `DocumentApprovalLogs`.
3. Set Northern Key (Partition Key) as `LogID` (String).

### D. Amazon Cognito (User Auth)
1. Go to the Cognito Console.
2. Create a **User Pool**.
3. Create an **App Client** (disable "Generate client secret" for simple usage).
4. Note the User Pool ID and App Client ID.

### E. AWS Lambda (Automation)
1. Create a Lambda function named `ProcessDocumentApproval`.
2. Use Python 3.9+ runtime.
3. Copy the code from `aws_lambda/process_approval.py`.
4. Ensure the Lambda has permissions to read from S3 and write to DynamoDB.

## 3. Local Project Setup
1. Clone the repository to your computer.
2. Create a virtual environment: `python -m venv .venv`.
3. Activate it and install requirements: `pip install -r requirements.txt`.
4. Create a `.env` file based on `.env.example` and fill in your AWS credentials.
5. Run migrations: `python manage.py migrate`.
6. Start the server: `python manage.py run_server`.

## 4. Testing the Workflow
1. **Register**: Create an account (it will sync to Cognito).
2. **Upload**: Submit a document; it will go to S3 and notify via SNS.
3. **Approve**: Log in with an Approver role to review and decision.
4. **Audit**: Use the Admin dashboard to view logs from DynamoDB.
