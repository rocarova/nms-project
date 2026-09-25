from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse


class SignupDisabledTests(TestCase):
    def test_signup_is_off_by_default(self):
        self.assertEqual(self.client.get(reverse('signup')).status_code, 404)
        self.assertEqual(self.client.post(reverse('signup'), {'username': 'x'}).status_code, 404)
        self.assertNotContains(self.client.get(reverse('login')), reverse('signup'))


@override_settings(ALLOW_SIGNUP=True)
class AuthViewTests(TestCase):
    def test_signup_rejects_weak_password(self):
        response = self.client.post(reverse('signup'), {'username': 'bob', 'password1': '123', 'password2': '123'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='bob').exists())

    def test_signup_missing_fields_does_not_crash(self):
        response = self.client.post(reverse('signup'), {})
        self.assertEqual(response.status_code, 200)

    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(reverse('signup'), {
            'username': 'bob', 'password1': 'Tr1cky-passw0rd', 'password2': 'Tr1cky-passw0rd',
        })
        self.assertRedirects(response, reverse('dashboard'))
        self.assertTrue(User.objects.filter(username='bob').exists())

    def test_login_follows_safe_next(self):
        User.objects.create_user('bob', password='Tr1cky-passw0rd')
        response = self.client.post(reverse('login'), {
            'username': 'bob', 'password': 'Tr1cky-passw0rd', 'next': reverse('inventory'),
        })
        self.assertRedirects(response, reverse('inventory'))

    def test_login_ignores_external_next(self):
        User.objects.create_user('bob', password='Tr1cky-passw0rd')
        response = self.client.post(reverse('login'), {
            'username': 'bob', 'password': 'Tr1cky-passw0rd', 'next': 'https://evil.example.com/',
        })
        self.assertRedirects(response, reverse('dashboard'))

    def test_login_bad_password(self):
        User.objects.create_user('bob', password='Tr1cky-passw0rd')
        response = self.client.post(reverse('login'), {'username': 'bob', 'password': 'wrong'})
        self.assertContains(response, 'did not match')

    def test_logout_get_redirects_without_logging_out(self):
        user = User.objects.create_user('bob', password='Tr1cky-passw0rd')
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse('logoutuser')), reverse('login'), target_status_code=302)
        self.assertIn('_auth_user_id', self.client.session)
