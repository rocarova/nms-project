from django.contrib import admin
from.models import Device, Vendor, Location

# Register your models here.

class DeviceAdmin(admin.ModelAdmin):
    readonly_fields = ('created',)

admin.site.register(Device, DeviceAdmin)
admin.site.register(Vendor)
admin.site.register(Location)
