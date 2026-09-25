import asyncio
import json
from unittest import mock

import asyncssh
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.test import TransactionTestCase
from django.urls import path, reverse

from .consumers import SSHConsumer
from .models import Device, Location, Vendor

app = URLRouter([path('ws/ssh/<int:device_id>/', SSHConsumer.as_asgi())])


class FakeProcess:
    def __init__(self, output):
        chunks = iter(output + [b''])

        async def read(n):
            await asyncio.sleep(0.05)
            return next(chunks, b'')

        self.stdout = mock.Mock(read=read)
        self.stdin = mock.Mock()
        self.change_terminal_size = mock.Mock()


class SSHConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('admin', password='x')
        self.device = Device.objects.create(
            hostname='core-sw1', ip_address='10.0.0.1', vendor=Vendor.objects.create(name='Cisco'),
            location=Location.objects.create(name='HQ'), username='netops', password='secret', creator=self.user,
        )

    def communicator(self, user=None, origin='http://testserver', query='cols=100&rows=30'):
        headers = [(b'host', b'testserver')]
        if origin:
            headers.append((b'origin', origin.encode()))
        comm = WebsocketCommunicator(app, f'/ws/ssh/{self.device.id}/?{query}', headers=headers)
        comm.scope['user'] = user or self.user
        return comm

    async def test_rejects_anonymous(self):
        connected, code = await self.communicator(user=AnonymousUser()).connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    async def test_rejects_cross_origin(self):
        connected, code = await self.communicator(origin='http://evil.example.com').connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    async def test_session_bridges_input_output_and_resize(self):
        process = FakeProcess([b'core-sw1#'])
        conn = mock.Mock()
        conn.create_process = mock.AsyncMock(return_value=process)

        with mock.patch('inventory.consumers.asyncssh.connect', mock.AsyncMock(return_value=conn)) as connect:
            comm = self.communicator()
            connected, _ = await comm.connect()
            self.assertTrue(connected)

            self.assertIn('Connecting to core-sw1', json.loads(await comm.receive_from())['message'])
            self.assertEqual(json.loads(await comm.receive_from()), {'type': 'status', 'message': 'connected'})
            self.assertEqual(connect.call_args.args[0], '10.0.0.1')
            self.assertEqual(connect.call_args.kwargs['username'], 'netops')
            self.assertEqual(connect.call_args.kwargs['password'], 'secret')
            self.assertEqual(conn.create_process.call_args.kwargs['term_size'], (100, 30))

            await comm.send_to(text_data=json.dumps({'type': 'input', 'data': 'show ver\r'}))
            await comm.send_to(text_data=json.dumps({'type': 'resize', 'cols': 9999, 'rows': 40}))
            self.assertEqual(await comm.receive_from(), b'core-sw1#')
            await asyncio.sleep(0.05)
            process.stdin.write.assert_called_with(b'show ver\r')
            process.change_terminal_size.assert_called_with(500, 40)  # clamped

            self.assertEqual(json.loads(await comm.receive_from())['type'], 'closed')
            await comm.disconnect()

    async def test_auth_failure_is_reported(self):
        with mock.patch('inventory.consumers.asyncssh.connect',
                        mock.AsyncMock(side_effect=asyncssh.PermissionDenied('denied'))):
            comm = self.communicator()
            await comm.connect()
            await comm.receive_from()  # "Connecting..."
            message = json.loads(await comm.receive_from())
            self.assertEqual(message['type'], 'error')
            self.assertIn('Authentication failed', message['message'])
            await comm.disconnect()

    def test_console_page_requires_login_and_renders(self):
        url = reverse('ssh_console', args=[self.device.id])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(url), '/ws/ssh/')
        self.assertEqual(self.client.get(reverse('ssh_console', args=[999])).status_code, 404)
