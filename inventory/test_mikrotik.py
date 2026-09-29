"""MikroTik RouterOS support across backups, pushes, SSH logins and syslog."""
import asyncio
from unittest import mock

from django.test import SimpleTestCase

from automation import engine
from backups import collector
from syslog_server import parser
from . import platforms, ssh

V7_EXPORT = (
    '# 2026-09-29 10:00:00 by RouterOS 7.16.1\r\n'
    '# software id = ABCD-1234\r\n'
    '#\r\n'
    '# model = RB5009UG+S+\r\n'
    '/interface bridge add name=bridge1\r\n'
    '/ip address add address=10.0.99.1/24 interface=bridge1 network=10.0.99.0\r\n'
    '/system identity set name=mt-core\r\n'
)


def mikrotik(**kwargs):
    return mock.Mock(vendor_id=1, vendor='MikroTik', hostname='mt-core', username='netops', **kwargs)


class FakeRouterOS:
    """A RouterOS CLI on an interactive shell: no config mode, errors like "failure: ..." and "bad command name"."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.stdin = mock.Mock(write=self.write)
        self.stdout = mock.Mock(read=self.read)
        self.queue.put_nowait('\r\n\r\n  MikroTik RouterOS 7.16.1\r\n\r\n[netops@mt-core] > ')

    def write(self, data):
        line = data.rstrip('\n')
        self.sent.append(line)
        if line.startswith('/ip address add') and '10.0.99.1/' in line:
            out = 'failure: already have such address\r\n'
        elif line.startswith('/nonsense'):
            out = 'bad command name nonsense (line 1 column 2)\r\n'
        elif line == '/system clock print':
            out = '  time: 10:00:00\r\n  date: 2026-09-29\r\n'
        else:
            out = ''
        self.queue.put_nowait(f'{line}\r\n{out}[netops@mt-core] > ')

    async def read(self, n):
        return await self.queue.get()

    def close(self):
        pass


class PlatformTests(SimpleTestCase):
    def test_detection(self):
        for vendor, platform in [('MikroTik', platforms.MIKROTIK), ('Mikrotik RouterOS', platforms.MIKROTIK),
                                 ('Cisco', platforms.IOS), ('Cisco Nexus', platforms.NXOS), ('Juniper', platforms.JUNOS),
                                 ('Some New Vendor', platforms.IOS)]:
            self.assertEqual(platforms.platform_for(mock.Mock(vendor_id=1, vendor=vendor)), platform, vendor)

    def test_login_options_only_for_automated_mikrotik_sessions(self):
        self.assertEqual(ssh.login_name(mikrotik(), automation=True), 'netops+cet511w4098h')
        self.assertEqual(ssh.login_name(mikrotik(), automation=False), 'netops')  # SSH console: normal login
        cisco = mock.Mock(vendor_id=1, vendor='Cisco', username='netops')
        self.assertEqual(ssh.login_name(cisco, automation=True), 'netops')


class MikrotikBackupTests(SimpleTestCase):
    def test_export_header_timestamps_are_ignored(self):
        v6 = V7_EXPORT.replace('# 2026-09-29 10:00:00 by RouterOS 7.16.1', '# sep/29/2026 10:00:00 by RouterOS 6.49.10')
        later = V7_EXPORT.replace('10:00:00 by RouterOS 7.16.1', '11:45:12 by RouterOS 7.16.1')
        cleaned = collector.clean_config(V7_EXPORT)
        self.assertNotIn('by RouterOS', cleaned)
        self.assertIn('# software id = ABCD-1234', cleaned)          # Stable header lines are kept
        self.assertEqual(cleaned, collector.clean_config(later))
        self.assertEqual(cleaned, collector.clean_config(v6))

    def test_uses_terse_export_and_falls_back_on_old_routeros(self):
        def run(command, check=False):
            if command == '/export terse':  # e.g. an old RouterOS 6 release
                return mock.Mock(stdout='expected end of command (line 1 column 9)\r\n')
            return mock.Mock(stdout=V7_EXPORT)
        conn = mock.Mock(run=mock.AsyncMock(side_effect=run))
        with mock.patch('backups.collector.ssh.connect', mock.AsyncMock(return_value=conn)) as connect:
            config = asyncio.run(collector.fetch_config(mikrotik()))
        self.assertIn('/system identity set name=mt-core', config)
        self.assertEqual([c.args[0] for c in conn.run.call_args_list], ['/export terse', '/export'])
        self.assertTrue(connect.call_args.kwargs['automation'])

    def test_routeros_errors_fail_the_backup(self):
        with self.assertRaisesRegex(collector.BackupError, 'not enough permissions'):
            collector.validate('not enough permissions (9)\n', '/export terse')


class MikrotikPushTests(SimpleTestCase):
    def push(self, lines, mode='config', save=True):
        cli = FakeRouterOS()
        conn = mock.Mock(create_process=mock.AsyncMock(return_value=cli))
        with mock.patch('automation.engine.ssh.connect', mock.AsyncMock(return_value=conn)) as connect:
            transcript, error, saved = asyncio.run(engine.push_to_device(mikrotik(), lines, mode, save))
        self.assertTrue(connect.call_args.kwargs['automation'])
        return cli, transcript, error, saved

    def test_no_config_mode_and_no_save_command(self):
        cli, _, error, saved = self.push(['/ip address add address=10.0.50.1/24 interface=bridge1', '/system identity set name=mt-core'])
        self.assertEqual(error, '')
        self.assertFalse(saved)  # RouterOS saves every change itself; nothing to "write memory"
        self.assertEqual(cli.sent, ['/ip address add address=10.0.50.1/24 interface=bridge1',
                                    '/system identity set name=mt-core', 'exit'])

    def test_routeros_failure_stops_the_device(self):
        cli, _, error, _ = self.push(['/ip address add address=10.0.99.1/24 interface=bridge1', '/system identity set name=x'])
        self.assertIn('failure: already have such address', error)
        self.assertNotIn('/system identity set name=x', cli.sent)

    def test_bad_command_detected(self):
        _, _, error, _ = self.push(['/nonsense'])
        self.assertIn('bad command name', error)

    def test_exec_output_collected(self):
        _, transcript, error, _ = self.push(['/system clock print'], mode='exec')
        self.assertEqual(error, '')
        self.assertIn('date: 2026-09-29', transcript)


class MikrotikSyslogTests(SimpleTestCase):
    def test_config_change_messages(self):
        for message in ['system,info address added by admin', 'system,info filter rule changed by netops',
                        'system,info route 0.0.0.0/0 removed by admin', 'system,info,account user admin logged in from 10.0.0.5 via ssh']:
            expected = 'logged in' not in message
            self.assertEqual(parser.is_config_change(message), expected, message)
        # Cisco interface messages must not look like config changes
        self.assertFalse(parser.is_config_change('%LINK-3-UPDOWN: Interface Gi1/0/1, changed state to down'))

    def test_routeros_bsd_syslog_parses(self):
        _, severity, hostname, message = parser.parse(b'<30>Sep 29 10:00:00 mt-core system,info address added by admin')
        self.assertEqual((severity, hostname), (6, 'mt-core'))
        self.assertTrue(parser.is_config_change(message))
