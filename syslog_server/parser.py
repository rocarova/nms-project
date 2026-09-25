"""Parsing for RFC 3164 / RFC 5424 syslog messages and config-change detection."""
import re

MAX_MESSAGE_LENGTH = 8192

PRI_RE = re.compile(r'^<(\d{1,3})>')
# RFC 5424: VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP SD [SP MSG]
RFC5424_RE = re.compile(
    r'^1 \S+ (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) (?P<msgid>\S+) (?P<sd>-|(?:\[.*?\])+) ?(?P<msg>.*)$', re.S)
# RFC 3164: "Mmm dd hh:mm:ss HOSTNAME MSG"
RFC3164_RE = re.compile(r'^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d (?P<host>[^\s:]+) (?P<msg>.*)$', re.S)

# Messages network devices send when their configuration is changed
CONFIG_CHANGE_RE = re.compile(
    r'%SYS-\d-CONFIG_I'           # Cisco IOS / IOS-XE, Arista EOS
    r'|%SYS-\d-CONFIG_E'          # Arista EOS config session commit
    r'|config_i:'                 # Cisco NX-OS VSHD
    r'|%VSHD-\d-VSHD_SYSLOG_CONFIG_I'
    r'|UI_COMMIT'                 # Juniper Junos
    r'|configuration (?:was )?changed'  # Aruba, HPE, generic
    r'|running-config (?:was )?(?:changed|modified)',
    re.I,
)


def parse(data):
    """Returns (facility, severity, hostname, message). Unparseable parts are left as None / ''."""
    text = data.decode('utf-8', errors='replace').strip().lstrip('\ufeff')
    facility = severity = None
    hostname = ''

    match = PRI_RE.match(text)
    if match and int(match.group(1)) <= 191:
        pri = int(match.group(1))
        facility, severity = divmod(pri, 8)
        text = text[match.end():]

    body = RFC5424_RE.match(text)
    if body:
        hostname = body.group('host')
        text = rfc5424_tag(body) + body.group('msg').lstrip('\ufeff')
    else:
        body = RFC3164_RE.match(text)
        if body:
            hostname = body.group('host')
            text = body.group('msg')
    hostname = '' if hostname == '-' else hostname

    return facility, severity, hostname[:255], text[:MAX_MESSAGE_LENGTH]


def rfc5424_tag(body):
    """Rebuilds a 3164-style "app[procid]: MSGID " prefix so the app and MSGID stay visible and searchable."""
    app, procid, msgid = (None if v == '-' else v for v in body.group('app', 'procid', 'msgid'))
    tag = ''
    if app:
        tag = f'{app}[{procid}]: ' if procid else f'{app}: '
    if msgid:
        tag += f'{msgid} '
    return tag


def is_config_change(message):
    return bool(CONFIG_CHANGE_RE.search(message))
