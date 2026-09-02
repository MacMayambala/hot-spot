import logging
import random
import string
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from ..models import WifiSubscription, WifiPackage, Customer, WifiPayment, WifiDevice
from . import mikrotik

logger = logging.getLogger(__name__)

def generate_voucher_code(length=6):
    """Generate a unique alphanumeric voucher code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        if not WifiSubscription.objects.filter(voucher_code=code).exists():
            return code

def create_subscription(customer, package, payment, device_mac, username, password):
    """
    Create a subscription and activate hotspot user.
    Returns subscription object.
    MikroTik errors are logged but do not prevent subscription creation.
    """
    with transaction.atomic():
        # Calculate expiry
        duration_seconds = package.duration * 60
        if package.duration_unit == 'hours':
            duration_seconds = package.duration * 3600
        elif package.duration_unit == 'days':
            duration_seconds = package.duration * 86400
        else:  # minutes
            duration_seconds = package.duration * 60
        expires_at = timezone.now() + timedelta(seconds=duration_seconds)

        # Generate unique voucher code
        voucher_code = generate_voucher_code()

        # Create subscription – now storing password and MAC
        sub = WifiSubscription.objects.create(
            customer=customer,
            package=package,
            payment=payment,
            username=username,
            password=password,          # store password for re-creation
            device_mac=device_mac,
            expires_at=expires_at,
            data_limit=package.data_limit,
            speed_limit=package.speed_limit,
            status='active',
            voucher_code=voucher_code,
        )

        # Attempt to create hotspot user on MikroTik – but don't fail
        try:
            created = mikrotik.create_hotspot_user(username, password, profile='default')
            if created:
                mikrotik.set_user_expiry(username, expires_at)
                mikrotik.activate_hotspot_user(username)
                # Set MAC address for auto‑authentication
                mikrotik.update_user_mac(username, device_mac)
                logger.info(f"Hotspot user {username} created with MAC {device_mac} for subscription {sub.id}")
            else:
                logger.warning(f"Router returned failure for user {username} – subscription still created")
        except Exception as e:
            logger.error(f"Failed to create hotspot user on router: {e} – subscription {sub.id} created anyway")

        return sub

def expire_subscription(subscription):
    """Expire a single subscription."""
    if subscription.status != 'active':
        return
    subscription.status = 'expired'
    subscription.save()
    try:
        mikrotik.disable_hotspot_user(subscription.username)
        mikrotik.disconnect_hotspot_user(subscription.username)
    except Exception as e:
        logger.error(f"Error expiring user {subscription.username}: {e}")
    logger.info(f"Subscription {subscription.id} expired")

def get_active_subscription_for_device(mac_address):
    """Get the most recent active subscription for a device."""
    try:
        sub = WifiSubscription.objects.filter(
            device_mac=mac_address,
            status='active',
            expires_at__gt=timezone.now()
        ).latest('started_at')
        return sub
    except WifiSubscription.DoesNotExist:
        return None