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
]