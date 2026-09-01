# wifi/management/commands/check_devices.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from wifi.models import MikroTikDevice
from wifi.services.mikrotik import RemoteMikroTikManager
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Check connectivity of all active MikroTik devices and update status.'

    def handle(self, *args, **options):
        devices = MikroTikDevice.objects.filter(is_active=True)
        self.stdout.write(f"Checking {devices.count()} devices...")
        for device in devices:
            mgr = RemoteMikroTikManager(device=device)
            success = mgr.test_connectivity()
            device.last_connection_status = success
            device.last_check = timezone.now()
            device.save(update_fields=['last_connection_status', 'last_check'])
            status = "OK" if success else "FAIL"
            self.stdout.write(f"{device.name}: {status}")