from collections import Counter
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q
from django.db.models.functions import TruncHour
from django.shortcuts import render
from django.utils import timezone

from backups.models import ConfigBackup, with_latest_backup
from inventory.models import Device
from syslog_server.models import SyslogMessage

CHANGE_WINDOW = timedelta(hours=24)
RECENT_CHANGES_LIMIT = 8


def recent_config_changes(since):
    backup_changes = ConfigBackup.objects.filter(changed=True, created__gte=since).select_related('device')
    syslog_changes = SyslogMessage.objects.filter(is_config_change=True, received_at__gte=since).select_related('device')
    changes = [
        {'at': b.created, 'device': b.device.hostname, 'ip': b.device.ip_address, 'source': 'Backup diff',
         'detail': 'Running config differs from the previous backup'}
        for b in backup_changes.order_by('-created')[:RECENT_CHANGES_LIMIT]
    ] + [
        {'at': m.received_at, 'device': m.device.hostname if m.device else m.hostname or None, 'ip': m.source_ip,
         'source': 'Syslog', 'detail': m.message}
        for m in syslog_changes.order_by('-id')[:RECENT_CHANGES_LIMIT]
    ]
    return sorted(changes, key=lambda c: c['at'], reverse=True)[:RECENT_CHANGES_LIMIT]


def syslog_per_hour(now):
    first_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    counts = {
        row['hour']: row['n']
        for row in SyslogMessage.objects.filter(received_at__gte=first_hour)
        .annotate(hour=TruncHour('received_at')).values('hour').annotate(n=Count('id'))
    }
    hours = [first_hour + timedelta(hours=i) for i in range(24)]
    return [{'t': h.isoformat(), 'n': counts.get(h, 0)} for h in hours]


def syslog_by_severity(since):
    counts = dict(
        SyslogMessage.objects.filter(received_at__gte=since).values_list('severity').annotate(n=Count('id'))
    )
    rows = [{'level': level, 'label': label, 'n': counts.get(level, 0)} for level, label in SyslogMessage.SEVERITY_CHOICES]
    if counts.get(None):
        rows.append({'level': None, 'label': 'Unknown', 'n': counts[None]})
    return rows


# Create your views here.
@login_required
def dashboard(request):
    now = timezone.now()
    since = now - CHANGE_WINDOW

    devices = with_latest_backup(Device.objects.all())
    backup_status = Counter(devices.values_list('last_backup_status', flat=True))
    total = sum(backup_status.values())

    changed_devices = set(
        ConfigBackup.objects.filter(changed=True, created__gte=since).values_list('device_id', flat=True)
    ) | set(
        SyslogMessage.objects.filter(is_config_change=True, received_at__gte=since, device__isnull=False)
        .values_list('device_id', flat=True)
    )

    severity_rows = syslog_by_severity(since)

    return render(request, 'dashboard/dashboard.html', {
        'total_devices': total,
        'enabled_devices': Device.objects.filter(enabled=True).count(),
        'backup_ok': backup_status[ConfigBackup.SUCCESS],
        'backup_failed': backup_status[ConfigBackup.FAILED],
        'backup_never': backup_status[None],
        'changed_count': len(changed_devices),
        'recent_changes': recent_config_changes(since),
        # Failed first (most recent first), then never backed up
        'attention_devices': devices.filter(Q(last_backup_status=ConfigBackup.FAILED) | Q(last_backup_status__isnull=True))
            .select_related('location').order_by(F('last_backup_at').desc(nulls_last=True), 'hostname')[:8],
        'syslog_total': sum(row['n'] for row in severity_rows),
        'severity_max': max([row['n'] for row in severity_rows] + [1]),
        'severity_rows': severity_rows,
        'syslog_hourly': syslog_per_hour(now),
    })
