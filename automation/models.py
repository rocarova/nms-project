from django.contrib.auth.models import User
from django.db import models

from backups.models import ConfigBackup
from inventory.models import Device

MODE_CONFIG = 'config'
MODE_EXEC = 'exec'
MODE_CHOICES = [(MODE_CONFIG, 'Configuration'), (MODE_EXEC, 'Show / exec')]


class Script(models.Model):
    """A saved, reusable set of commands. {{ variables }} are filled in per device when pushed."""

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_CONFIG)
    commands = models.TextField()
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PushJob(models.Model):
    """One run of commands against a set of devices. Kept as the audit history of pushes."""

    PENDING, RUNNING, SUCCEEDED, FAILED, STOPPED = 'pending', 'running', 'succeeded', 'failed', 'stopped'
    STATUS_CHOICES = [(PENDING, 'Pending'), (RUNNING, 'Running'), (SUCCEEDED, 'Succeeded'),
                      (FAILED, 'Finished with errors'), (STOPPED, 'Stopped')]

    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='push_jobs')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    script = models.ForeignKey(Script, null=True, blank=True, on_delete=models.SET_NULL, related_name='jobs')
    commands = models.TextField(help_text='The commands as entered, before per-device variables are filled in')
    save_config = models.BooleanField(default=False)
    stop_on_error = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Push #{self.pk} ({self.get_mode_display()}, {self.status})'

    @property
    def is_finished(self):
        return self.status in (self.SUCCEEDED, self.FAILED, self.STOPPED)


class PushResult(models.Model):
    """What happened on one device in a PushJob."""

    PENDING, RUNNING, SUCCEEDED, FAILED, SKIPPED = 'pending', 'running', 'succeeded', 'failed', 'skipped'
    STATUS_CHOICES = [(PENDING, 'Waiting'), (RUNNING, 'Running'), (SUCCEEDED, 'Succeeded'),
                      (FAILED, 'Failed'), (SKIPPED, 'Skipped')]

    job = models.ForeignKey(PushJob, on_delete=models.CASCADE, related_name='results')
    device = models.ForeignKey(Device, null=True, on_delete=models.SET_NULL, related_name='push_results')
    # Snapshots, so the history still reads correctly after a device is renamed or deleted
    hostname = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    commands = models.TextField(blank=True, help_text='The commands sent to this device, variables filled in')
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)
    backup_before = models.ForeignKey(ConfigBackup, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    backup_after = models.ForeignKey(ConfigBackup, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    config_changed = models.BooleanField(null=True)
    saved = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['hostname', 'id']

    def __str__(self):
        return f'{self.hostname}: {self.status}'
