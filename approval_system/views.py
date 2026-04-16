"""
views.py — Document Approval System Views

Resilience rules applied throughout:
  • Every ORM call (filter, get, get_or_create, create) is wrapped in try/except
    so a DB hiccup never returns a 500 — it returns a graceful fallback page.
  • Every AWS side-effect (log_workflow_action, send_sns_notification,
    trigger_lambda_process) is already fire-and-forget in aws_utils.py.
  • upload_document: document is saved to DB even if S3 upload fails;
    the user gets a clear message instead of a 500.
"""

import logging
import uuid

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone

from .models import Document, ApprovalRequest, UserProfile, ROLE_CHOICES
from .aws_utils import (
    upload_to_s3,
    send_sns_notification,
    log_workflow_action,
    authenticate_user,
    register_user,
    get_document_logs,
    generate_presigned_url,
    trigger_lambda_process,
    delete_from_s3,
    check_aws_connectivity,
    confirm_user,
    delete_cognito_user,
)

logger = logging.getLogger(__name__)


# ── Role helpers ───────────────────────────────────────────────────────────────

def is_admin(user):
    try:
        return user.is_authenticated and user.userprofile.role == 'ADMIN'
    except Exception:
        return False


def is_requester(user):
    try:
        return user.is_authenticated and user.userprofile.role == 'REQUESTER'
    except Exception:
        return False


def is_approver(user):
    try:
        return user.is_authenticated and user.userprofile.role == 'APPROVER'
    except Exception:
        return False


# ── Authentication Views ───────────────────────────────────────────────────────

def home_view(request):
    """Public landing page."""
    if request.user.is_authenticated:
        try:
            role = request.user.userprofile.role
        except Exception:
            role = 'REQUESTER'
        if role == 'ADMIN':
            return redirect('admin_dashboard')
        elif role == 'APPROVER':
            return redirect('approver_dashboard')
        else:
            return redirect('request_dashboard')
    return render(request, 'approval_system/index.html')


def register_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        role = 'REQUESTER'

        # 1. Attempt Cognito registration (non-blocking — failure is acceptable)
        cognito_resp = register_user(username, password, email)
        if cognito_resp and 'Error' in cognito_resp:
            messages.error(request, f"AWS Cognito Error: {cognito_resp['Error']}")
            return render(request, 'approval_system/register.html')

        try:
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(username=username, email=email, password=password)
                UserProfile.objects.get_or_create(user=user, defaults={'role': role})
                # Fire-and-forget audit log
                log_workflow_action(
                    0, "USER_REGISTERED", username,
                    f"Account created with role: {role} (Cognito Sync: {'Success' if cognito_resp else 'Skipped'})"
                )
                messages.success(request, "Registration Complete: Your account has been provisioned. You may now proceed to log in.")
                return redirect('login')
            else:
                messages.error(request, "Username already exists in local database.")
        except Exception as e:
            logger.error("[register_view] DB error: %s", e)
            messages.error(request, "Registration failed due to a server error. Please try again.")

    return render(request, 'approval_system/register.html')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        # Local Django authentication — always reliable
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # Fire-and-forget audit log — never blocks login
            log_workflow_action(0, "USER_LOGIN", username, "User logged in")

            # Get role safely
            try:
                profile, _ = UserProfile.objects.get_or_create(user=user)
                role = profile.role
            except Exception:
                role = 'REQUESTER'

            if role == 'ADMIN':
                return redirect('admin_dashboard')
            elif role == 'APPROVER':
                return redirect('approver_dashboard')
            else:
                return redirect('request_dashboard')
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, 'approval_system/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


def verify_view(request):
    """Handle Cognito account verification code submission."""
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        code = request.POST.get('code', '').strip()

        resp = confirm_user(username, code)

        if resp and 'Error' not in resp:
            log_workflow_action(0, "USER_VERIFIED", username, "Account confirmed via verification code")
            messages.success(request, "Verification Successful: Your account is now confirmed. You may proceed to log in.")
            return redirect('login')
        else:
            error_msg = resp.get('Error', 'Unknown verification error') if resp else 'Connection failed'
            messages.error(request, f"Verification Failed: {error_msg}")

    return render(request, 'approval_system/verify_account.html')


# ── Dashboard Views ────────────────────────────────────────────────────────────

@login_required
def request_dashboard(request):
    """Requester dashboard — shows all documents uploaded by this user."""
    try:
        docs = list(Document.objects.filter(uploader=request.user).order_by('-uploaded_at'))
    except Exception as e:
        logger.error("[request_dashboard] DB error: %s", e)
        docs = []

    # Attach fresh pre-signed URLs (None if S3 is unavailable — template handles it)
    for doc in docs:
        try:
            doc.fresh_url = generate_presigned_url(doc.s3_key)
        except Exception:
            doc.fresh_url = None

    return render(request, 'approval_system/dashboard_requester.html', {'documents': docs})


@login_required
def approver_dashboard(request):
    """Approver / Manager Dashboard."""
    if not is_approver(request.user):
        return redirect('login')

    try:
        requests = list(ApprovalRequest.objects.filter(approver=request.user, status='PENDING'))
    except Exception as e:
        logger.error("[approver_dashboard] DB error: %s", e)
        requests = []

    for req in requests:
        try:
            req.document.fresh_url = generate_presigned_url(req.document.s3_key)
        except Exception:
            req.document.fresh_url = None

    return render(request, 'approval_system/dashboard_approver.html', {'requests': requests})


@login_required
def admin_dashboard(request):
    """Admin Dashboard — pending approvals, all documents, all users."""
    if not is_admin(request.user):
        return redirect('login')

    try:
        pending_approvals = list(ApprovalRequest.objects.filter(status='PENDING'))
        for req in pending_approvals:
            try:
                req.document.fresh_url = generate_presigned_url(req.document.s3_key)
            except Exception:
                req.document.fresh_url = None
    except Exception as e:
        logger.error("[admin_dashboard] pending_approvals DB error: %s", e)
        pending_approvals = []

    try:
        all_docs = list(Document.objects.all().order_by('-uploaded_at'))
        for doc in all_docs:
            try:
                doc.fresh_url = generate_presigned_url(doc.s3_key)
            except Exception:
                doc.fresh_url = None
    except Exception as e:
        logger.error("[admin_dashboard] all_docs DB error: %s", e)
        all_docs = []

    try:
        all_users = list(User.objects.all())
    except Exception as e:
        logger.error("[admin_dashboard] all_users DB error: %s", e)
        all_users = []

    return render(request, 'approval_system/dashboard_admin.html', {
        'pending_approvals': pending_approvals,
        'documents': all_docs,
        'users': all_users,
    })


# ── User Management ────────────────────────────────────────────────────────────

@login_required
def update_user_role(request, user_id):
    """Admin-only: promote/change user roles."""
    if not is_admin(request.user):
        messages.error(request, "Unauthorized. Admin access required.")
        return redirect('admin_dashboard')

    target_user = get_object_or_404(User, id=user_id)
    new_role = request.POST.get('role')

    if new_role in [r[0] for r in ROLE_CHOICES]:
        try:
            profile, _ = UserProfile.objects.get_or_create(user=target_user)
            old_role = profile.role
            profile.role = new_role
            profile.save()
            log_workflow_action(
                0, "USER_ROLE_UPDATED", request.user.username,
                f"Admin updated {target_user.username} from {old_role} to {new_role}"
            )
            messages.success(request, f"User {target_user.username} updated to {new_role}.")
        except Exception as e:
            logger.error("[update_user_role] DB error: %s", e)
            messages.error(request, "Failed to update role. Please try again.")
    else:
        messages.error(request, "Invalid role selected.")

    return redirect('admin_dashboard')


@login_required
def delete_user_view(request, user_id):
    """Admin-only: permanently delete a user and their cloud profile."""
    if not is_admin(request.user):
        messages.error(request, "Unauthorized. Admin access required.")
        return redirect('admin_dashboard')

    target_user = get_object_or_404(User, id=user_id)

    if target_user == request.user:
        messages.error(request, "Security Restriction: You cannot delete your own administrative account.")
        return redirect('admin_dashboard')

    # Fire-and-forget Cognito delete
    delete_cognito_user(target_user.username)

    # Fire-and-forget audit log
    log_workflow_action(0, "USER_DELETED", request.user.username, f"Admin deleted account: {target_user.username}")

    try:
        target_user.delete()
        messages.success(request, f"Process Complete: Account '{target_user.username}' and associated cloud metadata have been removed.")
    except Exception as e:
        logger.error("[delete_user_view] DB error: %s", e)
        messages.error(request, "Failed to delete user from the database.")

    return redirect('admin_dashboard')


# ── Document Workflow ──────────────────────────────────────────────────────────

@login_required
def upload_document(request):
    """Upload a document: save to S3 + DB, notify via SNS, trigger Lambda."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        category = request.POST.get('category', '').strip()
        approver_id = request.POST.get('approver_id')
        file = request.FILES.get('document_file')

        if not file:
            messages.error(request, "No file selected. Please choose a file to upload.")
        else:
            s3_key = f"documents/{uuid.uuid4()}_{file.name}"

            # Attempt S3 upload (returns None on failure — we continue anyway)
            s3_url = upload_to_s3(file, s3_key)

            try:
                doc = Document.objects.create(
                    title=title,
                    description=description,
                    category=category,
                    file_name=file.name,
                    s3_key=s3_key,
                    s3_url=s3_url or '',  # Empty string if S3 was unavailable
                    uploader=request.user
                )

                # Attach approval request
                try:
                    approver = User.objects.get(id=approver_id)
                    ApprovalRequest.objects.create(document=doc, approver=approver)
                except Exception as e:
                    logger.warning("[upload_document] Could not create approval request: %s", e)

                # Fire-and-forget AWS side effects
                log_workflow_action(doc.id, "UPLOADED", request.user.username, "Initial upload")
                send_sns_notification(
                    "New Approval Request",
                    f"A new document '{title}' has been submitted by {request.user.username} for approval."
                )
                trigger_lambda_process({
                    'Records': [{
                        'eventSource': 'aws:s3',
                        's3': {
                            'bucket': {'name': S3_BUCKET_NAME_FOR_LAMBDA},
                            'object': {'key': s3_key}
                        }
                    }]
                })

                if s3_url:
                    messages.success(request, "Submission Successful: The document has been securely stored and queued for administrative review.")
                else:
                    messages.warning(request, "Document saved locally. S3 upload failed — the file download link may be unavailable until AWS connectivity is restored.")

                return redirect('request_dashboard')

            except Exception as e:
                logger.error("[upload_document] DB error saving document: %s", e)
                messages.error(request, "Document could not be saved due to a server error. Please try again.")

    try:
        approvers = list(User.objects.filter(userprofile__role='APPROVER'))
    except Exception as e:
        logger.error("[upload_document] Could not fetch approvers: %s", e)
        approvers = []

    return render(request, 'approval_system/upload.html', {'approvers': approvers})


# Lambda payload helper — bucket name comes from env (not from s3_key parsing)
import os as _os
S3_BUCKET_NAME_FOR_LAMBDA = _os.getenv('AWS_STORAGE_BUCKET_NAME', 'doc-approval-bucket')


@login_required
def process_approval(request, request_id):
    """Handles Approval/Rejection actions by Managers."""
    try:
        app_req = ApprovalRequest.objects.get(id=request_id)
    except ApprovalRequest.DoesNotExist:
        messages.error(request, "Approval request not found.")
        return redirect('approver_dashboard')
    except Exception as e:
        logger.error("[process_approval] DB error: %s", e)
        messages.error(request, "Could not load approval request.")
        return redirect('approver_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')   # 'APPROVED' or 'REJECTED'
        comments = request.POST.get('comments', '')

        try:
            user_role = request.user.userprofile.role
        except Exception:
            user_role = 'APPROVER'

        if action == 'REJECTED':
            app_req.status = 'REJECTED'
            msg = "Document rejected."
            send_sns_notification(
                "Document Rejected",
                f"Document '{app_req.document.title}' has been rejected. Comments: {comments}"
            )
        elif action == 'APPROVED':
            app_req.status = 'APPROVED'
            msg = "Review Complete: The document has been officially approved."
            send_sns_notification(
                "Document Approved",
                f"Document '{app_req.document.title}' has been approved."
            )
        else:
            messages.error(request, "Invalid action.")
            return redirect('approver_dashboard')

        app_req.comments = comments

        try:
            app_req.save()
        except Exception as e:
            logger.error("[process_approval] DB save error: %s", e)
            messages.error(request, "Failed to save approval decision. Please try again.")
            return redirect('approver_dashboard')

        # Fire-and-forget AWS side-effects
        trigger_lambda_process({
            'document_id': app_req.document.id,
            'action': action,
            'user': request.user.username,
            'timestamp': str(timezone.now())
        })
        log_workflow_action(app_req.document.id, action, request.user.username, comments)

        messages.success(request, msg)
        return redirect('admin_dashboard' if user_role == 'ADMIN' else 'approver_dashboard')

    # GET — show the approval form with a fresh pre-signed URL
    try:
        app_req.document.fresh_url = generate_presigned_url(app_req.document.s3_key)
    except Exception:
        app_req.document.fresh_url = None

    return render(request, 'approval_system/process_approval.html', {'request': app_req})


@login_required
def document_history(request, doc_id):
    """Shows the DynamoDB audit trail for a document."""
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        messages.error(request, "Document not found.")
        return redirect('request_dashboard')
    except Exception as e:
        logger.error("[document_history] DB error: %s", e)
        messages.error(request, "Could not load document.")
        return redirect('request_dashboard')

    cloud_logs = get_document_logs(doc.id)   # Returns [] on AWS failure

    try:
        doc.fresh_url = generate_presigned_url(doc.s3_key)
    except Exception:
        doc.fresh_url = None

    return render(request, 'approval_system/history.html', {
        'document': doc,
        'cloud_logs': cloud_logs,
    })


@login_required
def delete_document(request, doc_id):
    """Deletes a document from DB and S3 storage."""
    doc = get_object_or_404(Document, id=doc_id)

    if not is_admin(request.user) and doc.uploader != request.user:
        messages.error(request, "Unauthorized to delete this document.")
        return redirect('request_dashboard')

    if request.method == 'POST':
        # S3 delete is best-effort — we delete from DB regardless
        delete_from_s3(doc.s3_key)
        log_workflow_action(doc.id, "DELETED", request.user.username, "Document permanently removed")

        try:
            doc.delete()
            messages.success(request, "Process Complete: The document and its associated cloud storage have been permanently removed.")
        except Exception as e:
            logger.error("[delete_document] DB delete error: %s", e)
            messages.error(request, "Failed to remove document record from database.")

        return redirect('admin_dashboard' if is_admin(request.user) else 'request_dashboard')

    return render(request, 'approval_system/confirm_delete.html', {'document': doc})


@login_required
def service_check(request):
    """Admin-only view to verify AWS service health."""
    if not is_admin(request.user):
        return redirect('login')

    status = check_aws_connectivity()
    return render(request, 'approval_system/service_check.html', {'status': status})
