from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from backups.models import ConfigBackup
from syslog_server.models import SyslogMessage
from .models import Device, Location, Vendor


class EditDeleteDeviceTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('admin')
        self.client.force_login(user)
        self.vendor, self.location = Vendor.objects.create(name='Cisco'), Location.objects.create(name='HQ')
        self.device = Device.objects.create(hostname='sw1', ip_address='10.0.0.1', vendor=self.vendor, location=self.location,
                                            username='netops', password='original', creator=user)

    def data(self, **overrides):
        data = {'hostname': 'sw1', 'ip_address': '10.0.0.1', 'vendor': self.vendor.pk, 'location': self.location.pk,
                'username': 'netops', 'password': '', 'enabled': 'on'}
        data.update(overrides)
        return data

    def test_edit_form_prefilled_without_password(self):
        response = self.client.get(reverse('edit_device', args=[self.device.id]))
        self.assertContains(response, 'Edit sw1')
        self.assertContains(response, 'value="10.0.0.1"')
        self.assertNotContains(response, 'original')

    def test_edit_blank_password_keeps_saved_one(self):
        response = self.client.post(reverse('edit_device', args=[self.device.id]), self.data(hostname='core-sw1', ip_address='10.0.0.9'))
        self.assertRedirects(response, reverse('inventory'))
        self.device.refresh_from_db()
        self.assertEqual((self.device.hostname, self.device.ip_address, self.device.password), ('core-sw1', '10.0.0.9', 'original'))

    def test_edit_new_password_and_validation(self):
        self.client.post(reverse('edit_device', args=[self.device.id]), self.data(password='changed'))
        self.device.refresh_from_db()
        self.assertEqual(self.device.password, 'changed')
        response = self.client.post(reverse('edit_device', args=[self.device.id]), self.data(ip_address='bad'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors['ip_address'])

    def test_delete_confirmation_then_delete(self):
        ConfigBackup.record_success(self.device, 'hostname sw1')
        SyslogMessage.objects.create(received_at=timezone.now(), source_ip='10.0.0.1', device=self.device, message='x')
        url = reverse('delete_device', args=[self.device.id])

        response = self.client.get(url)
        self.assertContains(response, 'Delete sw1?')
        self.assertContains(response, '1 configuration backup')
        self.assertTrue(Device.objects.filter(pk=self.device.id).exists())  # GET never deletes

        response = self.client.post(url, follow=True)
        self.assertContains(response, 'sw1 was deleted.')
        self.assertFalse(Device.objects.exists())
        self.assertFalse(ConfigBackup.objects.exists())
        self.assertIsNone(SyslogMessage.objects.get().device)  # log kept, unlinked

    def test_inventory_has_working_actions(self):
        response = self.client.get(reverse('inventory'))
        self.assertContains(response, reverse('edit_device', args=[self.device.id]))
        self.assertContains(response, reverse('delete_device', args=[self.device.id]))
        self.assertContains(response, 'Add Device')
        self.assertNotContains(response, 'coming soon')
