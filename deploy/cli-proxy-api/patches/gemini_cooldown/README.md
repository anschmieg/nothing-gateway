# Temporary Gemini cooldowns

Applied to the archived native `8.0.49-tokligence.27-portable-history` source
with `prepare_gemini_cooldown.py`. Input hashes prevent applying the patch to
an unrelated revision. Production rollout evidence is in
`docs/design/gemini-cooldown-deployment-20261115.json`.

Gemini 402/429 responses explicitly reporting depleted prepayment credits are
credential-scoped. The native quota machinery temporarily excludes sibling
models on that credential. Other credentials remain eligible. The client still
receives the original HTTP status and body. Standard Retry-After headers and
Google RetryInfo retryDelay hints are preserved; depleted credits without a
hint use a finite 30-minute cooldown.

503 overloads retain the existing model-scoped transient cooldown (one minute
by default), with retry hints now preserved. Generic model rate limits do not
become credential-wide billing failures. Existing persistence and operator
cooldown controls remain in use.

After expiry, one demand-driven recovery attempt runs at a time per affected
scope. Streams hold that lease until completion or cancellation. A successful
request restores eligibility; a new failure renews the appropriate cooldown.
No background inference, permanent disables, or generation deadlines are added.

Validation covers scope, expiry, unrelated credentials, concurrent recovery,
stream cancellation, and original error delivery for streaming and non-streaming
HTTP calls. Fake upstreams are used; no production provider inference was sent.
