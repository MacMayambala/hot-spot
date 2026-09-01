from django.urls import path
from . import views

app_name = 'wifi'

urlpatterns = [
    # Public customer routes
    path('', views.portal, name='portal'),
    path('packages/', views.packages, name='packages'),
    path('checkout/', views.checkout, name='checkout'),
    path('payment/<uuid:reference>/', views.payment_status, name='payment_status'),
    path('payment/<uuid:reference>/status/', views.payment_status_json, name='payment_status_json'),
    path('success/', views.payment_success, name='payment_success'),
    path('failed/', views.payment_failed, name='payment_failed'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('history/', views.history, name='history'),
    path('payment/webhook/', views.webhook, name='webhook'),
    path('voucher-login/', views.voucher_login, name='voucher_login'), 

    # Admin routes (staff only)
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/packages/', views.admin_packages, name='admin_packages'),
    path('admin/packages/create/', views.admin_package_create, name='admin_package_create'),
    path('admin/packages/<int:pk>/edit/', views.admin_package_edit, name='admin_package_edit'),
    path('admin/packages/<int:pk>/toggle/', views.admin_package_toggle, name='admin_package_toggle'),
    path('admin/customers/', views.admin_customers, name='admin_customers'),
    path('admin/customers/<int:pk>/', views.admin_customer_detail, name='admin_customer_detail'),
    path('admin/customers/<int:pk>/toggle/', views.admin_customer_toggle, name='admin_customer_toggle'),
    path('admin/payments/', views.admin_payments, name='admin_payments'),
    path('admin/payments/export/', views.admin_payments_export, name='admin_payments_export'),
    path('admin/mikrotik-status/', views.mikrotik_status, name='mikrotik_status'),
    path('admin/api/device-status/<int:device_id>/', views.device_detail_status, name='device_status_api'),
    path('admin/execute-command/', views.execute_command, name='execute_command'),
    path('admin/branches/add/', views.branch_add, name='branch_add'),
    path('admin/remote-troubleshoot/', views.remote_troubleshoot, name='remote_troubleshoot'),
    path('admin/api/device-info/<int:device_id>/', views.device_info_api, name='device_info_api'),
    path('admin/branches/', views.admin_branches, name='admin_branches'),
    path('admin/branches/add/', views.branch_add, name='branch_add'),
    path('admin/branches/<int:pk>/edit/', views.branch_edit, name='branch_edit'),
    path('admin/branches/<int:pk>/delete/', views.branch_delete, name='branch_delete'),
    path('admin/devices/add/', views.device_add, name='device_add'),
    path('admin/devices/', views.admin_devices, name='admin_devices'),
    path('admin/devices/add/', views.device_add, name='device_add'),
    path('admin/metrics/', views.device_metrics, name='device_metrics'),
    path('admin/devices/<int:pk>/edit/', views.device_edit, name='device_edit'),
    path('admin/devices/<int:pk>/delete/', views.device_delete, name='device_delete'),
    ]