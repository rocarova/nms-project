from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .views import is_valid_target


class TargetValidationTests(SimpleTestCase):
    def test_valid_targets(self):
        for target in ('8.8.8.8', '2001:db8::1', 'google.com', 'core-sw1', 'sw1.site.example.net.'):
            self.assertTrue(is_valid_target(target), target)

    def test_invalid_targets(self):
        for target in ('-f', '--help', '8.8.8.8; rm -rf /', 'host name', '-host', 'a' * 300, ''):
            self.assertFalse(is_valid_target(target), target)


class ToolViewTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('admin', password='s3cret-pass!'))

    def test_ping_rejects_option_injection(self):
        response = self.client.get(reverse('ping'), {'target': '-f'})
        self.assertEqual(response.status_code, 400)

    @mock.patch('net_tools.views.subprocess.Popen')
    def test_ping_streams_output(self, popen):
        process = popen.return_value
        process.stdout = mock.MagicMock(__iter__=lambda self: iter(['Reply from 8.8.8.8\n']))
        process.poll.return_value = 0

        response = self.client.get(reverse('ping'), {'target': '8.8.8.8'})
        self.assertEqual(b''.join(response.streaming_content), b'Reply from 8.8.8.8\n')
        self.assertEqual(popen.call_args.args[0][-1], '8.8.8.8')

    @mock.patch('net_tools.views.subprocess.Popen')
    def test_nslookup_builds_command(self, popen):
        popen.return_value.stdout = mock.MagicMock(__iter__=lambda self: iter([]))
        popen.return_value.poll.return_value = 0
        response = self.client.get(reverse('nslookup'), {'target': 'example.com', 'type': 'mx', 'server': '1.1.1.1'})
        b''.join(response.streaming_content)
        self.assertEqual(popen.call_args.args[0], ['nslookup', '-type=MX', 'example.com', '1.1.1.1'])

    def test_nslookup_rejects_bad_input(self):
        for params in ({'target': '-x'}, {'target': 'example.com', 'type': 'BOGUS'},
                       {'target': 'example.com', 'server': '-debug'}):
            self.assertEqual(self.client.get(reverse('nslookup'), params).status_code, 400, params)

    def test_nslookup_page_renders(self):
        self.assertContains(self.client.get(reverse('nslookup')), 'name="type"')

    @mock.patch('net_tools.views.shutil.which', return_value=None)
    def test_traceroute_missing_binary(self, _which):
        response = self.client.get(reverse('traceroute'), {'target': '8.8.8.8'})
        self.assertEqual(response.status_code, 500)
