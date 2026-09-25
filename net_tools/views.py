import ipaddress
import platform
import re
import shutil
import subprocess

from django.http import HttpResponseBadRequest, HttpResponseServerError, StreamingHttpResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required

IS_WINDOWS = platform.system() == 'Windows'

# RFC 1123 hostname: dot-separated labels of letters, digits and inner hyphens
HOSTNAME_RE = re.compile(r'^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$')


def is_valid_target(target):
    # Rejects anything that isn't an IP or hostname, e.g. "-f" being passed through as a command option
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return bool(HOSTNAME_RE.match(target))


def stream_command(cmd):
    # Yields each output line as the process produces it
    def generate():
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors='replace',
        )
        try:
            for line in process.stdout:
                yield line
            process.wait()
        finally:
            # Client disconnected mid-stream: don't leave the process running
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    response = StreamingHttpResponse(generate(), content_type='text/plain; charset=utf-8')
    response['X-Accel-Buffering'] = 'no'  # Stop nginx from buffering the stream
    return response


# Create your views here.

@login_required
def ping(request):
    target = request.GET.get('target', '').strip()

    if not target:
        return render(request, 'net_tools/ping.html')
    if not is_valid_target(target):
        return HttpResponseBadRequest('Invalid target: enter an IP address or hostname.\n', content_type='text/plain')

    count_flag = '-n' if IS_WINDOWS else '-c'
    return stream_command(['ping', count_flag, '4', target])

@login_required
def traceroute(request):
    target = request.GET.get('target', '').strip()

    if not target:
        return render(request, 'net_tools/traceroute.html')
    if not is_valid_target(target):
        return HttpResponseBadRequest('Invalid target: enter an IP address or hostname.\n', content_type='text/plain')

    cmd = ['tracert' if IS_WINDOWS else 'traceroute', target]
    if shutil.which(cmd[0]) is None:
        return HttpResponseServerError(f'{cmd[0]} is not installed on the server.\n', content_type='text/plain')
    return stream_command(cmd)

NSLOOKUP_RECORD_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'NS', 'PTR', 'SOA', 'SRV', 'TXT', 'ANY']

@login_required
def nslookup(request):
    target = request.GET.get('target', '').strip()

    if not target:
        return render(request, 'net_tools/nslookup.html', {'record_types': NSLOOKUP_RECORD_TYPES})
    if not is_valid_target(target):
        return HttpResponseBadRequest('Invalid target: enter an IP address or hostname.\n', content_type='text/plain')

    record_type = request.GET.get('type', 'A').upper()
    if record_type not in NSLOOKUP_RECORD_TYPES:
        return HttpResponseBadRequest('Invalid record type.\n', content_type='text/plain')

    server = request.GET.get('server', '').strip()
    if server and not is_valid_target(server):
        return HttpResponseBadRequest('Invalid DNS server: enter an IP address or hostname.\n', content_type='text/plain')

    if shutil.which('nslookup') is None:
        return HttpResponseServerError('nslookup is not installed on the server.\n', content_type='text/plain')

    # Same syntax on Windows and Linux: nslookup -type=MX example.com [server]
    cmd = ['nslookup', f'-type={record_type}', target]
    if server:
        cmd.append(server)
    return stream_command(cmd)

@login_required
def tools(request):
    return render(request, 'net_tools/tools.html')
