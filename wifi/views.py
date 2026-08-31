from django.shortcuts import render

# Create your views here.
import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.contrib import messages
from django.db import transaction
from .models import Customer, WifiDevice, WifiPackage, WifiPayment, WifiSubscription, WifiSession
from .forms import CustomerPhoneForm, PackageSelectForm
from .services.payments import initiate_payment, process_webhook_payload
from .services.subscriptions import get_active_subscription_for_device
import uuid

logger = logging.getLogger(__name__)

# Helper to get or create device from request
def get_or_create_device(request):
    mac = request.GET.get('mac') or request.headers.get('X-MAC-Address') or request.session.get('device_mac')
    ip = request.META.get('REMOTE_ADDR')
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    if not mac:
        # Generate a temporary ID? In real captive portal, router sends MAC.
        # For development, we may use a cookie
        mac = request.session.get('device_mac')
        if not mac:
            mac = f"temp-{str(uuid.uuid4())[:8]}"
            request.session['device_mac'] = mac

    # Try to find existing device
    device, created = WifiDevice.objects.get_or_create(
        mac_address=mac,
        defaults={'ip_address': ip, 'user_agent': user_agent}
    )
    if not created:
        device.last_seen = timezone.now()
        if ip:
            device.ip_address = ip
        device.user_agent = user_agent
        device.save()

    # If device has customer, attach to session
    if device.customer:
        request.session['customer_id'] = device.customer.id

    return device

def portal(request):
    device = get_or_create_device(request)
    # Check if device already has active subscription
    sub = get_active_subscription_for_device(device.mac_address)
    if sub:
        # Redirect to dashboard
        return redirect('wifi:dashboard')
    return render(request, 'wifi/portal.html', {'device': device})

def packages(request):
    device = get_or_create_device(request)
    packages = WifiPackage.objects.filter(is_active=True)
    form = PackageSelectForm()
    if request.method == 'POST':
        form = PackageSelectForm(request.POST)
        if form.is_valid():
            package = form.cleaned_data['package']
            # Store package in session
            request.session['selected_package_id'] = package.id
            return redirect('wifi:checkout')
    return render(request, 'wifi/packages.html', {'packages': packages, 'form': form})

def checkout(request):
    device = get_or_create_device(request)
    package_id = request.session.get('selected_package_id')
    if not package_id:
        return redirect('wifi:packages')
    package = get_object_or_404(WifiPackage, id=package_id, is_active=True)

    if request.method == 'POST':
        form = CustomerPhoneForm(request.POST)
        if form.is_valid():
            phone = form.cleaned_data['phone_number']
            name = form.cleaned_data.get('name', '')
            provider = request.POST.get('provider')
            if provider not in ('mtn', 'airtel'):
                messages.error(request, 'Invalid payment provider')
                return render(request, 'wifi/checkout.html', {'package': package, 'form': form})

            # Initiate payment
            try:
                payment, marz_response = initiate_payment(
                    phone_number=phone,
                    package_id=package.id,
                    provider=provider,
                    device_mac=device.mac_address,
                    name=name
                )
                # Associate device with customer if not already
                if payment.customer and device.customer != payment.customer:
                    device.customer = payment.customer
                    device.save()
                return redirect('wifi:payment_status', reference=payment.reference)
            except Exception as e:
                logger.error(f"Payment initiation failed: {e}")
                messages.error(request, f"Payment initiation failed: {str(e)}")
                return render(request, 'wifi/checkout.html', {'package': package, 'form': form})
    else:
        form = CustomerPhoneForm()
    return render(request, 'wifi/checkout.html', {'package': package, 'form': form})

def payment_status(request, reference):
    payment = get_object_or_404(WifiPayment, reference=reference)
    return render(request, 'wifi/payment_processing.html', {'payment': payment})

def payment_status_json(request, reference):
    payment = get_object_or_404(WifiPayment, reference=reference)
    data = {
        'status': payment.status,
        'message': f"Payment {payment.status}",
        'reference': str(payment.reference)
    }
    if payment.status == 'successful':
        data['success_url'] = request.build_absolute_uri('/wifi/success/')
    elif payment.status in ('failed', 'cancelled', 'expired'):
        data['retry_url'] = request.build_absolute_uri('/wifi/packages/')
    return JsonResponse(data)

def payment_success(request):
    # Optionally show success page; user may be redirected here after webhook
    return render(request, 'wifi/payment_success.html')

def payment_failed(request):
    return render(request, 'wifi/payment_failed.html')

def dashboard(request):
    device = get_or_create_device(request)
    customer = device.customer
    if not customer:
        # No customer associated; go to portal
        return redirect('wifi:portal')

    # Get active subscription
    sub = get_active_subscription_for_device(device.mac_address)
    context = {
        'customer': customer,
        'subscription': sub,
        'device': device,
    }
    return render(request, 'wifi/dashboard.html', context)

def history(request):
    device = get_or_create_device(request)
    customer = device.customer
    if not customer:
        return redirect('wifi:portal')
    payments = WifiPayment.objects.filter(customer=customer).order_by('-created_at')
    return render(request, 'wifi/history.html', {'payments': payments})

import json
import hmac
import hashlib
import logging
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponseBadRequest, JsonResponse
from django.conf import settings
from .services.payments import process_webhook_payload

logger = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def webhook(request):
    """
    MarzPay webhook handler with HMAC signature verification.
    Handles both direct callback and dashboard-wrapped payloads.
    """
    # 1. Validate HMAC signature (if secret is configured)
    webhook_secret = getattr(settings, 'MARZPAY_WEBHOOK_SECRET', None)
    if webhook_secret:
        # Get signature header
        signature_header = request.headers.get('X-MarzPay-Signature')
        if not signature_header:
            logger.warning("Webhook missing X-MarzPay-Signature header")
            return HttpResponseBadRequest("Missing signature header")

        # Parse signature: format "t=1234567890,v1=abcdef..."
        try:
            parts = dict(item.split('=') for item in signature_header.split(','))
            timestamp = parts.get('t')
            signature = parts.get('v1')
        except (ValueError, KeyError):
            logger.warning("Invalid signature format")
            return HttpResponseBadRequest("Invalid signature format")

        # Build the signed string: timestamp + '.' + raw request body
        raw_body = request.body
        signed_string = f"{timestamp}.{raw_body.decode('utf-8')}"

        # Compute HMAC
        computed = hmac.new(
            webhook_secret.encode('utf-8'),
            signed_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Compare in constant time
        if not hmac.compare_digest(computed, signature):
            logger.warning(f"Invalid webhook signature. Expected: {computed}, got: {signature}")
            return HttpResponseBadRequest("Invalid signature")

    # 2. Parse JSON payload
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in webhook: {e}")
        return HttpResponseBadRequest("Invalid JSON")

    logger.info(f"Webhook received: {payload}")

    # 3. Process payment (idempotent)
    try:
        success = process_webhook_payload(payload)
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    if success:
        return JsonResponse({'status': 'ok'})
    else:
        return JsonResponse({'status': 'error'}, status=500)
# Admin views (optional custom)
# For simplicity, we can use Django admin; but we might add custom admin dashboard later.


from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
from django.http import HttpResponse
import csv
from .models import WifiPackage, WifiPayment, WifiSubscription, Customer, WifiDevice
from .forms import PackageForm

# ---------- ADMIN DASHBOARD ----------
@staff_member_required
def admin_dashboard(request):
    today = timezone.now().date()
    start_of_day = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    end_of_day = timezone.make_aware(datetime.combine(today, datetime.max.time()))

    total_revenue = WifiPayment.objects.filter(status='successful').aggregate(Sum('amount'))['amount__sum'] or 0
    today_revenue = WifiPayment.objects.filter(status='successful', completed_at__range=(start_of_day, end_of_day)).aggregate(Sum('amount'))['amount__sum'] or 0
    total_customers = Customer.objects.count()
    active_subscriptions = WifiSubscription.objects.filter(status='active', expires_at__gt=timezone.now()).count()
    expired_subscriptions = WifiSubscription.objects.filter(status='expired').count()
    successful_payments = WifiPayment.objects.filter(status='successful').count()
    failed_payments = WifiPayment.objects.filter(status='failed').count()

    revenue_by_day = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        day_end = timezone.make_aware(datetime.combine(day, datetime.max.time()))
        total = WifiPayment.objects.filter(status='successful', completed_at__range=(day_start, day_end)).aggregate(Sum('amount'))['amount__sum'] or 0
        revenue_by_day.append({'date': day.strftime('%a'), 'amount': float(total)})

    mtn_revenue = WifiPayment.objects.filter(status='successful', provider='mtn').aggregate(Sum('amount'))['amount__sum'] or 0
    airtel_revenue = WifiPayment.objects.filter(status='successful', provider='airtel').aggregate(Sum('amount'))['amount__sum'] or 0
    popular_packages = WifiSubscription.objects.values('package__name').annotate(count=Count('id')).order_by('-count')[:5]

    context = {
        'total_revenue': total_revenue,
        'today_revenue': today_revenue,
        'total_customers': total_customers,
        'active_subscriptions': active_subscriptions,
        'expired_subscriptions': expired_subscriptions,
        'successful_payments': successful_payments,
        'failed_payments': failed_payments,
        'revenue_by_day': revenue_by_day,
        'mtn_revenue': mtn_revenue,
        'airtel_revenue': airtel_revenue,
        'popular_packages': popular_packages,
    }
    return render(request, 'wifi/admin/dashboard.html', context)

# ---------- PACKAGE MANAGEMENT ----------
@staff_member_required
def admin_packages(request):
    packages = WifiPackage.objects.all().order_by('price')
    return render(request, 'wifi/admin/packages.html', {'packages': packages})

@staff_member_required
def admin_package_create(request):
    if request.method == 'POST':
        form = PackageForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Package created successfully.')
            return redirect('wifi:admin_packages')
    else:
        form = PackageForm()
    return render(request, 'wifi/admin/package_form.html', {'form': form, 'title': 'Create Package'})

@staff_member_required
def admin_package_edit(request, pk):
    package = get_object_or_404(WifiPackage, pk=pk)
    if request.method == 'POST':
        form = PackageForm(request.POST, instance=package)
        if form.is_valid():
            form.save()
            messages.success(request, 'Package updated successfully.')
            return redirect('wifi:admin_packages')
    else:
        form = PackageForm(instance=package)
    return render(request, 'wifi/admin/package_form.html', {'form': form, 'title': 'Edit Package'})

@staff_member_required
def admin_package_toggle(request, pk):
    package = get_object_or_404(WifiPackage, pk=pk)
    package.is_active = not package.is_active
    package.save()
    status = 'activated' if package.is_active else 'deactivated'
    messages.success(request, f'Package {status}.')
    return redirect('wifi:admin_packages')

# ---------- CUSTOMER MANAGEMENT ----------

from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.utils import timezone

from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.utils import timezone

@staff_member_required
def admin_customers(request):
    query = request.GET.get('q', '')
    customers = Customer.objects.all()
    if query:
        customers = customers.filter(Q(phone_number__icontains=query) | Q(name__icontains=query))
    customers = customers.order_by('-created_at')

    now = timezone.now()

    # Add active status to each customer
    for customer in customers:
        customer.is_active = customer.subscriptions.filter(status='active', expires_at__gt=now).exists()

    # Statistics
    total_customers = Customer.objects.count()
    active_customers = Customer.objects.filter(subscriptions__status='active', subscriptions__expires_at__gt=now).distinct().count()
    expired_customers = Customer.objects.filter(subscriptions__status='expired').distinct().count()
    total_revenue = WifiPayment.objects.filter(status='successful').aggregate(Sum('amount'))['amount__sum'] or 0

    # Pagination (20 per page)
    paginator = Paginator(customers, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'wifi/admin/customers.html', {
        'customers': page_obj,
        'query': query,
        'total_customers': total_customers,
        'active_customers': active_customers,
        'expired_customers': expired_customers,
        'total_revenue': total_revenue,
        'is_paginated': page_obj.has_other_pages(),
        'page_obj': page_obj,
        'now': now,  # Pass now for other uses (if needed)
    })
@staff_member_required
def admin_customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    payments = WifiPayment.objects.filter(customer=customer).order_by('-created_at')
    subscriptions = WifiSubscription.objects.filter(customer=customer).order_by('-started_at')
    devices = WifiDevice.objects.filter(customer=customer)
    return render(request, 'wifi/admin/customer_detail.html', {
        'customer': customer,
        'payments': payments,
        'subscriptions': subscriptions,
        'devices': devices,
    })

@staff_member_required
def admin_customer_toggle(request, pk):
    # Placeholder: add a 'suspended' field to Customer for future use.
    messages.warning(request, 'This feature is not yet implemented.')
    return redirect('wifi:admin_customers')

# ---------- PAYMENT HISTORY ----------
@staff_member_required
def admin_payments(request):
    payments = WifiPayment.objects.all().order_by('-created_at')
    status_filter = request.GET.get('status')
    provider_filter = request.GET.get('provider')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if status_filter:
        payments = payments.filter(status=status_filter)
    if provider_filter:
        payments = payments.filter(provider=provider_filter)
    if date_from:
        payments = payments.filter(created_at__gte=date_from)
    if date_to:
        payments = payments.filter(created_at__lte=date_to)

    return render(request, 'wifi/admin/payments.html', {
        'payments': payments,
        'status_filter': status_filter,
        'provider_filter': provider_filter,
        'date_from': date_from,
        'date_to': date_to,
    })

@staff_member_required
def admin_payments_export(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="payments_export.csv"'
    writer = csv.writer(response)
    writer.writerow(['Reference', 'Customer', 'Phone', 'Package', 'Amount', 'Provider', 'Status', 'Date'])

    payments = WifiPayment.objects.all().order_by('-created_at')
    for p in payments:
        writer.writerow([
            str(p.reference),
            p.customer.name or '',
            p.phone_number,
            p.package.name,
            p.amount,
            p.get_provider_display(),
            p.status,
            p.created_at.strftime('%Y-%m-%d %H:%M'),
        ])
    return response


# In wifi/views.py, add these functions and modify payment_status_json:

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import WifiPayment, WifiSubscription, WifiDevice, Customer
from .services.subscriptions import get_active_subscription_for_device

# ... (your existing views) ...

def payment_status_json(request, reference):
    payment = get_object_or_404(WifiPayment, reference=reference)
    data = {
        'status': payment.status,
        'message': f"Payment {payment.status}",
        'reference': str(payment.reference),
    }
    if payment.status == 'successful':
        try:
            sub = payment.subscription
            data['voucher_code'] = sub.voucher_code
        except WifiSubscription.DoesNotExist:
            data['voucher_code'] = None
        data['success_url'] = request.build_absolute_uri('/wifi/success/')
    elif payment.status in ('failed', 'cancelled', 'expired'):
        data['retry_url'] = request.build_absolute_uri('/wifi/packages/')
    return JsonResponse(data)

def voucher_login(request):
    device = get_or_create_device(request)
    if request.method == 'POST':
        code = request.POST.get('voucher', '').strip().upper()
        if not code:
            messages.error(request, 'Please enter a voucher code.')
            return render(request, 'wifi/voucher_login.html')

        try:
            sub = WifiSubscription.objects.get(
                voucher_code=code,
                status='active',
                expires_at__gt=timezone.now()
            )
            # Check: voucher is locked to the device that purchased it
            if sub.device_mac != device.mac_address:
                messages.error(request, 'This voucher is locked to another device.')
                return render(request, 'wifi/voucher_login.html')

            # Authorize the device
            device.customer = sub.customer
            device.is_authorized = True
            device.save()
            request.session['voucher_subscription_id'] = sub.id
            messages.success(request, 'You are now connected!')
            return redirect('wifi:dashboard')
        except WifiSubscription.DoesNotExist:
            messages.error(request, 'Invalid or expired voucher code.')

    return render(request, 'wifi/voucher_login.html')