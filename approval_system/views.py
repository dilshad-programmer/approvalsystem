from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from .models import Document, ApprovalRequest, UserProfile, ROLE_CHOICES
from django.utils import timezone
from .aws_utils import (
    upload_to_s3, send_sns_notification, log_workflow_action, 
    authenticate_user, register_user, get_document_logs, generate_presigned_url,
    trigger_lambda_process, delete_from_s3, check_aws_connectivity, confirm_user,
    delete_cognito_user
)
from django.views.decorators.csrf import csrf_exempt
import uuid
import logging

logger = logging.getLogger(__name__)

# Helper to check roles
def is_admin(user):
    try:
        return user.is_authenticated and user.userprofile.role == 'ADMIN'
    except:
        return False

def is_requester(user):
    try:
        return user.is_authenticated and user.userprofile.role == 'REQUESTER'
    except:
        return False

def is_approver(user):
    try:
        return user.is_authenticated and user.userprofile.role == 'APPROVER'
    except:
        return False

# --- Authentication Views ---

def home_view(request):
    """Public landing page."""
    try:
        if request.user.is_authenticated:
            try:
                role = request.user.userprofile.role
                if role == 'ADMIN': return redirect('admin_dashboard')
                elif role == 'APPROVER': return redirect('approver_dashboard')
                else: return redirect('request_dashboard')
            except:
                return redirect('request_dashboard')
    except:
        pass
    return render(request, 'approval_system/index.html')

def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        role = 'REQUESTER' 

        # 1. Register in Cognito
        cognito_resp = register_user(username, password, email)
        if cognito_resp and 'Error' in cognito_resp:
            messages.error(request, f"AWS Cognito Error: {cognito_resp['Error']}")
            return render(request, 'approval_system/register.html')
        
        try:
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(username=username, email=email, password=password)
                UserProfile.objects.create(user=user, role=role)
                log_workflow_action(0, "USER_REGISTERED", username, f"Account created with role: {role}")
                messages.success(request, "Registration Complete: Your account has been provisioned. You may now proceed to log in.")
                return redirect('login')
            else:
                messages.error(request, "Username already exists in local database.")
        except Exception as e:
            logger.warning(f"Registration DB Error: {e}")
            messages.error(request, "Database error during registration. Please try again.")
            
    return render(request, 'approval_system/register.html')

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        try:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                log_workflow_action(0, "USER_LOGIN", username, "User logged in")
                
                try:
                    role = user.userprofile.role
                except:
                    role = 'REQUESTER'

                if role == 'ADMIN': return redirect('admin_dashboard')
                elif role == 'APPROVER': return redirect('approver_dashboard')
                else: return redirect('request_dashboard')
            else:
                messages.error(request, "Invalid username or password.")
        except Exception as e:
            logger.warning(f"Login Error: {e}")
            messages.error(request, "An unexpected error occurred during login.")

    return render(request, 'approval_system/login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

def verify_view(request):
    """View to handle Cognito account verification code submission."""
    if request.method == 'POST':
        username = request.POST.get('username')
        code = request.POST.get('code')
        
        resp = confirm_user(username, code)
        if resp and 'Error' not in resp:
            log_workflow_action(0, "USER_VERIFIED", username, "Account confirmed via verification code")
            messages.success(request, "Verification Successful: Your account is now confirmed. You may proceed to log in.")
            return redirect('login')
        else:
            error_msg = resp.get('Error', 'Unknown verification error') if resp else 'Connection failed'
            messages.error(request, f"Verification Failed: {error_msg}")
            
    return render(request, 'approval_system/verify_account.html')

# --- Dashboard Views ---

@login_required
def request_dashboard(request):
    try:
        docs = Document.objects.filter(uploader=request.user).order_by('-uploaded_at')
        # Generate fresh pre-signed URLs for display
        for doc in docs:
            doc.fresh_url = generate_presigned_url(doc.s3_key)
    except Exception as e:
        logger.warning(f"Request Dashboard Error: {e}")
        docs = []
    return render(request, 'approval_system/dashboard_requester.html', {'documents': docs})

@login_required
def approver_dashboard(request):
    """Approver / Manager Dashboard."""
    if not is_approver(request.user): return redirect('login')
    try:
        requests = ApprovalRequest.objects.filter(approver=request.user, status='PENDING')
        for req in requests:
            req.document.fresh_url = generate_presigned_url(req.document.s3_key)
    except Exception as e:
        logger.warning(f"Approver Dashboard Error: {e}")
        requests = []
    return render(request, 'approval_system/dashboard_approver.html', {'requests': requests})

@login_required
def admin_dashboard(request):
    """Level 2 Admin Dashboard."""
    if not is_admin(request.user): return redirect('login')
    
    pending_approvals = []
    all_docs = []
    all_users = []
    
    try:
        pending_approvals = list(ApprovalRequest.objects.filter(status='PENDING'))
        for req in pending_approvals:
            req.document.fresh_url = generate_presigned_url(req.document.s3_key)
            
        all_docs = list(Document.objects.all().order_by('-uploaded_at'))
        for doc in all_docs:
            doc.fresh_url = generate_presigned_url(doc.s3_key)
            
        all_users = list(User.objects.all())
    except Exception as e:
        logger.warning(f"Admin Dashboard Error: {e}")
    
    return render(request, 'approval_system/dashboard_admin.html', {
        'pending_approvals': pending_approvals,
        'documents': all_docs, 
        'users': all_users
    })

@login_required
def update_user_role(request, user_id):
    if not is_admin(request.user):
        messages.error(request, "Unauthorized. Admin access required.")
        return redirect('admin_dashboard')
    
    try:
        target_user = get_object_or_404(User, id=user_id)
        new_role = request.POST.get('role')
        
        if new_role in [role[0] for role in ROLE_CHOICES]:
            profile, _ = UserProfile.objects.get_or_create(user=target_user)
            old_role = profile.role
            profile.role = new_role
            profile.save()
            log_workflow_action(0, "USER_ROLE_UPDATED", request.user.username, f"Admin updated {target_user.username} from {old_role} to {new_role}")
            messages.success(request, f"User {target_user.username} updated to {new_role}.")
        else:
            messages.error(request, "Invalid role selected.")
    except Exception as e:
        logger.warning(f"Update Role Error: {e}")
        messages.error(request, "Database error while updating user role.")
        
    return redirect('admin_dashboard')

@login_required
def delete_user_view(request, user_id):
    if not is_admin(request.user):
        messages.error(request, "Unauthorized. Admin access required.")
        return redirect('admin_dashboard')
    
    try:
        target_user = get_object_or_404(User, id=user_id)
        if target_user == request.user:
            messages.error(request, "Security Restriction: You cannot delete your own administrative account.")
            return redirect('admin_dashboard')
        
        delete_cognito_user(target_user.username)
        log_workflow_action(0, "USER_DELETED", request.user.username, f"Admin deleted account: {target_user.username}")
        target_user.delete()
        messages.success(request, f"Process Complete: Account '{target_user.username}' and associated cloud metadata have been removed.")
    except Exception as e:
        logger.warning(f"Delete User Error: {e}")
        messages.error(request, "Database error during user deletion.")
        
    return redirect('admin_dashboard')

@login_required
def upload_document(request):
    if request.method == 'POST':
        try:
            title = request.POST.get('title')
            description = request.POST.get('description')
            category = request.POST.get('category')
            approver_id = request.POST.get('approver_id')
            file = request.FILES.get('document_file')

            if file:
                s3_key = f"documents/{uuid.uuid4()}_{file.name}"
                s3_url = upload_to_s3(file, s3_key)

                if s3_url:
                    doc = Document.objects.create(
                        title=title, description=description, category=category,
                        file_name=file.name, s3_key=s3_key, s3_url=s3_url, uploader=request.user
                    )
                    approver = User.objects.get(id=approver_id)
                    ApprovalRequest.objects.create(document=doc, approver=approver)
                    
                    log_workflow_action(doc.id, "UPLOADED", request.user.username, "Initial upload")
                    send_sns_notification("New Approval Request", f"A new document '{title}' has been submitted.")
                    
                    trigger_lambda_process({'document_id': doc.id, 'action': 'UPLOAD'})
                    messages.success(request, "Submission Successful: The document has been securely stored.")
                    return redirect('request_dashboard')
                else:
                    messages.error(request, "S3 Upload failed.")
        except Exception as e:
            logger.warning(f"Upload Document Error: {e}")
            messages.error(request, "An unexpected error occurred during upload.")
        
    try:
        approvers = User.objects.filter(userprofile__role='APPROVER')
    except:
        approvers = []
    return render(request, 'approval_system/upload.html', {'approvers': approvers})

@login_required
def process_approval(request, request_id):
    try:
        app_req = ApprovalRequest.objects.get(id=request_id)
    except:
        return redirect('admin_dashboard')
    
    if request.method == 'POST':
        try:
            action = request.POST.get('action') 
            comments = request.POST.get('comments')
            user_role = request.user.userprofile.role

            app_req.status = action
            app_req.comments = comments
            app_req.save()

            log_workflow_action(app_req.document.id, action, request.user.username, comments)
            send_sns_notification(f"Document {action}", f"Document '{app_req.document.title}' has been {action}.")
            trigger_lambda_process({'document_id': app_req.document.id, 'action': action})

            messages.success(request, f"Review Complete: Document {action}.")
            return redirect('admin_dashboard' if user_role == 'ADMIN' else 'approver_dashboard')
        except Exception as e:
            logger.warning(f"Process Approval Error: {e}")
            messages.error(request, "Database error during processing.")

    app_req.document.fresh_url = generate_presigned_url(app_req.document.s3_key)
    return render(request, 'approval_system/process_approval.html', {'request': app_req})

@login_required
def document_history(request, doc_id):
    try:
        doc = Document.objects.get(id=doc_id)
        cloud_logs = get_document_logs(doc.id)
        doc.fresh_url = generate_presigned_url(doc.s3_key)
    except:
        doc = None
        cloud_logs = []
    return render(request, 'approval_system/history.html', {'document': doc, 'cloud_logs': cloud_logs})

@login_required
def delete_document(request, doc_id):
    try:
        doc = get_object_or_404(Document, id=doc_id)
        if not is_admin(request.user) and doc.uploader != request.user:
            messages.error(request, "Unauthorized to delete.")
            return redirect('request_dashboard')

        if request.method == 'POST':
            if delete_from_s3(doc.s3_key):
                log_workflow_action(doc.id, "DELETED", request.user.username)
                doc.delete()
                messages.success(request, "Document permanently removed.")
            else:
                messages.error(request, "Failed to delete from S3.")
            return redirect('admin_dashboard' if is_admin(request.user) else 'request_dashboard')
    except Exception as e:
        logger.warning(f"Delete Document Error: {e}")
        messages.error(request, "Database error during deletion.")

    return render(request, 'approval_system/confirm_delete.html', {'document': doc})

@login_required
def service_check(request):
    if not is_admin(request.user): return redirect('login')
    status = check_aws_connectivity()
    return render(request, 'approval_system/service_check.html', {'status': status})
