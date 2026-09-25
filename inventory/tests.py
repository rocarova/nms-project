from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Device, Location, Vendor


class InventoryViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('admin', password='s3cret-pass!')
        self.client.force_login(self.user)
        self.vendor = Vendor.objects.create(name='Cisco')
        self.location = Location.objects.create(name='Houston')

    def device_data(self, **overrides):
        data = {
            'hostname': 'core-sw1', 'ip_address': '10.0.0.1', 'vendor': self.vendor.pk,
            'username': 'netops', 'password': 'pw', 'location': self.location.pk, 'enabled': 'on',
        }
        data.update(overrides)
        return data

    def test_add_device_saves_and_sets_creator(self):
        response = self.client.post(reverse('add_device'), self.device_data())
        self.assertRedirects(response, reverse('inventory'))
        self.assertEqual(Device.objects.get().creator, self.user)

    def test_add_device_invalid_ip_rerenders_form(self):
        response = self.client.post(reverse('add_device'), self.device_data(ip_address='not-an-ip'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Device.objects.exists())
        self.assertContains(response, 'Enter a valid IPv4 or IPv6 address')

    def test_add_location_redirects_back_to_device_form(self):
        response = self.client.post(reverse('add_location'), {'name': 'Guadalajara'})
        self.assertRedirects(response, reverse('add_device'))

    def test_inventory_shows_vendor_location_and_status(self):
        self.client.post(reverse('add_device'), self.device_data(enabled=''))
        response = self.client.get(reverse('inventory'))
        self.assertContains(response, 'Cisco')
        self.assertContains(response, 'Houston')
        self.assertContains(response, 'Disabled')

    def test_pages_require_login(self):
        self.client.logout()
        for name in ('inventory', 'add_device', 'add_vendor', 'add_location', 'dashboard'):
            url = reverse(name)
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
