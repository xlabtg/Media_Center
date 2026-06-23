#!/usr/bin/env bash
set -euo pipefail

if ! command -v helm >/dev/null 2>&1; then
  printf '%s\n' "helm is required for issue #248 validation" >&2
  exit 127
fi

if ! command -v kubeconform >/dev/null 2>&1; then
  printf '%s\n' "kubeconform is required for issue #248 validation" >&2
  exit 127
fi

rendered="$(mktemp --suffix=.yaml)"
trap 'rm -f "$rendered"' EXIT

helm lint deploy/helm/media-center
helm template media-center deploy/helm/media-center > "$rendered"
kubeconform -strict -summary "$rendered"
