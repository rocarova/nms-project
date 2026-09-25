from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backups.models import ConfigBackup
from inventory.models import Device, Location, Vendor
from syslog_server.models import SyslogMessage


class DashboardTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('admin')
        self.client.force_login(user)
        vendor, location = Vendor.objects.create(name='Cisco'), Location.objects.create(name='HQ')
        self.devices = [
            Device.objects.create(hostname=f'sw{i}', ip_address=f'10.0.0.{i}', vendor=vendor, location=location,
                                  username='u', password='p', creator=user)
            for i in range(1, 5)
        ]

    def context(self):
        return self.client.get(reverse('dashboard')).context

    def test_backup_counts_use_latest_attempt(self):
        sw1, sw2, sw3, _sw4 = self.devices
        ConfigBackup.record_failure(sw1, 'timeout')
        ConfigBackup.record_success(sw1, 'cfg')          # sw1 recovered -> ok
        ConfigBackup.record_success(sw2, 'cfg')
        ConfigBackup.record_failure(sw2, 'auth failed')  # sw2 latest failed
        ConfigBackup.record_failure(sw3, 'timeout')      # sw3 failed; sw4 never

        ctx = self.context()
        self.assertEqual(ctx['total_devices'], 4)
        self.assertEqual((ctx['backup_ok'], ctx['backup_failed'], ctx['backup_never']), (1, 2, 1))
        self.assertEqual([d.hostname for d in ctx['attention_devices']], ['sw3', 'sw2', 'sw4'])

    def test_changed_counts_devices_from_both_sources_once(self):
        sw1, sw2, sw3, _ = self.devices
        now = timezone.now()
        ConfigBackup.record_success(sw1, 'a')
        ConfigBackup.record_success(sw1, 'b')  # changed via backup diff
        SyslogMessage.objects.create(received_at=now, source_ip='10.0.0.1', device=sw1, message='%SYS-5-CONFIG_I', is_config_change=True)
        SyslogMessage.objects.create(received_at=now, source_ip='10.0.0.2', device=sw2, message='%SYS-5-CONFIG_I', is_config_change=True)
        # Outside the 24h window: not counted
        SyslogMessage.objects.create(received_at=now - timedelta(days=2), source_ip='10.0.0.3', device=sw3,
                                     message='%SYS-5-CONFIG_I', is_config_change=True)

        ctx = self.context()
        self.assertEqual(ctx['changed_count'], 2)
        self.assertEqual(len(ctx['recent_changes']), 3)

    def test_empty_dashboard_renders(self):
        Device.objects.all().delete()
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'No switches yet')
        self.assertContains(response, 'No syslog received yet')
        self.assertEqual(len(response.context['syslog_hourly']), 24)
