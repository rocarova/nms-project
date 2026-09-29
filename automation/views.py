from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from inventory.models import Device
from . import engine
from .forms import PushForm, ScriptForm
from .models import MODE_CONFIG, PushJob, PushResult, Script


@login_required
def history(request):
    jobs = (PushJob.objects.select_related('created_by', 'script')
            .annotate(device_count=Count('results'),
                      ok_count=Count('results', filter=Q(results__status=PushResult.SUCCEEDED)),
                      failed_count=Count('results', filter=Q(results__status=PushResult.FAILED)))[:100])
    for job in jobs:
        engine.reconcile(job)
    return render(request, 'automation/history.html', {'jobs': jobs, 'scripts': Script.objects.select_related('created_by')})


def device_rows(selected_ids):
    """Every device for the picker: disabled ones are listed but can't be selected."""
    devices = Device.objects.select_related('vendor', 'location').order_by('hostname')
    return [{'device': d, 'selected': d.id in selected_ids} for d in devices]


@login_required
def push(request):
    if request.method == 'GET':
        ids = {int(i) for i in request.GET.get('devices', '').split(',') if i.isdigit()}
        initial = {'devices': list(ids)}
        script = Script.objects.filter(pk=request.GET.get('script') or 0).first()
        if script:
            initial.update(script=script.id, mode=script.mode, commands=script.commands)
        form = PushForm(initial=initial)
        return render(request, 'automation/push.html', {'form': form, 'rows': device_rows(ids), 'step': 'edit',
                                                        'scripts': Script.objects.all()})

    form = PushForm(request.POST)
    action = request.POST.get('action')
    selected = {int(i) for i in request.POST.getlist('devices') if i.isdigit()}
    context = {'form': form, 'rows': device_rows(selected), 'scripts': Script.objects.all(), 'step': 'edit'}
    if action == 'edit' or not form.is_valid():
        return render(request, 'automation/push.html', context)

    devices = list(form.cleaned_data['devices'].select_related('vendor', 'location').order_by('hostname'))
    commands = form.cleaned_data['commands'].replace('\r\n', '\n')
    try:
        previews = [{'device': d, 'lines': engine.command_lines(engine.render(commands, d)),
                     'profile': engine.profile_for(d)} for d in devices]
    except engine.ScriptError as exc:
        form.add_error('commands', str(exc))
        return render(request, 'automation/push.html', context)

    context.update(step='preview', previews=previews, devices=devices,
                   is_config=form.cleaned_data['mode'] == MODE_CONFIG)
    if action != 'run' or not form.require_confirmation():
        return render(request, 'automation/push.html', context)

    job = PushJob.objects.create(
        created_by=request.user, mode=form.cleaned_data['mode'], script=form.cleaned_data['script'],
        commands=commands, stop_on_error=form.cleaned_data['stop_on_error'],
        save_config=form.cleaned_data['save_config'] and form.cleaned_data['mode'] == MODE_CONFIG,
    )
    PushResult.objects.bulk_create([
        PushResult(job=job, device=p['device'], hostname=p['device'].hostname, ip_address=p['device'].ip_address,
                   commands='\n'.join(p['lines']))
        for p in previews
    ])
    engine.start(job)
    return redirect('push_job', job_id=job.id)


@login_required
def job_detail(request, job_id):
    job = engine.reconcile(get_object_or_404(PushJob.objects.select_related('created_by', 'script'), pk=job_id))
    return render(request, 'automation/job.html', {'job': job})


@login_required
def job_status(request, job_id):
    job = engine.reconcile(get_object_or_404(PushJob, pk=job_id))
    results = job.results.select_related('backup_before', 'backup_after')
    counts = {status: 0 for status, _ in PushResult.STATUS_CHOICES}
    rows = []
    for r in results:
        counts[r.status] += 1
        compare = before = after = None
        if r.device_id and r.backup_before_id and r.backup_after_id and r.backup_after.status == 'success':
            compare = f"{reverse('backup_compare', args=[r.device_id])}?a={r.backup_before_id}&b={r.backup_after_id}"
        if r.device_id and r.backup_before_id and r.backup_before.status == 'success':
            before = reverse('backup_detail', args=[r.device_id, r.backup_before_id])
        if r.device_id and r.backup_after_id and r.backup_after.status == 'success':
            after = reverse('backup_detail', args=[r.device_id, r.backup_after_id])
        rows.append({
            'id': r.id, 'hostname': r.hostname, 'ip_address': r.ip_address,
            'status': r.status, 'status_label': r.get_status_display(),
            'error': r.error, 'output': r.output, 'commands': r.commands,
            'config_changed': r.config_changed, 'saved': r.saved,
            'compare_url': compare, 'before_url': before, 'after_url': after,
            'started_at': r.started_at.isoformat() if r.started_at else None,
            'finished_at': r.finished_at.isoformat() if r.finished_at else None,
        })
    return JsonResponse({'status': job.status, 'status_label': job.get_status_display(),
                         'finished': job.is_finished, 'counts': counts, 'results': rows})


@login_required
def script_edit(request, script_id=None):
    script = get_object_or_404(Script, pk=script_id) if script_id else None
    form = ScriptForm(request.POST or None, instance=script)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        if not saved.created_by_id:
            saved.created_by = request.user
        saved.save()
        messages.success(request, f'Script "{saved.name}" saved.')
        return redirect(f"{reverse('automation')}#scripts")
    return render(request, 'automation/script_form.html', {'form': form, 'script': script,
                                                           'variables': list(engine.VARIABLES)})


@login_required
@require_POST
def script_delete(request, script_id):
    script = get_object_or_404(Script, pk=script_id)
    script.delete()  # Past pushes keep their own copy of the commands
    messages.success(request, f'Script "{script.name}" deleted.')
    return redirect(f"{reverse('automation')}#scripts")
