from django.contrib.auth.models import User
from django.test import TestCase

from inventory.models import Device, Location, Vendor
from .models import ConfigBackup


class ConfigBackupTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            hostname='core-sw1', ip_address='10.0.0.1', vendor=Vendor.objects.create(name='Cisco'),
            location=Location.objects.create(name='HQ'), username='u', password='p',
            creator=User.objects.create_user('admin'),
        )

    def test_first_backup_is_not_a_change(self):
        self.assertFalse(ConfigBackup.record_success(self.device, 'hostname core-sw1').changed)

    def test_identical_config_is_not_a_change(self):
        ConfigBackup.record_success(self.device, 'hostname core-sw1')
        self.assertFalse(ConfigBackup.record_success(self.device, 'hostname core-sw1').changed)

    def test_different_config_is_a_change(self):
        ConfigBackup.record_success(self.device, 'hostname core-sw1')
        self.assertTrue(ConfigBackup.record_success(self.device, 'hostname core-sw1\nvlan 20').changed)

    def test_failures_are_ignored_when_comparing(self):
        ConfigBackup.record_success(self.device, 'hostname core-sw1')
        ConfigBackup.record_failure(self.device, 'SSH timeout')
        self.assertFalse(ConfigBackup.record_success(self.device, 'hostname core-sw1').changed)
