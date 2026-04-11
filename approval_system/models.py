from django.db import models
from django.contrib.auth.models import User

# User Roles
ROLE_CHOICES = (
    ('ADMIN', 'Admin (Level 2)'),
    ('REQUESTER', 'Requester / Employee'),
    ('REVIEWER', 'Reviewer (Level 1)'),
)

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='REQUESTER')

    def __str__(self):
        return f"{self.user.username} - {self.role}"

# Document metadata
class Document(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)
    file_name = models.CharField(max_length=255)
    s3_key = models.CharField(max_length=512)
    s3_url = models.URLField(max_length=1024)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploader = models.ForeignKey(User, on_delete=models.CASCADE, related_name='uploaded_documents')

    def __str__(self):
        return self.title

# Approval Workflow status definitions
STATUS_CHOICES = (
    ('PENDING_REVIEW', 'Pending L1 Review'),
    ('PENDING_ADMIN', 'Pending L2 Approval'),
    ('APPROVED', 'Approved'),
    ('REJECTED', 'Rejected'),
)

class ApprovalRequest(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='approval_requests')
    approver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assigned_approvals', help_text="Level 1 Reviewer")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING_REVIEW')
    comments = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.document.title} - {self.status}"
