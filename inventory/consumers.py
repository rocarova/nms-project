"""WebSocket <-> SSH bridge for the in-browser switch console.

Protocol:
  client -> server (text JSON): {"type": "input", "data": "..."} | {"type": "resize", "cols": N, "rows": N}
  server -> client: binary frames with raw terminal output,
                    text JSON {"type": "status" | "error" | "closed", "message": "..."}
"""
import asyncio
import json
import logging
from urllib.parse import parse_qs, urlparse

import asyncssh
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Device

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15
DEFAULT_SIZE = (120, 32)

# Older switches (e.g. Cisco IOS 12.x/15.x) only offer legacy algorithms; '+' appends them after the secure defaults
LEGACY_ALGORITHMS = {
    'kex_algs': '+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1,diffie-hellman-group-exchange-sha1',
    'server_host_key_algs': '+ssh-rsa',
    'encryption_algs': '+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc',
    'mac_algs': '+hmac-sha1',
}


def clamp_size(cols, rows):
    try:
        return max(20, min(int(cols), 500)), max(5, min(int(rows), 200))
    except (TypeError, ValueError):
        return DEFAULT_SIZE


@database_sync_to_async
def get_device(device_id):
    return Device.objects.filter(pk=device_id).first()


class SSHConsumer(AsyncWebsocketConsumer):
    conn = None
    process = None
    pump_task = None

    async def connect(self):
        user = self.scope['user']
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not self.same_origin():
            # Stops other websites from opening a console with the viewer's session
            await self.close(code=4403)
            return
        await self.accept()

        device = await get_device(self.scope['url_route']['kwargs']['device_id'])
        if device is None:
            await self.send_json('error', 'Device not found.')
            await self.close()
            return

        query = parse_qs(self.scope['query_string'].decode())
        cols, rows = clamp_size(query.get('cols', [None])[0], query.get('rows', [None])[0])

        await self.send_json('status', f'Connecting to {device.hostname} ({device.ip_address}) as {device.username}...')
        logger.info('SSH console: %s opened a session to %s (%s)', user.username, device.hostname, device.ip_address)

        try:
            self.conn = await asyncio.wait_for(asyncssh.connect(
                device.ip_address,
                username=device.username,
                password=device.password,
                known_hosts=None,  # Switch host keys aren't tracked yet; see README note on host key checking
                client_keys=None,  # Only use the stored password, never the server's own SSH keys
                agent_path=None,
                preferred_auth='keyboard-interactive,password',
                **LEGACY_ALGORITHMS,
            ), timeout=CONNECT_TIMEOUT)
            self.process = await self.conn.create_process(
                term_type='xterm-256color', term_size=(cols, rows), encoding=None,
            )
        except asyncio.TimeoutError:
            await self.fail(f'Timed out after {CONNECT_TIMEOUT}s connecting to {device.ip_address}:22.')
            return
        except asyncssh.PermissionDenied:
            await self.fail('Authentication failed: check the username and password saved for this device.')
            return
        except (OSError, asyncssh.Error) as exc:
            await self.fail(f'Could not connect: {exc}')
            return

        await self.send_json('status', 'connected')
        self.pump_task = asyncio.create_task(self.pump_output())

    async def pump_output(self):
        try:
            while data := await self.process.stdout.read(65536):
                await self.send(bytes_data=data)
            await self.send_json('closed', 'Session closed by the switch.')
        except (asyncssh.Error, ConnectionError) as exc:
            await self.send_json('closed', f'Connection lost: {exc}')
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('SSH console: unexpected error while relaying output')
            await self.send_json('closed', 'Connection lost: unexpected server error.')
        finally:
            await self.close()

    async def receive(self, text_data=None, bytes_data=None):
        if self.process is None or not text_data:
            return
        try:
            message = json.loads(text_data)
        except ValueError:
            return
        if message.get('type') == 'input' and isinstance(message.get('data'), str):
            self.process.stdin.write(message['data'].encode())
        elif message.get('type') == 'resize':
            self.process.change_terminal_size(*clamp_size(message.get('cols'), message.get('rows')))

    async def disconnect(self, code):
        if self.pump_task:
            self.pump_task.cancel()
        if self.conn:
            self.conn.close()

    async def fail(self, message):
        await self.send_json('error', message)
        await self.close()

    async def send_json(self, kind, message):
        await self.send(text_data=json.dumps({'type': kind, 'message': message}))

    def same_origin(self):
        headers = dict(self.scope['headers'])
        origin = headers.get(b'origin', b'').decode()
        host = headers.get(b'host', b'').decode()
        return bool(origin) and urlparse(origin).netloc == host
