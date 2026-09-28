from datetime import timedelta

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import Device, Location, Vendor
from .management.commands.syslog_server import Command
from .models import SyslogMessage
from . import parser


class ParserTests(SimpleTestCase):
    def test_cisco_style_message(self):
        facility, severity, hostname, message = parser.parse(
            b'<189>42: *Sep 24 12:00:01.123: %SYS-5-CONFIG_I: Configured from console by admin')
        self.assertEqual((facility, severity), (23, 5))
        self.assertEqual(hostname, '')
        self.assertIn('%SYS-5-CONFIG_I', message)

    def test_rfc3164(self):
        _, severity, hostname, message = parser.parse(b'<190>Sep 24 12:00:10 access-sw3 sshd[811]: Accepted password')
        self.assertEqual(severity, 6)
        self.assertEqual(hostname, 'access-sw3')
        self.assertEqual(message, 'sshd[811]: Accepted password')

    def test_rfc5424(self):
        _, severity, hostname, message = parser.parse(
            b'<164>1 2026-09-24T12:00:12.000Z edge-rtr1 mgd 4242 UI_COMMIT - User requested commit')
        self.assertEqual(severity, 4)
        self.assertEqual(hostname, 'edge-rtr1')
        self.assertEqual(message, 'mgd[4242]: UI_COMMIT User requested commit')
        self.assertTrue(parser.is_config_change(message))

    def test_rfc5424_nil_fields(self):
        _, _, hostname, message = parser.parse(b'<14>1 - - - - - - just the message')
        self.assertEqual((hostname, message), ('', 'just the message'))

    def test_no_pri(self):
        facility, severity, _, message = parser.parse(b'plain text message')
        self.assertIsNone(severity)
        self.assertEqual(message, 'plain text message')

    def test_invalid_utf8_does_not_crash(self):
        self.assertIn('�', parser.parse(b'<13>bad \xff bytes')[3])

    def test_config_change_detection(self):
        self.assertTrue(parser.is_config_change('%SYS-5-CONFIG_I: Configured from console by admin'))
        self.assertTrue(parser.is_config_change("mgd[4242]: UI_COMMIT: User 'netops' requested 'commit'"))
        self.assertFalse(parser.is_config_change('%LINK-3-UPDOWN: Interface Gi1/0/12, changed state to down'))


class TcpFramingTests(SimpleTestCase):
    def setUp(self):
        self.command = Command()
        self.frames = []
        self.command.ingest = lambda data, addr: self.frames.append(data)

    def test_newline_framing_keeps_partial_frame(self):
        rest = self.command.split_tcp_frames(b'<13>one\n<13>two\n<13>thr', ('1.1.1.1', 1))
        self.assertEqual(self.frames, [b'<13>one', b'<13>two'])
        self.assertEqual(rest, b'<13>thr')

    def test_octet_counting(self):
        rest = self.command.split_tcp_frames(b'7 <13>one8 <13>two\n5 <13>', ('1.1.1.1', 1))
        self.assertEqual(self.frames, [b'<13>one', b'<13>two\n'])
        self.assertEqual(rest, b'5 <13>')

    def test_nul_framing(self):
        self.command.split_tcp_frames(b'<13>one\x00<13>two\n', ('1.1.1.1', 1))
        self.assertEqual(self.frames, [b'<13>one', b'<13>two'])


class SaveTests(TestCase):
    def test_save_links_device_and_flags_config_change(self):
        user = User.objects.create_user('admin')
        device = Device.objects.create(hostname='core-sw1', ip_address='10.0.0.1', vendor=Vendor.objects.create(name='Cisco'),
                                       location=Location.objects.create(name='HQ'), username='u', password='p', creator=user)
        command = Command()
        command.devices_loaded_at = command.purged_at = command.settings_loaded_at = 0
        command.retention_days = 30
        command.port, command.error, command.failed_port = 514, '', None
        command.port_pinned = command.retention_pinned = False
        command.config_notified_at = {}
        command.save([
            (timezone.now(), '10.0.0.1', b'<189>1: %SYS-5-CONFIG_I: Configured from console'),
            (timezone.now(), '10.9.9.9', b'<187>2: %LINK-3-UPDOWN: down'),
        ])
        known, unknown = SyslogMessage.objects.order_by('id')
        self.assertEqual(known.device, device)
        self.assertTrue(known.is_config_change)
        self.assertIsNone(unknown.device)
        self.assertEqual(unknown.severity, 3)


class MessagesApiTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('admin'))
        now = timezone.now()
        for severity, msg in [(3, '%LINK-3-UPDOWN: Gi1/0/12 down'), (6, 'sshd: Accepted password'), (5, '%SYS-5-CONFIG_I: Configured')]:
            SyslogMessage.objects.create(received_at=now, source_ip='10.0.0.1', severity=severity, message=msg)

    def get(self, **params):
        return self.client.get(reverse('syslog_messages'), params)

    def test_plain_search_is_case_insensitive(self):
        messages = self.get(q='gi1/0/12').json()['messages']
        self.assertEqual([m['message'] for m in messages], ['%LINK-3-UPDOWN: Gi1/0/12 down'])

    def test_regex_search(self):
        messages = self.get(q=r'%\w+-[35]-', regex='1').json()['messages']
        self.assertEqual(len(messages), 2)

    def test_invalid_regex_returns_400(self):
        response = self.get(q='([', regex='1')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Invalid regular expression', response.json()['error'])

    def test_severity_filter_includes_more_severe(self):
        messages = self.get(severity='5').json()['messages']
        self.assertEqual(sorted(m['severity'] for m in messages), [3, 5])

    def test_newest_first_and_limit(self):
        messages = self.get(limit='2').json()['messages']
        self.assertEqual(len(messages), 2)
        self.assertGreater(messages[0]['id'], messages[1]['id'])

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    def test_syslog_page_renders(self):
        self.assertContains(self.client.get(reverse('syslog')), 'id="log-body"')
