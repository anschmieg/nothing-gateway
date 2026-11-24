# Temporary Gemini cooldowns

The runtime bundle applies `prepare_gemini_cooldown.py` after the canonical CPA
`v8.0.49` rebuild. Input hashes and exact patch context guard the four touched
files. The earlier `.28` overlay applied to archived `.27` source, with rollout
evidence in [the November 15 deployment record](../../../../docs/design/gemini-cooldown-deployment-20261115.json).
The current patch carries forward its recovery hooks using the formatted
canonical dispatch code. The bundle gates publication on the Gemini regression
suite with the Go race detector and an isolated network namespace.

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
