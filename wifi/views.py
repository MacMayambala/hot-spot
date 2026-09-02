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
from django.urls import path
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
    sub = get_active_subscription_for_device(device.mac_address)
    if sub:
        # Ensure the hotspot user is active on the router
        try:
            # Try to activate – if it fails, recreate the user
            from .services.mikrotik import activate_hotspot_user, create_hotspot_user, set_user_expiry, update_user_mac
            if not activate_hotspot_user(sub.username):
                # Recreate using stored password
                create_hotspot_user(sub.username, sub.password, profile='default')
                set_user_expiry(sub.username, sub.expires_at)
                activate_hotspot_user(sub.username)
                # Set MAC address for auto‑auth
                update_user_mac(sub.username, device.mac_address)
        except Exception as e:
            logger.error(f"Auto-login failed for {sub.username}: {e}")
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

import json
import hmac
import hashlib
import logging
import uuid
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponseBadRequest, JsonResponse
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .models import WifiPayment, WifiDevice
from .services.subscriptions import create_subscription
from .services.mikrotik import RemoteMikroTikManager

logger = logging.getLogger(__name__)

import json
import hmac
import hashlib
import logging
import uuid
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponseBadRequest, JsonResponse
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .models import WifiPayment, WifiDevice
from .services.subscriptions import create_subscription

logger = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def webhook(request):
    """
    MarzPay webhook handler – Uganda collections (MTN/Airtel).
    Accepts both direct and dashboard-wrapped payloads.
    Signature verification is optional: if header present, verify; if missing, log and proceed.
    """
    raw_body = request.body
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        return HttpResponseBadRequest("Invalid JSON")

    logger.info(f"Webhook received: {payload}")

    # ---- Optional HMAC signature verification ----
    webhook_secret = getattr(settings, 'MARZPAY_WEBHOOK_SECRET', None)
    if webhook_secret:
        signature_header = request.headers.get('X-MarzPay-Signature')
        if signature_header:
            try:
                parts = dict(item.split('=') for item in signature_header.split(','))
                timestamp = parts.get('t')
                signature = parts.get('v1')
                if timestamp and signature:
                    signed_string = f"{timestamp}.{raw_body.decode('utf-8')}"
                    computed = hmac.new(
                        webhook_secret.encode('utf-8'),
                        signed_string.encode('utf-8'),
                        hashlib.sha256
                    ).hexdigest()
                    if not hmac.compare_digest(computed, signature):
                        logger.warning(f"Invalid signature for reference: {payload.get('transaction', {}).get('reference')}")
                        return HttpResponseBadRequest("Invalid signature")
                else:
                    logger.warning("Signature header missing parts")
            except Exception as e:
                logger.warning(f"Signature parsing error: {e}")
        else:
            # Missing signature header – just log and continue (as per user request)
            logger.info("No signature header, skipping HMAC verification")

    # ---- Unwrap dashboard wrapper ----
    if 'data' in payload and isinstance(payload['data'], dict):
        payload = payload['data']

    # ---- Extract reference ----
    transaction_data = payload.get('transaction', {})
    reference = transaction_data.get('reference') or payload.get('reference')
    if not reference:
        logger.error("No reference in webhook payload")
        return HttpResponseBadRequest("Missing reference")

    event_type = payload.get('event_type', '')

    # ---- Determine status ----
    if event_type.endswith('.completed'):
        status = 'successful'
    elif event_type.endswith('.failed'):
        status = 'failed'
    elif event_type.endswith('.cancelled'):
        status = 'cancelled'
    else:
        txn_status = transaction_data.get('status', '')
        if txn_status in ('completed', 'success'):
            status = 'successful'
        elif txn_status in ('failed', 'error'):
            status = 'failed'
        elif txn_status == 'cancelled':
            status = 'cancelled'
        else:
            status = 'pending'

    logger.info(f"Payment {reference}: status -> {status} (event: {event_type})")

    # ---- Get payment ----
    try:
        payment = WifiPayment.objects.get(reference=reference)
    except WifiPayment.DoesNotExist:
        logger.error(f"Payment not found for reference {reference}")
        return HttpResponseBadRequest("Payment not found")

    if payment.status in ('successful', 'failed', 'cancelled', 'expired'):
        logger.info(f"Payment {reference} already final, ignoring")
        return JsonResponse({'status': 'ok'})

    # ---- Process ----
    try:
        with transaction.atomic():
            payment.status = status
            if status == 'successful':
                payment.completed_at = timezone.now()
                collection = payload.get('collection', {})
                if collection.get('provider_transaction_id'):
                    payment.provider_transaction_id = collection['provider_transaction_id']
            payment.save()

            if status == 'successful':
                # Extract device_mac from metadata
                metadata = payload.get('metadata', [])
                device_mac = None
                if isinstance(metadata, list):
                    for item in metadata:
                        if isinstance(item, dict) and 'device_mac' in item:
                            device_mac = item['device_mac']
                            break

                # Fallback to customer's latest device
                if not device_mac:
                    device = payment.customer.devices.order_by('-last_seen').first()
                    if device:
                        device_mac = device.mac_address

                if not device_mac:
                    logger.error(f"No device MAC for payment {reference}")
                    raise Exception("No device MAC found")

                username = f"WIFI-{str(reference).split('-')[0].upper()}"
                password = str(uuid.uuid4())[:8]
                create_subscription(
                    customer=payment.customer,
                    package=payment.package,
                    payment=payment,
                    device_mac=device_mac,
                    username=username,
                    password=password
                )
                # Authorize device
                WifiDevice.objects.filter(mac_address=device_mac).update(is_authorized=True)
                logger.info(f"Subscription created for payment {reference}")

            else:
                logger.info(f"Payment {reference} marked as {status}")

    except Exception as e:
        logger.error(f"Error processing webhook for {reference}: {e}")
        # Return 500 to ask MarzPay to retry later
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'status': 'ok'})
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
from django.http import HttpResponse
import csv
from .models import WifiPackage, WifiPayment, WifiSubscription, Customer, WifiDevice
from .forms import PackageForm

# ---------- ADMIN DASHBOARD ----------
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from datetime import datetime, timedelta
from django.db.models import Sum, Count
from wifi.models import WifiPayment, Customer, WifiSubscription, MikroTikDevice
from wifi.services.mikrotik import RemoteMikroTikManager
import logging

logger = logging.getLogger(__name__)

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

    # ---------- MikroTik Devices Status ----------
    devices = MikroTikDevice.objects.filter(is_active=True).select_related('branch')
    devices_status = []
    for device in devices:
        # Use cached last_connection_status if you prefer – but for real‑time we test fresh.
        # For performance, you might want to use the stored status (updated via cron).
        mgr = RemoteMikroTikManager(device=device)
        reachable = mgr.test_connectivity()   # This does a TCP + API check – can be slow.
        active_users = []
        if reachable:
            if mgr.connect():
                active_users = mgr.get_active_users()
                mgr.disconnect()
        devices_status.append({
            'device': device,
            'reachable': reachable,
            'active_users': active_users,
        })

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
        'devices_status': devices_status,   # new
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


# wifi/admin.py
from django.contrib import admin
from .models import Branch, MikroTikDevice
from .services.mikrotik import RemoteMikroTikManager
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

class MikroTikDeviceInline(admin.TabularInline):
    model = MikroTikDevice
    extra = 1
    fields = ('name', 'ip_address', 'username', 'api_port', 'use_ssl', 'is_active', 'last_connection_status')
    readonly_fields = ('last_connection_status',)

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'contact_person', 'contact_phone', 'created_at')
    search_fields = ('name', 'address', 'contact_person')
    inlines = [MikroTikDeviceInline]

@admin.register(MikroTikDevice)
class MikroTikDeviceAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'ip_address', 'username', 'is_active', 'last_connection_status', 'last_check')
    list_filter = ('branch', 'is_active', 'use_ssl')
    search_fields = ('name', 'ip_address', 'username')
    actions = ['test_connection_action']

    def test_connection_action(self, request, queryset):
        """Admin action to test connection for selected devices."""
        for device in queryset:
            mgr = RemoteMikroTikManager(device=device)
            success = mgr.test_connectivity()
            device.last_connection_status = success
            device.last_check = datetime.now()
            device.save()
            if success:
                self.message_user(request, f"{device.name} connected successfully.", level='SUCCESS')
            else:
                self.message_user(request, f"{device.name} failed to connect.", level='ERROR')
        self.message_user(request, "Connection tests completed.")
    test_connection_action.short_description = "Test connection for selected devices"

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('status/', self.admin_site.admin_view(self.status_dashboard), name='mikrotik_status'),
        ]
        return custom_urls + urls

    # ---------- MIKROTIK STATUS DASHBOARD ----------
@staff_member_required
def mikrotik_status(request):
    """Dashboard showing connection status for all active devices."""
    devices = MikroTikDevice.objects.filter(is_active=True).select_related('branch')
    status_list = []
    for device in devices:
        mgr = RemoteMikroTikManager(device=device)
        reachable = mgr.test_connectivity()
        system_info = None
        active_users = []
        if reachable:
            if mgr.connect():
                system_info = mgr.get_system_info()
                active_users = mgr.get_active_users()
                mgr.disconnect()
        status_list.append({
            'device': device,
            'reachable': reachable,
            'system_info': system_info,
            'active_users': active_users,
        })
    context = {
        'status_list': status_list,
        'total_devices': len(devices),
    }
    return render(request, 'wifi/admin/mikrotik_status.html', context)


@staff_member_required
def device_detail_status(request, device_id):
    """API endpoint for real‑time status of a single device."""
    device = MikroTikDevice.objects.get(id=device_id)
    mgr = RemoteMikroTikManager(device=device)
    data = {
        'id': device.id,
        'name': device.name,
        'reachable': mgr.test_connectivity(),
    }
    if data['reachable']:
        if mgr.connect():
            data['system_info'] = mgr.get_system_info()
            data['active_users'] = mgr.get_active_users()
            mgr.disconnect()
    return JsonResponse(data)


@staff_member_required
def execute_command(request):
    """View to run arbitrary RouterOS commands on a device."""
    if request.method == 'POST':
        device_id = request.POST.get('device_id')
        command = request.POST.get('command')
        if not device_id or not command:
            messages.error(request, "Device and command are required.")
            return redirect('wifi:execute_command')
        device = MikroTikDevice.objects.get(id=device_id)
        mgr = RemoteMikroTikManager(device=device)
        result = mgr.execute_raw_command(command)
        context = {
            'device': device,
            'command': command,
            'result': result,
        }
        return render(request, 'wifi/admin/command_result.html', context)
    else:
        devices = MikroTikDevice.objects.filter(is_active=True)
        context = {'devices': devices}
        return render(request, 'wifi/admin/execute_command.html', context)


from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import Branch, MikroTikDevice
from .services.mikrotik import RemoteMikroTikManager
from django.http import JsonResponse

@staff_member_required
def branch_add(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        address = request.POST.get('address')
        contact_person = request.POST.get('contact_person')
        contact_phone = request.POST.get('contact_phone')
        if name:
            Branch.objects.create(
                name=name,
                address=address,
                contact_person=contact_person,
                contact_phone=contact_phone
            )
            messages.success(request, f'Branch "{name}" created successfully.')
            return redirect('admin:app_list', app_label='wifi')
        else:
            messages.error(request, 'Branch name is required.')
    return render(request, 'wifi/admin/add_branch.html')

@staff_member_required
def remote_troubleshoot(request):
    devices = MikroTikDevice.objects.filter(is_active=True).select_related('branch')
    context = {'devices': devices}
    if request.method == 'POST':
        device_id = request.POST.get('device_id')
        command = request.POST.get('command')
        if device_id and command:
            device = MikroTikDevice.objects.get(id=device_id)
            mgr = RemoteMikroTikManager(device=device)
            result = mgr.execute_raw_command(command)
            context.update({
                'selected_device': int(device_id),
                'command': command,
                'result': result,
            })
    return render(request, 'wifi/admin/remote_troubleshoot.html', context)

@staff_member_required
def device_info_api(request, device_id):
    device = MikroTikDevice.objects.get(id=device_id)
    mgr = RemoteMikroTikManager(device=device)
    data = {'id': device.id, 'name': device.name}
    if mgr.connect():
        data['system_info'] = mgr.get_system_info()
        mgr.disconnect()
    else:
        data['system_info'] = None
    return JsonResponse(data)



@staff_member_required
def admin_branches(request):
    """Display a list of all branches with actions."""
    branches = Branch.objects.all().order_by('name')
    context = {'branches': branches}
    return render(request, 'wifi/admin/branches.html', context)



from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import Branch

@staff_member_required
def branch_edit(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        name = request.POST.get('name')
        address = request.POST.get('address')
        contact_person = request.POST.get('contact_person')
        contact_phone = request.POST.get('contact_phone')
        if name:
            branch.name = name
            branch.address = address
            branch.contact_person = contact_person
            branch.contact_phone = contact_phone
            branch.save()
            messages.success(request, f'Branch "{name}" updated successfully.')
            return redirect('wifi:admin_branches')
        else:
            messages.error(request, 'Branch name is required.')
    context = {'branch': branch}
    return render(request, 'wifi/admin/branch_form.html', context)

@staff_member_required
def branch_delete(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == 'POST':
        branch_name = branch.name
        branch.delete()
        messages.success(request, f'Branch "{branch_name}" deleted successfully.')
        return redirect('wifi:admin_branches')
    context = {'branch': branch}
    return render(request, 'wifi/admin/branch_confirm_delete.html', context)

from .models import Branch, MikroTikDevice
from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required
def device_add(request):
    """Add a new MikroTik device."""
    branches = Branch.objects.all().order_by('name')
    
    if request.method == 'POST':
        branch_id = request.POST.get('branch')
        name = request.POST.get('name')
        ip_address = request.POST.get('ip_address')
        username = request.POST.get('username')
        password = request.POST.get('password')
        api_port = request.POST.get('api_port', 8728)
        use_ssl = request.POST.get('use_ssl') == 'on'
        is_active = request.POST.get('is_active') == 'on'
        
        # Validation
        errors = []
        if not branch_id:
            errors.append("Branch is required.")
        if not name:
            errors.append("Device name is required.")
        if not ip_address:
            errors.append("IP address is required.")
        if not username:
            errors.append("Username is required.")
        if not password:
            errors.append("Password is required.")
        
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            try:
                branch = Branch.objects.get(id=branch_id)
                device = MikroTikDevice(
                    branch=branch,
                    name=name,
                    ip_address=ip_address,
                    username=username,
                    password=password,  # Will be encrypted via setter
                    api_port=int(api_port),
                    use_ssl=use_ssl,
                    is_active=is_active,
                )
                device.save()
                
                # Test connection after adding
                from .services.mikrotik import RemoteMikroTikManager
                mgr = RemoteMikroTikManager(device=device)
                if mgr.test_connectivity():
                    messages.success(request, f'Device "{name}" added and connected successfully!')
                else:
                    messages.warning(request, f'Device "{name}" added but could not connect. Please check credentials and network.')
                
                return redirect('wifi:admin_devices')  # We'll create this list view next
            
            except Branch.DoesNotExist:
                messages.error(request, "Selected branch does not exist.")
            except Exception as e:
                messages.error(request, f"Error adding device: {str(e)}")
    
    context = {
        'branches': branches,
    }
    return render(request, 'wifi/admin/device_add.html', context)


@staff_member_required
def admin_devices(request):
    """List all MikroTik devices."""
    devices = MikroTikDevice.objects.all().select_related('branch').order_by('branch__name', 'name')
    context = {'devices': devices}
    return render(request, 'wifi/admin/devices.html', context)



from .models import DeviceMetric, MikroTikDevice
from django.db.models import Avg, Max, Min
from django.utils import timezone
from datetime import timedelta

@staff_member_required
def device_metrics(request):
    devices = MikroTikDevice.objects.filter(is_active=True)
    device_id = request.GET.get('device')
    selected_device = None
    if device_id:
        selected_device = MikroTikDevice.objects.filter(id=device_id).first()
    if not selected_device and devices.exists():
        selected_device = devices.first()
    
    metrics_data = None
    if selected_device:
        # Get metrics for the last 24 hours
        since = timezone.now() - timedelta(hours=24)
        qs = DeviceMetric.objects.filter(device=selected_device, timestamp__gte=since).order_by('timestamp')
        # Prepare data for charts
        timestamps = [m.timestamp.strftime('%H:%M') for m in qs]
        active_users = [m.active_users for m in qs]
        cpu_load = [m.cpu_load for m in qs]
        # Compute throughput rates (bits per second) from rx_byte / tx_byte differences
        # Since we store only the latest byte count, we cannot compute rate without previous.
        # We need to store rx_byte and tx_byte in the model. Let's add them.
        # For now, we'll use rx_rate/tx_rate if we had them. Let's extend the model.
        # I'll add fields in next step.
        # We'll compute rates by storing previous values in a dict.
        rx_rates = []
        tx_rates = []
        prev_time = None
        prev_rx = None
        prev_tx = None
        for m in qs:
            # We need rx_byte/tx_byte. Let's add those fields to DeviceMetric.
            pass
        # For now, we'll pass empty.
        metrics_data = {
            'labels': timestamps,
            'active_users': active_users,
            'cpu_load': cpu_load,
            'rx_rates': rx_rates,
            'tx_rates': tx_rates,
        }
    
    context = {
        'devices': devices,
        'selected_device': selected_device,
        'metrics': metrics_data,
    }
    return render(request, 'wifi/admin/device_metrics.html', context)

from .models import DeviceMetric, MikroTikDevice
from django.db.models import Avg, Max, Min
from django.utils import timezone
from datetime import timedelta

@staff_member_required
def device_metrics(request):
    devices = MikroTikDevice.objects.filter(is_active=True)
    device_id = request.GET.get('device')
    selected_device = None
    if device_id:
        selected_device = MikroTikDevice.objects.filter(id=device_id).first()
    if not selected_device and devices.exists():
        selected_device = devices.first()
    
    metrics_data = None
    if selected_device:
        # Get metrics for the last 24 hours
        since = timezone.now() - timedelta(hours=24)
        qs = DeviceMetric.objects.filter(device=selected_device, timestamp__gte=since).order_by('timestamp')
        if qs.exists():
            # Prepare data for charts
            timestamps = [m.timestamp.strftime('%H:%M') for m in qs]
            active_users = [m.active_users for m in qs]
            cpu_load = [m.cpu_load for m in qs]
            
            # Compute throughput rates (bits per second) from rx_byte / tx_byte differences
            rx_rates = []
            tx_rates = []
            prev_time = None
            prev_rx = None
            prev_tx = None
            for m in qs:
                if prev_time and prev_rx is not None:
                    delta_sec = (m.timestamp - prev_time).total_seconds()
                    if delta_sec > 0:
                        rx_rate = (m.rx_byte - prev_rx) * 8 / delta_sec  # bits per second
                        tx_rate = (m.tx_byte - prev_tx) * 8 / delta_sec
                    else:
                        rx_rate = 0
                        tx_rate = 0
                    rx_rates.append(round(rx_rate, 0))
                    tx_rates.append(round(tx_rate, 0))
                else:
                    rx_rates.append(0)
                    tx_rates.append(0)
                prev_time = m.timestamp
                prev_rx = m.rx_byte
                prev_tx = m.tx_byte
            
            metrics_data = {
                'labels': timestamps,
                'active_users': active_users,
                'cpu_load': cpu_load,
                'rx_rates': rx_rates,
                'tx_rates': tx_rates,
            }
        else:
            # No metrics yet – show placeholder
            metrics_data = {
                'labels': [],
                'active_users': [],
                'cpu_load': [],
                'rx_rates': [],
                'tx_rates': [],
            }
    
    context = {
        'devices': devices,
        'selected_device': selected_device,
        'metrics': metrics_data,
    }
    return render(request, 'wifi/admin/device_metrics.html', context)


from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from .models import Branch, MikroTikDevice

@staff_member_required
def device_edit(request, pk):
    device = get_object_or_404(MikroTikDevice, pk=pk)
    branches = Branch.objects.all().order_by('name')
    
    if request.method == 'POST':
        branch_id = request.POST.get('branch')
        name = request.POST.get('name')
        ip_address = request.POST.get('ip_address')
        username = request.POST.get('username')
        password = request.POST.get('password')
        api_port = request.POST.get('api_port', 8728)
        use_ssl = request.POST.get('use_ssl') == 'on'
        is_active = request.POST.get('is_active') == 'on'
        
        # Validation
        errors = []
        if not branch_id:
            errors.append("Branch is required.")
        if not name:
            errors.append("Device name is required.")
        if not ip_address:
            errors.append("IP address is required.")
        if not username:
            errors.append("Username is required.")
        
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            try:
                branch = Branch.objects.get(id=branch_id)
                device.branch = branch
                device.name = name
                device.ip_address = ip_address
                device.username = username
                if password:  # Only update password if provided
                    device.password = password
                device.api_port = int(api_port)
                device.use_ssl = use_ssl
                device.is_active = is_active
                device.save()
                messages.success(request, f'Device "{name}" updated successfully.')
                return redirect('wifi:admin_devices')
            except Branch.DoesNotExist:
                messages.error(request, "Selected branch does not exist.")
            except Exception as e:
                messages.error(request, f"Error updating device: {str(e)}")
    
    context = {
        'device': device,
        'branches': branches,
    }
    return render(request, 'wifi/admin/device_form.html', context)


@staff_member_required
def device_delete(request, pk):
    device = get_object_or_404(MikroTikDevice, pk=pk)
    if request.method == 'POST':
        device_name = device.name
        device.delete()
        messages.success(request, f'Device "{device_name}" deleted successfully.')
        return redirect('wifi:admin_devices')
    context = {'device': device}
    return render(request, 'wifi/admin/device_confirm_delete.html', context)