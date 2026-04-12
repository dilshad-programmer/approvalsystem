from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from .models import Document, ApprovalRequest, UserProfile
from .aws_utils import (
    upload_to_s3, send_email_notification, log_workflow_action, 
    authenticate_user, register_user, get_document_logs, generate_presigned_url,
    trigger_lambda_process, delete_from_s3, check_aws_connectivity
)
from django.views.decorators.csrf import csrf_exempt
import uuid

# Helper to check roles
def is_admin(user):
    return user.is_authenticated and user.userprofile.role == 'ADMIN'

def is_requester(user):
    return user.is_authenticated and user.userprofile.role == 'REQUESTER'

def is_reviewer(user):
    return user.is_authenticated and user.userprofile.role == 'REVIEWER'

# --- Authentication Views ---

def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        role = 'REQUESTER' # Default role for all new registrations

        # 1. Register in Cognito
        cognito_resp = register_user(username, password, email)
        if cognito_resp and 'Error' in cognito_resp:
            messages.error(request, f"AWS Cognito Error: {cognito_resp['Error']}")
            return render(request, 'approval_system/register.html')
        
        if not User.objects.filter(username=username).exists():
            user = User.objects.create_user(username=username, email=email, password=password)
            UserProfile.objects.create(user=user, role=role)
            
            # --- Audit Log: DynamoDB ---
            log_workflow_action(0, "USER_REGISTERED", username, f"Account created with role: {role} (Cognito Sync: {'Success' if cognito_resp else 'Skipped'})")
            
            messages.success(request, "Registration successful. Please log in.")
            return redirect('login')
        else:
            messages.error(request, "Username already exists in local database.")
            
    return render(request, 'approval_system/register.html')

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        # 1. Authenticate with Cognito (Optional verification)
        cog_auth = authenticate_user(username, password)
        # Suppress non-critical Cognito warnings during login to ensure a clean local experience
        
        # 2. Authenticate locally for Django session
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            
            # --- Audit Log: DynamoDB ---
            log_workflow_action(0, "USER_LOGIN", username, "User logged in with local credential verification")
            
            # Redirect based on role
            role = user.userprofile.role
            if role == 'ADMIN': return redirect('admin_dashboard')
            elif role == 'REVIEWER': return redirect('approver_dashboard')
            else: return redirect('request_dashboard')
        else:
            messages.error(request, "Invalid username or password.")
            
    return render(request, 'approval_system/login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

# --- Dashboard Views ---

@login_required
def request_dashboard(request):
    docs = Document.objects.filter(uploader=request.user).order_by('-uploaded_at')
    # Generate fresh pre-signed URLs for display
    for doc in docs:
        doc.fresh_url = generate_presigned_url(doc.s3_key)
    return render(request, 'approval_system/dashboard_requester.html', {'documents': docs})

@login_required
def approver_dashboard(request):
    """Level 1 Reviewer Dashboard."""
    if not is_reviewer(request.user): return redirect('login')
    # Show requests assigned to this reviewer that are PENDING_REVIEW
    requests = ApprovalRequest.objects.filter(approver=request.user, status='PENDING_REVIEW')
    for req in requests:
        req.document.fresh_url = generate_presigned_url(req.document.s3_key)
    return render(request, 'approval_system/dashboard_approver.html', {'requests': requests})

@login_required
def admin_dashboard(request):
    """Level 2 Admin Dashboard."""
    if not is_admin(request.user): return redirect('login')
    
    # Docs needing final approval (Level 2)
    final_approvals = ApprovalRequest.objects.filter(status='PENDING_ADMIN')
    for req in final_approvals:
        req.document.fresh_url = generate_presigned_url(req.document.s3_key)
        
    all_docs = Document.objects.all().order_by('-uploaded_at')
    for doc in all_docs:
        doc.fresh_url = generate_presigned_url(doc.s3_key)
    all_users = User.objects.all()
    
    return render(request, 'approval_system/dashboard_admin.html', {
        'final_approvals': final_approvals,
        'documents': all_docs, 
        'users': all_users
    })

@login_required
def update_user_role(request, user_id):
    """
    Admin-only action to promote/change user roles.
    """
    if not is_admin(request.user):
        messages.error(request, "Unauthorized. Admin access required.")
        return redirect('admin_dashboard')
    
    target_user = get_object_or_404(User, id=user_id)
    new_role = request.POST.get('role')
    
    if new_role in [role[0] for role in ROLE_CHOICES]:
        profile, created = UserProfile.objects.get_or_create(user=target_user)
        old_role = profile.role
        profile.role = new_role
        profile.save()

        # --- Audit Log: DynamoDB ---
        log_workflow_action(0, "USER_ROLE_UPDATED", request.user.username, f"Admin updated {target_user.username} from {old_role} to {new_role}")
        
        messages.success(request, f"User {target_user.username} updated to {new_role}.")
    else:
        messages.error(request, "Invalid role selected.")
        
    return redirect('admin_dashboard')

@login_required
def upload_document(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        category = request.POST.get('category')
        approver_id = request.POST.get('approver_id')
        file = request.FILES.get('document_file')

        if file:
            # 1. Upload to S3
            s3_key = f"documents/{uuid.uuid4()}_{file.name}"
            s3_url = upload_to_s3(file, s3_key)

            if s3_url:
                # 2. Save metadata in Django
                doc = Document.objects.create(
                    title=title,
                    description=description,
                    category=category,
                    file_name=file.name,
                    s3_key=s3_key,
                    s3_url=s3_url,
                    uploader=request.user
                )

                # 3. Create Approval Request
                approver = User.objects.get(id=approver_id)
                ApprovalRequest.objects.create(document=doc, approver=approver)

                # 4. Log to DynamoDB
                log_workflow_action(doc.id, "UPLOADED", request.user.username, "Initial upload")

                # 5. Notify Approver via SES
                send_email_notification(
                    approver.email,
                    "New Approval Request",
                    f"Hi {approver.username}, a new document '{title}' has been submitted for your approval."
                )

                # 6. Trigger Lambda for background processing
                lambda_payload = {
                    'Records': [{
                        'eventSource': 'aws:s3',
                        's3': {
                            'bucket': {'name': doc.s3_key.split('/')[0]}, # Simple simulation
                            'object': {'key': doc.s3_key}
                        }
                    }]
                }
                trigger_lambda_process(lambda_payload)

                messages.success(request, "Document uploaded and sent to Level 1 Reviewer!")
                return redirect('request_dashboard')
            else:
                messages.error(request, "S3 Upload failed. Check your AWS config.")
        
    approvers = User.objects.filter(userprofile__role='REVIEWER')
    return render(request, 'approval_system/upload.html', {'approvers': approvers})

@login_required
def process_approval(request, request_id):
    """Handles both L1 (Verification) and L2 (Approval) actions."""
    app_req = ApprovalRequest.objects.get(id=request_id)
    
    if request.method == 'POST':
        action = request.POST.get('action') # 'VERIFIED', 'APPROVED', or 'REJECTED'
        comments = request.POST.get('comments')
        user_role = request.user.userprofile.role

        if action == 'REJECTED':
            app_req.status = 'REJECTED'
            msg = "Document rejected."
            # Notify Requester
            send_email_notification(
                app_req.document.uploader.email,
                "Document Rejected",
                f"Your document '{app_req.document.title}' has been rejected.\nComments: {comments}"
            )
        elif user_role == 'REVIEWER' and action == 'VERIFIED':
            app_req.status = 'PENDING_ADMIN'
            msg = "Document verified and sent to Level 2 Admin."
            # Notify Admins
            admins = User.objects.filter(userprofile__role='ADMIN')
            for admin in admins:
                send_email_notification(
                    admin.email,
                    "New Verification Pending Admin Approval",
                    f"A document '{app_req.document.title}' has been verified by {request.user.username} and needs your final approval."
                )
        elif user_role == 'ADMIN' and action == 'APPROVED':
            app_req.status = 'APPROVED'
            msg = "Document fully approved!"
            # Notify Requester
            send_email_notification(
                app_req.document.uploader.email,
                "Document Fully Approved",
                f"Congratulations! Your document '{app_req.document.title}' has received final approval."
            )
        else:
            messages.error(request, "Invalid action for your role or stage.")
            return redirect('login')

        app_req.comments = comments
        app_req.save()

        # --- AWS Lambda Integration: Trigger Background Workflow ---
        if app_req.status in ['APPROVED', 'REJECTED', 'PENDING_ADMIN']:
            payload = {
                'document_id': app_req.document.id,
                'action': action, # VERIFIED, APPROVED, or REJECTED
                'user': request.user.username,
                'timestamp': str(timezone.now())
            }
            trigger_lambda_process(payload)

        # Log to DynamoDB with explicit stage names
        audit_action = "VERIFIED_BY_L1" if action == "VERIFIED" else "APPROVED_BY_L2" if action == "APPROVED" else "REJECTED"
        log_workflow_action(app_req.document.id, audit_action, request.user.username, comments)

        messages.success(request, msg)
        return redirect('admin_dashboard' if user_role == 'ADMIN' else 'approver_dashboard')

    # Generate fresh pre-signed URL for the preview link
    app_req.document.fresh_url = generate_presigned_url(app_req.document.s3_key)
    return render(request, 'approval_system/process_approval.html', {'request': app_req})

@login_required
def document_history(request, doc_id):
    doc = Document.objects.get(id=doc_id)
    # Fetch real logs from DynamoDB
    cloud_logs = get_document_logs(doc.id)
    # Ensure pre-signed URL is attached to the document
    doc.fresh_url = generate_presigned_url(doc.s3_key)
    return render(request, 'approval_system/history.html', {
        'document': doc, 
        'cloud_logs': cloud_logs
    })

@login_required
def delete_document(request, doc_id):
    """Deletes a document from Database and physical S3 storage."""
    doc = get_object_or_404(Document, id=doc_id)
    
    # Allow only Admin or the Uploader to delete
    if not is_admin(request.user) and doc.uploader != request.user:
        messages.error(request, "Unauthorized to delete this document.")
        return redirect('request_dashboard')

    if request.method == 'POST':
        # 1. Delete from S3
        if delete_from_s3(doc.s3_key):
            # 2. Log deletion to DynamoDB
            log_workflow_action(doc.id, "DELETED", request.user.username, "Document permanently removed")
            
            # 3. Delete from Django Database
            doc.delete()
            messages.success(request, "Document and S3 storage cleared successfully.")
        else:
            messages.error(request, "Failed to delete file from S3 storage.")
        
        return redirect('admin_dashboard' if is_admin(request.user) else 'request_dashboard')

    return render(request, 'approval_system/confirm_delete.html', {'document': doc})

@login_required
def service_check(request):
    """Admin-only view to verify AWS service health."""
    if not is_admin(request.user):
        return redirect('login')
    
    status = check_aws_connectivity()
    return render(request, 'approval_system/service_check.html', {'status': status})
