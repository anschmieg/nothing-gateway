# Local Messages translation

The model-provider router intercepts `local/*` before the native gateway. Local
text models now accept `/v1/messages` through the native OpenAI-compatible
executor. Existing chat, Responses, image and video paths retain their handlers.

`router.py` and `wake_diagnostics.py` were imported from `/opt/model-provider-router`
on the gateway host. The router input SHA-256 is recorded in `deploy.py`.

The native registration targets the existing local controller. Canonical routing
uses the exact `local-pc/heretic` override, with a credential-scoped no-charge
allowance for this self-hosted endpoint. The allowance expires after 365 days and
must be renewed if that endpoint remains local and unmetered. Authentication and wake failures stop dispatch. A provider-qualified override
keeps dispatch local even when the discovery catalog hides an unavailable route;
native dispatch reports registration or cooldown failures directly. The router forwards caller
session headers and all system/tool/cache fields to native translation.

Translation preserves a stable prompt prefix; it does not create cache hits.
Anthropic cache directives have no guaranteed equivalent on a chat-only backend.
The native executor reports upstream cached-token usage. Derived `prompt_cache_key`
injection is disabled until the local backend's support is established.

Validation runs in a network namespace with only loopback enabled:

```sh
NATIVE_GATEWAY_BINARY=/path/to/cli-proxy-api python -m unittest -v \
  test_router test_native_translation
```

The 24 router/native tests cover the router and the actual native binary with synthetic
credentials, disabled plugins, empty auth state and a loopback chat fixture.
Both ordinary and canonical registration modes are exercised. Assertions cover
system prompts, tool definitions and results, buffered/streamed tool calls,
stable prefixes, cached-token accounting, authentication, gzip, media and failures.

`deploy.py` is a one-time, source-pinned installer for the existing host. It backs
up configuration and router source, registers the local endpoint, restarts the
native process, checks publication, then installs the router. Failures restore
the backups. It makes no inference requests.

The follow-up regression removes the discovery-presence guard: a generation
failure can cool a registered route and remove it from `/v1/models`. That must
not become “Local translation route is not registered.” Tests cover successful
and failed native dispatch with the local model absent from discovery, and a
native configuration reload before streaming translation.

`local-backend/` contains the pinned compatibility patch for the PC formatting
gateway. This Qwen template accepts `low`, `medium`, and `xhigh`; standard `high`
is mapped to `xhigh` at the final local request boundary. Prompt, tool and cache
fields retain their values. All 29 backend tests pass in an isolated network
namespace, including four HTTP combinations (chat/Responses, buffered/streamed).
The patch is deployed in the existing `heretic-server` service. Only the local
credential's cooldown was reset after fixing the rejected effort value.

Live follow-up verification passed streamed high-effort tool selection and the
subsequent tool-result continuation. The continuation reported 320 cached input
tokens; see `hidden-route-fix-20261115.json` for sanitized evidence.

Cold-start follow-up: native credential cooling is disabled only for the
self-hosted `local-pc` provider, with per-request retries still zero. The local
controller retains resource admission and loading responses. A regression sends
`model_not_ready`/503 followed by a successful request through the actual native
binary without resetting state. The formatting gateway change and its helper are
both pinned in the PC profile registry; all unrelated source hashes and model
artifact stat checks remain verified. `local-backend/update_integrity.py` performs
that scoped update. A real request was verified after the model had unloaded,
covering profile selection, source verification, model loading, and generation.

## Heretic reasoning supervision

The PC formatting gateway applies a 16,384-token thinking ceiling by default.
Set `HERETIC_REASONING_BUDGET` in the gateway service to change the ceiling.
Smaller per-request budgets are honored. The setting targets llama.cpp's
thinking sampler and keeps the endpoint's existing maximum generation length
and caller output limits separate.

The gateway examines only reasoning deltas. It detects nearby repeated
self-acknowledgments and recurring passages. It closes a looping attempt's
native stream, checks `/slots` for release, and then retries once with an
explicit instruction to take a materially different approach. The PC admission
lease stays held through the retry. After the first answer or tool delta is
committed to a streaming client, the gateway will no longer replay an attempt.
Ordinary buffered calls use a supervised native stream and return a single
buffered result. Explicit buffered Chat Completions logprobs retain the native
buffered path and can recover only after the initial result completes.

Live summaries use the PC's loopback Ollama with CPU-only options and the
published [Qwen 3.5 0.8B reasoning summarizer](https://huggingface.co/mithulaartigala/reasoning-summarizer-qwen3.5-0.8b-preview),
installed under the local alias `heretic-trace-summary:0.8b` (Q4_K_M).
The gateway reads the `summary` field from the checkpoint's JSON output.
`HERETIC_SUMMARY_MODEL` can select another installed local model or disable
summaries with an empty value. The bounded, asynchronous summary worker
does not delay generation, and a status update covers summary failures.
It receives only a recent window of reasoning. It does not feed the detector
or modify the model's conversation history.

Streaming Chat Completions and Responses carry the side channel as SSE comments
of the form `: heretic-trace {"type":"summary","text":"..."}`. The available
types are `summary`, `status`, and `recovery`. A client needs to read these
comments from the raw SSE stream to render them. Standard model events still
deliver the answer or tool call. Translated Messages requests benefit from
supervision on their upstream chat stream, but their translation path does not
expose the SSE comment side channel as Anthropic events.

The source-pinned `local-backend/prepare_supervisor.py` prepares the patched
server from the deployed compatibility version. Run `test_reasoning_supervisor`
alongside the PC gateway's `test_gateway`, `test_server`, and
`test_reasoning_compat` in a loopback-only namespace. The
`install_supervisor.py` helper installs the prepared server and supervisor
on the PC, verifies the previous source hashes, and stores the previous
server and profile registry under `state/reasoning-supervisor-before-*`.
