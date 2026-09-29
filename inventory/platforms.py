"""Which CLI a device speaks, worked out from its vendor name. Backups, pushes and SSH logins use this."""

IOS = 'ios'
NXOS = 'nxos'
ARISTA = 'arista'
JUNOS = 'junos'
ARUBA = 'aruba'
MIKROTIK = 'mikrotik'
FORTINET = 'fortinet'

# (platform, vendor-name keywords, label); the first match wins, anything else is treated as Cisco IOS-like
PLATFORMS = [
    (NXOS, ('nexus', 'nx-os', 'nxos'), 'Cisco NX-OS'),
    (JUNOS, ('juniper', 'junos'), 'Juniper Junos'),
    (ARISTA, ('arista',), 'Arista EOS'),
    (ARUBA, ('aruba', 'procurve', 'hpe', 'hewlett'), 'Aruba / HPE'),
    (MIKROTIK, ('mikrotik', 'routeros'), 'MikroTik RouterOS'),
    (FORTINET, ('fortinet', 'fortigate'), 'Fortinet FortiOS'),
]
DEFAULT_LABEL = 'Cisco IOS / IOS-XE (and other Cisco-like CLIs)'

# RouterOS login options added to the username for automated sessions: no colors (c), "dumb" terminal (e),
# no terminal auto-detection (t), and a terminal wide/tall enough that output never wraps or pages
MIKROTIK_LOGIN_OPTIONS = '+cet511w4098h'


def platform_for(device):
    vendor = str(device.vendor).lower() if device.vendor_id else ''
    for platform, keywords, _label in PLATFORMS:
        if any(keyword in vendor for keyword in keywords):
            return platform
    return IOS


def label_for(platform):
    return next((label for p, _k, label in PLATFORMS if p == platform), DEFAULT_LABEL)


def recognized_vendors_help():
    """For the vendor form: which names select which CLI behaviour."""
    names = '; '.join(f"{label} ({', '.join(keywords)})" for _p, keywords, label in PLATFORMS)
    return f'The name picks the CLI commands used for backups and pushes. Recognized: {names}. Anything else: {DEFAULT_LABEL}.'
