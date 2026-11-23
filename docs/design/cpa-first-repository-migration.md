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

1. Record the current Coolify build settings and deployed CPA/CPAMC artifact pins. Keep the old CPA Dockerfile path usable through the merge so an automatic build on `main` still starts CPA.
2. Validate the new and compatibility Dockerfile paths from a clean checkout. Run billing, routing, plugin, and management tests with isolated state. Keep the in-flight Responses canonical-routing change separate from this move.
3. Switch Coolify to `deploy/cli-proxy-api/Dockerfile.coolify` using the repository root as build context. Do not move `/home/agent/cliproxy-staging` or change the binary, panel, or config as part of the switch.
4. Confirm one runtime owner, health, protected dashboard access, and authenticated model discovery without issuing live inference. Roll back to the previous CPA image and build settings if necessary. Never restore a second OAuth owner.
5. Once the new path is confirmed on the live deployment, remove the old-path compatibility Dockerfile.

Historical design records may name paths that were valid when they were written. Use the root README and CPA runbook for current deployment instructions.
