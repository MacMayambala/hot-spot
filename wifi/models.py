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
    


# wifi/models.py (add these)

# wifi/models.py
from django.db import models
from cryptography.fernet import Fernet
from django.conf import settings

# Helper for encryption (you can put this in a separate utils.py)
from django.conf import settings

def encrypt_password(password):
    return settings.CIPHER.encrypt(password.encode()).decode()

def decrypt_password(encrypted):
    return settings.CIPHER.decrypt(encrypted.encode()).decode()
class Branch(models.Model):
    name = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    contact_person = models.CharField(max_length=100, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class MikroTikDevice(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='devices')
    name = models.CharField(max_length=100, help_text="e.g., Main Router - Kampala")
    ip_address = models.GenericIPAddressField()
    username = models.CharField(max_length=50)
    _password = models.CharField(max_length=255, db_column='password', help_text="Encrypted password")
    api_port = models.IntegerField(default=8728)
    use_ssl = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    last_connection_status = models.BooleanField(default=False, help_text="Last health check result")
    last_check = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['branch', 'name']

    def __str__(self):
        return f"{self.name} ({self.branch.name})"

    @property
    def password(self):
        return decrypt_password(self._password)

    @password.setter
    def password(self, raw_password):
        self._password = encrypt_password(raw_password)

    def test_connection(self):
        """Quick connectivity test using librouteros"""
        from .services.mikrotik import RemoteMikroTikManager
        mgr = RemoteMikroTikManager(device=self)
        return mgr.test_connectivity()
    


class DeviceMetric(models.Model):
    device = models.ForeignKey(MikroTikDevice, on_delete=models.CASCADE, related_name='metrics')
    timestamp = models.DateTimeField(auto_now_add=True)
    active_users = models.IntegerField(default=0)
    cpu_load = models.FloatField(default=0.0)
    free_memory = models.BigIntegerField(default=0)
    total_memory = models.BigIntegerField(default=0)
    uptime = models.CharField(max_length=50, blank=True)
    # Throughput (bits per second) – we’ll store rx/tx rates
    rx_rate = models.FloatField(default=0.0)  # bps
    tx_rate = models.FloatField(default=0.0)  # bps
    rx_byte = models.BigIntegerField(default=0)
    tx_byte = models.BigIntegerField(default=0)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['device', 'timestamp'])]