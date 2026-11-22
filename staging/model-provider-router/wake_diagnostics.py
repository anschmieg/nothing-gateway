"""Bounded local diagnostics. Never persist worker text, request bodies or headers."""
import asyncio
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import time

EVENTS = {'router_start', 'wake_start', 'worker_start', 'worker_exit', 'wake_end',
          'wake_join', 'wake_wait_timeout', 'wake_cooldown', 'controller_auth_failed',
          'controller_recovered', 'local_response', 'local_transport_error'}
LABELS = {'ready', 'not_ready', 'failed', 'timeout', 'cancelled', 'error', 'none',
          'route_unavailable', 'controller_timeout', 'worker_error', 'controller_ready',
          'model_not_ready', 'model_loading', 'local_provider_error', 'upstream_error'}
NUMBERS = {'duration_ms', 'exit_code', 'stdout_bytes', 'stderr_bytes', 'status',
           'rx_bytes', 'tx_bytes', 'rx_delta', 'tx_delta', 'handshake_age_seconds',
           'peer_count'}

class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)

class Audit:
    def __init__(self, path, max_bytes=262144, backups=8):
        self.handler = None
        self.fault_reported = False
        try:
            path = Path(path)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.handler = PrivateRotatingHandler(path, maxBytes=max_bytes,
                                               backupCount=backups, encoding='utf-8')
            os.chmod(path, 0o600)
            self.handler.setFormatter(logging.Formatter('%(message)s'))
        except OSError:
            self._fault()

    def _fault(self):
        if not self.fault_reported:
            try:
                print('{"event":"wake_diagnostics_unavailable"}', flush=True)
            except OSError:
                pass
            self.fault_reported = True

    def emit(self, event, job_id=None, **fields):
        if event not in EVENTS:
            return
        value = {'time': datetime.now(timezone.utc).isoformat(), 'event': event}
        if isinstance(job_id, str) and re.fullmatch('[a-f0-9]{32}', job_id):
            value['job_id'] = job_id
        for key, item in fields.items():
            if key in NUMBERS and isinstance(item, int) and not isinstance(item, bool):
                value[key] = item
            elif key in {'result', 'reason'} and isinstance(item, str) and item in LABELS:
                value[key] = item
            elif key == 'interface' and isinstance(item, str) and re.fullmatch(r'[\w.-]{1,32}', item):
                value[key] = item
        line = json.dumps(value, separators=(',', ':'))
        # Journald retains service-level ordering and catches file-storage failures.
        try:
            print(line, flush=True)
        except OSError:
            pass
        if self.handler is not None:
            try:
                self.handler.emit(logging.makeLogRecord({'msg': line, 'levelno': logging.INFO}))
            except OSError:
                self._fault()

    def close(self):
        if self.handler is not None:
            self.handler.close()

async def drain(stream, limit=4096):
    """Drain continuously to avoid pipe deadlocks; retain only a bounded tail in RAM."""
    tail = b''
    size = 0
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return tail, size
        size += len(chunk)
        tail = (tail + chunk)[-limit:]

def worker_reason(stdout, stderr, exit_code):
    # Only fixed known categories leave memory. Unknown/error output is not logged.
    if exit_code == 0:
        return 'controller_ready'
    if b'home WireGuard route is not active' in stderr:
        return 'route_unavailable'
    if b'PC did not become reachable' in stderr:
        return 'controller_timeout'
    return 'worker_error'

async def command(*args):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.DEVNULL)
    reader = asyncio.create_task(drain(process.stdout, 16384))
    try:
        await asyncio.wait_for(process.wait(), 1)
        raw, size = await reader
        if process.returncode != 0 or size > 16384:
            return None
        return raw.decode('utf-8')
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)

async def network_snapshot():
    """Best-effort fixed-target telemetry. No probes, keys, endpoints or payloads logged."""
    result = {}
    try:
        ip = shutil.which('ip')
        wg = shutil.which('wg')
        if ip:
            raw = await command(ip, '-j', 'route', 'get', '192.168.178.47')
            routes = json.loads(raw) if raw else []
            if routes:
                result['interface'] = routes[0].get('dev')
        if wg:
            raw = await command(wg, 'show', 'heretic-home', 'latest-handshakes')
            if raw:
                timestamps = [int(line.split()[1]) for line in raw.splitlines()]
                result['peer_count'] = len(timestamps)
                # Only a single peer can be attributed unambiguously to this route.
                if len(timestamps) == 1:
                    result['handshake_age_seconds'] = max(0, int(time.time()) - timestamps[0]) if timestamps[0] else -1
            raw = await command(wg, 'show', 'heretic-home', 'transfer')
            if raw and result.get('peer_count') == 1:
                rows = [line.split() for line in raw.splitlines()]
                if len(rows) == 1:
                    result.update(rx_bytes=int(rows[0][1]), tx_bytes=int(rows[0][2]))
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, TimeoutError):
        pass
    return result
