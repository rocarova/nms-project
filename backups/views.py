from django.contrib.auth.decorators import login_required
from django.db.models.functions import Length
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from inventory.models import Device
from .diff import diff_rows, numbered_lines
from .models import ConfigBackup

HISTORY_LIMIT = 500


def get_device(device_id):
    return get_object_or_404(Device.objects.select_related('vendor', 'location'), pk=device_id)


def successful_backups(device):
    # Lists only need metadata; skip loading every config's text
    return device.backups.filter(status=ConfigBackup.SUCCESS).defer('config')


@login_required
def device_backups(request, device_id):
    device = get_device(device_id)
    backups = list(device.backups.defer('config').annotate(size=Length('config'))[:HISTORY_LIMIT])

    # Link each successful backup to the successful one before it, for "compare with previous"
    previous = None
    for backup in reversed(backups):
        if backup.status == ConfigBackup.SUCCESS:
            backup.previous_id = previous.id if previous else None
            previous = backup

    successes = [b for b in backups if b.status == ConfigBackup.SUCCESS]
    return render(request, 'backups/device_backups.html', {
        'device': device,
        'backups': backups,
        'total': device.backups.count(),
        'success_count': len(successes),
        'change_count': sum(1 for b in successes if b.changed),
        'last_success': successes[0] if successes else None,
        'limit': HISTORY_LIMIT,
        'inventory_url': reverse('inventory'),
    })


@login_required
def backup_detail(request, device_id, backup_id):
    device = get_device(device_id)
    backup = get_object_or_404(ConfigBackup, pk=backup_id, device=device, status=ConfigBackup.SUCCESS)
    history = successful_backups(device)
    return render(request, 'backups/backup_detail.html', {
        'device': device,
        'backup': backup,
        'lines': numbered_lines(backup.config),
        'previous': history.filter(created__lt=backup.created).order_by('-created').first(),
        'next': history.filter(created__gt=backup.created).order_by('created').first(),
    })


@login_required
def backup_download(request, device_id, backup_id):
    device = get_device(device_id)
    backup = get_object_or_404(ConfigBackup, pk=backup_id, device=device, status=ConfigBackup.SUCCESS)
    response = HttpResponse(backup.config, content_type='text/plain; charset=utf-8')
    filename = f'{device.hostname}_{backup.created:%Y%m%d-%H%M%S}.cfg'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def backup_compare(request, device_id):
    device = get_device(device_id)
    try:
        ids = {int(request.GET['a']), int(request.GET['b'])}
    except (KeyError, ValueError):
        return redirect('device_backups', device_id=device.id)
    if len(ids) != 2:
        return redirect('backup_detail', device_id=device.id, backup_id=ids.pop())

    pair = list(ConfigBackup.objects.filter(pk__in=ids, device=device, status=ConfigBackup.SUCCESS).order_by('created'))
    if len(pair) != 2:
        raise Http404('Backup not found')
    old, new = pair  # Always show older -> newer, whichever order they were picked in

    full = request.GET.get('view') == 'full'
    rows, added, removed = diff_rows(old.config, new.config, context=None if full else 3)
    return render(request, 'backups/backup_compare.html', {
        'device': device,
        'old': old,
        'new': new,
        'rows': rows,
        'added': added,
        'removed': removed,
        'full': full,
        'history': successful_backups(device)[:HISTORY_LIMIT],
        'changes_url': f"{reverse('backup_compare', args=[device.id])}?a={old.id}&b={new.id}",
    })
