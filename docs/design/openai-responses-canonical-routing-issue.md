# Canonical-only routing drops static OpenAI Responses plugin routes

## Symptom

On `fra`, CPA loads and registers `openai-responses`, but `GET /v1/models`
omits its provider-qualified model IDs. An explicit request to
`POST /v1/responses` for `openai-responses/gpt-4.1-mini` returns HTTP 503 with
`no eligible route satisfies model and billing policy`, even in a session
with `PAID` enabled. The operator reported HTTP 200 for a direct OpenAI request
using the same project key and model.

## Reproduction

The operator reproduced the failure with the production binary and a local
HTTPS upstream. A separate local test rebuilt CPA from the gateway's checked-in
canonical and session-billing patches, installed the published v0.2.0 artifact,
and exercised the same path with a fixture model and HTTPS mock. Both local
test sessions explicitly selected `PAID`.

| `models.canonical-only` | `GET /v1/models` | Explicit Responses request | Mock upstream calls |
| --- | --- | --- | --- |
| `false` | Plugin model listed | HTTP 200 | 1 |
| `true` | Plugin model absent | HTTP 503 | 0 |

Wait for CPA to complete model registration before checking the 503. During
startup, an early request can return HTTP 404 because the model has yet to enter
the registry.

## Cause

CPA's static plugin executor registers model metadata under a synthetic
registry client ID, `plugin:openai-responses:openai-responses:executor`. This
publication path bypasses the gateway's per-auth canonicalization. The
canonical-only discovery filter therefore hides the static entries, and
`CanonicalRouteInfos` excludes them because they have no canonical ID.
Adding metadata by itself would still leave the billing planner unable to
resolve the synthetic client ID to an auth record. With canonical-only
enabled, billing planning runs before the plugin's self-routing hook. A
`PAID` policy permits metered routes that reach admission, but it cannot supply
the missing route binding.

## Expected behavior

Publish a supported provider-qualified or canonical route with a binding
the billing planner and dispatcher can both validate. A caller authorized
for that billing class should reach the exact plugin and upstream model
once. Requests outside the permitted billing policy should make zero upstream
calls. The explicit route should never fall back to a different provider.
Test these cases with canonical-only enabled, including streaming and
compaction.

## Host fix

The gateway now includes models registered by the exact static
`openai-responses` executor in ordinary model discovery. The qualified route
is available on `/v1/responses` and `/v1/responses/compact` with explicit
`PAID` session policy. Its billing class remains `UNKNOWN` because a project
key does not establish which charges credits cover. The host checks the
registered model again after interception and dispatches directly to the
plugin. The route is outside the canonical fallback pools and profile offers.

The isolated end-to-end test uses the published v0.3.0 plugin. It verifies
catalog discovery, buffered and streamed dispatch, compaction, opaque
continuation, upstream errors without retry, and rejection under `SUB`,
`FREE`, or an explicit `:free` suffix. It also verifies rejection of unknown
models, Codex markers, and requests on the Chat Completions endpoint. Check
the patched host with:

```sh
python3 deploy/cli-proxy-api/catalog-fallback/testing/openai_responses_smoke.py \
  --binary /path/to/patched/cli-proxy-api --plugin /path/to/openai-responses.so --canonical
```

## Production status

The operator reports that the rebuilt plugin is installed on `fra`, the
shared model refresh interval is 60 minutes, and the route remains unavailable.
The local tests made zero live inference requests. The production deployment,
canonical-only setting, and billing policy were untouched during this
investigation.

## Release provenance

The plugin's upstream discovery commit `81f03ad` was included in v0.3.0,
tagged at `96fafa7`. This release also retains the masked, encrypted
credential flow. A fresh export of the release source rebuilt the same
Linux ARM64 shared library byte for byte with the same toolchain. The
published archive matches the public registry checksum, and isolated
Plugin Store and HTTPS mock checks passed on stock CPA v8.0.49 and v8.1.1.
The v0.3.0 rerun against the rebuilt gateway host before the fix reproduced
both rows above. The fix ships with the CPA host build.
