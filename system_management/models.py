from datetime import time

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SystemSettings(models.Model):
    """Application-wide settings. There is only ever one row; use SystemSettings.load()."""

    # Syslog
    syslog_port = models.PositiveIntegerField(
        default=514, validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text='UDP and TCP port the syslog listener accepts messages on.')
    syslog_retention_days = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(3650)],
        help_text='Messages older than this are deleted automatically.')

    # Backups (on-demand backups use backup_retention; the schedule is for the upcoming scheduler)
    FREQUENCY_DISABLED = 'disabled'
    FREQUENCY_CHOICES = [
        ('disabled', 'Disabled'),
        ('hourly', 'Every hour'),
        ('6h', 'Every 6 hours'),
        ('12h', 'Every 12 hours'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
    ]
    WEEKDAY_CHOICES = [(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
                       (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday')]
    backup_frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, default='daily')
    backup_time = models.TimeField(default=time(2, 0), help_text='Time of day for daily and weekly backups (server time).')
    backup_weekday = models.PositiveSmallIntegerField(choices=WEEKDAY_CHOICES, default=6)
    backup_retention = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text='How many backups to keep per network device.')

    # Email notifications (sent by system_management.notifications)
    SECURITY_CHOICES = [('starttls', 'STARTTLS'), ('ssl', 'SSL/TLS'), ('none', 'None')]
    email_enabled = models.BooleanField(default=False)
    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.PositiveIntegerField(default=587, validators=[MinValueValidator(1), MaxValueValidator(65535)])
    smtp_security = models.CharField(max_length=10, choices=SECURITY_CHOICES, default='starttls')
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password = models.CharField(max_length=255, blank=True)
    email_from = models.EmailField(blank=True)
    email_recipients = models.TextField(blank=True, help_text='One address per line, or separated by commas.')
    notify_backup_failed = models.BooleanField(default=True)
    notify_config_changed = models.BooleanField(default=True)

    # Written by the syslog listener process so the UI can show whether it is running
    listener_port = models.PositiveIntegerField(null=True, blank=True)
    listener_error = models.CharField(max_length=500, blank=True)
    listener_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'system settings'
        verbose_name_plural = 'system settings'

    def __str__(self):
        return 'System settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
