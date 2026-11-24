# CPA plugin evaluation: source receipts

Companion to the [decision matrix](cpa-plugin-routing-decisions.md). Revisions below are evidence anchors, not instructions to install or pin production indefinitely. **Source only** unless listed in isolated results. Candidate branch heads were resolved to immutable commits; their README compatibility statements are not ABI tests. No account entitlements or live artifacts were checked.

## Host and artifacts

| Source | Revision and review anchor |
| --- | --- |
| CPA comparison | [v8.0.49 `250d598175db61a4e99ac38b9164ed04745754e5`](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.49), [v8.1.1 `6eb8554f0ede597b2aa3e508ffb3f111d3c2b9e6`](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.1.1), [v8.1.2 `85225381d89915595598ed13766cd79890c03a84`](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.1.2). GitHub's `releases/latest` returned `v8.1.2`, not a prerelease. Compare `config.example.yaml`, `sdk/cliproxy/auth/{scheduler,selector,auto_refresh_loop}.go`, `internal/pluginhost/adapters.go`, and `sdk/pluginapi/types.go`. |
| Native provider details | `internal/runtime/executor/{antigravity_executor_auth,codex_executor_execute,openai_compat_executor}.go`; `internal/auth/copilot/catalog.go` (`auto` filtered in both 8.1 releases); `sdk/cliproxy/auth/conductor_credits_issue7907_test.go` (401/cancel fixture for the credits path in 8.1.2). [PR #5382](https://github.com/router-for-me/CLIProxyAPI/pull/5382) head `2d1310af7c02a84ba3a316b67dd9d92dce8e3b6a`, unmerged at review. |
| Nothing Gateway | Baseline `1b44fe9`, including `0f051a34a2e67c0c24417590acf089968ae36b7a` (Responses) and `a9c0631f4cb4e5342c7174405f6197c97ae9c906` (panel). Read [architecture](../../deploy/cli-proxy-api/catalog-fallback/ARCHITECTURE.md), [session/billing](../../deploy/cli-proxy-api/catalog-fallback/SESSION-BILLING.md), [profiles](../../deploy/cli-proxy-api/catalog-fallback/PROFILES.md), [OpenCode](../../plugins/opencode/README.md), [Cline](../../plugins/cline/README.md), and [Coolify](../../deploy/cli-proxy-api/COOLIFY.md). Host witnesses: `catalog_fallback_handlers_billing.go`, `catalog_fallback_handlers_responses.go`, `catalog_fallback_registry_responses.go`, `catalog_fallback_auth_cost.go`, `catalog_fallback_sessions.go`, `catalog_fallback_handlers_profile.go`. |
| CPAMC | [v1.29.1 `3128b0ba10ae425d243f52e865b96e7e7c518291`](https://github.com/router-for-me/Cli-Proxy-API-Management-Center/releases/tag/v1.29.1) was latest at review. [Local patch and earlier isolated evidence](../../deploy/cli-proxy-api/patches/cloudflare_dashboard/README.md); not retested here. |
| Copilot SDK | [v2.0.1 `3ce61458503b6b27ebd1f674a980f4d9596d9b22`](https://github.com/microsoft/copilot-sdk/tree/3ce61458503b6b27ebd1f674a980f4d9596d9b22), `go/types.go` and `docs/session-management.md`: session model selection is configurable; unspecified model alone does not establish the semantics or Education availability of Copilot Auto. No SDK service session opened. |
| Responses / parity samples | [Responses v0.3.0](https://github.com/anschmieg/cpa-openai-responses/tree/96fafa7dd8f937c0fd70e5425b4602187dfea7f1) uses plugin-owned credential storage and executor/route hooks. [Ollama](https://github.com/azwildfire/cliproxyapi-ollama-plugin/tree/3436c29f49f1d1e43913e27eec9b05cc60d8c3fb) (`internal/provider/{client,models,executor}.go`) and [DeepSeek](https://github.com/xspeed1989/cpa-plugin-deepseek/tree/6db01949050524de020572b465a7b9ea7066aa8d) (`executor.go`) illustrate the pattern, not OpenAI-compatible auth/discovery. |
| Free Model Hub | [Reviewed integration pin](../../deploy/cli-proxy-api/free-model-hub/SOURCE-PINS.md) `4d025efe8046af70327d80f75c5bd5ac6b4505af` with local patches, not [review-time head](https://github.com/Yousha-dev/free-model-hub/tree/11b5200bf7b6532d01a0117640574b0fb37f1a51). Preserve the repo's allowlist and independent provider ownership. |

## Candidate source anchors

| Candidate | Commit and inspected boundary |
| --- | --- |
| [DoingDog/cpa-plugin-model-mapper v0.5.17](https://github.com/DoingDog/cpa-plugin-model-mapper/tree/e2823528de3000fe6907d75845c45e1616971d81) | `model_router.go`, `rules.go`, stream writer: format/client selection and response rewrite, not discovery or economic admission. May use a caller key to select a rule; avoid storing raw keys for this. |
| [markhuangai/cpa-plugin-model-router](https://github.com/markhuangai/cpa-plugin-model-router/tree/7abcb24c7557e4cc6ea4acecee3d4ad88de6baee) | `router.go`, `execute.go`, `stream.go`, dashboard docs. Declared v7.3.9+ baseline, not verified v8 integration. Independent attempts and management UI reads configured access keys for catalog requests. |
| [ngvoicu/cpa-pools-and-aliases](https://github.com/ngvoicu/cpa-pools-and-aliases/tree/8c42b3eae126840041275f60bf3872f48fcddb28) | `src/`, usage/design README: aliases across members, stored config/cache metadata and optional active model measurements; not a static alias file or authorization owner. |
| [xg1990/quota-pacer](https://github.com/xg1990/quota-pacer/tree/c63b848fca34782d189edb6a4dc0a11df8a6cdd7) | `internal/apply`: patches auth priority/weight from a polling plan. |
| [nitansde/smart-load-balancer](https://github.com/nitansde/smart-load-balancer/tree/e3da7bd1109f7a5de33b10e12f62dbffc5db9f1d) | `balancer/`, `quota/`, `main.go`: candidate picker, quota fetch and signed-in profiles, not a separate provider executor. |
| [daodeqing/cpa-plugin-quota-rotator](https://github.com/daodeqing/cpa-plugin-quota-rotator/tree/7f5dbfc21cbc97678e11e756f7c02664609919ce) | `internal/{quota,scheduler}`, `cmd/`: background account scans/quota polling, provider usage and OAuth HTTP. |
| [Yousha-dev/team-routing](https://github.com/Yousha-dev/team-routing/tree/7b884553d499c11f99446ffe39704415732c04cd) | Go routing, ranking and quota cache files: client routes, scheduler, provider HTTP, persisted snapshots and reservations. |
| [ygq-future/antigravity-priority](https://github.com/ygq-future/antigravity-priority/tree/01311498db10d82c26645cc504c50ec1cc429629) | `internal/`, `main.go`: model-specific account evidence, probing and optional unrelated remote phrase sync. |
| [WebDevCaptain/quota-reset-router](https://github.com/WebDevCaptain/quota-reset-router/tree/62b73e7c66b40bcef7b3eb2879f99d727290a740) | `engine.go`, `quota.go`, `runtime.go`: weekly usage ordering, bounded polling and stale fallback. |
| [Senn-s-Team/cpa-antigravity-proactive-refresh](https://github.com/Senn-s-Team/cpa-antigravity-proactive-refresh/tree/b095b06d628552ac50b4d49aeaabdf15b0113792) | Readme lifecycle contract: independent OAuth reads/writes and periodic/manual refresh. |
| [DongHua3/cpa-plugin-antigravity](https://github.com/DongHua3/cpa-plugin-antigravity/tree/d80e3475e1c62144c921bbea8ac674f3ef919d62), [esauvisky/CLIProxyAPI-Safeguard-Killer-Plugin](https://github.com/esauvisky/CLIProxyAPI-Safeguard-Killer-Plugin/tree/9680541593910f2e49304e539452bdd24920dcf7) | Resolved source revisions; excluded from execution/integration because bypass is out of scope. No claim that either improves authorized native refresh. |
| [dohwi/usage-audit](https://github.com/dohwi/cliproxyapi-plugin-usage-audit/tree/cd36e7e79d90bfdb96549fc7bed005bbcd3833db), [dohwi/quota-monitor](https://github.com/dohwi/cliproxyapi-plugin-quota-monitor/tree/78784cfdc28ecb600018eb99fa13606c3bec2f4c) | Documented usage JSONL/dashboard/rotation and periodic provider quota probes/protected data API, respectively. **Defer** both whole dashboard installs; their scopes differ and neither guarantees exact-once billing. |
| [KKKKeybird/cpa-codex-auto-ping](https://github.com/KKKKeybird/cpa-codex-auto-ping/tree/e452faa97dc640ef4a4e6d39ea851b1d01d7225b) | `src/` and schedule config: timer calls host inference, not login, access or quota discovery. |
| [tsaQB/cpa-freetier-plugin](https://github.com/tsaQB/cpa-freetier-plugin/tree/ffb6d5cd1979a72c25cdd83cd862bb43f47bde56), [sy0u1ti/cpa-our-free-model](https://github.com/sy0u1ti/cpa-our-free-model/tree/3ebef7be52c5754d9d329d9c6184a5a24071bdd0) | README/provider scope and OFM vendor manifest: multiple upstreams, non-portable workbench, separate identity/credential lifecycle. Protocol adapters and source catalog lists are not proof of account access. |
| [AkkunYo/kiro4cpa-plugin](https://github.com/AkkunYo/kiro4cpa-plugin/tree/272c87104de3a51010ba4b0520558bc565bc2b36) | Provider login/refresh/usage and executor declarations; claims Chat/Responses/Messages incl. streaming, not AWS account entitlement. |
| [jianli9158/aistudio2api](https://github.com/jianli9158/cpa-plugin-aistudio2api/tree/8a8e5c2eadf1a9c815ac8e178339c5c23c655d1e), [jianli9158/gemini-web2api](https://github.com/jianli9158/cpa-plugin-gemini-web2api/tree/0d82602f3f29dbb8c0d7bbcaa71c93980cc6adb6) | Provider and browser-auth descriptions: Playground OAuth vs web cookies and session background activity. Neither is the standard API-key integration. |
| [JHXSMatthew/codex-localcompact](https://github.com/JHXSMatthew/codex-localcompact/tree/8c525a224c6ba7026a454b34e4cd4871544c8df5), [patrick-fu/cpa-codex-compact-bridge](https://github.com/patrick-fu/cpa-codex-compact-bridge/tree/8d49b7a4951a3a3c4106151d2a740202e0d3ce20) | Compact transform/codec and per-model bridge architecture: host execution can send full conversation to a second provider. No automatic state-sharing consent; classify native vs synthesized SSE separately. |
| [Zesuy/Plugin-Deepseek-Vision](https://github.com/Zesuy/Plugin-Deepseek-Vision/tree/fe888b9511793fe4cdca94552d80d514b5177aaf), [wangwq7/cpa-glm-vision-bridge](https://github.com/wangwq7/cpa-glm-vision-bridge/tree/b5f6ecd1061817abf9924202b0135fae85af3e92) | `host_model.go` / `image_pipeline.go`, `protocol_adapter.go`: image extraction, secondary inference, then main stream; data-transfer and billing risk, not a presentation-only shim. |
| [dotiful/model-sort](https://github.com/dotiful/cpa-plugin-model-sort/tree/c7eb679b25e77bfb5eae1e00df36aa36449545b0), [LIEGU0317/model-meta-sync](https://github.com/LIEGU0317/cpa-plugin-model-meta-sync/tree/9e3a3852e97d0af948e479961c1febb7bb9e0a43), [alex4o/effort-router](https://github.com/alex4o/cliproxy-effort-router/tree/ae34789b646d0708edd7d3f4a3863112ad38b76b) | Discovery response rewrite; authenticated upstream catalog polling/writeback; request normalization plus classifier/state, respectively. Keep these concerns separate from entitlement and signed state. |
| [Batuhan4/cpa-mistral-normalizer](https://github.com/Batuhan4/cpa-mistral-normalizer/tree/dbc96d61a958ba48cf62303883c0a026d9fd645d) | Pre-translation response normalization, not a new auth provider, summarizer or realtime handler. |
| [PlaygrndLabs/sub2api](https://github.com/PlaygrndLabs/sub2api/tree/87f7906a838c7b15e4f9ab4d74e012ec98d11057) | Cloudflare container and Worker configuration; no evidence about Nothing Gateway's latency or Access guarantees. |

### Public `ahoo` survey and saved references

- [OpenCode Session Mapper](https://github.com/ahoo/cpa-plugin-opencode-session-mapper/tree/01702a4b21568a8cd022b8188f2b2741b9d6e07a) and [Zen](https://github.com/ahoo/cpa-plugin-zen/tree/64a176416c632a9c577057ca11581e11770b0216): **defer**; session mapping preserves client identity on an already authorized path; Zen bundles mapping and paid-key providers. Mock conflicting session headers and caller/credential isolation before any mapper adoption.
- [SystemOne](https://github.com/ahoo/cpa-plugin-systemone/tree/5bf828ea1a26f94f27a63882361b0698a0f9a8bd): **defer** unless typed-request use is wanted; wraps an authenticated provider as chat. [CommandCode](https://github.com/ahoo/cpa-plugin-commandcode/tree/b094d95d779c24d1060f99ddf79a8f270f7f4a25): **defer**; independent provider, response reasoning normalization before translation, not OpenCode or a summary feature.
- [DeepSeek reasoning fixer](https://github.com/ahoo/cpa-plugin-deepseek-reasoning-fixer/tree/efc3a8dc8aa607d6f5ef1b766cf714b8603c202e), [DeepSeek Harness session adapter](https://github.com/ahoo/cpa-plugin-deepseek-harness-session/tree/32332cc5f2ad7b88c7b1f73c151d899678b1fbe3) and its [store](https://github.com/ahoo/cliproxy-plugins/tree/ee66caf459accf3ac1a7d181df89ce96372c78fb): **defer**; targeted request normalization and session header mapping. Neither generates summaries or owns credentials. [Claude structured-output fixer](https://github.com/ahoo/cpa-plugin-claude-structured-output-fixer/tree/55e8217939d880e16346ebd3a2dfa462b75d509a) is documented as legacy; **native config/code first**, no additional compatibility shim.
- [Muse Tools Stripper](https://github.com/ahoo/cpa-plugin-muse-tools-stripper/tree/14d997ce9ca0341c67e8919ede063b1fbf9a8c05) and [Privacy Filter](https://github.com/ahoo/cpa-plugin-privacyfilter/tree/f85a0f59f1cb8b8f6dbebe0bb55e26458457907a): **defer** both. Stripping a request field is not an entitlement mechanism; privacy redaction can change payloads, tools and cached prefixes, outside this request. Upstream/fork and store listings are references, not additional runtime owners.
- The non-overlapping saved-only candidates remain **deferred without inspection** as instructed by the original backlog: `conversun/cliproxy-plugin-opencode-cloak` (do not adopt impersonation), `adeebahmad01/cliproxy-antigravity`, `xiao-qiu-qiu/ClinePassBridge`, `Azurefly/antigravity-category-plugin`, and `stavarengo/CLIProxyAPI-Expiring-Quota-First`. The reviewed reset router already provides a scoped discriminator for earliest-reset selection.

## Isolated results (November 23, 2026)

| Check | Result and scope |
| --- | --- |
| Patched CPA `v8.0.49` candidate | Fresh `catalog-fallback/rebuild.py` from the exact upstream clone, then `go build -ldflags='-s -w' -o <scratch-binary> ./cmd/server` with Go `1.26.10` on Linux ARM64 (debug link hit local disk quota; stripped link passed). `go test ./internal/registry ./sdk/api/handlers -run '^TestSession' -count=1` passed in an isolated network namespace. |
| Expanded canonical smoke | `openai_responses_smoke.py --canonical` passed with the fresh patched backend and Responses `v0.3.0` built from tagged source. The rejected same-session/different-key and same-key/different-session requests both returned `403` before any generation POST. Existing covered behavior still passes. HTTPS fixture and CPA ran under `bwrap --unshare-net` with synthetic keys and temporary state. |
| Stock CPA `v8.1.2` | Built and passed the existing non-canonical Responses HTTPS smoke (no host billing claim). Focused `sdk/cliproxy/auth` tests for weighted and mixed selection, exclusion of ineligible free auth, refresh queue lifecycle and Antigravity credits-path 401/cancellation passed. `go test ./internal/auth/copilot -run '^TestResolveAccountEndpoint$' -count=1` passed and covers rejection of `auto` as a concrete model. Direct `prepare.py` port probe rejected changed input digests; no patched `v8.1.2` candidate. |
| Responses `v0.3.0` | `go test ./...` and `go build -buildmode=c-shared` passed from the exact checkout; this turn did not compare against published archive bytes. No full gateway suite, live config, production inference, new dashboard build, or account check in this turn. |

## Dated bundle follow-up (November 24, 2026)

- GitHub's stable release endpoints returned CPA `v8.1.2` at
  `85225381d89915595598ed13766cd79890c03a84` and CPAMC `v1.29.2` at
  `3866a16878b04c19627ef0db06a33f5cd79878cf`. From `v1.29.1`
  to `v1.29.2`, `useAuthStore.ts`, `LoginPage.tsx`, `pluginResources.ts`
  and `bun.lock` were byte-identical. The release changes excluded-model
  editing, so its upstream rules suite joins the Access checks.
  [CPA release](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.1.2) ·
  [CPAMC comparison](https://github.com/router-for-me/Cli-Proxy-API-Management-Center/compare/v1.29.1...v1.29.2).
- The Gemini overlay's conductor, cooldown and executor input hashes matched
  the fresh canonical `v8.0.49` rebuild. Its archived dispatch hunk also
  reformatted the file, so the new hunk applies just the recovery hooks to
  the already formatted input. The existing explicit depleted-credit
  classification, retry hints and model-scoped overload code carry forward.
  [Recorded prior rollout](gemini-cooldown-deployment-20261115.json) ·
  [Overlay](../../deploy/cli-proxy-api/patches/gemini_cooldown/README.md).
- The CPA upgrade needs a semantic port. The first provider-budget step
  exports its internal baseline commit even from a clone checked out at
  `v8.1.2` when both commits are available. A review of `v8.0.49...v8.1.2`
  found changes in the SDK selector, plugin-host execution/usage adapters and
  Gemini executor. First verify that the exported source matches the candidate
  revision. Then compare native HTTP dispatch and canonical admission at the
  exact hooks before reapplying local patches. A candidate must reject a lost
  billing permit before any provider POST and count attempts across native
  retries, redirects and plugin paths.
  [Provider patch preparation](../../deploy/cli-proxy-api/prepare_provider_patch.py) ·
  [CPA comparison](https://github.com/router-for-me/CLIProxyAPI/compare/v8.0.49...v8.1.2).

The Linux ARM64 bundle passed the focused host billing/profile suite, the
Gemini cooldown suite with race detection, the Responses `v0.3.0` canonical
HTTPS smoke, 35 CPAMC tests plus the Access login-transition regression,
and manifest and archive checksum verification. The discovery script handled
a new version, an unchanged pin, a moved tag, and malformed or unavailable
metadata in fixtures. Cline and OpenCode unit and isolated host smokes also
passed against the local candidate. The patched Free Model Hub pin passed
its focused race tests and the canonical-routing smoke with OpenCode present.
These three plugins were built separately from the candidate archive.

The local rollback rehearsal used a separately built `v1.29.1` panel, an
earlier local canonical CPA binary, and temporary config and state. The
single-owner lock excluded a second process. Both pairs served health and
management successfully, a deliberately wrong HTML payload failed the
digest check, and restoring the previous pair brought back both original
digests and health. This exercises the replacement sequence with synthetic
state. The actual installed pair, existing state compatibility, and live
Cloudflare Access remain production verification gates.
