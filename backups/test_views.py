import io

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from inventory.models import Device, Location, Vendor
from .diff import diff_rows, line_kind
from .models import ConfigBackup


class DiffTests(SimpleTestCase):
    OLD = 'hostname sw1\n!\nvlan 10\n name USERS\n!\nend'
    NEW = 'hostname sw1\n!\nvlan 10\n name STAFF\n!\nvlan 30\n!\nend'

    def test_counts_and_rows(self):
        rows, added, removed = diff_rows(self.OLD, self.NEW)
        self.assertEqual((added, removed), (3, 1))
        self.assertEqual(rows[0]['kind'], 'hunk')
        self.assertIn({'kind': 'del', 'old': 4, 'new': None, 'text': ' name USERS'}, rows)
        self.assertIn({'kind': 'add', 'old': None, 'new': 4, 'text': ' name STAFF'}, rows)

    def test_changes_only_trims_context(self):
        old = '\n'.join(f'line {i}' for i in range(100))
        new = old.replace('line 50', 'line fifty')
        rows, _, _ = diff_rows(old, new, context=3)
        self.assertEqual(len(rows), 1 + 3 + 2 + 3)  # hunk header, context, del+add, context
        full, _, _ = diff_rows(old, new, context=None)
        self.assertEqual(len(full), 101)

    def test_identical(self):
        self.assertEqual(diff_rows(self.OLD, self.OLD), ([], 0, 0))

    def test_line_kind(self):
        self.assertEqual([line_kind(l) for l in ['interface Gi1/0/1', ' shutdown', '!', '']],
                         ['section', 'child', 'comment', 'blank'])


class BackupViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('admin')
        self.client.force_login(user)
        vendor, location = Vendor.objects.create(name='Cisco'), Location.objects.create(name='HQ')
        self.device = Device.objects.create(hostname='sw1', ip_address='10.0.0.1', vendor=vendor, location=location,
                                            username='u', password='p', creator=user)
        self.other = Device.objects.create(hostname='sw2', ip_address='10.0.0.2', vendor=vendor, location=location,
                                           username='u', password='p', creator=user)
        self.b1 = ConfigBackup.record_success(self.device, 'hostname sw1\nvlan 10\n')
        self.failed = ConfigBackup.record_failure(self.device, 'SSH timeout')
        self.b2 = ConfigBackup.record_success(self.device, 'hostname sw1\nvlan 10\nvlan 20\n')

    def test_history_lists_backups_with_previous_links(self):
        response = self.client.get(reverse('device_backups', args=[self.device.id]))
        self.assertContains(response, 'SSH timeout')
        backups = {b.id: b for b in response.context['backups']}
        self.assertEqual(backups[self.b2.id].previous_id, self.b1.id)
        self.assertIsNone(backups[self.b1.id].previous_id)
        self.assertEqual(response.context['change_count'], 1)

    def test_detail_shows_full_config(self):
        response = self.client.get(reverse('backup_detail', args=[self.device.id, self.b2.id]))
        self.assertEqual([l['text'] for l in response.context['lines']], ['hostname sw1', 'vlan 10', 'vlan 20'])
        self.assertEqual(response.context['previous'], self.b1)
        self.assertIsNone(response.context['next'])

    def test_compare_orders_older_to_newer(self):
        url = reverse('backup_compare', args=[self.device.id])
        response = self.client.get(url, {'a': self.b2.id, 'b': self.b1.id})  # picked newest first
        self.assertEqual((response.context['old'], response.context['new']), (self.b1, self.b2))
        self.assertEqual((response.context['added'], response.context['removed']), (1, 0))
        full = self.client.get(url, {'a': self.b1.id, 'b': self.b2.id, 'view': 'full'})
        self.assertEqual(len(full.context['rows']), 3)

    def test_cannot_mix_devices_or_use_failed_backups(self):
        other_backup = ConfigBackup.record_success(self.other, 'hostname sw2')
        self.assertEqual(self.client.get(reverse('backup_detail', args=[self.device.id, other_backup.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('backup_detail', args=[self.device.id, self.failed.id])).status_code, 404)
        url = reverse('backup_compare', args=[self.device.id])
        self.assertEqual(self.client.get(url, {'a': self.b1.id, 'b': other_backup.id}).status_code, 404)

    def test_download(self):
        response = self.client.get(reverse('backup_download', args=[self.device.id, self.b1.id]))
        self.assertEqual(response.content, b'hostname sw1\nvlan 10\n')
        self.assertIn('sw1_', response['Content-Disposition'])

    def test_inventory_links_hostname_to_backups(self):
        response = self.client.get(reverse('inventory'))
        self.assertContains(response, reverse('device_backups', args=[self.device.id]))

    def test_demo_backups_command(self):
        call_command('demo_backups', 'sw2', stdout=io.StringIO())
        self.assertEqual(self.other.backups.count(), 7)
        self.assertEqual(self.other.backups.filter(changed=True).count(), 3)
        call_command('demo_backups', '--clear', stdout=io.StringIO())
        self.assertEqual(self.other.backups.count(), 0)
        self.assertEqual(self.device.backups.count(), 3)  # real backups untouched
