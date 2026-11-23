"""Run the built plugin through a local CPA v8 host and HTTPS mock upstream."""

import argparse
import base64
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_MODEL = "gpt-4.1-mini"
MODEL = "openai-responses/" + UPSTREAM_MODEL
TOKEN = "gAAAA-openai-api-test"
STREAM_TOKEN = "gAAAA-openai-stream-test"
COMPACT_TOKEN = "gAAAA-openai-compact-test"
KEY = "mock-upstream-key"


def run(binary, library, root, canonical=False):
    certificate, private_key = root / "cert.pem", root / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
         "-keyout", str(private_key), "-out", str(certificate)],
        check=True, capture_output=True,
    )
    calls = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/v1/models" or self.headers.get("Authorization") != f"Bearer {KEY}":
                self.reply(403, {"error": "unexpected catalog request"})
                return
            self.reply(200, {"data": [{"id": UPSTREAM_MODEL}, {"id": "text-embedding-3-large"}]})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.loads(raw)
            calls.append((self.path, self.headers, raw, body))
            if body.get("input") == "rate limit":
                self.reply(429, {"error": {"code": "mock_quota", "message": "mock limit"}})
                return
            if body.get("stream"):
                events = [] if body["input"] == "truncate" else [
                    {"type": "response.output_item.done", "item": {
                        "type": "reasoning", "encrypted_content": STREAM_TOKEN, "summary": []}},
                    {"type": "response.completed", "response": {
                        "id": "resp_stream", "output": [], "usage": {
                            "input_tokens": 1800, "input_tokens_details": {"cached_tokens": 1500}}}},
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in events:
                    data = json.dumps(event)
                    for part in (f"event: {event['type']}\r\ndata: ", data, "\r\n\r\n"):
                        self.wfile.write(part.encode())
                        self.wfile.flush()
                return
            compact = self.path.endswith("/compact")
            self.reply(200, {"object": "response", "id": "resp_native", "output": [{
                "type": "compaction" if compact else "reasoning",
                "encrypted_content": COMPACT_TOKEN if compact else TOKEN,
                "summary": [],
            }], "usage": {"input_tokens": 1800, "input_tokens_details": {"cached_tokens": 1500}}})

        def reply(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    upstream.socket = context.wrap_socket(upstream.socket, server_side=True)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    plugins = root / "plugins"
    plugins.mkdir()
    (root / "auth").mkdir()
    shutil.copyfile(library, plugins / "openai-responses.so")
    config = root / "config.yaml"
    canonical_config = "  canonical-only: true" if canonical else ""
    config.write_text(f"""
config-version: 8
models:
{canonical_config}
  catalog: ""
  codex-catalog: ""
  devin-catalog: ""
server:
  host: "127.0.0.1"
  port: {port}
management:
  disable-control-panel: true
  secret-key: mock-management-key
access:
  api-keys: [mock-downstream-a, mock-downstream-b]
oauth:
  auth-dir: "{root / 'auth'}"
routing:
  retry:
    request-retry: 0
    max-retry-credentials: 1
  streaming:
    bootstrap-retries: 0
plugins:
  enabled: true
  dir: "{plugins}"
  configs:
    openai-responses:
      enabled: true
""")
    env = dict(os.environ, SSL_CERT_FILE=str(certificate))
    env.pop("OPENAI_RESPONSES_API_KEY", None)
    env.pop("OPENAI_RESPONSES_STORAGE_KEY", None)
    env = {k: v for k, v in env.items() if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}

    session_headers = {"X-Gateway-Session": "responses-smoke"} if canonical else {}

    def request(method, path, body=None, key="mock-downstream-a", **headers):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        data = body if isinstance(body, str) else json.dumps(body) if body is not None else None
        try:
            conn.request(method, path, data, {"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json", "User-Agent": "responses-smoke",
                         **(session_headers | headers)})
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def start(log, expect_model=True):
        host = subprocess.Popen([binary, "--config", config, "--local-model"],
                                cwd=root, env=env, stdout=log, stderr=log)
        for _ in range(100):
            if host.poll() is not None:
                break
            try:
                status, body = request("GET", "/v1/models")
                if status == 200 and (MODEL in body) == expect_model:
                    return host
            except OSError:
                pass
            time.sleep(0.05)
        stop(host)
        raise AssertionError("Responses model was not published")

    def stop(host):
        host.terminate()
        try:
            host.wait(timeout=5)
        except subprocess.TimeoutExpired:
            host.kill()
            host.wait()

    def set_billing(policy, key="mock-downstream-a"):
        status, body = request("PUT", "/v1/routing/session", {"policy": policy}, key=key)
        assert status == 200 and json.loads(body)["policy"] == policy, (status, body)

    def post(input_value, **body):
        return request("POST", "/v1/responses", {"model": MODEL, "input": input_value, **body})

    def replay(token):
        return [{"type": "reasoning", "encrypted_content": token, "summary": []}]

    log_path = root / "host.log"
    try:
        with log_path.open("w") as log:
            host = start(log, expect_model=False)
            try:
                management_path = "/v8/management/config/plugins/configs/openai-responses"
                assert request("GET", management_path)[0] == 401
                status, body = request("GET", "/v8/management/plugins", key="mock-management-key")
                assert status == 200 and '"name":"api_key"' not in body, (status, body)
                status, body = request("GET", management_path, key="mock-management-key")
                assert status == 200, (status, body)
                updated = json.loads(body)
                updated.update(base_url=f"https://127.0.0.1:{upstream.server_port}/v1",
                               state_file=str(root / "opaque.db"))
                assert request("PUT", management_path, updated)[0] == 401
                status, body = request("PUT", management_path, updated, key="mock-management-key")
                assert status == 200, (status, body)
                credential_path = "/v0/management/plugins/openai-responses/credential"
                assert request("GET", credential_path)[0] == 401
                status, page = request("GET", credential_path, key="mock-management-key")
                assert status == 200 and 'type="password"' in page and KEY not in page, (status, page)
                public = re.search(r"atob\('([A-Za-z0-9+/=]+)'\)", page)
                assert public, page
                form_public_key = root / "credential-form-public.der"
                form_public_key.write_bytes(base64.b64decode(public[1]))
                encrypted = subprocess.run(
                    ["openssl", "pkeyutl", "-pubin", "-keyform", "DER", "-inkey", str(form_public_key),
                     "-encrypt", "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"],
                    input=KEY.encode(), check=True, capture_output=True,
                ).stdout
                submission = json.dumps(base64.b64encode(encrypted).decode())
                assert request("PUT", credential_path, submission)[0] == 401
                assert request("PUT", credential_path, submission, key="mock-management-key",
                               **{"Sec-Fetch-Site": "cross-site"})[0] == 404
                assert request("PUT", credential_path, submission, key="mock-management-key")[0] == 204
                assert KEY not in config.read_text()
                assert KEY.encode() not in (root / "opaque.db").read_bytes()
                assert (root / "opaque.db.key").stat().st_mode & 0o777 == 0o600
                status, page = request("GET", credential_path, key="mock-management-key")
                assert status == 200 and "A project key is configured." in page and KEY not in page
            finally:
                stop(host)
            host = start(log)
            try:
                if canonical:
                    assert post("hi")[0] == 403
                    set_billing("FREE")
                    assert post("hi", stream=True)[0] == 403
                    assert request("POST", "/v1/responses/compact", {"model": MODEL, "input": "hi"})[0] == 403
                    set_billing("PAID")
                    set_billing("PAID", key="mock-downstream-b")
                    assert post("hi", model=MODEL + ":free")[0] == 403
                    assert post("hi", model="openai-responses/unknown")[0] == 503
                    assert request("POST", "/v1/chat/completions", {"model": MODEL, "messages": [
                        {"role": "user", "content": "hi"}
                    ]})[0] == 400
                    assert not calls
                original = ('{ "model": "' + MODEL + '", "input": "hi", '
                            '"previous_response_id": "resp_previous", "store": false, '
                            '"prompt_cache_key": "stable", "prompt_cache_retention": "24h", '
                            '"prompt_cache_options": {"ttl": "12h"}, '
                            '"tools": [ { "type": "web_search" } ], '
                            '"include": ["reasoning.encrypted_content"] }')
                status, result = request("POST", "/v1/responses", original,
                                         **{"X-Unrelated": "private", "Session_id": "private"})
                assert status == 200, (status, result)
                assert json.loads(result)["usage"]["input_tokens_details"]["cached_tokens"] == 1500
                path, headers, raw, _ = calls[-1]
                assert path == "/v1/responses"
                assert raw == original.replace(MODEL, UPSTREAM_MODEL, 1).encode()
                assert headers["Authorization"] == f"Bearer {KEY}"
                assert "private" not in str(headers)
                count = len(calls)
                assert post(replay("gAAAA-codex"))[0] == 400
                assert post([{"type": "thinking", "signature": "codex-signature"}])[0] == 400
                assert request("POST", "/v1/responses", {"model": MODEL, "input": "hi"},
                               **{"X-Codex-Turn-State": "foreign"})[0] == 400
                assert request("POST", "/v1/responses", {"model": MODEL, "input": replay(TOKEN)},
                               key="mock-downstream-b")[0] == 400
                assert len(calls) == count
                assert post(replay(TOKEN))[0] == 200
                assert "prompt_cache_key" not in calls[-1][3]
                status, body = post("hi", stream=True)
                assert status == 200 and "response.completed" in body and STREAM_TOKEN in body, (status, body)
                assert "[DONE]" not in body and '"cached_tokens": 1500' in body, body
                assert post(replay(STREAM_TOKEN))[0] == 200
                status, body = post("truncate", stream=True)
                assert status != 200 or "error" in body, (status, body)
                for stream in (False, True):
                    before = len(calls)
                    status, body = post("rate limit", stream=stream)
                    assert status == 429 and "mock_quota" in body, (status, body)
                    assert len(calls) == before + 1
                status, body = request("POST", "/v1/responses/compact", {"model": MODEL, "input": "hi"})
                assert status == 200 and calls[-1][0] == "/v1/responses/compact", (status, body)
                assert COMPACT_TOKEN in body
            finally:
                stop(host)
            host = start(log)
            try:
                if canonical:
                    set_billing("PAID")
                assert post(replay(TOKEN))[0] == 200
                assert post(replay(STREAM_TOKEN))[0] == 200
                assert post([{"type": "compaction", "encrypted_content": COMPACT_TOKEN}])[0] == 200
            finally:
                stop(host)
    except Exception:
        print(log_path.read_text())
        raise
    finally:
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--canonical", action="store_true", help="exercise the gateway canonical billing policy")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="responses-plugin-smoke-") as temp:
        run(args.binary.resolve(), args.plugin.resolve(), Path(temp), args.canonical)
    print("CPA Responses plugin mock-host checks passed")
