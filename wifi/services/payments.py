import logging
import uuid
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from ..models import Customer, WifiPayment, WifiPackage, WifiDevice
from .marz import initiate_collection

logger = logging.getLogger(__name__)

def initiate_payment(phone_number, package_id, provider, device_mac, name=None):
    """
    Initiate payment via Marz.
    Returns payment object and API response.
    """
    # Normalize phone
    if phone_number.startswith('0'):
        phone_number = '+256' + phone_number[1:]
    elif not phone_number.startswith('+256'):
        phone_number = '+256' + phone_number

    # Get or create customer
    customer, created = Customer.objects.get_or_create(
        phone_number=phone_number,
        defaults={'name': name or ''}
    )
    if name and not customer.name:
        customer.name = name
        customer.save()

    # Get package
    package = WifiPackage.objects.get(id=package_id, is_active=True)

    # Generate reference
    reference = uuid.uuid4()

    # Create payment record
    with transaction.atomic():
        payment = WifiPayment.objects.create(
            reference=reference,
            customer=customer,
            package=package,
            amount=package.price,
            phone_number=phone_number,
            provider=provider,
            status='pending',
            description=f"Payment for {package.name}"
        )

        # Call Marz
        try:
            description = f"WiFi package: {package.name}"
            metadata = {
                'package_id': package.id,
                'customer_id': customer.id,
                'device_mac': device_mac
            }
            marz_response = initiate_collection(
                phone_number=phone_number,
                amount=package.price,
                reference=reference,
                description=description,
                metadata=metadata
            )
            # Update payment with raw response
            payment.raw_response = marz_response
            payment.status = 'processing'  # Marz returns processing initially
            payment.save()
            logger.info(f"Payment initiated: {reference}")
            return payment, marz_response
        except Exception as e:
            payment.status = 'failed'
            payment.raw_response = {'error': str(e)}
            payment.save()
            raise
def process_webhook_payload(payload):
    """
    Process incoming webhook from MarzPay.
    Handles both direct callback and dashboard wrapper { data: ... }.
    """
    # Unwrap if dashboard wrapper
    if 'data' in payload and isinstance(payload['data'], dict):
        payload = payload['data']

    # Extract reference (can be top-level or inside transaction)
    reference = payload.get('transaction', {}).get('reference') or payload.get('reference')
    if not reference:
        logger.error("Webhook missing reference")
        return False

    # Determine status from event_type
    event_type = payload.get('event_type', '')
    if event_type.endswith('.completed'):
        status = 'successful'
    elif event_type.endswith('.failed'):
        status = 'failed'
    elif event_type.endswith('.cancelled'):
        status = 'cancelled'
    else:
        # Fallback to transaction.status
        txn_status = payload.get('transaction', {}).get('status', '')
        if txn_status in ('completed', 'success'):
            status = 'successful'
        elif txn_status in ('failed', 'error'):
            status = 'failed'
        elif txn_status == 'cancelled':
            status = 'cancelled'
        else:
            status = 'pending'

    # Get provider transaction ID
    provider_transaction_id = payload.get('collection', {}).get('provider_transaction_id')
    if not provider_transaction_id:
        # Fallback to disbursement or bill_payment
        provider_transaction_id = (
            payload.get('disbursement', {}).get('provider_transaction_id') or
            payload.get('bill_payment', {}).get('provider_reference')
        )

    # Now do database update (idempotent)
    try:
        payment = WifiPayment.objects.get(reference=reference)
    except WifiPayment.DoesNotExist:
        logger.error(f"Payment not found for reference {reference}")
        return False

    if payment.status in ('successful', 'failed', 'cancelled', 'expired'):
        logger.info(f"Payment {reference} already final, ignoring webhook")
        return True

    with transaction.atomic():
        payment.status = status
        if provider_transaction_id:
            payment.provider_transaction_id = provider_transaction_id
        if status == 'successful':
            payment.completed_at = timezone.now()
        payment.save()

        if status == 'successful':
            # Extract device_mac from metadata
            metadata = payload.get('metadata', [])
            device_mac = None
            for item in metadata:
                if 'device_mac' in item:
                    device_mac = item['device_mac']
                    break
            if not device_mac:
                # fallback from stored raw_response (or latest device)
                device_mac = payment.raw_response.get('metadata', {}).get('device_mac')
            if not device_mac:
                device = payment.customer.devices.order_by('-last_seen').first()
                if device:
                    device_mac = device.mac_address

            if not device_mac:
                logger.error(f"No device MAC for payment {reference}, cannot activate")
                return False

            username = f"WIFI-{str(reference).split('-')[0].upper()}"
            password = str(uuid.uuid4())[:8]

            from .subscriptions import create_subscription
            try:
                create_subscription(
                    customer=payment.customer,
                    package=payment.package,
                    payment=payment,
                    device_mac=device_mac,
                    username=username,
                    password=password
                )
                WifiDevice.objects.filter(mac_address=device_mac).update(is_authorized=True)
                logger.info(f"Subscription created for payment {reference}")
            except Exception as e:
                logger.error(f"Failed to create subscription: {e}")
                return False
        else:
            logger.info(f"Payment {reference} status {status} processed")
    return True