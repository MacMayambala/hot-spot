from django.core.management.base import BaseCommand
from django.test.client import Client
from django.urls import reverse
import json

class Command(BaseCommand):
    help = 'Test the webhook endpoint with a sample payload'

    def add_arguments(self, parser):
        parser.add_argument('reference', type=str, help='Payment reference')
        parser.add_argument('--status', type=str, default='completed', help='completed/failed/cancelled')

    def handle(self, *args, **options):
        reference = options['reference']
        status = options['status']
        payload = {
            "event_type": f"collection.{status}",
            "transaction": {
                "reference": reference,
                "status": "completed" if status == 'completed' else 'failed',
            },
            "collection": {
                "provider_transaction_id": "TEST-TXN-123",
                "provider": "mtn",
                "phone_number": "+256712345678",
                "amount": {"raw": 5000, "formatted": "5,000.00", "currency": "UGX"}
            },
            "metadata": [
                {"device_mac": "aa:bb:cc:dd:ee:ff"}
            ]
        }
        client = Client()
        url = reverse('wifi:webhook')
        response = client.post(url, data=json.dumps(payload), content_type='application/json')
        self.stdout.write(f"Status: {response.status_code}")
        self.stdout.write(f"Response: {response.content}")