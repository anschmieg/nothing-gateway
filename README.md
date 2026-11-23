# Nothing Gateway

Nothing Gateway runs [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA) with the CLIProxyAPI Management Center (CPAMC), native routing and billing patches, and provider plugins. Coolify starts CPA directly; the old Tokligence stack is not part of this deployment.

## Deferred review findings

The migration review found no new regressions in the local checks. Two issues
predate the migration, and live auto-deploy after the rename remains unverified.
These follow-ups are deferred:

| Finding | What to verify or fix when resumed |
| --- | --- |
| **Startup lock ordering.** The [entrypoint](deploy/cli-proxy-api/coolify-entrypoint.sh) rejects a second CPA process, but an isolated test confirmed that the rejected container can overwrite `state/coolify-container-address` first. Operational tools can then target the wrong container. | Acquire the runtime lock before publishing the address and hold it for CPA's lifetime. Verify that a rejected launch leaves the owner's address unchanged. |
| **Fallback test remains red.** [`TestDraftNativeCoordinatorChoosesEvidenceBeforeFamily`](deploy/cli-proxy-api/catalog-fallback/host/catalog_fallback_handlers_test.go) returned `503` instead of selecting its expected fallback on both the current and pre-migration builds. | Determine whether the fixture is stale or the router rejects a valid fallback. Don't change routing policy or test expectations until the cause is clear. |
| **Coolify auto-deploy not verified after the rename.** GitHub serves `main` at `anschmieg/nothing-gateway`, but that does not confirm Coolify's live configuration or push delivery. | At the next planned push, confirm the [Coolify app](deploy/cli-proxy-api/COOLIFY.md) tracks the renamed repo at `main`/`HEAD` using `deploy/cli-proxy-api/Dockerfile.coolify`, then verify that the push queues a deployment. |

## Work on the gateway

| Area | Source |
| --- | --- |
| Coolify image, entrypoint, bootstrap, and provider templates | [CPA deployment](deploy/cli-proxy-api/README.md) · [Coolify runbook](deploy/cli-proxy-api/COOLIFY.md) |
| Canonical discovery, billing, model fallback, and task profiles | [Native catalog fallback](deploy/cli-proxy-api/catalog-fallback/README.md) · [Canonical model routing](docs/design/canonical-model-routing.md) |
| CPA host and CPAMC dashboard patches | [Patch inventory](deploy/cli-proxy-api/patches/README.md) |
| Provider integrations | [Cline](plugins/cline/README.md) · [OpenCode](plugins/opencode/README.md) · [Free Model Hub](deploy/cli-proxy-api/free-model-hub/README.md) |
| Local model controller and wake support | [Local model router](staging/model-provider-router/README.md) |

## Build and operate

From the repo root, build the Coolify image with `docker build -f deploy/cli-proxy-api/Dockerfile.coolify .`. The image contains the entrypoint and system dependencies, not CPA or private runtime data. The deployment uses the existing `/home/agent/cliproxy-staging` bind mount on `fra` for the pinned backend and panel, `config/config.yaml`, installed plugins, credentials, and state. `providers.yaml` is a template, not the deployed config. Do not copy production config, keys, or state into Git or the Docker build context.

For changes to the native backend, follow the [isolated catalog fallback checks](deploy/cli-proxy-api/catalog-fallback/README.md) and the relevant [plugin checks](deploy/cli-proxy-api/free-model-hub/testing/README.md). Use fixtures and temporary state, not the live bind mount.

For the legacy boundary and cutover steps, see the [CPA-first migration inventory](docs/design/cpa-first-repository-migration.md).
