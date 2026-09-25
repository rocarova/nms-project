import re

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render

from .models import SyslogMessage

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000
SEARCH_FIELDS = ('message', 'hostname', 'source_ip', 'device__hostname')


@login_required
def syslog(request):
    return render(request, 'syslog_server/syslog.html', {'severities': SyslogMessage.SEVERITY_CHOICES})


@login_required
def messages_api(request):
    messages = SyslogMessage.objects.select_related('device')

    pattern = request.GET.get('q', '').strip()
    if pattern:
        if request.GET.get('regex') == '1':
            try:
                re.compile(pattern)
            except re.error as exc:
                return JsonResponse({'error': f'Invalid regular expression: {exc}'}, status=400)
            lookup = 'iregex'
        else:
            lookup = 'icontains'
        query = Q()
        for field in SEARCH_FIELDS:
            query |= Q(**{f'{field}__{lookup}': pattern})
        messages = messages.filter(query)

    severity = request.GET.get('severity', '')
    if severity.isdigit():
        # "Warning" shows warnings and everything more severe
        messages = messages.filter(severity__lte=int(severity))

    try:
        limit = min(max(int(request.GET.get('limit', DEFAULT_LIMIT)), 1), MAX_LIMIT)
    except ValueError:
        limit = DEFAULT_LIMIT

    try:
        page = list(messages[:limit])
    except DatabaseError:
        # PostgreSQL's regex dialect rejects some Python-only syntax, e.g. lookbehind "(?<=...)"
        return JsonResponse({'error': 'The database could not run this regular expression. '
                                      'Try a simpler pattern (lookarounds are not supported).'}, status=400)

    return JsonResponse({
        'messages': [
            {
                'id': m.id,
                'received_at': m.received_at.isoformat(),
                'source_ip': m.source_ip,
                'device': m.device.hostname if m.device else None,
                'hostname': m.hostname,
                'severity': m.severity,
                'severity_label': m.get_severity_display() if m.severity is not None else None,
                'message': m.message,
                'config_change': m.is_config_change,
            }
            for m in page
        ],
        'limit': limit,
    })
