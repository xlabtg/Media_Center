#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "missing file: $path"
}

assert_contains() {
  local path="$1"
  local pattern="$2"
  grep -Fq -- "$pattern" "$path" || fail "missing marker in $path: $pattern"
}

expected_services=(
  activity-command-center
  analytics-engine
  api-gateway
  blockchain-auditor
  cglr
  contribution-ledger
  hitl-payout-gateway
  messenger-adapter
  neuro-agent-orchestrator
  notification-gateway
  policy-manager
  voice-to-chain
  wallet
  web-cabinet
)

assert_file ".github/workflows/ci.yml"
assert_file ".github/workflows/build-service.yml"
assert_file "infra/docker/service.Dockerfile"
assert_file "pyproject.toml"
assert_file "requirements-dev.txt"
assert_file ".dockerignore"
assert_file ".gitleaks.toml"
assert_file ".github/dependabot.yml"
assert_file "tests/test_ci_contract.py"

quality_markers=(
  "ruff check ."
  "ruff format --check ."
  "mypy ."
  "pytest"
)

for marker in "${quality_markers[@]}"; do
  assert_contains ".github/workflows/ci.yml" "$marker"
done

security_markers=(
  "pip-audit . --progress-spinner off"
  "gitleaks detect --source . --no-git"
  "aquasecurity/trivy-action@v0.36.0"
  "scan-type: fs"
  "scanners: vuln,misconfig"
)

for marker in "${security_markers[@]}"; do
  assert_contains ".github/workflows/ci.yml" "$marker"
done

image_markers=(
  "docker/setup-buildx-action@v4.1.0"
  "docker/build-push-action@v7.2.0"
  "infra/docker/service.Dockerfile"
)

for marker in "${image_markers[@]}"; do
  assert_contains ".github/workflows/build-service.yml" "$marker"
done

assert_contains ".github/workflows/ci.yml" "uses: ./.github/workflows/build-service.yml"
assert_contains ".github/workflows/build-service.yml" "workflow_call:"
assert_contains ".github/workflows/build-service.yml" "SERVICE_PATH=services/\${{ inputs.service }}"

for service in "${expected_services[@]}"; do
  assert_contains ".github/workflows/ci.yml" "- $service"
done

tool_pins=(
  "ruff==0.15.17"
  "mypy==2.1.0"
  "pytest==9.1.0"
  "pip-audit==2.10.1"
)

for marker in "${tool_pins[@]}"; do
  assert_contains "requirements-dev.txt" "$marker"
done

assert_contains "infra/docker/service.Dockerfile" "FROM python:3.13.14-slim"
assert_contains "infra/README.md" "service.Dockerfile"
assert_contains "CONTRIBUTING.md" "python -m pip install -r requirements-dev.txt"
assert_contains "README.md" "actions/workflows/ci.yml"

echo "OK: CI/CD contract for issue #9 is configured"
