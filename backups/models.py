import hashlib

from django.db import models
from django.db.models import OuterRef, Subquery

from inventory.models import Device


class ConfigBackup(models.Model):
    """One attempt to pull a device's running config. Successful attempts keep the config text."""

    SUCCESS = 'success'
    FAILED = 'failed'
    STATUS_CHOICES = [(SUCCESS, 'Success'), (FAILED, 'Failed')]

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='backups')
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    config = models.TextField(blank=True)
    config_hash = models.CharField(max_length=64, blank=True)
    error = models.TextField(blank=True)
    # True when this config differs from the device's previous successful backup
    changed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created']
        indexes = [models.Index(fields=['device', '-created'])]

    def __str__(self):
        return f'{self.device} @ {self.created:%Y-%m-%d %H:%M} ({self.status})'

    @classmethod
    def record_success(cls, device, config):
        """Store a pulled config, flagging it as changed if it differs from the last good one."""
        config_hash = hashlib.sha256(config.encode()).hexdigest()
        previous = cls.objects.filter(device=device, status=cls.SUCCESS).order_by('-created').first()
        return cls.objects.create(
            device=device, status=cls.SUCCESS, config=config, config_hash=config_hash,
            changed=previous is not None and previous.config_hash != config_hash,
        )

    @classmethod
    def record_failure(cls, device, error):
        return cls.objects.create(device=device, status=cls.FAILED, error=error)


def with_latest_backup(devices):
    """Annotates a Device queryset with last_backup_status / last_backup_at from each device's latest attempt."""
    latest = ConfigBackup.objects.filter(device=OuterRef('pk')).order_by('-created')
    return devices.annotate(
        last_backup_status=Subquery(latest.values('status')[:1]),
        last_backup_at=Subquery(latest.values('created')[:1]),
    )
