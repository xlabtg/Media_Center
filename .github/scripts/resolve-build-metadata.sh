#!/usr/bin/env bash
set -euo pipefail

tag_match_args=(
  --match "[0-9]*.[0-9]*.[0-9]*"
  --match "v[0-9]*.[0-9]*.[0-9]*"
)

is_req_semver_tag() {
  local value="$1"

  [[ "$value" =~ ^v?[0-9]+[.][0-9]+[.][0-9]+([.+-][0-9A-Za-z.-]+)?$ ]]
}

is_official_semver_tag() {
  local value="$1"

  [[ "$value" =~ ^v?(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$ ]]
}

short_git_sha() {
  local sha="${GITHUB_SHA:-}"

  if [[ -z "$sha" ]]; then
    sha="$(git rev-parse --short=12 HEAD 2>/dev/null || true)"
  fi

  if [[ -z "$sha" ]]; then
    printf '%s\n' "unknown"
    return
  fi

  printf '%s\n' "${sha:0:12}"
}

resolve_git_tag() {
  local tag

  tag="$(
    git describe --tags --abbrev=0 "${tag_match_args[@]}" 2>/dev/null || true
  )"

  if is_req_semver_tag "$tag"; then
    printf '%s\n' "$tag"
  fi
}

resolve_service_version() {
  local git_tag="$1"
  local git_describe

  if [[ -z "$git_tag" ]]; then
    printf '0.0.0-%s\n' "$(short_git_sha)"
    return
  fi

  git_describe="$(
    git describe --tags --dirty "${tag_match_args[@]}" 2>/dev/null || true
  )"

  if [[ -z "$git_describe" ]]; then
    git_describe="$git_tag"
  fi

  printf '%s\n' "${git_describe#v}"
}

emit_output() {
  local key="$1"
  local value="$2"

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
  else
    printf '%s=%s\n' "$key" "$value"
  fi
}

build_date="${BUILD_DATE:-$(date -u +'%Y-%m-%dT%H:%M:%SZ')}"
git_tag="$(resolve_git_tag)"
service_version="$(resolve_service_version "$git_tag")"
image_source="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-xlabtg/Media_Center}"
official_semver="false"
if is_official_semver_tag "$git_tag"; then
  official_semver="true"
fi

emit_output "build_date" "$build_date"
emit_output "git_tag" "$git_tag"
emit_output "service_version" "$service_version"
emit_output "image_source" "$image_source"
emit_output "official_semver" "$official_semver"
