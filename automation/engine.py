"""Pushes commands to network devices over SSH and records the outcome per device."""
import asyncio
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import asyncssh
from django.db import close_old_connections, connection
from django.utils import timezone

from backups.collector import ANSI_ESCAPE, ERROR_MARKERS, backup_device
from backups.models import ConfigBackup
from inventory import platforms, ssh
from .models import MODE_CONFIG, PushJob, PushResult

logger = logging.getLogger(__name__)

MAX_PARALLEL = 5            # devices pushed at the same time (1 when "stop on first error" is on)
COMMAND_TIMEOUT = 60        # seconds one command may keep producing output
IDLE_TIMEOUT = 5            # no output and no prompt for this long: the device is waiting, send the next line
DEVICE_TIMEOUT = 10 * 60    # whole session with one device
MAX_OUTPUT = 1_000_000      # characters of transcript kept per device

# Prompt at the end of the output, e.g. "core-sw1#", "core-sw1(config-if)#", "user@mx1>", "[admin@mt] >"
PROMPT_END = re.compile(r'(^|\n)[^\n]{0,80}[#>$%\]]\s?$')
# Push-specific errors on top of the ones backups recognise (e.g. Junos commit failures)
PUSH_ERRORS = re.compile(ERROR_MARKERS.pattern + r'|^\s*error:|^\s*% ?(Incomplete|Ambiguous) command', re.I | re.M)

VARIABLE_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')
VARIABLES = {
    'hostname': lambda d: d.hostname,
    'ip_address': lambda d: d.ip_address,
    'location': lambda d: str(d.location),
    'vendor': lambda d: str(d.vendor),
    'username': lambda d: d.username,
}


class ScriptError(Exception):
    """A problem with the commands themselves, found before anything is sent."""


class PushError(Exception):
    """A device session failed; the message is shown to the user."""


# --- Commands --------------------------------------------------------------------------------

def render(commands, device):
    """Fills {{ variables }} in for one device. Unknown variables are an error, never sent as-is."""
    def value(match):
        name = match.group(1)
        if name not in VARIABLES:
            raise ScriptError(f'Unknown variable {{{{ {name} }}}}. Available: '
                              + ', '.join(f'{{{{ {v} }}}}' for v in VARIABLES))
        return VARIABLES[name](device)
    return VARIABLE_RE.sub(value, commands)


def command_lines(text):
    """The lines to send: blank lines and '#' script comments are left out."""
    return [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]


@dataclass
class Profile:
    enter: str = ''                        # enters configuration mode
    exit: str = ''                         # leaves it (and applies, on Junos)
    abort: list = field(default_factory=list)  # leaves it without applying, after an error
    save: str = ''                         # saves to startup config
    pager: list = field(default_factory=list)


IOS = Profile('configure terminal', 'end', ['end'], 'write memory', ['terminal length 0'])
PROFILES = {
    platforms.JUNOS: Profile('configure', 'commit and-quit', ['rollback 0', 'exit configuration-mode'], '',
                             ['set cli screen-length 0']),
    platforms.NXOS: Profile('configure terminal', 'end', ['end'], 'copy running-config startup-config',
                            ['terminal length 0']),
    platforms.ARUBA: Profile('configure terminal', 'end', ['end'], 'write memory', ['no page']),
    # RouterOS: no configuration mode, every command takes effect and is saved immediately; the login
    # options (see inventory.platforms) already give an unpaged, uncoloured terminal
    platforms.MIKROTIK: Profile(),
    platforms.FORTINET: Profile(),  # "config ... end" blocks go in the script itself
}


def profile_for(device):
    return PROFILES.get(platforms.platform_for(device), IOS)  # Cisco IOS/IOS-XE, Arista and most others


# --- Device session --------------------------------------------------------------------------

class Shell:
    """An interactive CLI session: send a line, collect output until the prompt returns."""

    def __init__(self, process):
        self.process = process

    async def read(self, timeout=COMMAND_TIMEOUT):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        chunks = []
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise PushError(f'The device kept sending output for more than {timeout}s.')
            try:
                chunk = await asyncio.wait_for(self.process.stdout.read(65536), timeout=min(IDLE_TIMEOUT, remaining))
            except asyncio.TimeoutError:
                break  # Quiet: waiting at a prompt or a question
            if not chunk:
                break  # Session closed
            chunks.append(chunk)
            if PROMPT_END.search(''.join(chunks)):
                # A prompt-like ending; make sure it really is the end and not a line that happens to end in '#'
                try:
                    more = await asyncio.wait_for(self.process.stdout.read(65536), timeout=0.3)
                except asyncio.TimeoutError:
                    break
                if not more:
                    break
                chunks.append(more)
        return ANSI_ESCAPE.sub('', ''.join(chunks)).replace('\r\n', '\n').replace('\r', '\n')

    async def send(self, line):
        self.process.stdin.write(line + '\n')
        return await self.read()


def rejection(output, line):
    """The device's error message if it rejected the line, else ''. The echoed command itself is ignored."""
    body = output.split('\n', 1)[1] if line and output.lstrip().startswith(line[:20]) and '\n' in output else output
    match = PUSH_ERRORS.search(body)
    return body[match.start():].strip().split('\n')[0] if match else ''


async def push_to_device(device, lines, mode, save):
    """Runs the commands on one device. Returns (transcript, error, saved); error is '' on success."""
    profile = profile_for(device)
    conn = await ssh.connect(device, automation=True)
    transcript = []
    error = ''
    saved = False
    try:
        process = await conn.create_process(term_type='vt100', term_size=(512, 1000))
        shell = Shell(process)
        transcript.append(await shell.read(timeout=15))  # Banner and first prompt
        for pager in profile.pager:
            transcript.append(await shell.send(pager))

        in_config = False
        if mode == MODE_CONFIG and profile.enter:
            output = await shell.send(profile.enter)
            transcript.append(output)
            if rejection(output, profile.enter):
                raise PushError(f'Could not enter configuration mode: {rejection(output, profile.enter)}')
            in_config = True

        for line in lines:
            output = await shell.send(line)
            transcript.append(output)
            problem = rejection(output, line)
            if problem:
                error = f'"{line}" was rejected: {problem}'
                break  # Nothing more is sent to this device

        if in_config:
            if error:
                for abort in profile.abort:  # Leave config mode; on Junos this discards the uncommitted changes
                    transcript.append(await shell.send(abort))
            else:
                output = await shell.send(profile.exit)
                transcript.append(output)
                if rejection(output, profile.exit):
                    error = f'"{profile.exit}" failed: {rejection(output, profile.exit)}'
                    for abort in profile.abort:
                        transcript.append(await shell.send(abort))

        if mode == MODE_CONFIG and save and not error and profile.save:
            output = await shell.send(profile.save)
            transcript.append(output)
            problem = rejection(output, profile.save)
            if problem:
                error = f'Saving the configuration failed: {problem}'
            else:
                saved = True

        process.stdin.write('exit\n')
        process.close()
    except (OSError, asyncssh.Error) as exc:
        error = error or f'SSH session failed: {exc}'
    finally:
        conn.close()
    return ''.join(transcript)[-MAX_OUTPUT:], error, saved


# --- Jobs ------------------------------------------------------------------------------------

_active_jobs = set()  # Jobs running in this process; anything else marked "running" was cut off by a restart


def start(job):
    """Runs a job in the background; the job page polls for progress."""
    _active_jobs.add(job.id)
    threading.Thread(target=run_job, args=(job.id,), name=f'push-job-{job.id}', daemon=True).start()


def reconcile(job):
    """Marks a job interrupted if it says it's running but no thread in this process is running it."""
    if job.is_finished or job.id in _active_jobs:
        return job
    job.results.filter(status__in=[PushResult.PENDING, PushResult.RUNNING]).update(
        status=PushResult.FAILED, finished_at=timezone.now(),
        error='Interrupted: the web server restarted during the push. Check the device before retrying.')
    job.status, job.finished_at = PushJob.FAILED, timezone.now()
    job.save(update_fields=['status', 'finished_at', 'updated_at'])
    return job


def run_job(job_id):
    _active_jobs.add(job_id)
    try:
        job = PushJob.objects.get(pk=job_id)
        job.status, job.started_at = PushJob.RUNNING, timezone.now()
        job.save(update_fields=['status', 'started_at', 'updated_at'])
        logger.info('Push #%s started by %s on %s device(s)', job.id, job.created_by, job.results.count())

        stop = threading.Event()
        workers = 1 if job.stop_on_error else MAX_PARALLEL
        result_ids = list(job.results.values_list('id', flat=True))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f'push-{job.id}') as pool:
            list(pool.map(lambda rid: run_device(job, rid, stop), result_ids))

        statuses = set(job.results.values_list('status', flat=True))
        job.status = (PushJob.STOPPED if stop.is_set() and PushResult.SKIPPED in statuses
                      else PushJob.FAILED if statuses & {PushResult.FAILED, PushResult.SKIPPED}
                      else PushJob.SUCCEEDED)
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'finished_at', 'updated_at'])
        logger.info('Push #%s finished: %s', job.id, job.status)
    except Exception:
        logger.exception('Push #%s crashed', job_id)
        PushJob.objects.filter(pk=job_id).update(status=PushJob.FAILED, finished_at=timezone.now())
    finally:
        _active_jobs.discard(job_id)
        connection.close()


def run_device(job, result_id, stop):
    close_old_connections()
    result = PushResult.objects.select_related('device__vendor', 'device__location').get(pk=result_id)
    try:
        if stop.is_set():
            finish(result, PushResult.SKIPPED, error='Not run: the push stopped after an earlier device failed.')
            return
        device = result.device
        if device is None:
            finish(result, PushResult.SKIPPED, error='The device was deleted before the push reached it.')
            return

        result.status, result.started_at = PushResult.RUNNING, timezone.now()
        result.save(update_fields=['status', 'started_at'])

        if job.mode == MODE_CONFIG:
            before = backup_device(device)
            result.backup_before = before
            if before.status != ConfigBackup.SUCCESS:
                finish(result, PushResult.FAILED,
                       error=f'Nothing was pushed: the pre-change backup failed ({before.error})')
                return

        try:
            transcript, error, saved = asyncio.run(asyncio.wait_for(
                push_to_device(device, command_lines(result.commands), job.mode, job.save_config), DEVICE_TIMEOUT))
        except ssh.ConnectError as exc:
            transcript, error, saved = '', str(exc), False
        except PushError as exc:
            transcript, error, saved = '', str(exc), False
        except asyncio.TimeoutError:
            transcript, error, saved = '', f'Gave up after {DEVICE_TIMEOUT // 60} minutes.', False
        result.output, result.saved = transcript, saved

        if job.mode == MODE_CONFIG:
            # Even after an error: some lines may have been applied, and the diff shows exactly what changed
            after = backup_device(device)
            result.backup_after = after
            if after.status == ConfigBackup.SUCCESS:
                result.config_changed = after.config_hash != result.backup_before.config_hash

        finish(result, PushResult.FAILED if error else PushResult.SUCCEEDED, error=error)
    except Exception as exc:
        logger.exception('Push #%s: %s crashed', job.id, result.hostname)
        finish(result, PushResult.FAILED, error=f'Unexpected error: {exc}')
    finally:
        if result.status == PushResult.FAILED and job.stop_on_error:
            stop.set()
        connection.close()


def finish(result, status, error=''):
    result.status, result.error, result.finished_at = status, error, timezone.now()
    result.save()
