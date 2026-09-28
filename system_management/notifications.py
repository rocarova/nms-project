"""Email notifications, using the SMTP server configured in Settings -> Email."""
import logging
import os
import re
import smtplib
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor

from django.core.mail import EmailMessage, get_connection

from .models import SystemSettings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 15
# Settings flag that must be on for each event type
EVENT_FLAGS = {
    'backup_failed': 'notify_backup_failed',
    'config_changed': 'notify_config_changed',
}

# Sending happens off the request/listener thread so a slow mail server never blocks them (tests turn this off)
SEND_IN_BACKGROUND = True
_sender = ThreadPoolExecutor(max_workers=2, thread_name_prefix='nms-mail')


def recipients(system):
    return [a for a in re.split(r'[\s,;]+', system.email_recipients) if a]


def connection_for(system):
    return get_connection(
        host=system.smtp_host,
        port=system.smtp_port,
        username=system.smtp_username or None,
        password=system.smtp_password or None,
        use_tls=system.smtp_security == 'starttls',
        use_ssl=system.smtp_security == 'ssl',
        timeout=SMTP_TIMEOUT,
        fail_silently=False,
    )


def link(path):
    """Absolute link into the app, if NMS_BASE_URL (e.g. https://nms.example.com) is configured."""
    base = os.environ.get('NMS_BASE_URL', '').rstrip('/')
    return f'{base}{path}' if base and path else ''


def send(system, subject, body, path=''):
    """Sends one email now to the configured recipients. Raises on any SMTP error."""
    url = link(path)
    if url:
        body += f'\nOpen in NetOps Center: {url}\n'
    body += '\n-- \nSent by NetOps Center. Change notifications in Settings -> Email.\n'
    message = EmailMessage(
        subject=f'[NetOps Center] {subject}', body=body,
        from_email=system.email_from, to=recipients(system), connection=connection_for(system),
    )
    message.send()


def describe_error(exc, system):
    """Turns an SMTP/network exception into a message that says what to fix."""
    server = f'{system.smtp_host}:{system.smtp_port}'
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'the mail server rejected the username or password.'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return f"the mail server refused the recipients ({', '.join(exc.recipients)})."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return f'the mail server refused the From address {system.email_from}.'
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return f'{server} does not support {system.get_smtp_security_display()} or login; check the Security setting.'
    if isinstance(exc, ssl.SSLError):
        return f'TLS problem talking to {server} ({exc.reason or exc}); check that Security matches the port (587 = STARTTLS, 465 = SSL/TLS).'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f'timed out connecting to {server}.'
    if isinstance(exc, ConnectionRefusedError):
        return f'{server} refused the connection; check the server and port.'
    if isinstance(exc, socket.gaierror):
        return f'could not find the mail server "{system.smtp_host}".'
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return f'{server} closed the connection; check the port and Security setting.'
    return f'{type(exc).__name__}: {exc}'


def send_test(system):
    send(system, 'Test email',
         'This is a test message from NetOps Center.\n\nIf you received it, email notifications are set up correctly.\n')


def notify(event, subject, body, path=''):
    """Queues a notification if email is enabled and the user wants this event. Returns True if queued."""
    system = SystemSettings.load()
    if not (system.email_enabled and getattr(system, EVENT_FLAGS[event]) and system.smtp_host and recipients(system)):
        return False
    if SEND_IN_BACKGROUND:
        _sender.submit(_send_logged, system, subject, body, path)
    else:
        _send_logged(system, subject, body, path)
    return True


def _send_logged(system, subject, body, path):
    try:
        send(system, subject, body, path)
        logger.info('Notification sent: %s', subject)
    except Exception:
        logger.exception('Could not send notification "%s"', subject)
