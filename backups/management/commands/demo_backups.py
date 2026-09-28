from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from backups.models import ConfigBackup
from inventory.models import Device

DEMO_MARKER = '! NMS demo backup'
DEMO_ERROR_PREFIX = '[demo] '

BASE_CONFIG = """{marker}
!
version 15.2
service timestamps debug datetime msec
service timestamps log datetime msec
service password-encryption
!
hostname {hostname}
!
logging buffered 16384
enable secret 5 $1$demo$Xj2kEXAMPLEHASH
!
username netops privilege 15 secret 5 $1$demo$EXAMPLEHASH2
!
ip domain-name corp.example.com
ip ssh version 2
!
spanning-tree mode rapid-pvst
spanning-tree extend system-id
!
vlan 10
 name USERS
!
vlan 20
 name VOICE
!
vlan 99
 name MGMT
!
interface GigabitEthernet1/0/1
 description Uplink to core
 switchport mode trunk
 switchport trunk allowed vlan 10,20,99
!
interface GigabitEthernet1/0/2
 description Desk 2-14
 switchport access vlan 10
 switchport voice vlan 20
 switchport mode access
 spanning-tree portfast
!
interface GigabitEthernet1/0/3
 description Printer 2nd floor
 switchport access vlan 10
 switchport mode access
 spanning-tree portfast
!
interface Vlan99
 ip address {ip} 255.255.255.0
!
ip default-gateway 10.0.99.1
!
logging host 10.0.99.10
snmp-server community demo-ro RO
!
line vty 0 4
 transport input ssh
 login local
!
end
"""

# Each step edits the previous config, like a real change window
CHANGES = [
    lambda c: c,  # first backup
    lambda c: c,  # nightly backup, no change
    lambda c: c.replace(' description Desk 2-14\n', ' description Desk 2-14 (Ana)\n'),
    lambda c: c.replace('vlan 99\n name MGMT\n!\n', 'vlan 30\n name GUEST\n!\nvlan 99\n name MGMT\n!\n')
               .replace('allowed vlan 10,20,99', 'allowed vlan 10,20,30,99'),
    'fail',
    lambda c: c.replace(' switchport access vlan 10\n switchport mode access\n spanning-tree portfast\n!\ninterface Vlan99',
                        ' switchport access vlan 30\n switchport mode access\n spanning-tree portfast\n spanning-tree bpduguard enable\n!\ninterface Vlan99')
               .replace('snmp-server community demo-ro RO\n', 'snmp-server community demo-ro RO\nntp server 10.0.99.5\n'),
    lambda c: c,
]


class Command(BaseCommand):
    help = 'Creates sample configuration backups for network devices, to try the backup history and compare pages.'

    def add_arguments(self, parser):
        parser.add_argument('hostnames', nargs='*', help='Network devices to create demo backups for (default: all)')
        parser.add_argument('--clear', action='store_true', help='Remove all demo backups instead')

    def handle(self, *args, **options):
        if options['clear']:
            deleted = (ConfigBackup.objects.filter(config__startswith=DEMO_MARKER).delete()[0]
                       + ConfigBackup.objects.filter(error__startswith=DEMO_ERROR_PREFIX).delete()[0])
            self.stdout.write(self.style.SUCCESS(f'Removed {deleted} demo backups.'))
            return

        devices = Device.objects.all()
        if options['hostnames']:
            devices = devices.filter(hostname__in=options['hostnames'])
        if not devices.exists():
            raise CommandError('No matching network devices in the inventory.')

        now = timezone.now()
        for device in devices:
            config = BASE_CONFIG.format(marker=DEMO_MARKER, hostname=device.hostname, ip=device.ip_address)
            for i, change in enumerate(CHANGES):
                # One backup per day, oldest first, ending today
                taken = now - timedelta(days=len(CHANGES) - 1 - i, hours=1)
                if change == 'fail':
                    backup = ConfigBackup.record_failure(device, f'{DEMO_ERROR_PREFIX}SSH timeout: no response from {device.ip_address}:22 after 15s')
                else:
                    config = change(config)
                    backup = ConfigBackup.record_success(device, config)
                ConfigBackup.objects.filter(pk=backup.pk).update(created=taken)
            self.stdout.write(f'  {device.hostname}: {len(CHANGES)} demo backups')

        self.stdout.write(self.style.SUCCESS('Done. Remove them any time with: python manage.py demo_backups --clear'))
