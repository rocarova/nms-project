from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SystemSettings


class SettingsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('admin', password='Old-passw0rd!')
        self.client.force_login(self.user)
        self.url = reverse('settings')

    def post(self, section, **data):
        return self.client.post(self.url, {'section': section, **data})

    def test_page_renders_all_tabs(self):
        response = self.client.get(self.url, {'tab': 'email'})
        for pane in ('pane-account', 'pane-syslog', 'pane-backups', 'pane-email'):
            self.assertContains(response, pane)
        self.assertEqual(response.context['tab'], 'email')

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_change_password_keeps_session(self):
        response = self.post('account', old_password='Old-passw0rd!', new_password1='N3w-Passw0rd!x', new_password2='N3w-Passw0rd!x')
        self.assertRedirects(response, f'{self.url}?tab=account')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('N3w-Passw0rd!x'))
        self.assertEqual(self.client.get(self.url).status_code, 200)  # still signed in

    def test_change_password_wrong_old_password(self):
        response = self.post('account', old_password='nope', new_password1='N3w-Passw0rd!x', new_password2='N3w-Passw0rd!x')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['tab'], 'account')
        self.assertTrue(response.context['forms']['account'].errors)

    def test_syslog_port_saved_and_validated(self):
        self.post('syslog', syslog_port='1514', syslog_retention_days='14')
        self.assertEqual(SystemSettings.load().syslog_port, 1514)
        response = self.post('syslog', syslog_port='70000', syslog_retention_days='14')
        self.assertTrue(response.context['forms']['syslog'].errors['syslog_port'])
        self.assertEqual(SystemSettings.load().syslog_port, 1514)

    def test_saving_does_not_overwrite_listener_status(self):
        seen = timezone.now()
        SystemSettings.objects.filter(pk=SystemSettings.load().pk).update(listener_port=514, listener_seen_at=seen)
        self.post('backups', backup_frequency='weekly', backup_time='03:30', backup_weekday='2', backup_retention='10')
        system = SystemSettings.load()
        self.assertEqual((system.backup_frequency, system.backup_weekday, system.backup_retention), ('weekly', 2, 10))
        self.assertEqual(system.listener_port, 514)
        self.assertEqual(system.listener_seen_at, seen)

    def email_data(self, **overrides):
        data = {'email_enabled': 'on', 'smtp_host': 'smtp.example.com', 'smtp_port': '587', 'smtp_security': 'starttls',
                'smtp_username': 'nms', 'smtp_password': 'mail-secret', 'email_from': 'nms@example.com',
                'email_recipients': 'noc@example.com, oncall@example.com', 'notify_backup_failed': 'on'}
        data.update(overrides)
        return data

    def test_email_settings_saved(self):
        self.post('email', **self.email_data())
        system = SystemSettings.load()
        self.assertTrue(system.email_enabled)
        self.assertEqual(system.email_recipients, 'noc@example.com\noncall@example.com')
        self.assertTrue(system.notify_backup_failed)
        self.assertFalse(system.notify_config_changed)

    def test_blank_smtp_password_keeps_saved_one(self):
        self.post('email', **self.email_data())
        self.post('email', **self.email_data(smtp_password='', smtp_host='smtp2.example.com'))
        system = SystemSettings.load()
        self.assertEqual((system.smtp_host, system.smtp_password), ('smtp2.example.com', 'mail-secret'))

    def test_email_validation(self):
        response = self.post('email', **self.email_data(email_recipients='noc@example.com, not-an-email'))
        self.assertIn('not-an-email', str(response.context['forms']['email'].errors))
        response = self.post('email', **self.email_data(smtp_host=''))
        self.assertIn('smtp_host', response.context['forms']['email'].errors)
