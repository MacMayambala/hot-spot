from django.core.management.base import BaseCommand
from django.utils import timezone
from wifi.models import MikroTikDevice, DeviceMetric
from wifi.services.mikrotik import RemoteMikroTikManager
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Collect metrics from all active MikroTik devices'

    def handle(self, *args, **options):
        devices = MikroTikDevice.objects.filter(is_active=True)
        self.stdout.write(f"Collecting metrics for {devices.count()} devices...")
        for device in devices:
            mgr = RemoteMikroTikManager(device=device)
            metrics = mgr.get_full_metrics()
            if metrics:
                # Create a metric record
                DeviceMetric.objects.create(
                    device=device,
                    timestamp=timezone.now(),
                    active_users=metrics.get('active_users', 0),
                    cpu_load=metrics.get('cpu_load', 0.0),
                    free_memory=metrics.get('free_memory', 0),
                    total_memory=metrics.get('total_memory', 0),
                    uptime=metrics.get('uptime', ''),
                    rx_rate=0,  # We'll compute rates in the chart function
                    tx_rate=0,
                )
                # Also update last_connection_status and last_check
                device.last_connection_status = True
                device.last_check = timezone.now()
                device.save(update_fields=['last_connection_status', 'last_check'])
                self.stdout.write(f"  {device.name}: OK")
            else:
                self.stdout.write(f"  {device.name}: FAIL")
                device.last_connection_status = False
                device.last_check = timezone.now()
                device.save(update_fields=['last_connection_status', 'last_check'])