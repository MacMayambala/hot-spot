from django.core.management.base import BaseCommand
from django.utils import timezone
from wifi.models import MikroTikDevice
from wifi.services.mikrotik import RemoteMikroTikManager

class Command(BaseCommand):
    help = 'Update connection status of all active MikroTik devices'

    def handle(self, *args, **kwargs):
        for device in MikroTikDevice.objects.filter(is_active=True):
            mgr = RemoteMikroTikManager(device=device)
            device.last_connection_status = mgr.test_connectivity()
            device.last_check = timezone.now()
            device.save(update_fields=['last_connection_status', 'last_check'])
            self.stdout.write(f"{device.name}: {'OK' if device.last_connection_status else 'FAIL'}")