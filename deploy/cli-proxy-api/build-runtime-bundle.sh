#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 || ! ${1:-} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "usage: bash $0 YYYY-MM-DD-LABEL NEW_OUTPUT_DIR (lowercase letters, digits, and hyphen separators)" >&2
  exit 2
fi
tag=$1
bundle_date=${tag:0:10}
if ! parsed_date=$(date -u -d "$bundle_date" +%F 2>/dev/null) || [[ $parsed_date != "$bundle_date" ]]; then
  echo 'Bundle tag must contain a valid UTC calendar date' >&2
  exit 2
fi
if [[ $(uname -sm) != 'Linux aarch64' ]]; then
  echo 'Runtime bundles require a Linux ARM64 build host' >&2
  exit 2
fi

output=$(realpath -m "$2")
if [[ -e $output ]]; then
  echo 'Output directory must not already exist' >&2
  exit 2
fi

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
gateway_commit=$(git -C "$here" rev-parse HEAD)
source "$here/runtime-sources.sh"
export GOTOOLCHAIN=local
test "$(go env GOVERSION)" = go1.26.10
test "$(bun --version)" = 1.4.3
test "$(node --version)" = v22.22.1

scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT

checkout() {
  local repo=$1 commit=$2 directory=$3
  git init --quiet "$directory"
  git -C "$directory" fetch --quiet --depth=1 "https://github.com/$repo.git" "$commit"
  git -C "$directory" checkout --quiet --detach FETCH_HEAD
  test "$(git -C "$directory" rev-parse HEAD)" = "$commit"
}

isolated() {
  if [[ ${GITHUB_ACTIONS:-} == true ]]; then
    sudo unshare --net -- bash -eu -c 'ip link set lo up; user=$1; shift; exec runuser -u "$user" -- "$@"' bash "$(id -un)" "$@"
  else
    bwrap --unshare-net --dev-bind / / -- "$@"
  fi
}

checkout router-for-me/CLIProxyAPI "$cpa_commit" "$scratch/cpa"
checkout router-for-me/Cli-Proxy-API-Management-Center "$panel_commit" "$scratch/cpamc"
checkout anschmieg/cpa-openai-responses "$responses_commit" "$scratch/responses"

mkdir "$scratch/bundle"
python3 "$here/catalog-fallback/rebuild.py" --upstream "$scratch/cpa" --output "$scratch/patched"
jq -e --arg commit "$cpa_commit" '.base_commit == $commit' \
  "$scratch/patched/native-base/provider-budget-manifest.json" > /dev/null
python3 "$here/prepare_gemini_cooldown.py" --source "$scratch/patched/backend" --output "$scratch/backend"
(
  cd "$scratch/backend"
  CGO_ENABLED=1 go build -trimpath -ldflags "-s -w -X main.Version=${cpa_version#v}-$tag -X main.Commit=$gateway_commit" \
    -o "$scratch/bundle/cli-proxy-api" ./cmd/server
  go test -trimpath ./internal/registry ./sdk/api/handlers ./sdk/cliproxy/auth \
    -run '^TestSession|^TestProfileNativeStreamPreservesProfileAndNoPostOutputFallback$|^TestReviewSimilarityCapabilityRevocationAtFinalDispatch$' -count=1
)
(
  cd "$scratch/backend"
  go mod download
  isolated env GOTOOLCHAIN=local "GOMODCACHE=$(go env GOMODCACHE)" "GOCACHE=$(go env GOCACHE)" \
    "$(command -v go)" test -trimpath -race ./sdk/cliproxy/auth ./internal/runtime/executor \
    -run '^TestGemini(CreditCooldown|OverloadRemains|RecoveryStream|CooldownMetadata|CreditErrorPropagates)' -count=1
)
(
  cd "$scratch/responses"
  go test ./...
  CGO_ENABLED=1 go build -trimpath -buildmode=c-shared -o "$scratch/openai-responses.so" .
)
isolated python3 "$here/catalog-fallback/testing/openai_responses_smoke.py" --canonical \
  --binary "$scratch/bundle/cli-proxy-api" --plugin "$scratch/openai-responses.so"

dashboard="$here/patches/cloudflare_dashboard"
python3 "$dashboard/patch_dashboard.py" --source "$scratch/cpamc" --output "$scratch/panel"
jq -e --arg commit "$panel_commit" --arg version "$panel_version" \
  '.source_commit == $commit and .version == $version' \
  "$scratch/panel/cloudflare-dashboard-manifest.json" > /dev/null
(
  cd "$scratch/panel"
  mkdir -p tests
  cp "$dashboard/access_login.test.ts" "$dashboard/plugin_resource_url.test.ts" tests/
  bun install --frozen-lockfile
  bun test tests/access_login.test.ts tests/plugin_resource_url.test.ts tests/excludedModelRules.test.ts
  node "$dashboard/login_transition.test.cjs" "$PWD"
  VERSION="$panel_version" bun run build
  install -m 0644 dist/index.html "$scratch/bundle/management.html"
)

jq -n --arg tag "$tag" --arg gateway "$gateway_commit" --arg cpa "$cpa_commit" \
  --arg panel "$panel_commit" --arg responses "$responses_commit" \
  --arg cpa_version "$cpa_version" --arg panel_version "$panel_version" --arg responses_version "$responses_version" \
  --arg go "$(go env GOVERSION)" --arg bun "$(bun --version)" --arg node "$(node --version)" '{
    tag: $tag, platform: "linux-arm64", status: "candidate",
    gateway_commit: $gateway,
    cpa: {version: $cpa_version, commit: $cpa, patchset: ["catalog-fallback/rebuild.py", "prepare_gemini_cooldown.py"]},
    cpamc: {version: $panel_version, commit: $panel, patchset: "patch_dashboard.py"},
    toolchain: {go: $go, bun: $bun, node: $node},
    tested_plugin: {name: "openai-responses", version: $responses_version, commit: $responses, bundled: false},
    checks: ["focused host billing/profile regressions", "Gemini cooldown and recovery with race detection", "Responses plugin tests and canonical HTTPS smoke", "CPAMC Access login, transition, plugin resources and excluded-model rules"],
    limitations: ["other installed plugins and production config are not validated"]
  }' > "$scratch/bundle/manifest.json"

archive="$tag-linux-arm64.tar.gz"
mkdir "$scratch/bundle/licenses"
cp "$scratch/cpa/LICENSE" "$scratch/bundle/licenses/CLIProxyAPI.txt"
cp "$scratch/cpamc/LICENSE" "$scratch/bundle/licenses/CPAMC.txt"
cp "$here/catalog-fallback/NOTICE-modelsdev.txt" "$scratch/bundle/licenses/NOTICE-modelsdev.txt"
tar -C "$scratch/bundle" -czf "$scratch/$archive" cli-proxy-api management.html manifest.json licenses
(cd "$scratch" && sha256sum "$archive" > SHA256SUMS)
mkdir -- "$output"
cp -- "$scratch/$archive" "$scratch/SHA256SUMS" "$output/"
