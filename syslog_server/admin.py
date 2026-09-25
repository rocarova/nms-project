from django.contrib import admin
from .models import SyslogMessage


@admin.register(SyslogMessage)
class SyslogMessageAdmin(admin.ModelAdmin):
    list_display = ('received_at', 'source_ip', 'device', 'severity', 'is_config_change', 'message')
    list_filter = ('severity', 'is_config_change')
    search_fields = ('message', 'hostname', 'source_ip')
