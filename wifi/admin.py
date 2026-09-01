from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import Customer, WifiDevice, WifiPackage, WifiPayment, WifiSubscription, WifiSession, AdminAuditLog

admin.site.register(Customer)
admin.site.register(WifiDevice)
admin.site.register(WifiPackage)
admin.site.register(WifiPayment)
admin.site.register(WifiSubscription)
admin.site.register(WifiSession)
admin.site.register(AdminAuditLog)


