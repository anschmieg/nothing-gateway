# Native panel Access bootstrap

`patch_dashboard.py` supports the pinned source commits for CPAMC `v1.27.0`
and `v1.29.1`. The Access bootstrap applies only on `https://code.nothing.pink`.
A successful same-origin management config fetch with an empty key establishes
auth without a dummy management key, cross-origin API base, or stored credentials.
The backend separately validates signed Access assertions. Other hosts retain
native key login.

Focused parent verification: copy access_login.test.ts into prepared tree
`tests/` and run `bun test tests/access_login.test.ts`.
Tests stay outside upstreamsrc/typecheck inclusion; no changes to upstream
compiler settings or broad core tests. Four custom authentication-state cases
passed with fake config API and no provider/network calls.

Login-failure follow-up: initial production team was an old placeholder while
preview had the right team. Inspect production/runtime values separately; never
merge preview values when validating production. Global401 now calls local
invalidateSession only; explicit user Logout alone calls Access logout. The
error page retries /dashboard without revoking the SSO session. Regression
case drives the real unauthorized event handler; five focused tests pass.


Sidebar resource follow-up: the Access-only `/admin` API base must not prefix
native `/v0/resource/plugins/` URLs. `resolvePluginAssetURL` now resolves those
paths at the origin root on the Access host. FreeHub's static resource then
redirects into its existing authenticated `/admin/v0/management/.../console`.
Other deployments retain their configured mount prefixes. Three focused URL
regressions and all five Access-login cases pass; the panel builds successfully.
This does not expand backend aliases, weaken authentication or add browser keys.


Successful-login flash correction: LoginPage clears autoLoading after restoreSession
succeeds while autoLoginSuccess remains true for the upstream splash/exit animation.
The Access-only fallback previously interpreted this successful interval as failure.
Authenticated Access users now immediately render Navigate, independent of animation
flags. Pending admission still shows checking; rejected admission still offers retry.
No auth-store, JWT validation, cookie or key behavior is changed.

Run the actual render-branch regression with:
`node deploy/cli-proxy-api/patches/cloudflare_dashboard/login_transition.test.cjs <prepared-panel-tree>`.
It covers successful pending/animation/exiting states, genuine failure, pending
admission and unchanged non-Access transition behavior.

Install dependencies with `bun install --frozen-lockfile` and build with
`VERSION=<reported-tag> bun run build`, using the version printed by
`patch_dashboard.py`. The exported source has no Git tag metadata.
