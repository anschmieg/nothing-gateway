# Coolify alpha deployment

Target application: `iog8gk8g8o0s44840g4o4sso`, `https://code.nothing.pink`,
control plane `https://panel.nothing.pink`. Its API is reachable locally on
VPS port8000; use the existing private `COOLIFY_ACCESS_TOKEN` from the user's
`~/.env`, never log it. Public control-plane requests currently return403.

Use `deploy/cli-proxy-api/Dockerfile.coolify` with the repository root as the
build context. Use the existing runtime root bind-mounted at the SAME path,
`/home/agent/cliproxy-staging`. Runtime UID30033 owns existing private state.
The image contains only system runtime dependencies and the entrypoint; tested
backend/plugin/panel artifacts remain in the versioned deployment root.
No legacy gateway executable, credential import, second database or proxy layer.

Track Git branch `main`, use `HEAD` (no commit override), and enable automatic
deploys on push. Enable Coolify's **Consistent Container Name** setting without
a custom name: this makes Coolify stop the old container before starting its
replacement. Overlapping containers cannot acquire the same runtime lock, so
each successful redeploy includes a brief restart rather than a rolling swap.

Before first native container launch: verify/quiesce the owned host service,
retain rollback configuration, change native server binding to0.0.0.0:8080,
apply tested backend/panel, preserve native keys/cooldowns and write
`state/coolify-owner.ready`. The entrypoint holds `state/coolify-runtime.lock`
for the entire native process lifetime; duplicate containers fail locally.
Keep OpenCode disabled. Do not mount/reseed its old database. Existing production
`/data` storage remains untouched and is not read by this native runtime.

The panel is still the pinned upstream native panel with a small same-origin
Cloudflare login bootstrap for `code.nothing.pink`. It uses no management key,
no dummy key and persists no key there. Backend management authentication must
verify the signed Access assertion's issuer/audience/signature/time/email.
Existing native key authentication remains for trusted API/T3 clients. The
Cloudflare application, not a caller-supplied email header, controls who may log in.
Inference clients retain API-key authentication; Access login is not an
inference credential. No inference requests are part of deployment checks.

The exact before-change Coolify app definition is retained privately at
`coolify-before-alpha/application.json`; private environment values must not
be placed in Git or deployment output. Restore only reviewed app/runtime
settings for rollback, never reactivate an old secondary OAuth owner.

Existing Access policy discovery before cutover: unauthenticated
`/management.html` returned the old gateway's401 directly (not an Access login),
while `/admin/v8/management/config` redirected to Cloudflare Access. Therefore
the native panel is served at existing protected `/dashboard`; the browser API
base is same-origin `/admin`. A small in-process route alias re-enters the native
/v8 or /v0 management router and its signed-JWT middleware. It refuses inference
paths, preserves methods/body/query/If-Match and creates no proxy process.
Public-host `/` redirects to `/dashboard`. `/management.html` remains available
for direct native clients and redirects browsers on this host to `/dashboard`.
Existing Access application/personal-email policy stays unchanged.

## CPA and CPAMC artifact upgrades

Git push deployments rebuild the runtime image but do not upgrade the mounted
CPA binary or CPAMC panel. Build upgrades from pinned upstream source with the
gateway's host and Cloudflare Access patches applied, verify the release pair
and plugins in isolation, and retain the previous artifacts for rollback. The
stock panel updater would replace the patched dashboard asset.

Stock CPA `v8.1.1` passed isolated Responses plugin checks. The patched CPAMC
`v1.29.1` panel built with frozen dependencies and passed the Access login,
login-transition, and plugin-resource regressions. These are local candidate
checks, not a deployed upgrade or verification of the full host patchset on
CPA `v8.1.1`.

## OpenAI Responses plugin

Add the separately published plugin's registry under `plugins.store-sources`:

```text
https://raw.githubusercontent.com/anschmieg/cpa-openai-responses/main/registry.json
```

Install **OpenAI Responses** from **Plugins → Store** in an authenticated
Cloudflare Access dashboard session. Follow the [plugin setup guide](https://github.com/anschmieg/cpa-openai-responses#install-and-configure)
to configure persistent state, an optional model allowlist, and the project key
through the plugin's write-only **OpenAI Responses Key** page.

For a plugin upgrade from `v0.1.0`, remove the legacy plaintext `api_key` from
CPA's YAML after the updated plugin migrates it. Save a replacement project key
in **OpenAI Responses Key** before revoking the old one; old config backups may
still contain it.

Before enabling the provider, run the isolated smoke test against the patched
CPA binary intended for deployment. After restart, verify model discovery and
authentication under the configured caller policy. The [canonical routing issue report](../../docs/design/openai-responses-canonical-routing-issue.md)
records the host-side requirements and test coverage.
