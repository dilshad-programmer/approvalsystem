from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='home'),
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Dashboards
    path('dashboard/requester/', views.request_dashboard, name='request_dashboard'),
    path('dashboard/approver/', views.approver_dashboard, name='approver_dashboard'),
    path('dashboard/admin/', views.admin_dashboard, name='admin_dashboard'),
    
    # Actions
    path('upload/', views.upload_document, name='upload_document'),
    path('approve/<int:request_id>/', views.process_approval, name='process_approval'),
    path('history/<int:doc_id>/', views.document_history, name='document_history'),
    path('delete/<int:doc_id>/', views.delete_document, name='delete_document'),
    path('admin/service-check/', views.service_check, name='service_check'),
]
