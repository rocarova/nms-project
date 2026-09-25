from django.db import models

from inventory.models import Device


class SyslogMessage(models.Model):
    SEVERITY_CHOICES = [
        (0, 'Emergency'), (1, 'Alert'), (2, 'Critical'), (3, 'Error'),
        (4, 'Warning'), (5, 'Notice'), (6, 'Info'), (7, 'Debug'),
    ]

    received_at = models.DateTimeField(db_index=True)
    source_ip = models.GenericIPAddressField(db_index=True)
    # Set when source_ip matches a device in the inventory
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name='syslog_messages')
    facility = models.PositiveSmallIntegerField(null=True, blank=True)
    severity = models.PositiveSmallIntegerField(null=True, blank=True, choices=SEVERITY_CHOICES, db_index=True)
    hostname = models.CharField(max_length=255, blank=True)
    message = models.TextField()
    is_config_change = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.received_at:%Y-%m-%d %H:%M:%S} {self.source_ip} {self.message[:60]}'
