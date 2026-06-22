#!/usr/bin/env bash
# Локальная валидация артефактов issue #213 («Полный детальный план разработки 2»).
# Проверяет наличие case-study, генератора плана и корректность карты issue.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "нет обязательного файла: $path"
}

assert_contains() {
  local path="$1"
  local pattern="$2"
  grep -Fq "$pattern" "$path" || fail "в $path не найден маркер: $pattern"
}

CS="docs/case-studies/issue-213"

# 1. Case-study: индекс, разделы анализа и первоисточники.
required_files=(
  "$CS/README.md"
  "$CS/01-requirements.md"
  "$CS/02-gap-analysis.md"
  "$CS/03-research-and-libraries.md"
  "$CS/04-competitive-analysis.md"
  "$CS/05-solution-plan.md"
  "$CS/sources/qwen-chat-transcript.md"
  "$CS/sources/qwen-chat-raw-innertext.txt"
  "$CS/assets/qwen-chat-issue-213.png"
)
for file in "${required_files[@]}"; do
  assert_file "$file"
done

# 2. Скрипты генерации плана и карта результата.
assert_file "experiments/plan2_data.py"
assert_file "experiments/create_stage9_issues.py"
assert_file "experiments/issue_map_stage9.json"

# 3. Ключевые маркеры содержания case-study.
assert_contains "$CS/01-requirements.md" "REQ-1"
assert_contains "$CS/01-requirements.md" "REQ-12"
assert_contains "$CS/01-requirements.md" "REQ-N1"
assert_contains "$CS/README.md" "stage:9-prod-containerization"
assert_contains "$CS/README.md" "39 новых issue"
assert_contains "$CS/05-solution-plan.md" "Этап 9"
assert_contains "$CS/04-competitive-analysis.md" "REQ-N1"

# 4. Структура данных плана: 6 эпиков A–F и milestone «Этап 9».
plan_markers=(
  "PARENT_ISSUE = 213"
  "STAGE9_LABEL = "
  'EPIC_A'
  'EPIC_B'
  'EPIC_C'
  'EPIC_D'
  'EPIC_E'
  'EPIC_F'
  "PLAN2 = [EPIC_A, EPIC_B, EPIC_C, EPIC_D, EPIC_E, EPIC_F]"
)
for marker in "${plan_markers[@]}"; do
  assert_contains "experiments/plan2_data.py" "$marker"
done

# 5. Карта issue: parent=213, milestone «Этап 9», ровно 39 issue, эпики A–F.
python3 - <<'PY'
import json
import sys

with open("experiments/issue_map_stage9.json", encoding="utf-8") as f:
    data = json.load(f)

errors = []
if data.get("parent") != 213:
    errors.append(f"parent != 213: {data.get('parent')}")
if not str(data.get("milestone", "")).startswith("Этап 9"):
    errors.append(f"milestone не начинается с «Этап 9»: {data.get('milestone')!r}")

issues = data.get("issues", {})
if len(issues) != 39:
    errors.append(f"ожидалось 39 issue, найдено {len(issues)}")

for epic in ("A", "B", "C", "D", "E", "F"):
    if epic not in issues:
        errors.append(f"в карте нет эпика {epic}")

for key, node in issues.items():
    if not isinstance(node.get("number"), int):
        errors.append(f"{key}: number не int")
    if not isinstance(node.get("id"), int):
        errors.append(f"{key}: id не int")

if errors:
    print("FAIL: issue_map_stage9.json:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print(f"OK: карта issue корректна ({len(issues)} issue, parent 213, эпики A–F)")
PY

echo "OK: артефакты issue #213 (План 2) на месте и согласованы"
