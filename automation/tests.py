import asyncio
import threading
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse

from backups.models import ConfigBackup
from inventory.models import Device, Location, Vendor
from . import engine
from .models import PushJob, PushResult, Script


class FakeCLI:
    """Pretends to be a Cisco-like CLI on an interactive SSH shell."""

    def __init__(self, hostname='sw1', reject=()):
        self.queue = asyncio.Queue()
        self.hostname, self.mode, self.reject, self.sent = hostname, '', reject, []
        self.stdin = mock.Mock(write=self.write)
        self.stdout = mock.Mock(read=self.read)
        self.queue.put_nowait(f'\r\nWelcome\r\n{hostname}#')

    def write(self, data):
        line = data.rstrip('\n')
        self.sent.append(line)
        out = ''
        if line == 'configure terminal':
            self.mode = '(config)'
        elif line == 'end':
            self.mode = ''
        elif any(r in line for r in self.reject):
            out = "% Invalid input detected at '^' marker.\r\n"
        elif line == 'write memory':
            out = 'Building configuration...\r\n[OK]\r\n'
        elif line == 'show clock':
            out = '*10:00:00.000 UTC Mon Sep 28 2026\r\n'
        self.queue.put_nowait(f'{line}\r\n{out}{self.hostname}{self.mode}#')

    async def read(self, n):
        return await self.queue.get()

    def close(self):
        pass


def make_device(name='sw1', ip='10.0.0.1', enabled=True, vendor='Cisco'):
    user = User.objects.get_or_create(username='admin')[0]
    return Device.objects.create(hostname=name, ip_address=ip, enabled=enabled,
                                 vendor=Vendor.objects.get_or_create(name=vendor)[0],
                                 location=Location.objects.get_or_create(name='HQ')[0],
                                 username='netops', password='p', creator=user)


class RenderTests(SimpleTestCase):
    def test_variables_and_comments(self):
        device = mock.Mock(hostname='sw1', ip_address='10.0.0.1', location='HQ', vendor='Cisco', username='netops')
        text = engine.render('# comment\nhostname {{hostname}}\n\nsnmp-server location {{ location }}\n', device)
        self.assertEqual(engine.command_lines(text), ['hostname sw1', 'snmp-server location HQ'])
        with self.assertRaisesRegex(engine.ScriptError, 'Unknown variable'):
            engine.render('description {{ serial }}', device)

    def test_rejection_ignores_the_echo(self):
        self.assertEqual(engine.rejection('vlan 10\r\nsw1(config-vlan)#', 'vlan 10'), '')
        self.assertIn('Invalid input', engine.rejection("vlan abc\n% Invalid input detected at '^' marker.\nsw1(config)#", 'vlan abc'))
        self.assertIn('error:', engine.rejection('commit and-quit\nerror: configuration check-out failed\n', 'commit and-quit'))

    def test_vendor_profiles(self):
        self.assertEqual(engine.profile_for(mock.Mock(vendor_id=1, vendor='Juniper')).exit, 'commit and-quit')
        self.assertEqual(engine.profile_for(mock.Mock(vendor_id=1, vendor='Cisco')).enter, 'configure terminal')
        self.assertEqual(engine.profile_for(mock.Mock(vendor_id=1, vendor='MikroTik')).enter, '')


class PushToDeviceTests(SimpleTestCase):
    def run_push(self, cli, lines, mode='config', save=True):
        conn = mock.Mock(create_process=mock.AsyncMock(return_value=cli))
        device = mock.Mock(vendor_id=1, vendor='Cisco', hostname='sw1')
        with mock.patch('automation.engine.ssh.connect', mock.AsyncMock(return_value=conn)):
            return asyncio.run(engine.push_to_device(device, lines, mode, save))

    def test_config_push_enters_config_mode_and_saves(self):
        cli = FakeCLI()
        transcript, error, saved = self.run_push(cli, ['vlan 30', ' name GUEST'])
        self.assertEqual(error, '')
        self.assertTrue(saved)
        self.assertEqual(cli.sent, ['terminal length 0', 'configure terminal', 'vlan 30', ' name GUEST', 'end', 'write memory', 'exit'])
        self.assertIn('[OK]', transcript)

    def test_rejected_line_stops_and_does_not_save(self):
        cli = FakeCLI(reject=['bogus'])
        _, error, saved = self.run_push(cli, ['vlan 30', 'bogus command', 'vlan 40'])
        self.assertIn('"bogus command" was rejected: % Invalid input', error)
        self.assertFalse(saved)
        self.assertNotIn('vlan 40', cli.sent)          # Nothing more sent after the error
        self.assertNotIn('write memory', cli.sent)
        self.assertIn('end', cli.sent)                 # Left configuration mode

    def test_exec_mode_runs_commands_as_typed(self):
        cli = FakeCLI()
        transcript, error, saved = self.run_push(cli, ['show clock'], mode='exec')
        self.assertEqual((error, saved), ('', False))
        self.assertNotIn('configure terminal', cli.sent)
        self.assertIn('10:00:00.000 UTC', transcript)


class PushViewTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('operator'))
        self.sw1, self.sw2 = make_device('sw1', '10.0.0.1'), make_device('sw2', '10.0.0.2')
        self.off = make_device('sw3', '10.0.0.3', enabled=False)

    def data(self, **extra):
        return {'devices': [self.sw1.id, self.sw2.id], 'mode': 'config',
                'commands': 'snmp-server location {{ location }}\nhostname {{ hostname }}', **extra}

    def test_preselects_devices_and_script(self):
        script = Script.objects.create(name='NTP', mode='config', commands='ntp server 1.1.1.1')
        response = self.client.get(reverse('push'), {'devices': f'{self.sw1.id},{self.sw2.id}', 'script': script.id})
        rows = {r['device'].hostname: r['selected'] for r in response.context['rows']}
        self.assertEqual(rows, {'sw1': True, 'sw2': True, 'sw3': False})
        self.assertEqual(response.context['form'].initial['commands'], 'ntp server 1.1.1.1')

    def test_preview_renders_per_device(self):
        response = self.client.post(reverse('push'), self.data(action='preview'))
        self.assertEqual(response.context['step'], 'preview')
        lines = {p['device'].hostname: p['lines'] for p in response.context['previews']}
        self.assertEqual(lines['sw2'], ['snmp-server location HQ', 'hostname sw2'])
        self.assertContains(response, 'Type PUSH to confirm')

    @mock.patch('automation.views.engine.start')
    def test_config_push_requires_confirmation(self, start):
        response = self.client.post(reverse('push'), self.data(action='run', confirm='yes'))
        self.assertTrue(response.context['form'].errors['confirm'])
        start.assert_not_called()

        response = self.client.post(reverse('push'), self.data(action='run', confirm='push', save_config='on'))
        job = PushJob.objects.get()
        self.assertRedirects(response, reverse('push_job', args=[job.id]), fetch_redirect_response=False)
        self.assertTrue(job.save_config)
        self.assertEqual(sorted(job.results.values_list('commands', flat=True)),
                         ['snmp-server location HQ\nhostname sw1', 'snmp-server location HQ\nhostname sw2'])
        start.assert_called_once_with(job)

    @mock.patch('automation.views.engine.start')
    def test_exec_needs_no_confirmation_and_never_saves(self, start):
        self.client.post(reverse('push'), self.data(action='run', mode='exec', commands='show clock', save_config='on'))
        self.assertFalse(PushJob.objects.get().save_config)

    def test_validation(self):
        response = self.client.post(reverse('push'), self.data(action='preview', devices=[self.off.id]))
        self.assertIn('devices', response.context['form'].errors)  # Disabled devices can't be pushed to
        response = self.client.post(reverse('push'), self.data(action='preview', commands='hostname {{ serial }}'))
        self.assertIn('Unknown variable', str(response.context['form'].errors['commands']))

    def test_status_endpoint_and_orphaned_job(self):
        job = PushJob.objects.create(mode='config', commands='x', status=PushJob.RUNNING)
        PushResult.objects.create(job=job, device=self.sw1, hostname='sw1', ip_address='10.0.0.1', status=PushResult.RUNNING)
        data = self.client.get(reverse('push_job_status', args=[job.id])).json()
        # No thread in this process owns the job (e.g. after a restart): it's reported as interrupted
        self.assertTrue(data['finished'])
        self.assertEqual(data['results'][0]['status'], 'failed')
        self.assertIn('Interrupted', data['results'][0]['error'])

    def test_scripts_crud(self):
        response = self.client.post(reverse('script_new'), {'name': 'NTP', 'mode': 'config', 'commands': 'ntp server {{ ip_address }}'})
        self.assertRedirects(response, f"{reverse('automation')}#scripts", fetch_redirect_response=False)
        script = Script.objects.get()
        self.client.post(reverse('script_edit', args=[script.id]), {'name': 'NTP servers', 'mode': 'config', 'commands': 'ntp server 1.1.1.1'})
        script.refresh_from_db()
        self.assertEqual(script.name, 'NTP servers')
        response = self.client.post(reverse('script_new'), {'name': 'Bad', 'mode': 'config', 'commands': '{{ nope }}'})
        self.assertIn('commands', response.context['form'].errors)
        self.client.post(reverse('script_delete', args=[script.id]))
        self.assertFalse(Script.objects.exists())

    def test_inventory_has_checkboxes_and_automation_page_renders(self):
        response = self.client.get(reverse('inventory'))
        self.assertContains(response, f'class="row-check device-check" value="{self.sw1.id}"')
        self.assertNotContains(response, f'class="row-check device-check" value="{self.off.id}"')
        self.assertEqual(self.client.get(reverse('automation')).status_code, 200)


class RunJobTests(TransactionTestCase):
    """run_job uses worker threads, which need committed data."""

    def setUp(self):
        self.devices = [make_device(f'sw{i}', f'10.0.0.{i}') for i in (1, 2, 3)]
        self.lock = threading.Lock()
        self.pushed = []

    def make_job(self, stop_on_error=False, mode='config'):
        job = PushJob.objects.create(mode=mode, commands='vlan 30', stop_on_error=stop_on_error, save_config=True)
        for d in self.devices:
            PushResult.objects.create(job=job, device=d, hostname=d.hostname, ip_address=d.ip_address, commands='vlan 30')
        return job

    def fake_backup(self, device):
        # Before: "hostname X"; after a successful push: "hostname X / vlan 30"
        with self.lock:
            changed = device.hostname in self.pushed
        return ConfigBackup.record_success(device, f'hostname {device.hostname}\n' + ('vlan 30\n' if changed else ''))

    def fake_push(self, fail=()):
        async def push(device, lines, mode, save):
            if device.hostname in fail:
                return 'transcript', '"vlan 30" was rejected: % Invalid input', False
            with self.lock:
                self.pushed.append(device.hostname)
            return 'transcript', '', save
        return push

    def run_it(self, job, fail=(), backup=None):
        with mock.patch('automation.engine.backup_device', side_effect=backup or self.fake_backup), \
                mock.patch('automation.engine.push_to_device', side_effect=self.fake_push(fail)):
            engine.run_job(job.id)
        job.refresh_from_db()
        return {r.hostname: r for r in job.results.all()}

    def test_all_succeed_with_before_after_diff(self):
        job = self.make_job()
        results = self.run_it(job)
        self.assertEqual(job.status, PushJob.SUCCEEDED)
        for r in results.values():
            self.assertEqual(r.status, PushResult.SUCCEEDED)
            self.assertTrue(r.config_changed)
            self.assertTrue(r.saved)
            self.assertNotEqual(r.backup_before_id, r.backup_after_id)

    def test_stop_on_first_error_skips_the_rest(self):
        job = self.make_job(stop_on_error=True)
        results = self.run_it(job, fail=('sw1',))
        self.assertEqual(job.status, PushJob.STOPPED)
        self.assertEqual(results['sw1'].status, PushResult.FAILED)
        self.assertEqual({results['sw2'].status, results['sw3'].status}, {PushResult.SKIPPED})
        self.assertEqual(self.pushed, [])

    def test_errors_without_stop_continue(self):
        job = self.make_job()
        results = self.run_it(job, fail=('sw2',))
        self.assertEqual(job.status, PushJob.FAILED)
        self.assertEqual(sorted(self.pushed), ['sw1', 'sw3'])
        self.assertIn('rejected', results['sw2'].error)
        self.assertFalse(results['sw2'].config_changed)

    def test_failed_pre_change_backup_means_nothing_is_pushed(self):
        job = self.make_job()
        results = self.run_it(job, backup=lambda d: ConfigBackup.record_failure(d, 'Authentication failed'))
        self.assertEqual(self.pushed, [])
        self.assertIn('Nothing was pushed', results['sw1'].error)
        self.assertEqual(job.status, PushJob.FAILED)
