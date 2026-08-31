from django.core.management.base import BaseCommand
from django.utils import timezone
from wifi.models import WifiSubscription
from wifi.services.subscriptions import expire_subscription

class Command(BaseCommand):
    help = 'Expire WiFi subscriptions that have passed expiry'

    def handle(self, *args, **options):
        now = timezone.now()
        expired_subs = WifiSubscription.objects.filter(status='active', expires_at__lte=now)
        count = expired_subs.count()
        for sub in expired_subs:
            expire_subscription(sub)
        self.stdout.write(f"Expired {count} subscriptions.")