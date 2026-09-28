import smtplib
import socket
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import Device, Location, Vendor
from syslog_server.management.commands.syslog_server import Command
from syslog_server.models import SyslogMessage
from . import notifications
from .models import SystemSettings


class NotificationTestMixin:
    def setUp(self):
        notifications.SEND_IN_BACKGROUND = False
        self.addCleanup(setattr, notifications, 'SEND_IN_BACKGROUND', True)

    def configure(self, **overrides):
        values = dict(email_enabled=True, smtp_host='smtp.example.com', smtp_port=587, smtp_security='starttls',
                      email_from='nms@example.com', email_recipients='noc@example.com\noncall@example.com',
                      notify_backup_failed=True, notify_config_changed=True)
        values.update(overrides)
        SystemSettings.objects.filter(pk=SystemSettings.load().pk).update(**values)


class NotifyTests(NotificationTestMixin, TestCase):
    def test_sends_to_all_recipients(self):
        self.configure()
        self.assertTrue(notifications.notify('backup_failed', 'Backup failed for sw1', 'Details'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, '[NetOps Center] Backup failed for sw1')
        self.assertEqual(message.to, ['noc@example.com', 'oncall@example.com'])
        self.assertEqual(message.from_email, 'nms@example.com')

    def test_respects_enabled_and_event_flags(self):
        self.configure(email_enabled=False)
        self.assertFalse(notifications.notify('backup_failed', 's', 'b'))
        self.configure(notify_config_changed=False)
        self.assertFalse(notifications.notify('config_changed', 's', 'b'))
        self.assertTrue(notifications.notify('backup_failed', 's', 'b'))
        self.assertEqual(len(mail.outbox), 1)

    def test_link_uses_base_url(self):
        self.configure()
        with mock.patch.dict('os.environ', {'NMS_BASE_URL': 'https://nms.example.com/'}):
            notifications.notify('backup_failed', 's', 'b', path='/inventory/1/backups/')
        self.assertIn('https://nms.example.com/inventory/1/backups/', mail.outbox[0].body)

    def test_smtp_connection_settings(self):
        self.configure(smtp_security='ssl', smtp_port=465, smtp_username='nms', smtp_password='pw')
        with mock.patch('system_management.notifications.get_connection') as get_connection:
            notifications.connection_for(SystemSettings.load())
        kwargs = get_connection.call_args.kwargs
        self.assertEqual((kwargs['host'], kwargs['port'], kwargs['use_ssl'], kwargs['use_tls']), ('smtp.example.com', 465, True, False))
        self.assertEqual((kwargs['username'], kwargs['password']), ('nms', 'pw'))

    def test_describe_error(self):
        system = SystemSettings.load()
        system.smtp_host, system.smtp_port = 'smtp.example.com', 587
        self.assertIn('username or password', notifications.describe_error(smtplib.SMTPAuthenticationError(535, b'no'), system))
        self.assertIn('refused the connection', notifications.describe_error(ConnectionRefusedError(), system))
        self.assertIn('timed out', notifications.describe_error(socket.timeout(), system))


class TestEmailViewTests(NotificationTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user('admin'))

    def post(self, **overrides):
        data = {'section': 'email', 'action': 'test', 'smtp_host': 'smtp.example.com', 'smtp_port': '587',
                'smtp_security': 'starttls', 'email_from': 'nms@example.com', 'email_recipients': 'noc@example.com'}
        data.update(overrides)
        return self.client.post(reverse('settings'), data, follow=True)

    def test_sends_test_email_even_when_notifications_off(self):
        response = self.post()
        self.assertContains(response, 'A test email was sent to noc@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Test email', mail.outbox[0].subject)
        self.assertFalse(SystemSettings.load().email_enabled)

    def test_reports_smtp_failure(self):
        with mock.patch('system_management.notifications.EmailMessage.send', side_effect=smtplib.SMTPAuthenticationError(535, b'bad')):
            response = self.post()
        self.assertContains(response, 'the test email failed: the mail server rejected the username or password')

    def test_requires_server_details(self):
        response = self.post(smtp_host='')
        self.assertIn('smtp_host', response.context['forms']['email'].errors)
        self.assertEqual(len(mail.outbox), 0)


class SyslogConfigChangeEmailTests(NotificationTestMixin, TestCase):
    def test_one_email_per_device_per_window(self):
        self.configure()
        user = User.objects.create_user('admin')
        device = Device.objects.create(hostname='sw1', ip_address='10.0.0.1', vendor=Vendor.objects.create(name='Cisco'),
                                       location=Location.objects.create(name='HQ'), username='u', password='p', creator=user)
        command = Command()
        command.config_notified_at = {}
        rows = [SyslogMessage(received_at=timezone.now(), source_ip='10.0.0.1', device_id=device.id,
                              message='%SYS-5-CONFIG_I: Configured from console', is_config_change=True)
                for _ in range(3)]
        rows.append(SyslogMessage(received_at=timezone.now(), source_ip='10.9.9.9', message='%SYS-5-CONFIG_I', is_config_change=True))
        command.notify_config_changes(rows)
        command.notify_config_changes(rows[:1])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Configuration changed on sw1', mail.outbox[0].subject)
        self.assertIn('%SYS-5-CONFIG_I', mail.outbox[0].body)
