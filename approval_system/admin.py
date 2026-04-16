from django.contrib import admin
from .models import Document, ApprovalRequest, UserProfile

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'uploader', 'category', 'uploaded_at')
    list_filter = ('category',)
    search_fields = ('title', 'uploader__username')

@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ('document', 'approver', 'status', 'updated_at')
    list_filter = ('status',)
