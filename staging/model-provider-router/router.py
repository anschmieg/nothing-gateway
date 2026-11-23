"""Model-ID routing for the existing gateway. Never falls back for local IDs."""
import asyncio
from contextlib import AsyncExitStack
from datetime import timezone
from email.parser import BytesParser
from email.policy import default
from email.utils import parsedate_to_datetime
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import zlib
import time
import uuid
from urllib.request import build_opener, ProxyHandler
from urllib.parse import urlsplit

from aiohttp import web, ClientSession, ClientTimeout, ClientError, WSServerHandshakeError
from wake_diagnostics import Audit, drain, worker_reason, network_snapshot
from admission_errors import public_error_metadata

def audit(app, event, job_id=None, **fields):
    logger = app.get('audit')
    if logger is not None:
        logger.emit(event, job_id or app.get('wake_id'), **fields)

ALIASES = {
    'local/qwen3.8-heretic': ('heretic', 'llm'),
    'local/minimax-h3-fast': ('minimax-h3-fast', 'video'),
    'local/minimax-h3-high': ('minimax-h3-quality', 'video'),
    'local/minimax-h3-image': ('minimax-h3-image', 'image'),
}
TEXT_ENDPOINTS = ('/v1/chat/completions', '/v1/responses', '/v1/messages')
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length'}

# Leave time for the controller to return an HTTP admission error before SSE
# commits the response. Include authentication and wake in the edge budget.
FIRST_STREAM_PING = 85
STREAM_PING_INTERVAL = 15
STREAM_PEER_CHECK_INTERVAL = 0.25
STREAM_ERROR_BODY_TIMEOUT = 10
STREAM_PARTIAL_FRAME_TIMEOUT = 45
SSE_INSPECTION_LIMIT = 1024 * 1024
MESSAGES_PING = b'event: ping\ndata: {"type":"ping"}\n\n'
OPENAI_PING = b': keep-alive\n\n'
UNAVAILABLE_ERROR_BODY = 'Upstream error body was unavailable.'
UTF8_BOM = b'\xef\xbb\xbf'
SSE_LINE_END = re.compile(rb'[\r\n]')
SSE_TERMINAL_EVENTS = {
    '/v1/messages': ('message_stop', 'error'),
    '/v1/responses': ('response.completed', 'response.failed', 'response.incomplete', 'error'),
    '/v1/chat/completions': ('error',),
}


class SSEFraming:
    """Inspect a bounded copy of each event while forwarding bytes immediately."""

    def __init__(self, path):
        self.path = path
        self.at_boundary = True
        self.received_data_event = False
        self.received_terminal_event = False
        self.next_sequence_number = 0
        self.bom_prefix = b''
        self.line = bytearray()
        self.event_name = ''
        self.event_data = bytearray()
        self.event_data_over_limit = False
        self.after_cr = False
        self.skip_optional_lf = False

    def feed(self, chunk):
        if self.bom_prefix is not None:
            chunk = self.bom_prefix + chunk
            if len(chunk) < len(UTF8_BOM) and UTF8_BOM.startswith(chunk):
                self.bom_prefix = chunk
                return b''
            self.bom_prefix = None
            chunk = chunk.removeprefix(UTF8_BOM)
        if self.skip_optional_lf and chunk:
            chunk = chunk.removeprefix(b'\n')
            self.skip_optional_lf = False

        start = 0
        for match in SSE_LINE_END.finditer(chunk):
            end = match.start()
            if start < end:
                self.add_line_bytes(chunk, start, end)
            start = match.end()
            ending = match.group()
            if ending == b'\n' and self.after_cr:
                self.after_cr = False
                continue
            self.after_cr = ending == b'\r'
            if self.line:
                self.read_field()
                self.line.clear()
            else:
                self.at_boundary = True
                self.finish_event()
        if start < len(chunk):
            self.add_line_bytes(chunk, start, len(chunk))
        return chunk

    def add_line_bytes(self, chunk, start, end):
        self.at_boundary = False
        self.after_cr = False
        # Keep enough after the field prefix to identify an oversized data line.
        limit = SSE_INSPECTION_LIMIT + len(b'data: ')
        self.line.extend(chunk[start:min(end, start + limit - len(self.line))])

    def read_field(self):
        field, _, value = self.line.partition(b':')
        value = value.removeprefix(b' ')
        if field == b'event':
            self.event_name = value.decode('utf-8', errors='replace') if len(value) < 64 else ''
        elif field == b'data' and not self.event_data_over_limit:
            if len(self.event_data) + len(value) + 1 > SSE_INSPECTION_LIMIT:
                self.event_data.clear()
                self.event_data_over_limit = True
            else:
                self.event_data.extend(value)
                self.event_data.append(10)  # SSE joins consecutive data lines with LF.

    def finish_event(self):
        if self.event_data or self.event_data_over_limit:
            self.received_data_event = True
            self.received_terminal_event |= self.is_terminal_event()
        self.event_name = ''
        self.event_data.clear()
        self.event_data_over_limit = False

    def is_terminal_event(self):
        terminal_names = SSE_TERMINAL_EVENTS[self.path]
        if self.event_data_over_limit:
            return self.event_name in terminal_names
        chat = self.path == '/v1/chat/completions'
        if chat and self.event_data.strip() == b'[DONE]':
            return True
        try:
            value = json.loads(self.event_data)
        except (ValueError, RecursionError):
            return False
        if not isinstance(value, dict):
            return False
        if self.path == '/v1/responses':
            sequence = value.get('sequence_number')
            if type(sequence) is int:
                self.next_sequence_number = max(self.next_sequence_number, sequence + 1)
        if chat and isinstance(value.get('error'), dict):
            return True
        return self.event_name in terminal_names or value.get('type') in terminal_names

    def before_insertion(self):
        if not self.after_cr:
            return b''
        # CR already ends the line. Complete a possible split CRLF before
        # injecting, and consume the upstream LF if it arrives later.
        self.after_cr = False
        self.skip_optional_lf = True
        return b'\n'


def retry_after_seconds(value):
    if not value:
        return None
    if value.isascii() and value.isdecimal():
        return int(value) if len(value) <= 9 else None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, math.ceil(date.timestamp() - time.time()))
    except (ValueError, OverflowError, OSError):
        return None

def headers(source):
    return {k: v for k, v in source.items() if k.lower() not in HOP}

def cloud_url():
    if os.environ.get('ROUTER_CLOUD_URL'):
        return os.environ['ROUTER_CLOUD_URL'].rstrip('/')
    # Resolve the production Docker service, never a staging IP or this router.
    endpoint = ('http://127.0.0.1:8080/api/http/services/'
                'https-0-iog8gk8g8o0s44840g4o4sso@docker')
    with build_opener(ProxyHandler({})).open(endpoint, timeout=3) as response:
        service = json.load(response)
    if service.get('status') != 'enabled' or service.get('provider') != 'docker':
        raise ValueError('Production gateway service unavailable')
    servers = service.get('loadBalancer', {}).get('servers', [])
    if len(servers) != 1:
        raise ValueError('Expected one production gateway')
    url = servers[0]['url'].rstrip('/')
    parsed = urlsplit(url)
    address = ipaddress.ip_address(parsed.hostname)
    if (parsed.scheme != 'http' or parsed.port != 8080 or not address.is_private
            or address.is_loopback or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Invalid production gateway origin')
    return url

def error(message, status=503):
    return web.json_response({'error': {'message': message, 'type': 'local_provider_error'}}, status=status)

def retry_error(message, code='local_provider_starting'):
    return web.json_response({'error': {'message': message, 'type': 'service_unavailable',
                                       'code': code}}, status=503,
                             headers={'Retry-After': '15', 'Cache-Control': 'no-store'})

async def controller_available(app):
    # Health proves authenticated controller readiness; it does not load a model.
    try:
        token = Path(app['key_path']).read_text().strip()
        async with app['session'].get(app['local_url'] + '/health',
                headers={'Authorization': 'Bearer ' + token}, allow_redirects=False,
                timeout=ClientTimeout(total=3)) as response:
            if response.status in (401, 403):
                raise PermissionError('Controller rejected gateway credentials')
            if response.status != 200:
                return False
            value = await response.json()
            return isinstance(value, dict) and value.get('control') == 'ok' and isinstance(value.get('model_ready'), bool)
    except PermissionError:
        raise
    except (ClientError, TimeoutError, OSError, ValueError):
        return False

async def wake_pc(app):
    # Fixed local worker, no shell or client-controlled addresses/arguments/credentials.
    before = await network_snapshot()
    audit(app, 'worker_start', **before)
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec('/usr/bin/python3',
            '/opt/heretic-wake/wake-via-wireguard.py', '--timeout', '240',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError:
        audit(app, 'worker_exit', result='error', reason='worker_error',
              duration_ms=int((time.monotonic() - started) * 1000))
        raise
    readers = [asyncio.create_task(drain(stream)) for stream in (process.stdout, process.stderr)]
    result = 'error'
    try:
        await asyncio.wait_for(process.wait(), 270)
        result = 'failed' if process.returncode != 0 else 'ready'
        if process.returncode != 0:
            return False
        return await controller_available(app)
    except TimeoutError:
        result = 'timeout'
        raise
    except asyncio.CancelledError:
        result = 'cancelled'
        raise
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        output = await asyncio.gather(*readers)
        after = await network_snapshot()
        for key in ('rx_bytes', 'tx_bytes'):
            if key in before and key in after:
                after[key.replace('_bytes', '_delta')] = max(0, after[key] - before[key])
        audit(app, 'worker_exit', result=result, exit_code=process.returncode,
              reason=worker_reason(output[0][0], output[1][0], process.returncode),
              stdout_bytes=output[0][1], stderr_bytes=output[1][1],
              duration_ms=int((time.monotonic() - started) * 1000), **after)

async def wake_job(app):
    started = time.monotonic()
    result = 'failed'
    audit(app, 'wake_start')
    try:
        ready = await app['wake_impl'](app)
        result = 'ready' if ready else 'failed'
        return ready
    except asyncio.CancelledError:
        result = 'cancelled'
        raise
    except PermissionError:
        audit(app, 'controller_auth_failed')
        return False
    except (OSError, ClientError, TimeoutError):
        return False
    finally:
        audit(app, 'wake_end', result=result, duration_ms=int((time.monotonic() - started) * 1000))
        app['wake_cooldown_until'] = asyncio.get_running_loop().time() + 60

async def ensure_controller(app):
    try:
        if await controller_available(app):
            if app.get('wake_id') and app.get('recovered_id') != app['wake_id']:
                audit(app, 'controller_recovered')
                app['recovered_id'] = app['wake_id']
            return None
        task = app['wake_task']
        if task is None or task.done():
            if asyncio.get_running_loop().time() < app['wake_cooldown_until']:
                audit(app, 'wake_cooldown')
                return retry_error('Local PC is unavailable; wake retry is cooling down.',
                                   'local_provider_unavailable')
            app['wake_id'] = uuid.uuid4().hex
            task = app['wake_task'] = asyncio.create_task(wake_job(app))
        else:
            audit(app, 'wake_join')
        # Caller timeouts/disconnects must not cancel shared wake work.
        ready = await asyncio.wait_for(asyncio.shield(task), app['wake_wait_seconds'])
        if ready:
            return None
    except PermissionError:
        audit(app, 'controller_auth_failed')
        return retry_error('Local controller authentication failed.', 'local_provider_authentication')
    except TimeoutError:
        audit(app, 'wake_wait_timeout')
    return retry_error('Local PC is starting or unavailable; retry shortly.')

def local_models():
    return [{'id': name, 'object': 'model', 'owned_by': 'local', 'type': kind,
             **({'context_window': 94208, 'headless_context_window': 163840} if kind == 'llm' else {})}
            for name, (_, kind) in ALIASES.items()]

async def start(app):
    app['audit'] = Audit(Path(app['db_path']).parent / 'wake-events.jsonl')
    audit(app, 'router_start')
    app['session'] = ClientSession(auto_decompress=False,
        timeout=ClientTimeout(total=None, sock_connect=10, sock_read=900))
    db = sqlite3.connect(app['db_path'])
    db.execute('CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, kind TEXT NOT NULL)')
    db.commit()
    app['db'] = db
    yield
    if app['wake_task'] is not None and not app['wake_task'].done():
        app['wake_task'].cancel()
        await asyncio.gather(app['wake_task'], return_exceptions=True)
    await app['session'].close()
    db.close()
    app['audit'].close()

async def authenticated_catalog(request, canonical=False):
    # Delegate key validation to the live gateway; no credential copies or auth cache.
    auth_headers = headers(request.headers)
    if canonical:
        auth_headers = {k: v for k, v in auth_headers.items()
                        if k.lower() not in ('anthropic-version', 'user-agent')}
        auth_headers['User-Agent'] = 'model-provider-router'
    auth_headers['Accept-Encoding'] = 'identity'
    async with request.app['session'].get((await asyncio.to_thread(cloud_url)) + '/v1/models',
            headers=auth_headers, allow_redirects=False,
            timeout=ClientTimeout(total=30)) as response:
        raw = await response.read()
        if response.status != 200:
            return web.Response(status=response.status, body=raw, headers=headers(response.headers))
        request['native_cors'] = {k: v for k, v in response.headers.items()
                                  if k.lower().startswith('access-control-') or k.lower() == 'vary'}
        return json.loads(raw)

def remember(app, value, kind):
    ids = []
    if isinstance(value, dict):
        if isinstance(value.get('id'), str):
            ids.append(value['id'])
        for item in value.get('data', []):
            url = item.get('url', '') if isinstance(item, dict) else ''
            parts = url.split('/')
            if len(parts) >= 5 and parts[1:3] == ['v1', 'images']:
                ids.append(parts[3])
    for ident in ids:
        app['db'].execute('INSERT OR REPLACE INTO media VALUES (?,?)', (ident, kind))
    app['db'].commit()

async def wait_with_ping(awaitable, delay, ping):
    pending = asyncio.ensure_future(awaitable)
    try:
        while not (await asyncio.wait((pending,), timeout=delay))[0]:
            await ping()
            delay = STREAM_PING_INTERVAL
        return pending.result()
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def relay(request, url, body, *, first_ping_deadline=None, **options):
    forward = relay_upstream(request, url, body, first_ping_deadline=first_ping_deadline, **options)
    if first_ping_deadline is None:
        return await forward

    pending = asyncio.create_task(forward)
    try:
        # Scope disconnect cancellation to streaming requests. Buffered media
        # requests may need to save a returned ID even if the caller leaves.
        while True:
            transport = request.transport
            if transport is None or transport.is_closing():
                return web.Response(status=499)
            done, _ = await asyncio.wait((pending,), timeout=STREAM_PEER_CHECK_INTERVAL)
            if done:
                return pending.result()
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def relay_upstream(request, url, body, local=False, kind=None, decoded_body=False,
                         local_translation=False, first_ping_deadline=None):
    local_job_id = request.app.get('wake_id') if local else None
    outgoing = headers(request.headers)
    if decoded_body:
        outgoing = {k: v for k, v in outgoing.items() if k.lower() != 'content-encoding'}
    if local:
        outgoing.pop('X-Forwarded-Prefix', None)
        outgoing.pop('x-forwarded-prefix', None)
        outgoing['Authorization'] = 'Bearer ' + Path(request.app['key_path']).read_text().strip()
        outgoing = {k: v for k, v in outgoing.items() if k.lower() != 'content-encoding'}
    if local or local_translation or first_ping_deadline is not None:
        outgoing = {k:v for k,v in outgoing.items() if k.lower() != 'accept-encoding'}
        outgoing['Accept-Encoding'] = 'identity'
    downstream = None
    sent_ping = False
    framing = SSEFraming(request.path)
    messages = request.path == '/v1/messages'
    native_cors = request.get('native_cors', {})
    may_commit_early = ('Origin' not in request.headers or
                        any(key.lower() == 'access-control-allow-origin' for key in native_cors))

    async def read_error_body(response):
        if first_ping_deadline is None:
            return await response.read()
        try:
            return await asyncio.wait_for(response.read(), STREAM_ERROR_BODY_TIMEOUT)
        except (ClientError, TimeoutError):
            return None

    async def ping():
        nonlocal downstream, sent_ping
        if downstream is None:
            downstream = web.StreamResponse(headers={'Content-Type': 'text/event-stream',
                                                      'Cache-Control': 'no-cache, no-transform'})
            downstream.headers.update(native_cors)
            await downstream.prepare(request)
        await downstream.write(framing.before_insertion() + (MESSAGES_PING if messages else OPENAI_PING))
        sent_ping = True

    async def stream_error(detail=None, retry_after=None):
        if not isinstance(detail, dict):
            detail = {}
        detail = {key: detail[key] for key in ('message', 'type', 'code')
                  if isinstance(detail.get(key), str)}
        detail.setdefault('type', 'api_error')
        detail.setdefault('message', 'Upstream did not return an event stream.')
        delay = retry_after_seconds(retry_after)
        if delay is not None:
            detail['retry_after'] = delay
        event = 'event: error\n'
        if messages:
            payload = {'type': 'error', 'error': detail}
        elif request.path == '/v1/responses':
            payload = {'type': 'error', 'sequence_number': framing.next_sequence_number, 'param': None,
                       'code': detail.get('code'), 'message': detail['message']}
            if 'retry_after' in detail:
                payload['retry_after'] = detail['retry_after']
        else:
            event = ''
            payload = {'error': detail}
        await downstream.write(framing.before_insertion() + f'{event}data: {json.dumps(payload)}\n\n'.encode())
        await downstream.write_eof()
        return downstream

    try:
        async with AsyncExitStack() as upstream:
            opening = upstream.enter_async_context(request.app['session'].request(
                request.method, url, headers=outgoing, data=body, allow_redirects=False))
            if first_ping_deadline is None or not may_commit_early:
                response = await opening
            else:
                delay = max(0, first_ping_deadline - asyncio.get_running_loop().time())
                response = await wait_with_ping(opening, delay, ping)
            sse = (response.status == 200 and response.content_type == 'text/event-stream'
                   and response.headers.get('Content-Encoding', 'identity').lower() == 'identity')
            if sent_ping and response.status < 400 and not sse:
                return await stream_error(retry_after=response.headers.get('Retry-After'))
            if local_translation and response.status >= 400 and 'application/json' in response.headers.get('Content-Type', ''):
                raw = await read_error_body(response)
                try:
                    value = json.loads(raw) if raw is not None else None
                except ValueError:
                    value = None
                if not isinstance(value, dict):
                    detail = {'type': 'api_error', 'message': UNAVAILABLE_ERROR_BODY}
                    if sent_ping:
                        return await stream_error(detail, response.headers.get('Retry-After'))
                    reply_headers = headers(response.headers)
                    reply_headers = {k: v for k, v in reply_headers.items()
                                     if k.lower() not in ('content-type', 'content-encoding')}
                    return web.json_response({'error': detail}, status=response.status, headers=reply_headers)
                detail = value.get('error')
                metadata = public_error_metadata(response.status, detail.get('message')) if isinstance(detail, dict) else None
                reply_headers = headers(response.headers)
                if metadata is not None:
                    code, retry_after = metadata
                    detail['code'] = code
                    reply_headers['Cache-Control'] = 'no-store'
                    if retry_after is not None and 'Retry-After' not in response.headers:
                        reply_headers['Retry-After'] = str(retry_after)
                    reply_headers = {k:v for k,v in reply_headers.items() if k.lower() != 'content-type'}
                if sent_ping:
                    return await stream_error(detail, response.headers.get('Retry-After') or reply_headers.get('Retry-After'))
                if metadata is not None:
                    return web.json_response(value, status=response.status, headers=reply_headers)
                return web.Response(body=raw, status=response.status, headers=reply_headers)
            if local and 'application/json' in response.headers.get('Content-Type', ''):
                raw = await read_error_body(response) if response.status >= 400 else await response.read()
                try:
                    value = json.loads(raw) if raw is not None else None
                except ValueError:
                    if response.status < 400:
                        raise
                    value = None
                if response.status >= 400 and not isinstance(value, dict):
                    value = {'error': {'type': 'api_error', 'message': UNAVAILABLE_ERROR_BODY}}
                error_value = value.get('error') if isinstance(value, dict) else None
                code = error_value.get('code') if isinstance(error_value, dict) else None
                audit(request.app, 'local_response', job_id=local_job_id, status=response.status,
                      reason=code if code in ('model_not_ready', 'model_loading', 'resource_not_ready', 'concurrency_limit_exceeded', 'resource_busy', 'resource_conflict', 'initialization_failed', 'resource_unavailable') else
                      ('none' if response.status < 400 else 'upstream_error'))
                if sent_ping:
                    return await stream_error(error_value, response.headers.get('Retry-After'))
                if response.status < 300 and kind and request.method == 'POST':
                    remember(request.app, value, kind)
                reply_headers = {'Cache-Control': 'no-store'}
                if 'Retry-After' in response.headers:
                    reply_headers['Retry-After'] = response.headers['Retry-After']
                return web.json_response(value, status=response.status, headers=reply_headers)
            if sent_ping and not sse:
                retry_after = response.headers.get('Retry-After')
                if response.content_type == 'application/json' or response.content_type.endswith('+json'):
                    raw = await read_error_body(response)
                    try:
                        value = json.loads(raw) if raw is not None else None
                    except ValueError:
                        value = None
                    detail = value.get('error') if isinstance(value, dict) else None
                    if detail is None and response.content_type == 'application/problem+json' and isinstance(value, dict):
                        detail = {'message': value.get('detail') or value.get('title')}
                    return await stream_error(detail, retry_after)
                return await stream_error(retry_after=retry_after)
            if downstream is None:
                downstream = web.StreamResponse(status=response.status, headers=headers(response.headers))
                downstream.headers.update(native_cors)
                await downstream.prepare(request)
            if first_ping_deadline is not None and sse:
                delay = (STREAM_PING_INTERVAL if sent_ping else
                         max(0, first_ping_deadline - asyncio.get_running_loop().time()))
                while True:
                    if framing.at_boundary:
                        chunk = await wait_with_ping(response.content.readany(), delay, ping)
                    else:
                        chunk = await asyncio.wait_for(response.content.readany(), STREAM_PARTIAL_FRAME_TIMEOUT)
                    if not chunk:
                        break
                    forwarded = framing.feed(chunk)
                    if forwarded:
                        await downstream.write(forwarded)
                        delay = STREAM_PING_INTERVAL
                if not framing.at_boundary:
                    # An error frame could finish and dispatch incomplete data.
                    if request.transport:
                        request.transport.close()
                    return downstream
                if not framing.received_terminal_event:
                    message = ('Upstream closed before sending a terminal event.' if framing.received_data_event
                               else 'Upstream closed without sending events.')
                    return await stream_error({'code': 'incomplete_stream', 'message': message})
            else:
                async for chunk in response.content.iter_any():
                    await downstream.write(chunk)
            await downstream.write_eof()
            return downstream
    except (ClientError, TimeoutError, OSError, ValueError):
        if local:
            audit(request.app, 'local_transport_error', job_id=local_job_id)
        if downstream is not None and downstream.prepared:
            if sent_ping and not framing.received_data_event and framing.at_boundary:
                try:
                    return await stream_error({'message': 'Upstream failed before sending model data.'})
                except OSError:
                    pass
            request.transport.close() if request.transport else None
            return downstream
        return error('Local PC unavailable; no cloud fallback attempted.' if local else 'Gateway upstream unavailable.', 503 if local else 502)


async def websocket(request):
    if request.query.get('model', '').startswith('local/'):
        return error('Local models require HTTP streaming; no cloud fallback attempted.', 400)
    try:
        upstream = await request.app['session'].ws_connect((await asyncio.to_thread(cloud_url)) + request.rel_url.path_qs,
            headers=headers(request.headers), autoping=False, autoclose=False)
    except WSServerHandshakeError as exc:
        return error('Gateway WebSocket handshake rejected.', exc.status)
    except (ClientError, TimeoutError):
        return error('Gateway WebSocket unavailable.', 502)
    downstream = web.WebSocketResponse(autoping=False, autoclose=False)
    await downstream.prepare(request)
    async def pump(source, target, client=False):
        async for msg in source:
            if msg.type.name == 'TEXT':
                if client:
                    try:
                        data = json.loads(msg.data)
                        model = data.get('model') or data.get('response', {}).get('model')
                    except (ValueError, AttributeError):
                        model = None
                    if isinstance(model, str) and model.startswith('local/'):
                        await downstream.send_json({'type':'error', 'error':{'message':'Local models currently require HTTP streaming; no cloud fallback attempted.'}})
                        return
                await target.send_str(msg.data)
            elif msg.type.name == 'BINARY':
                await target.send_bytes(msg.data)
            elif msg.type.name == 'PING':
                await target.ping(msg.data)
            elif msg.type.name == 'PONG':
                await target.pong(msg.data)
            else:
                return
    tasks = [asyncio.create_task(pump(downstream, upstream, True)), asyncio.create_task(pump(upstream, downstream))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()
        await downstream.close()
    return downstream

async def handle(request):
    first_ping_deadline = asyncio.get_running_loop().time() + FIRST_STREAM_PING
    path = request.path
    if request.headers.get('Upgrade', '').lower() == 'websocket':
        return await websocket(request)
    body = await request.read()
    if request.method == 'GET' and path in ('/v1/models', '/v1/models/'):
        try:
            value = await authenticated_catalog(request)
        except (ClientError, TimeoutError, OSError, ValueError):
            return error('Gateway authentication service unavailable', 502)
        if isinstance(value, web.StreamResponse):
            return value
        value['data'] = [m for m in value.get('data', []) if not m.get('id', '').startswith('local/')] + local_models()
        return web.json_response(value, headers={'Cache-Control': 'no-store'})
    parsed = None
    model = None
    inspect_body = body
    encoding = request.headers.get('Content-Encoding', '').lower()
    if body and encoding:
        if encoding not in ('gzip', 'deflate'):
            return error('Unsupported request content encoding.', 415)
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == 'gzip' else zlib.MAX_WBITS)
            inspect_body = decoder.decompress(body, 128 * 1024**2 + 1)
            if len(inspect_body) > 128 * 1024**2 or not decoder.eof:
                return error('Decoded request exceeds the size limit.', 413)
        except zlib.error:
            return error('Invalid compressed request.', 400)
    if body and 'application/json' in request.content_type:
        try:
            parsed = json.loads(inspect_body)
            model = parsed.get('model') if isinstance(parsed, dict) else None
        except (ValueError, AttributeError):
            pass
    elif body and request.content_type.startswith(('multipart/', 'application/x-www-form-urlencoded')):
        if request.content_type.startswith('multipart/'):
            message = BytesParser(policy=default).parsebytes(
                ('Content-Type: ' + request.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + inspect_body)
            for part in message.iter_parts():
                if part.get_param('name', header='content-disposition') == 'model':
                    model = part.get_content()
        else:
            from urllib.parse import parse_qs
            model = parse_qs(inspect_body.decode()).get('model', [None])[0]
    explicit_local = isinstance(model, str) and model.startswith('local/')
    parts = path.strip('/').split('/')
    known_media = False
    kind = None
    keepalive_deadline = (first_ping_deadline if request.method == 'POST' and path in TEXT_ENDPOINTS
                          and isinstance(parsed, dict) and parsed.get('stream') is True else None)
    if len(parts) >= 3 and parts[:2] in (['v1', 'videos'], ['v1', 'images']):
        row = request.app['db'].execute('SELECT kind FROM media WHERE id=?', (parts[2],)).fetchone()
        kind = row[0] if row else None
        known_media = bool(row) and ((parts[1] == 'videos') == (kind == 'video'))
    if not explicit_local and not known_media:
        return await relay(request, (await asyncio.to_thread(cloud_url)) + request.rel_url.path_qs,
                           body, first_ping_deadline=keepalive_deadline)
    try:
        checked = await authenticated_catalog(request, canonical=True)
    except (ClientError, TimeoutError, OSError, ValueError):
        return error('Gateway authentication service unavailable', 502)
    if isinstance(checked, web.StreamResponse):
        return checked
    if explicit_local:
        if model not in ALIASES:
            return error('Unknown local model; no cloud fallback attempted.', 404)
        backend, kind = ALIASES[model]
        allowed = {'llm': TEXT_ENDPOINTS,
                   'video': ('/v1/videos',), 'image': ('/v1/images/generations',)}
        if request.method != 'POST' or path not in allowed[kind]:
            return error('Local model does not support this endpoint.', 400)
        if not isinstance(parsed, dict):
            return error('Local models require a JSON body.', 400)
        if kind == 'llm' and path == '/v1/messages':
            # The native OpenAI-compatible executor owns protocol translation.
            # Discovery hides temporarily unavailable routes. It is not a
            # registration check: pin the known provider override even when
            # absent from the catalog and let native dispatch report its error.
            native_model = 'local-pc/' + backend
            for entry in checked.get('data', []):
                if entry.get('owned_by') != 'local-pc':
                    continue
                if entry.get('id') == model:
                    native_model = model
                    break
                override = 'local-pc/' + backend
                if override in entry.get('overrides', []):
                    native_model = override
                    break
            unavailable = await ensure_controller(request.app)
            if unavailable is not None:
                return unavailable
            # Pin canonical deployments to the exact local provider override.
            # System/tools/cache fields and session headers reach translation
            # unchanged; only the routing model name is rewritten.
            if native_model != model:
                parsed['model'] = native_model
                body = json.dumps(parsed).encode()
            return await relay(request, (await asyncio.to_thread(cloud_url)) + request.rel_url.path_qs,
                               body, decoded_body=native_model != model, local_translation=True,
                               first_ping_deadline=keepalive_deadline)
        parsed['model'] = backend
        body = json.dumps(parsed).encode()
        unavailable = await ensure_controller(request.app)
        if unavailable is not None:
            return unavailable
    return await relay(request, request.app['local_url'] + request.rel_url.path_qs,
                       body, local=True, kind=kind, first_ping_deadline=keepalive_deadline)


@web.middleware
async def preserve_cors(request, handler):
    response = await handler(request)
    if not response.prepared:
        response.headers.update(request.get('native_cors', {}))
    return response

def make_app(db_path, key_path, local_url):
    app = web.Application(client_max_size=128 * 1024**2, middlewares=[preserve_cors], handler_args={'auto_decompress': False})
    app['db_path'], app['key_path'], app['local_url'] = str(db_path), str(key_path), local_url.rstrip('/')
    app['wake_task'] = None
    app['wake_id'] = None
    app['wake_impl'] = wake_pc
    app['wake_wait_seconds'] = 30
    app['wake_cooldown_until'] = 0
    app.cleanup_ctx.append(start)
    app.router.add_route('*', '/v1', handle)
    app.router.add_route('*', '/v1/{tail:.*}', handle)
    return app

if __name__ == '__main__':
    web.run_app(make_app('/var/lib/model-provider-router/media.sqlite',
        '/etc/model-provider-router/pc-api-key', 'http://100.83.43.41:11441'),
        host='100.74.55.86', port=11443, access_log=None)
