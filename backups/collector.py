"""Pulls running configurations from network devices over SSH and stores them as ConfigBackups."""
import asyncio
import logging
import re

import asyncssh

from inventory import platforms, ssh
from system_management.models import SystemSettings
from system_management.notifications import notify
from .models import ConfigBackup

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT = 45     # seconds for the config command to finish
SHELL_IDLE_TIMEOUT = 4   # interactive fallback: output is complete after this long without new data
MIN_CONFIG_LINES = 3

# platform -> (commands to disable paging in an interactive shell, commands that print the config, tried in order)
VENDOR_PROFILES = {
    platforms.JUNOS: (['set cli screen-length 0'], ['show configuration | no-more']),
    platforms.ARUBA: (['no page'], ['show running-config']),
    # "terse" puts every item on one line (clean diffs); RouterOS versions without it fall back to plain /export
    platforms.MIKROTIK: ([], ['/export terse', '/export']),
    platforms.FORTINET: ([], ['show']),
    platforms.NXOS: (['terminal length 0'], ['show running-config']),
    platforms.ARISTA: (['terminal length 0'], ['show running-config']),
}
DEFAULT_PROFILE = (['terminal length 0', 'terminal pager 0'], ['show running-config'])  # Cisco IOS/IOS-XE/ASA and most others

# Lines that change on every run without any real config change; stored configs leave them out
VOLATILE_LINES = re.compile(
    r'^(Building configuration'
    r'|Current configuration\s*:'
    r'|!\s*Time:'                             # NX-OS
    r'|!\s*Last configuration change at'      # IOS: changes when anyone enters config mode
    r'|!\s*NVRAM config last updated at'
    r'|!\s*No configuration change since last restart'
    r'|!\s*Running configuration last done at'
    r'|ntp clock-period'                      # IOS rewrites this as the clock drifts
    r'|#.* by RouterOS '                      # RouterOS export header, v7 "# 2026-09-29 10:00:00 by RouterOS 7.16"
                                              # and v6 "# sep/29/2026 10:00:00 by RouterOS 6.49.10"
    r')',
    re.I,
)
ERROR_MARKERS = re.compile(r'^\s*(% ?Invalid input|% ?Unknown command|% ?Authorization failed|Permission denied'
                           r'|syntax error|invalid command|Invalid input detected'
                           # MikroTik RouterOS
                           r'|bad command name|expected end of command|expected command name|failure:|no such item'
                           r'|input does not match any value|invalid value for argument|ambiguous value|missing value'
                           r'|not enough permissions)', re.I | re.M)
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]')


class BackupError(Exception):
    """A failed backup, with a message suitable for showing to the user."""


def profile_for(device):
    """(pager commands, config commands to try in order) for the device's platform."""
    return VENDOR_PROFILES.get(platforms.platform_for(device), DEFAULT_PROFILE)


def clean_config(text):
    """Normalizes device output: line endings, terminal escapes, volatile lines, trailing spaces."""
    text = ANSI_ESCAPE.sub('', text.replace('\r\n', '\n').replace('\r', '\n'))
    lines = [line.rstrip() for line in text.split('\n')]
    lines = [line for line in lines if not VOLATILE_LINES.match(line.strip())]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines) + '\n' if lines else ''


def validate(config, command):
    if ERROR_MARKERS.search(config[:2000]):
        first_error = next(l for l in config.splitlines() if ERROR_MARKERS.search(l))
        raise BackupError(f'The device rejected "{command}": {first_error.strip()}')
    if len([l for l in config.splitlines() if l.strip()]) < MIN_CONFIG_LINES:
        raise BackupError(f'"{command}" returned almost nothing; check that the account has privilege to read the configuration.')
    return config


async def run_exec(conn, command):
    """Runs one command on an SSH exec channel (no shell). Works on Cisco IOS/NX-OS, Arista, Juniper and others."""
    result = await asyncio.wait_for(conn.run(command, check=False), timeout=COMMAND_TIMEOUT)
    return result.stdout or ''


async def run_interactive(conn, pager_commands, command):
    """Types the commands into an interactive shell and returns what came back after the config command."""
    process = await conn.create_process(term_type='vt100', term_size=(512, 1000))
    output = []

    async def drain(idle):
        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=idle)
            except asyncio.TimeoutError:
                return
            if not chunk:
                return
            output.append(chunk)

    await drain(SHELL_IDLE_TIMEOUT)  # banner and prompt
    for pager_command in pager_commands:
        process.stdin.write(pager_command + '\n')
        await drain(2)
    output.clear()
    process.stdin.write(command + '\n')
    await asyncio.wait_for(drain(SHELL_IDLE_TIMEOUT), timeout=COMMAND_TIMEOUT)
    process.stdin.write('exit\n')
    process.close()

    return strip_shell_noise(''.join(output), command)


PROMPT_LINE = re.compile(r'^(\S*|\[[^\]]+\]\s*)[#>$%]\s*$')  # e.g. "core-sw1#", "user@router>", "[admin@mikrotik] >"


def strip_shell_noise(text, command):
    """Removes the echoed command (terminals may echo it more than once) and the prompt left after the output."""
    lines = ANSI_ESCAPE.sub('', text).replace('\r', '').split('\n')
    while lines and (not lines[0].strip() or command in lines[0]):
        lines.pop(0)
    while lines and (not lines[-1].strip() or PROMPT_LINE.match(lines[-1].strip())):
        lines.pop()
    return '\n'.join(lines)


async def fetch_config(device):
    pager_commands, commands = profile_for(device)
    conn = await ssh.connect(device, automation=True)
    try:
        last_error = None
        for command in commands:
            try:
                config = clean_config(await run_exec(conn, command))
                return validate(config, command)
            except (BackupError, asyncssh.Error) as exc:
                last_error = exc
                logger.info('Backup of %s: "%s" over exec failed (%s)', device.hostname, command, exc)
        # Some devices (e.g. HPE ProCurve) don't support exec channels; retry through a shell
        for command in commands:
            try:
                config = clean_config(await run_interactive(conn, pager_commands, command))
                return validate(config, command)
            except BackupError as exc:
                last_error = exc
        raise last_error if isinstance(last_error, BackupError) else BackupError(f'SSH error: {last_error}')
    finally:
        conn.close()


def backup_device(device):
    """Backs up one device now. Always records the attempt and returns the ConfigBackup (success or failed)."""
    try:
        config = asyncio.run(fetch_config(device))
    except (ssh.ConnectError, BackupError) as exc:
        error = str(exc)
    except asyncio.TimeoutError:
        error = f'Timed out after {COMMAND_TIMEOUT}s waiting for the configuration.'
    except (OSError, asyncssh.Error) as exc:
        error = f'SSH error: {exc}'
    else:
        backup = ConfigBackup.record_success(device, config)
        apply_retention(device)
        if backup.changed:
            notify('config_changed', f'Configuration changed on {device.hostname}',
                   f'A new backup of {device.hostname} ({device.ip_address}) differs from the previous one.\n\n'
                   f'Taken: {backup.created:%Y-%m-%d %H:%M:%S %Z}\n'
                   f'Location: {device.location}\n',
                   path=f'/inventory/{device.id}/backups/')
        return backup

    logger.warning('Backup of %s (%s) failed: %s', device.hostname, device.ip_address, error)
    backup = ConfigBackup.record_failure(device, error)
    apply_retention(device)
    notify('backup_failed', f'Backup failed for {device.hostname}',
           f'NetOps Center could not back up {device.hostname} ({device.ip_address}).\n\n'
           f'Error: {error}\n'
           f'Location: {device.location}\n',
           path=f'/inventory/{device.id}/backups/')
    return backup


def apply_retention(device):
    """Keeps only the newest N backups per device (Settings -> Backups)."""
    keep = SystemSettings.load().backup_retention
    old_ids = list(device.backups.order_by('-created').values_list('id', flat=True)[keep:])
    if old_ids:
        ConfigBackup.objects.filter(id__in=old_ids).delete()
