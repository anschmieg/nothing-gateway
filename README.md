# Nothing Gateway

Nothing Gateway runs [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) (CPA) with the CLIProxyAPI Management Center (CPAMC), native routing and billing patches, and provider plugins. Coolify starts CPA directly; the old Tokligence stack is not part of this deployment.

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
