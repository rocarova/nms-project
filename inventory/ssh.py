"""SSH connections to network devices, shared by the console and configuration backups."""
import asyncio

import asyncssh

CONNECT_TIMEOUT = 15

# Older devices (e.g. Cisco IOS 12.x/15.x) only offer legacy algorithms; '+' appends them after the secure defaults
LEGACY_ALGORITHMS = {
    'kex_algs': '+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1,diffie-hellman-group-exchange-sha1',
    'server_host_key_algs': '+ssh-rsa',
    'encryption_algs': '+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc',
    'mac_algs': '+hmac-sha1',
}


class ConnectError(Exception):
    """A connection problem, with a message that can be shown to the user as-is."""


async def connect(device):
    """Opens an SSH connection to a device with its stored credentials. Raises ConnectError."""
    try:
        return await asyncio.wait_for(asyncssh.connect(
            device.ip_address,
            username=device.username,
            password=device.password,
            known_hosts=None,  # Host keys aren't tracked yet; see the README security notes
            client_keys=None,  # Only use the stored password, never the server's own SSH keys
            agent_path=None,
            preferred_auth='keyboard-interactive,password',
            **LEGACY_ALGORITHMS,
        ), timeout=CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        raise ConnectError(f'Timed out after {CONNECT_TIMEOUT}s connecting to {device.ip_address}:22.')
    except asyncssh.PermissionDenied:
        raise ConnectError('Authentication failed: check the username and password saved for this device.')
    except (OSError, asyncssh.Error) as exc:
        raise ConnectError(f'Could not connect to {device.ip_address}:22: {exc}')
