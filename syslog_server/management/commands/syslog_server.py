import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from inventory.models import Device
from syslog_server import parser
from syslog_server.models import SyslogMessage
from system_management.models import SystemSettings
from system_management.notifications import notify

FLUSH_INTERVAL = 1            # seconds between database writes
SETTINGS_REFRESH_INTERVAL = 5 # seconds between checking Settings (port, retention) and reporting status
DEVICE_REFRESH_INTERVAL = 60  # seconds between reloading the IP -> device map
CONFIG_NOTIFY_WINDOW = 15 * 60  # one config-change email per device per 15 minutes (edits often log many lines)
PURGE_INTERVAL = 3600         # seconds between deleting expired messages
MAX_PENDING = 50_000          # drop messages beyond this if the database can't keep up
MAX_TCP_BUFFER = 64 * 1024

OCTET_COUNT_RE = re.compile(rb'^(\d{1,5}) ')


class Command(BaseCommand):
    help = 'Runs the syslog server, listening for messages on UDP and TCP.'

    def add_arguments(self, parser):
        parser.add_argument('--host', default='0.0.0.0', help='Address to listen on (default: all interfaces)')
        parser.add_argument('--port', type=int, help='UDP/TCP port. Default: the port in Settings (514), '
                                                     'which is then followed live when it changes')
        parser.add_argument('--retention-days', type=int, help='Delete messages older than this (0 = keep forever). '
                                                               'Default: the value in Settings')
        parser.add_argument('--no-udp', action='store_true', help="Don't listen on UDP")
        parser.add_argument('--no-tcp', action='store_true', help="Don't listen on TCP")

    def handle(self, *args, **options):
        if options['no_udp'] and options['no_tcp']:
            raise CommandError('Nothing to listen on: both --no-udp and --no-tcp were given.')

        system = SystemSettings.load()
        # Command-line values pin the setting; otherwise it follows the Settings page
        self.port_pinned = options['port'] is not None
        self.retention_pinned = options['retention_days'] is not None
        port = options['port'] if self.port_pinned else system.syslog_port
        self.retention_days = options['retention_days'] if self.retention_pinned else system.syslog_retention_days

        self.host = options['host']
        self.udp, self.tcp = not options['no_udp'], not options['no_tcp']
        self.port = None
        self.transport = self.server = None
        self.error = ''
        self.failed_port = None
        self.pending = []
        self.dropped = 0
        self.received = 0
        self.devices_by_ip = {}
        self.config_notified_at = {}
        self.devices_loaded_at = 0
        self.settings_loaded_at = 0
        self.wanted_port = port
        self.purged_at = 0
        # All database work happens on this one thread, so the listener never blocks on SQLite
        self.db = ThreadPoolExecutor(max_workers=1)

        try:
            asyncio.run(self.serve(port))
        except KeyboardInterrupt:
            pass
        finally:
            self.db.submit(self.save, self.take_pending()).result()
            self.db.submit(self.report_stopped).result()
            self.db.shutdown()
            self.stdout.write(f'Stopped. {self.received} messages received, {self.dropped} dropped.')

    async def serve(self, port):
        try:
            await self.bind(port)
        except OSError as exc:
            raise CommandError(
                f'Could not listen on {self.host}:{port} ({exc}). Another syslog server may already be using the port; '
                'on Linux, ports below 1024 need root or CAP_NET_BIND_SERVICE.'
            )

        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            batch = self.take_pending()
            await loop.run_in_executor(self.db, self.save, batch)
            if self.wanted_port not in (self.port, self.failed_port):
                await self.rebind(self.wanted_port)

    async def bind(self, port):
        loop = asyncio.get_running_loop()
        transport = server = None
        try:
            if self.udp:
                transport, _ = await loop.create_datagram_endpoint(lambda: UdpProtocol(self), local_addr=(self.host, port))
            if self.tcp:
                server = await asyncio.start_server(self.handle_tcp, self.host, port)
        except OSError:
            if transport:
                transport.close()
            raise
        self.transport, self.server, self.port = transport, server, port
        protocols = ' + '.join(p for p, on in (('UDP', self.udp), ('TCP', self.tcp)) if on)
        self.stdout.write(self.style.SUCCESS(f'Syslog server listening on {self.host}:{port} ({protocols}). Ctrl+C to stop.'))

    def unbind(self):
        # Open TCP connections keep working until the sender closes them; only new ones go to the new port
        if self.transport:
            self.transport.close()
        if self.server:
            self.server.close()
        self.transport = self.server = None

    async def rebind(self, new_port):
        old_port = self.port
        self.unbind()
        try:
            await self.bind(new_port)
            self.error = ''
            self.failed_port = None
        except OSError as exc:
            self.error = f'Could not listen on port {new_port} ({exc}). Still listening on {old_port}.'
            self.failed_port = new_port  # Don't retry every second; wait for the setting to change again
            self.stderr.write(self.error)
            await self.bind(old_port)

    def ingest(self, data, addr):
        if not data.strip():
            return
        if len(self.pending) >= MAX_PENDING:
            self.dropped += 1
            return
        self.received += 1
        self.pending.append((timezone.now(), normalize_ip(addr[0]), data))

    def take_pending(self):
        batch, self.pending = self.pending, []
        return batch

    async def handle_tcp(self, reader, writer):
        addr = writer.get_extra_info('peername')
        buffer = b''
        try:
            while chunk := await reader.read(65536):
                buffer = self.split_tcp_frames(buffer + chunk, addr)
                if len(buffer) > MAX_TCP_BUFFER:
                    # No frame boundary in sight: keep what we have rather than grow forever
                    self.ingest(buffer, addr)
                    buffer = b''
            self.ingest(buffer, addr)
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    def split_tcp_frames(self, buffer, addr):
        """Ingests every complete frame in buffer and returns the incomplete remainder.

        Supports both RFC 6587 framings: octet counting ("LEN MSG") and newline/NUL delimited.
        """
        while buffer:
            match = OCTET_COUNT_RE.match(buffer)
            if match:
                end = match.end() + int(match.group(1))
                if len(buffer) < end:
                    break
                self.ingest(buffer[match.end():end], addr)
                buffer = buffer[end:]
                continue

            ends = [i for i in (buffer.find(b'\n'), buffer.find(b'\x00')) if i != -1]
            if not ends:
                break
            end = min(ends)
            self.ingest(buffer[:end], addr)
            buffer = buffer[end + 1:]
        return buffer

    # --- Database thread ---

    def save(self, batch):
        now = time.monotonic()
        if now - self.settings_loaded_at > SETTINGS_REFRESH_INTERVAL:
            self.refresh_settings()
            self.settings_loaded_at = now

        if now - self.devices_loaded_at > DEVICE_REFRESH_INTERVAL:
            self.devices_by_ip = dict(Device.objects.values_list('ip_address', 'id'))
            self.devices_loaded_at = now

        if batch:
            rows = []
            for received_at, source_ip, data in batch:
                facility, severity, hostname, message = parser.parse(data)
                rows.append(SyslogMessage(
                    received_at=received_at, source_ip=source_ip, device_id=self.devices_by_ip.get(source_ip),
                    facility=facility, severity=severity, hostname=hostname, message=message,
                    is_config_change=parser.is_config_change(message),
                ))
            SyslogMessage.objects.bulk_create(rows)
            self.notify_config_changes(rows)

        if self.retention_days and now - self.purged_at > PURGE_INTERVAL:
            cutoff = timezone.now() - timedelta(days=self.retention_days)
            SyslogMessage.objects.filter(received_at__lt=cutoff).delete()
            self.purged_at = now

    def notify_config_changes(self, rows):
        """Emails about config-change messages from known devices, at most once per device per window."""
        now = time.monotonic()
        for row in rows:
            if not (row.is_config_change and row.device_id):
                continue
            if now - self.config_notified_at.get(row.device_id, -CONFIG_NOTIFY_WINDOW) < CONFIG_NOTIFY_WINDOW:
                continue
            self.config_notified_at[row.device_id] = now
            device = Device.objects.select_related('location').filter(pk=row.device_id).first()
            if device is None:
                continue
            notify('config_changed', f'Configuration changed on {device.hostname}',
                   f'{device.hostname} ({device.ip_address}) reported a configuration change:\n\n'
                   f'    {row.message}\n\n'
                   f'Received: {row.received_at:%Y-%m-%d %H:%M:%S %Z}\n'
                   f'Location: {device.location}\n'
                   f'Further changes on this device in the next {CONFIG_NOTIFY_WINDOW // 60} minutes '
                   f"won't be emailed separately.\n",
                   path=f'/syslog/?q={device.ip_address}')

    def refresh_settings(self):
        """Picks up Settings page changes and reports this listener's status back to it."""
        system = SystemSettings.load()
        if not self.port_pinned:
            self.wanted_port = system.syslog_port
            if self.wanted_port != self.failed_port:
                self.failed_port = None  # A different port was chosen: allow trying again
        if not self.retention_pinned and system.syslog_retention_days != self.retention_days:
            self.retention_days = system.syslog_retention_days
            self.purged_at = 0  # Apply a shorter retention right away
        SystemSettings.objects.filter(pk=system.pk).update(
            listener_port=self.port, listener_error=self.error, listener_seen_at=timezone.now(),
        )

    def report_stopped(self):
        SystemSettings.objects.filter(pk=1).update(listener_port=None, listener_seen_at=None, listener_error='')


class UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, command):
        self.command = command

    def datagram_received(self, data, addr):
        self.command.ingest(data, addr)


def normalize_ip(ip):
    # IPv4 clients on a dual-stack socket show up as ::ffff:a.b.c.d
    return ip[7:] if ip.startswith('::ffff:') else ip
