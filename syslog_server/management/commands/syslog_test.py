import socket

from django.core.management.base import BaseCommand

SAMPLES = [
    '<189>42: *Sep 24 12:00:01.123: %SYS-5-CONFIG_I: Configured from console by admin on vty0 (10.0.0.50)',
    '<187>43: *Sep 24 12:00:05.456: %LINK-3-UPDOWN: Interface GigabitEthernet1/0/12, changed state to down',
    '<189>44: *Sep 24 12:00:06.789: %LINEPROTO-5-UPDOWN: Line protocol on Interface GigabitEthernet1/0/12, changed state to down',
    '<190>Sep 24 12:00:10 access-sw3 sshd[811]: Accepted password for netops from 10.0.0.50 port 51234',
    '<164>1 2026-09-24T12:00:12.000Z edge-rtr1 mgd 4242 UI_COMMIT - User \'netops\' requested \'commit\' operation',
    '<186>45: *Sep 24 12:00:15.000: %SPANTREE-2-BLOCK_BPDUGUARD: Received BPDU on port Gi1/0/7 with BPDU Guard enabled',
]


class Command(BaseCommand):
    help = 'Sends sample syslog messages to a syslog server, to check that it is receiving.'

    def add_arguments(self, parser):
        parser.add_argument('--host', default='127.0.0.1')
        parser.add_argument('--port', type=int, default=514)
        parser.add_argument('--tcp', action='store_true', help='Send over TCP instead of UDP')

    def handle(self, *args, **options):
        host, port = options['host'], options['port']
        if options['tcp']:
            with socket.create_connection((host, port), timeout=5) as sock:
                for sample in SAMPLES:
                    sock.sendall(sample.encode() + b'\n')
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for sample in SAMPLES:
                    sock.sendto(sample.encode(), (host, port))

        protocol = 'TCP' if options['tcp'] else 'UDP'
        self.stdout.write(self.style.SUCCESS(f'Sent {len(SAMPLES)} messages to {host}:{port} over {protocol}.'))
