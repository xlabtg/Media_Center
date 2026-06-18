#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_dir() {
  local path="$1"
  [[ -d "$path" ]] || fail "missing directory: $path"
}

assert_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "missing file: $path"
}

assert_contains() {
  local path="$1"
  local pattern="$2"
  grep -Eq "$pattern" "$path" || fail "missing pattern in $path: $pattern"
}

expected_services=(
  api-gateway
  contribution-ledger
  cglr
  hitl-payout-gateway
  messenger-adapter
  blockchain-auditor
)

assert_dir "services"
assert_dir "libs"
assert_dir "libs/shared"
assert_dir "infra"
assert_dir "docs"

assert_file "LICENSE"
assert_contains "README.md" "License.*AGPL-3\\.0"
assert_contains "README.md" "services/"
assert_contains "README.md" "libs/shared"
assert_contains "README.md" "infra/"

for service in "${expected_services[@]}"; do
  assert_dir "services/$service"
  assert_file "services/$service/README.md"
  assert_contains "services/$service/README.md" "Статус"
  assert_contains "services/$service/README.md" "Назначение"
done

assert_file "libs/shared/README.md"
assert_contains "libs/shared/README.md" "tenant"
assert_contains "libs/shared/README.md" "audit"

assert_file "infra/README.md"
assert_contains "infra/README.md" "docker-compose"
assert_contains "infra/README.md" "observability"

assert_contains "CONTRIBUTING.md" "Conventional Commits"
assert_contains "CONTRIBUTING.md" "issue-<номер>-<краткое-описание>"
assert_contains "CONTRIBUTING.md" "feat\\|fix\\|docs\\|test\\|refactor\\|chore\\|ci\\|perf"

echo "OK: структура репозитория для issue #8 соответствует критериям"
