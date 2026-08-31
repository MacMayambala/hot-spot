from django.db import models

# Create your models here.
import uuid
from django.db import models
from django.utils import timezone

class Customer(models.Model):
    phone_number = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.phone_number

class WifiDevice(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='devices'
    )
    mac_address = models.CharField(max_length=17, unique=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    hostname = models.CharField(max_length=255, blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    is_authorized = models.BooleanField(default=False)

    def __str__(self):
        if self.customer:
            return f"{self.mac_address} ({self.customer.phone_number})"
        return f"{self.mac_address} (no customer)"
class WifiPackage(models.Model):
    DURATION_UNITS = (
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)  # in UGX
    duration = models.PositiveIntegerField()
    duration_unit = models.CharField(max_length=10, choices=DURATION_UNITS, default='hours')
    data_limit = models.PositiveIntegerField(help_text="Data limit in MB (0 = unlimited)", default=0)
    speed_limit = models.PositiveIntegerField(help_text="Speed limit in kbps (0 = unlimited)", default=0)
    is_unlimited = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def get_duration_display(self):
        return f"{self.duration} {self.duration_unit}"

class WifiPayment(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('successful', 'Successful'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
    )
    PROVIDER_CHOICES = (
        ('mtn', 'MTN Mobile Money'),
        ('airtel', 'Airtel Money'),
    )
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider_transaction_id = models.CharField(max_length=100, blank=True, null=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='payments')
    package = models.ForeignKey(WifiPackage, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    phone_number = models.CharField(max_length=15)
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    description = models.TextField(blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.reference} - {self.status}"

class WifiSubscription(models.Model):
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended'),
        ('cancelled', 'Cancelled'),
    )
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='subscriptions')
    package = models.ForeignKey(WifiPackage, on_delete=models.PROTECT)
    payment = models.OneToOneField(WifiPayment, on_delete=models.PROTECT, related_name='subscription')
    username = models.CharField(max_length=50, unique=True)
    voucher_code = models.CharField(max_length=10, unique=True, blank=True, null=True)
    device_mac = models.CharField(max_length=17)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    data_used = models.PositiveIntegerField(default=0)  # in MB
    data_limit = models.PositiveIntegerField(default=0)  # MB, 0 = unlimited
    speed_limit = models.PositiveIntegerField(default=0)  # kbps, 0 = unlimited
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.username} - {self.status}"

class WifiSession(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='sessions')
    device = models.ForeignKey(WifiDevice, on_delete=models.CASCADE, related_name='sessions')
    subscription = models.ForeignKey(WifiSubscription, on_delete=models.SET_NULL, null=True, blank=True)
    login_time = models.DateTimeField(auto_now_add=True)
    logout_time = models.DateTimeField(blank=True, null=True)
    ip_address = models.GenericIPAddressField()
    mac_address = models.CharField(max_length=17)
    bytes_uploaded = models.PositiveBigIntegerField(default=0)
    bytes_downloaded = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=20, default='active')  # active, ended

    def __str__(self):
        return f"Session {self.id} - {self.customer.phone_number}"

class AdminAuditLog(models.Model):
    admin_user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=255)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.admin_user} - {self.action} at {self.timestamp}"