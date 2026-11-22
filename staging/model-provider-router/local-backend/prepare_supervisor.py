"""Apply the supervisor to the pinned, deployed Heretic formatting gateway."""
import argparse
import hashlib
from pathlib import Path

PIN = "6c626b1bce22c3064e417ae68abede0aa58096433dc63edbf019122be939cafb"

METHODS = '''    def open_generation(self, body):
        request = urllib.request.Request(
            BACKEND+self.path, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        return urllib.request.urlopen(request, timeout=900)

    def generate_buffered(self, upstream_body, body, guarded, hosted, responses, schema):
        # Native chat rejects streaming logprobs alongside tools. Keep the native
        # buffered path for explicit logprobs so its response contract is preserved.
        if not responses and body.get('logprobs'):
            return self.generate_buffered_logprobs(upstream_body, body, guarded, hosted)
        upstream_body = dict(upstream_body, stream=True)
        if not responses:
            upstream_body['stream_options'] = dict(upstream_body.get('stream_options') or {},
                                                   include_usage=True)
        for attempt in range(2):
            events = []
            try:
                with self.open_generation(upstream_body) as upstream:
                    self.relay_attempt(upstream, body, guarded, hosted, responses, schema,
                                       buffered=events)
                return self.reply(200, buffered_result(events, responses))
            except ReasoningLoop:
                if attempt:
                    raise ReasoningLoop('Reasoning loop persisted after recovery')
                LOG.info('reasoning loop detected for buffered request; retrying')
                wait_until_idle(BACKEND)
                upstream_body = steer(upstream_body, responses)

    def generate_buffered_logprobs(self, upstream_body, body, guarded, hosted):
        for attempt in range(2):
            with self.open_generation(upstream_body) as upstream:
                raw = upstream.read(MAX_BODY+1)
            if len(raw) > MAX_BODY:
                raise RepairError('upstream response exceeds 8 MiB')
            data = json.loads(raw)
            looping = False
            for trace in scrub_response(data):
                detector = Detector()
                # Scan all windows, including a loop followed by an answer.
                if (any(detector.feed(trace[i:i+512]) for i in range(0, len(trace), 512))
                        or detector.repeated()):
                    looping = True
                    break
            if not looping:
                if guarded:
                    data, _ = transform(data, body, hosted)
                return self.reply(200, data)
            if attempt:
                raise ReasoningLoop('Reasoning loop persisted after recovery')
            LOG.info('reasoning loop detected for buffered request; retrying')
            wait_until_idle(BACKEND)
            upstream_body = steer(upstream_body, False)

    def trace_update(self, kind, message):
        self.wfile.write(trace_bytes(kind, message))
        self.wfile.flush()

    def relay_attempt(self, upstream, body, guarded, hosted, responses, schema, buffered=None):
        detector = Detector()
        summaries = LiveSummary() if buffered is None else None
        frame = []
        preliminary = []
        events = []
        prefix_open = True
        committed = False
        sent_prefix = 0
        total = 0
        done = False
        terminal = False
        for line in upstream:
            frame.append(line)
            if line.strip():
                continue
            raw = b''.join(frame)
            frame.clear()
            event = parse_sse(raw)
            if event is None:
                if any(part.strip() == b'data: [DONE]' for part in raw.splitlines()):
                    done = True
                elif buffered is None and raw.startswith(b':'):
                    self.wfile.write(raw)
                    self.wfile.flush()
                continue
            traces, forward, substantive = inspect_event(event, responses)
            for delta in traces:
                if not committed and detector.feed(delta):
                    raise ReasoningLoop('Repeated reasoning detected')
                if not committed and summaries:
                    summaries.feed(delta)
            if not committed and summaries:
                update = summaries.poll()
                if update:
                    self.trace_update(*update)
            if not forward:
                continue
            if event.get('error') or event.get('type') == 'error':
                raise RuntimeError('Upstream reported a streaming failure')
            if substantive and not committed and detector.pending and detector.repeated():
                raise ReasoningLoop('Repeated reasoning detected')
            terminal |= event.get('type') in ('response.completed', 'response.incomplete',
                                              'response.failed')
            encoded = event_bytes(event, responses)
            total += len(encoded)
            if (buffered is not None or guarded or not committed) and total > MAX_BODY:
                raise RepairError('pending stream exceeds 8 MiB')
            if buffered is not None:
                # All output remains retractable until the complete JSON reply.
                buffered.append(event)
                continue
            if not committed and not substantive:
                preliminary.append((event, encoded))
                continue
            committed = True
            pending = preliminary
            preliminary = []
            pending.append((event, encoded))
            for item, wire in pending:
                if guarded:
                    events.append(item)
                    prefix_open = prefix_open and safe_stream_prefix(item, responses, schema)
                    if not prefix_open:
                        continue
                    sent_prefix += 1
                self.wfile.write(wire)
                self.wfile.flush()
        if not (terminal if responses else done):
            raise RepairError('upstream stream ended before completion')
        if buffered is not None:
            if guarded:
                buffered[:] = transform_events(buffered, body, responses, hosted)
            return
        for item, wire in preliminary:
            if guarded:
                events.append(item)
            else:
                self.wfile.write(wire)
        if guarded:
            for item in transform_events(events, body, responses, hosted)[sent_prefix:]:
                self.wfile.write(event_bytes(item, responses))
        if done:
            self.wfile.write(b'data: [DONE]\\n\\n')
        self.wfile.flush()

'''

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
source = args.source.read_bytes()
if hashlib.sha256(source).hexdigest() != PIN:
    parser.error("gateway source does not match the verified input")
source = source.decode()
source = source.replace(
    "from reasoning_compat import normalize_reasoning\n",
    "from reasoning_compat import normalize_reasoning\n"
    "from reasoning_supervisor import (Detector, LiveSummary, ReasoningLoop, apply_budget,\n"
    "                                  buffered_result, inspect_event, scrub_response,\n"
    "                                  steer, trace_bytes,\n"
    "                                  wait_until_idle)\n",
)
source = source.replace("    def do_POST(self):\n", METHODS + "    def do_POST(self):\n")
source = source.replace("upstream_body = normalize_reasoning(upstream_body)",
                        "upstream_body = apply_budget(normalize_reasoning(upstream_body))")
start = source.index("            req = urllib.request.Request(BACKEND+self.path")
end = source.index("        except (BrokenPipeError, ConnectionResetError): pass", start)
source = source[:start] + '''            responses = self.path.endswith('/responses')
            if not body.get('stream'):
                return self.generate_buffered(upstream_body, body, guarded, hosted, responses, schema)
            for attempt in range(2):
                try:
                    with self.open_generation(upstream_body) as upstream:
                        if not streaming:
                            self.send_response(200)
                            self.send_header('Content-Type', 'text/event-stream')
                            self.send_header('Cache-Control', 'no-cache')
                            self.send_header('Connection', 'close')
                            self.end_headers()
                            self.close_connection = streaming = True
                            self.trace_update('status', 'Waiting for local generation')
                        self.relay_attempt(upstream, body, guarded, hosted, responses, schema)
                    break
                except ReasoningLoop:
                    if attempt:
                        raise ReasoningLoop('Reasoning loop persisted after recovery')
                    LOG.info('reasoning loop detected for streaming request; retrying')
                    self.trace_update('recovery', 'Reasoning loop detected. Retrying with a different approach.')
                    wait_until_idle(BACKEND)
                    upstream_body = steer(upstream_body, responses)
''' + source[end:]
source = source.replace(
    "reason = str(e) if isinstance(e, RepairError)",
    "reason = str(e) if isinstance(e, (RepairError, ReasoningLoop))",
).replace(
    "'type': 'repair_or_upstream_error', 'message': reason",
    "'type': 'reasoning_loop' if isinstance(e, ReasoningLoop) else 'repair_or_upstream_error', 'message': reason",
)
args.output.write_text(source)
