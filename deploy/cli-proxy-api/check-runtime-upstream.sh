#!/usr/bin/env bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$here/runtime-sources.sh"

check_release() {
  local repo=$1 bundled_version=$2 bundled_commit=$3 tag commit status
  tag=$(gh api "repos/$repo/releases/latest" --jq \
    'select(.draft == false and .prerelease == false) | .tag_name') || return 1
  [[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo "Unexpected stable tag for $repo: $tag" >&2
    return 1
  }
  commit=$(gh api "repos/$repo/commits/refs/tags/$tag" --jq .sha) || return 1
  [[ $commit =~ ^[0-9a-f]{40}$ ]] || {
    echo "Could not resolve $repo $tag to a commit" >&2
    return 1
  }

  status="Reviewed input"
  if [[ $tag != "$bundled_version" || $commit != "$bundled_commit" ]]; then
    status="Review needed"
    if [[ ${GITHUB_ACTIONS:-} == true ]]; then
      echo "::warning::$repo stable release $tag ($commit) differs from reviewed input $bundled_version ($bundled_commit)" >&2
    fi
  fi
  printf '| %s | %s `%s` | [%s](https://github.com/%s/releases/tag/%s) `%s` | %s |\n' \
    "$repo" "$bundled_version" "$bundled_commit" "$tag" "$repo" "$tag" "$commit" "$status"
}

report=$(
  printf '| Component | Reviewed bundle input | Latest stable | Status |\n'
  printf '| --- | --- | --- | --- |\n'
  check_release router-for-me/CLIProxyAPI "$cpa_version" "$cpa_commit" || exit 1
  check_release router-for-me/Cli-Proxy-API-Management-Center "$panel_version" "$panel_commit" || exit 1
)
printf '%s\n' "$report"
if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
  printf '%s\n' "$report" >> "$GITHUB_STEP_SUMMARY"
fi
