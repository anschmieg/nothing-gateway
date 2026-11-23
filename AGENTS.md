# Repository Guidelines

## Project layout

- `deploy/cli-proxy-api/` owns the native CLIProxyAPI (CPA) deployment, Coolify image, and patches. `catalog-fallback/` owns canonical discovery, routing, billing, and profiles; `patches/cloudflare_dashboard/` adapts CPAMC for Cloudflare Access.
- `plugins/` contains the Cline and OpenCode plugins. OpenAI Responses is maintained separately; this repo owns the host integration and smoke test. `staging/model-provider-router/` is the separate local-model controller, not the legacy gateway.
- Use the root `README.md` and `deploy/cli-proxy-api/COOLIFY.md` for current operating guidance. Historical design notes are not deployment instructions. Check the README's deferred findings before reopening them.

## Engineering preferences

- Favor progress and a clean, conventional design over backward compatibility. If a deliberate break is cleaner, document the cutover and remove the obsolete path rather than keeping shims, duplicate stacks, or old names. Git history preserves Tokligence; do not bring it back into the runtime.
- Prefer upstream CPA capabilities and plugin hooks. Keep host patches narrow where discovery, billing, dispatch, or Access guarantees require them. For exploratory routing or plugin work, record the request, assumptions, and evaluation plan before implementation.
- Make focused changes and preserve existing style. Use `gofmt` for Go. When asked to commit, use small, independently working increments with scoped messages such as `fix(cpa): ...`.

## Updates and deployment

- Prefer automatic updates plus rollback over static production pins. Coolify should track `anschmieg/nothing-gateway` at `main`/`HEAD`, building `deploy/cli-proxy-api/Dockerfile.coolify` from the repo root on push. Verify the live setting and trigger rather than assuming the runbook proves them.
- A push rebuilds the runtime image, not the mounted CPA/CPAMC artifacts. For patched artifacts, automate candidate builds and isolated checks before promotion; retain the last working artifact pair for rollback. Source revisions, checksums, and lockfiles make individual builds reproducible; they are not a reason to freeze updates or install a stock binary over a patched one.
- Keep one runtime owner. Do not push, redeploy, touch the live bind mount, or send live inference as part of local validation unless explicitly authorized. Keep production credentials, config, auth state, and raw logs out of Git and build contexts.

## Local validation

- Build the image from the root: `docker build -f deploy/cli-proxy-api/Dockerfile.coolify .`.
- Run `go test ./...` from the affected plugin module. For host or dashboard patches, follow the nearest README to prepare fresh scratch trees and run focused tests. Use synthetic credentials and isolated loopback fixtures; report candidate results separately from live verification.
