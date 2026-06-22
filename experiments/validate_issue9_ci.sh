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

assert_not_contains() {
  local path="$1"
  local pattern="$2"
  if grep -Fq -- "$pattern" "$path"; then
    fail "unexpected marker in $path: $pattern"
  fi
}

expected_services=(
  api-gateway
  contribution-ledger
  cglr
  hitl-payout-gateway
  messenger-adapter
  blockchain-auditor
)

assert_file ".github/workflows/ci.yml"
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
  "docker build \\"
  "max-parallel: 1"
  "--build-arg SERVICE_NAME=\${{ matrix.service }}"
  "Build and push image"
  "push: true"
  "if: github.event_name == 'push' && github.ref == 'refs/heads/main'"
  "image=moby/buildkit@sha256:0168606be2315b7c807a03b3d8aa79beefdb31c98740cebdffdfeebf31190c9f"
  "infra/docker/service.Dockerfile"
)

for marker in "${image_markers[@]}"; do
  assert_contains ".github/workflows/ci.yml" "$marker"
done

for service in "${expected_services[@]}"; do
  assert_contains ".github/workflows/ci.yml" "service: $service"
  assert_contains ".github/workflows/ci.yml" "path: services/$service"
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
assert_not_contains "infra/docker/service.Dockerfile" "# syntax="
assert_not_contains "infra/docker/service.Dockerfile" "docker/dockerfile"
assert_contains "infra/README.md" "service.Dockerfile"
assert_contains "CONTRIBUTING.md" "python -m pip install -r requirements-dev.txt"
assert_contains "README.md" "actions/workflows/ci.yml"

echo "OK: CI/CD contract for issue #9 is configured"
