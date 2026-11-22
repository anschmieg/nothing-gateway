import io
import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import reasoning_supervisor as supervisor
import server


ACK = (
    "Wait, I need to break out of this loop. "
    "OK, I'm clearly stuck in a loop. Let me try a completely different approach. "
    "Wait, I need to break out of this loop. Let me try a completely different approach. "
)


class SupervisorTests(unittest.TestCase):
    def test_published_model_uses_raw_input_and_reads_only_summary(self):
        model = supervisor.DEFAULT_SUMMARY_MODEL
        tags = {"models": [{"name": model, "size": 529289792}]}
        for payload, stop, expected in (
            ({"summary": "Checked the queue.", "title": "Other text"}, "stop", "Checked the queue."),
            ({"summary": "SKIP"}, "stop", ""),
            ({"summary": "Incomplete."}, "length", None),
            ({"title": "Missing summary"}, "stop", None),
        ):
            result = {"response": json.dumps(payload), "done_reason": stop}
            frames = [io.BytesIO(json.dumps(value).encode()) for value in (tags, result)]
            with self.subTest(payload=payload, stop=stop), patch.object(
                supervisor, "SUMMARY_MODEL", model
            ), patch.object(supervisor.LOCAL_HTTP, "open", side_effect=frames) as opener:
                if expected is None:
                    with self.assertRaises(ValueError):
                        supervisor.summarize("Trace")
                else:
                    self.assertEqual(supervisor.summarize("Trace"), expected)
                request = json.loads(opener.call_args.args[0].data)
                self.assertEqual(request["prompt"], "Trace\n")
                self.assertTrue(request["raw"])
                self.assertNotIn("system", request)
                self.assertEqual(request["options"]["num_gpu"], 0)

    def test_budget_is_high_and_keeps_lower_client_budget(self):
        constrained = supervisor.apply_budget({
            "reasoning_budget_tokens": -1,
            "thinking_budget_tokens": 9500,
            "max_tokens": 20000,
            "reasoning_budget_message": "user-controlled budget message",
        })
        self.assertEqual(constrained["reasoning_budget_tokens"], 9500)
        self.assertEqual(constrained["max_tokens"], 20000)
        self.assertNotIn("reasoning_budget_message", constrained)
        self.assertEqual(supervisor.apply_budget({
            "reasoning_budget_tokens": 1000000,
        })["reasoning_budget_tokens"], supervisor.BUDGET)

    def test_detects_acknowledgments_across_deltas(self):
        detector = supervisor.Detector()
        self.assertTrue(any(detector.feed(ACK[i:i+7]) for i in range(0, len(ACK), 7)))

    def test_detects_repeated_passage_without_loop_language(self):
        text = "First review the facts already available and derive the result by comparing the two candidate rows. "
        self.assertTrue(supervisor.Detector().feed(text * 4))

    def test_single_acknowledgment_followed_by_progress_is_safe(self):
        trace = ("Wait, I need to break out of this loop. "
                 "The second approach identifies a missing constraint. "
                 "Use the ordering relation to express this quantity directly. ")
        self.assertFalse(supervisor.Detector().feed(trace))

    def test_responses_steering_preserves_tool_results(self):
        body = {
            "instructions": "System prefix",
            "input": [{"type": "function_call_output", "call_id": "call_1", "output": "42"}],
        }
        retry = supervisor.steer(body, True)
        self.assertEqual(retry["input"], body["input"])
        self.assertEqual(retry["instructions"], "System prefix\n\n" + supervisor.STEERING)
        self.assertEqual(body["instructions"], "System prefix")

    def test_buffered_chat_reassembles_tool_arguments_and_usage(self):
        events = [
            {"id": "chat-1", "choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call-1", "function": {"name": "read", "arguments": "{\"x\":"}},
            ]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "2}"}},
            ]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"completion_tokens": 9}},
        ]
        result = supervisor.buffered_result(events, False)
        self.assertEqual(result["usage"], {"completion_tokens": 9})
        call = result["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "call-1")
        self.assertEqual(call["function"], {"name": "read", "arguments": '{"x":2}'})


class FixtureBackend(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.slot_checks += 1
        body = json.dumps([{"is_processing": self.server.processing.is_set()}]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        request_index = len(self.server.requests)
        responses = self.path.endswith("/responses")
        self.send_response(200)
        self.end_headers()
        self.server.processing.set()
        try:
            if responses:
                self.event({"type": "response.created", "response": {"id": str(request_index)}})
            if body.get("scenario") == "summary":
                # A progressing trace supplies a sufficiently large first summary window.
                self.trace(" ".join(f"step {i}: inspect coordinate {i*i}." for i in range(28)))
                self.server.summary_started.wait(2)
                time.sleep(0.03)
                self.trace("Proceed with the result. ")
            elif body.get("scenario") == "after-answer":
                self.event({"choices": [{"index": 0, "delta": {"content": "started"}}]})
                self.trace(ACK)
            elif request_index == 1 or body.get("scenario") == "persistent":
                for _ in range(150):
                    self.trace(ACK)
                    time.sleep(0.01)
            if responses:
                self.event({"type": "response.completed", "response": {
                    "id": str(request_index),
                    "output": [{"type": "reasoning", "content": [
                        {"type": "reasoning_text", "text": "private raw trace"},
                    ]}, {"type": "message", "content": [{"type": "output_text", "text": "answer"}]}],
                }})
            else:
                self.event({"id": str(request_index), "choices": [
                    {"index": 0, "delta": {"content": "answer"}, "finish_reason": "stop"},
                ]})
                self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            self.server.cancelled += 1
        finally:
            self.server.processing.clear()

    def event(self, event):
        self.wfile.write(server.event_bytes(event, self.path.endswith("/responses")))
        self.wfile.flush()

    def trace(self, text):
        if self.path.endswith("/responses"):
            self.event({"type": "response.reasoning_text.delta", "delta": text})
        else:
            self.event({"choices": [{"index": 0, "delta": {"reasoning_content": text}}]})


class GatewaySupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.backend = self.start_server(FixtureBackend)
        self.backend.requests = []
        self.backend.slot_checks = self.backend.cancelled = 0
        self.backend.processing = threading.Event()
        self.backend.summary_started = threading.Event()
        self.gateway = self.start_server(server.Handler)
        self.enterContext(patch.object(server, "BACKEND",
                                       f"http://127.0.0.1:{self.backend.server_port}"))
        self.enterContext(patch.object(server, "LOCK", pathlib.Path(self.tmp) / "worker.lock"))

    def start_server(self, handler):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    def call(self, endpoint, stream, scenario=None):
        body = {"stream": stream, "scenario": scenario}
        if endpoint == "responses":
            body["input"] = "Original question"
        else:
            body["messages"] = [{"role": "user", "content": "Original question"}]
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.gateway.server_port}/v1/{endpoint}",
            json.dumps(body).encode(), {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            return response.read().decode()

    def test_stream_cancels_before_steered_retry(self):
        result = self.call("chat/completions", True)
        self.assertEqual(result.count("Retrying with a different approach"), 1)
        self.assertEqual(result.count('"content": "answer"'), 1)
        self.assertNotIn("I'm clearly stuck", result)
        self.assertGreater(self.backend.slot_checks, 0)
        self.assertEqual(self.backend.cancelled, 1)
        original, retry = self.backend.requests
        self.assertEqual(retry["messages"][0]["content"], supervisor.STEERING)
        self.assertEqual(retry["messages"][1:], original["messages"])
        self.assertEqual(retry["reasoning_budget_tokens"], supervisor.BUDGET)

    def test_buffered_responses_cancel_and_rebuild_terminal(self):
        data = self.call("responses", False)
        response = json.loads(data)
        self.assertEqual(response["id"], "2")
        self.assertNotIn("private raw trace", data)
        self.assertEqual(self.backend.cancelled, 1)
        self.assertEqual(self.backend.requests[1]["instructions"], supervisor.STEERING)

    def test_second_loop_is_terminal(self):
        result = self.call("chat/completions", True, "persistent")
        self.assertEqual(len(self.backend.requests), 2)
        self.assertIn('"type": "reasoning_loop"', result)
        self.assertNotIn("[DONE]", result)

    def test_stream_never_retries_after_answer_output(self):
        result = self.call("chat/completions", True, "after-answer")
        self.assertEqual(len(self.backend.requests), 1)
        self.assertIn('"content": "started"', result)
        self.assertIn('"content": "answer"', result)
        self.assertNotIn("I'm clearly stuck", result)
        self.assertNotIn("Retrying", result)

    def test_live_summary_is_distinct_from_model_output(self):
        def summarize(_):
            self.backend.summary_started.set()
            return "Compared the candidate coordinates."

        with patch.object(supervisor, "summarize", side_effect=summarize):
            result = self.call("responses", True, "summary")
        self.assertIn(': heretic-trace {"type": "summary", "text": "Compared', result)
        self.assertNotIn("inspect coordinate", result)
        self.assertNotIn("private raw trace", result)
        self.assertLess(result.index('"type": "summary"'), result.index("response.completed"))
        self.assertEqual(len(self.backend.requests), 1)


if __name__ == "__main__":
    unittest.main()
