# CPA-first repository migration

CLIProxyAPI and CPAMC are the supported gateway. The root Tokligence Docker image and Node proxy were a separate deployment. This migration changes repository ownership and paths, not the running backend, panel, configuration, or billing policy.

## Keep, port, or retire

| Capability | Decision |
| --- | --- |
| Native CPA/CPAMC deployment, canonical discovery, billing, fallbacks, profiles, protected management routes, and provider patches | Keep as the supported deployment. Move `staging/cli-proxy-api/` to `deploy/cli-proxy-api/` with its fixtures and isolated tests. |
| Cline, OpenCode, Free Model Hub, and separately published plugins | Keep CPA plugins and their tests. Preserve the upstream OpenCode source pins referenced by the CPA plugin; the former Node/Bun bridge does not run in CPA. |
| Local model router and wake/controller tooling | Keep in `staging/model-provider-router/`. It integrates with native CPA and is not the legacy Tokligence public process. Its independent deployment layout is outside this migration. |
| Copilot Auto, SystemOne, Mistral specialist/realtime, and Node-only routing behavior | The old implementations are not CPA deployments. Treat any desired capabilities as explicit follow-up ports with native acceptance tests; do not expose legacy routes by accident or assume CPA parity. |
| Root Docker/Compose stack, `gatewayd` bootstrap, Node public proxy, old dashboard, route compiler, and their tests | Retire. Preserve their source and tests in Git history. Do not route production traffic back to this stack. |
| One-time Tokligence credential/export/admission and native OpenCode bridge tooling | Retire the legacy tooling. It is not needed to build Coolify, patch CPA, or build the current plugins. Don't import old credentials as part of this migration. |

## Cutover and rollback

1. Record the current Coolify build settings and deployed CPA/CPAMC artifact pins. Validate the new Dockerfile from a clean checkout and run billing, routing, plugin, and management tests with isolated state. Keep the in-flight Responses canonical-routing change separate from this move.
2. Disable automatic deploys during the cutover. Set the Coolify Dockerfile location to `deploy/cli-proxy-api/Dockerfile.coolify` with the repository root as build context. Do not move `/home/agent/cliproxy-staging` or change the binary, panel, or native config.
3. Merge the validated branch into `main` and deploy it. Confirm one runtime owner, health, protected dashboard access, and authenticated model discovery without issuing live inference. Restore the previous auto-deploy policy after validation.
4. If the cutover fails, restore the previous CPA image and matching build settings with auto-deploy paused until `main` and the build path agree. Never restore a second OAuth owner.

Historical design records may name paths that were valid when they were written. Use the root README and CPA runbook for current deployment instructions.
