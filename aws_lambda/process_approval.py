import json
import boto3
import os

# AWS Clients
sns = boto3.client('sns')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    This Lambda function is automatically triggered when a document is uploaded to S3 
    or when an approval action is logged in DynamoDB.
    """
    
    # Log the event for visibility
    print("Received event: " + json.dumps(event, indent=2))
    
    for record in event.get('Records', []):
        # Example: Handle DynamoDB Stream (triggered by log_workflow_action in Django)
        if record.get('eventSource') == 'aws:dynamodb':
            new_image = record['dynamodb'].get('NewImage', {})
            action = new_image.get('Action', {}).get('S')
            document_id = new_image.get('DocumentID', {}).get('S')
            user = new_image.get('User', {}).get('S')
            
            print(f"Workflow Action Detected: {action} on Document {document_id} by {user}")
            
            # Here you could trigger additional logic, like sending a summary email
            # or updating a third-party audit system.
            
        # Example: Handle S3 Put (triggered by file upload)
        elif record.get('eventSource') == 'aws:s3':
            bucket = record['s3']['bucket']['name']
            key = record['s3']['object']['key']
            
            print(f"New Document Uploaded to S3: s3://{bucket}/{key}")
            
            # Example logic: Pre-process document or scan for viruses
            # send_email_notification("admin@example.com", "Security Scan", f"New doc {key}")

    return {
        'statusCode': 200,
        'body': json.dumps('Workflow background processing complete')
    }

def send_sns_notification(subject, body):
    """Utility to send notification from Lambda via SNS Topic."""
    topic_arn = os.environ.get('AWS_SNS_TOPIC_ARN')
    if not topic_arn:
        print("SNS Topic ARN not configured in Lambda environment.")
        return
        
    try:
        sns.publish(
            TopicArn=topic_arn,
            Message=body,
            Subject=subject
        )
        print(f"SNS Notification sent: {subject}")
    except Exception as e:
        print(f"Failed to send SNS notification: {str(e)}")
