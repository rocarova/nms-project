from django.contrib import admin
from .models import ConfigBackup


@admin.register(ConfigBackup)
class ConfigBackupAdmin(admin.ModelAdmin):
    list_display = ('device', 'created', 'status', 'changed')
    list_filter = ('status', 'changed')
    readonly_fields = ('created', 'config_hash')
