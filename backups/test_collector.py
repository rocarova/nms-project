from unittest import mock

import asyncssh
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse

from inventory.models import Device, Location, Vendor
from inventory.ssh import ConnectError
from system_management import notifications
from system_management.models import SystemSettings
from . import collector
from .models import ConfigBackup

CISCO_OUTPUT = (
    'Building configuration...\r\n\r\n'
    'Current configuration : 1234 bytes\r\n'
    '!\r\n! Last configuration change at 10:00:01 UTC Mon Sep 28 2026 by admin\r\n'
    '! NVRAM config last updated at 09:00:00 UTC Mon Sep 28 2026 by admin\r\n'
    '!\r\nversion 15.2\r\nhostname sw1\r\n!\r\nntp clock-period 36028804\r\n'
    'interface Gi1/0/1\r\n switchport mode trunk   \r\n!\r\nend\r\n\r\n'
)


def fake_connection(stdout='', exec_error=None):
    conn = mock.Mock()
    if exec_error:
        conn.run = mock.AsyncMock(side_effect=exec_error)
    else:
        conn.run = mock.AsyncMock(return_value=mock.Mock(stdout=stdout))
    return conn


class CleanConfigTests(SimpleTestCase):
    def test_removes_volatile_lines_and_normalizes(self):
        self.assertEqual(collector.clean_config(CISCO_OUTPUT),
                         '!\n!\nversion 15.2\nhostname sw1\n!\ninterface Gi1/0/1\n switchport mode trunk\n!\nend\n')

    def test_same_config_different_timestamps_is_identical(self):
        later = CISCO_OUTPUT.replace('1234 bytes', '1240 bytes').replace('10:00:01', '11:30:00')
        self.assertEqual(collector.clean_config(CISCO_OUTPUT), collector.clean_config(later))

    def test_strips_terminal_escapes(self):
        self.assertEqual(collector.clean_config('\x1b[?25lhostname sw1\x1b[0m\r\n'), 'hostname sw1\n')

    def test_strip_shell_noise(self):
        shell_output = ('show running-config\r\nshow running-config\r\n\r\n!\r\nhostname sw1\r\n!\r\nend\r\n'
                        'sw1#\r\n')
        self.assertEqual(collector.strip_shell_noise(shell_output, 'show running-config'), '!\nhostname sw1\n!\nend')
        self.assertEqual(collector.strip_shell_noise('/export\r\n# comment\r\n/ip address\r\n[admin@mt] > ', '/export'),
                         '# comment\n/ip address')

    def test_validate_rejects_errors_and_empty_output(self):
        with self.assertRaisesRegex(collector.BackupError, 'rejected'):
            collector.validate("% Invalid input detected at '^' marker.\n", 'show running-config')
        with self.assertRaisesRegex(collector.BackupError, 'almost nothing'):
            collector.validate('end\n', 'show running-config')

    def test_vendor_profiles(self):
        for vendor, commands in [('Juniper', ['show configuration | no-more']), ('Cisco', ['show running-config']),
                                 ('Aruba', ['show running-config']), ('MikroTik', ['/export terse', '/export'])]:
            self.assertEqual(collector.profile_for(mock.Mock(vendor_id=1, vendor=vendor))[1], commands, vendor)


class BackupDeviceTests(TestCase):
    def setUp(self):
        notifications.SEND_IN_BACKGROUND = False
        self.addCleanup(setattr, notifications, 'SEND_IN_BACKGROUND', True)
        user = User.objects.create_user('admin')
        self.device = Device.objects.create(
            hostname='sw1', ip_address='10.0.0.1', vendor=Vendor.objects.create(name='Cisco'),
            location=Location.objects.create(name='HQ'), username='u', password='p', creator=user)
        SystemSettings.objects.filter(pk=SystemSettings.load().pk).update(
            email_enabled=True, smtp_host='smtp.example.com', email_from='nms@example.com',
            email_recipients='noc@example.com')

    def run_backup(self, **conn_kwargs):
        with mock.patch('backups.collector.ssh.connect', mock.AsyncMock(return_value=fake_connection(**conn_kwargs))):
            return collector.backup_device(self.device)

    def test_first_backup_then_unchanged_then_changed(self):
        first = self.run_backup(stdout=CISCO_OUTPUT)
        self.assertEqual((first.status, first.changed), ('success', False))
        self.assertIn('hostname sw1', first.config)
        self.assertNotIn('Current configuration', first.config)

        # Only timestamps differ: not a change
        second = self.run_backup(stdout=CISCO_OUTPUT.replace('1234 bytes', '1300 bytes'))
        self.assertFalse(second.changed)
        self.assertEqual(len(mail.outbox), 0)

        third = self.run_backup(stdout=CISCO_OUTPUT.replace('hostname sw1', 'hostname sw1\nvlan 30'))
        self.assertTrue(third.changed)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Configuration changed on sw1', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['noc@example.com'])

    def test_connection_failure_is_recorded_and_emailed(self):
        with mock.patch('backups.collector.ssh.connect', mock.AsyncMock(side_effect=ConnectError('Authentication failed: check it.'))):
            backup = collector.backup_device(self.device)
        self.assertEqual(backup.status, 'failed')
        self.assertEqual(backup.error, 'Authentication failed: check it.')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Backup failed for sw1', mail.outbox[0].subject)
        self.assertIn('Authentication failed', mail.outbox[0].body)

    def test_falls_back_to_interactive_shell(self):
        with mock.patch('backups.collector.run_interactive', mock.AsyncMock(return_value=CISCO_OUTPUT)) as shell:
            backup = self.run_backup(exec_error=asyncssh.ChannelOpenError(1, 'exec not supported'))
        self.assertEqual(backup.status, 'success')
        shell.assert_awaited_once()
        self.assertEqual(shell.call_args.args[2], 'show running-config')

    def test_device_error_output_is_a_failure(self):
        with mock.patch('backups.collector.run_interactive', mock.AsyncMock(return_value='% Invalid input detected\nsw1#')):
            backup = self.run_backup(stdout='% Invalid input detected\n')
        self.assertEqual(backup.status, 'failed')
        self.assertIn('rejected', backup.error)

    def test_retention_keeps_newest(self):
        SystemSettings.objects.filter(pk=1).update(backup_retention=2)
        for i in range(4):
            self.run_backup(stdout=CISCO_OUTPUT.replace('version 15.2', f'version 15.{i}'))
        self.assertEqual(self.device.backups.count(), 2)
        self.assertIn('version 15.3', self.device.backups.first().config)


class RequestBackupViewTests(TransactionTestCase):
    # The view runs the backup on a separate thread, which needs committed data (so not TestCase)
    def setUp(self):
        self.user = User.objects.create_user('admin')
        self.client.force_login(self.user)
        self.device = Device.objects.create(
            hostname='sw1', ip_address='10.0.0.1', vendor=Vendor.objects.create(name='Cisco'),
            location=Location.objects.create(name='HQ'), username='u', password='p', creator=self.user)
        self.url = reverse('request_backup', args=[self.device.id])

    def test_post_runs_backup_and_reports(self):
        with mock.patch('backups.collector.ssh.connect', mock.AsyncMock(return_value=fake_connection(stdout=CISCO_OUTPUT))):
            response = self.client.post(self.url)
        self.assertRedirects(response, reverse('device_backups', args=[self.device.id]))
        self.assertEqual(self.device.backups.get().status, 'success')
        self.assertIn('First backup of sw1 saved', [str(m) for m in get_messages(response.wsgi_request)][0])

    def test_failure_message(self):
        with mock.patch('backups.collector.ssh.connect', mock.AsyncMock(side_effect=ConnectError('Timed out after 15s connecting to 10.0.0.1:22.'))):
            response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'Backup of sw1 failed: Timed out')

    def test_get_not_allowed_and_login_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertFalse(self.device.backups.exists())
