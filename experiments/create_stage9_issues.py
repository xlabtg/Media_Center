#!/usr/bin/env python3
"""Генератор issue «Этап 9» (План 2) для issue #213.

Делает (идемпотентно):
  1. Создаёт milestone «Этап 9» и метку stage:9-prod-containerization.
  2. Создаёт эпики A–F и их задачи (по точному заголовку), переиспользуя
     шаблоны тела/меток из create_issues.py.
  3. Линкует нативные sub-issues: эпики → #213, задачи → их эпик (Sub-issues API).
  4. Проставляет зависимости blocked_by (Dependencies API) по полю `bdeps`.
  5. Пишет experiments/issue_map_stage9.json.

Использование:
  DRY=1 python3 experiments/create_stage9_issues.py   # предпросмотр без записи
  python3 experiments/create_stage9_issues.py          # создание/обновление
"""
import json

import create_issues as ci
from plan2_data import (
    MILESTONE_DESC,
    MILESTONE_TITLE,
    PARENT_ISSUE,
    PLAN2,
    STAGE9_LABEL,
    STAGE9_LABEL_COLOR,
    STAGE9_LABEL_DESC,
)

REPO = ci.REPO
DRY = ci.DRY

# Расширяем справочники этапов create_issues, чтобы шаблоны тела/меток
# корректно работали для stage=9.
ci.MS[9] = MILESTONE_TITLE
ci.STAGE_LABEL[9] = STAGE9_LABEL


# --------------------------------------------------------------------------- #
# Вспомогательные функции для milestone/метки                                  #
# --------------------------------------------------------------------------- #
def ensure_milestone():
    """Создаёт milestone «Этап 9», если его ещё нет. Возвращает его номер."""
    existing_ms = ci.gh_json(["api", f"repos/{REPO}/milestones?state=all&per_page=100"])
    for m in existing_ms:
        if m["title"] == MILESTONE_TITLE:
            print(f"= milestone #{m['number']} {MILESTONE_TITLE}")
            return m["number"]
    if DRY:
        print(f"+ (dry) milestone {MILESTONE_TITLE}")
        return None
    m = ci.gh_json([
        "api", "--method", "POST", f"repos/{REPO}/milestones",
        "-f", f"title={MILESTONE_TITLE}",
        "-f", f"description={MILESTONE_DESC}",
        "-f", "state=open",
    ])
    print(f"+ milestone #{m['number']} {MILESTONE_TITLE}")
    return m["number"]


def ensure_label():
    """Создаёт/обновляет метку этапа 9 (идемпотентно через --force)."""
    if DRY:
        print(f"+ (dry) label {STAGE9_LABEL}")
        return
    r = ci.sh([
        "gh", "label", "create", STAGE9_LABEL, "--repo", REPO,
        "--color", STAGE9_LABEL_COLOR, "--description", STAGE9_LABEL_DESC, "--force",
    ])
    if r.returncode == 0:
        print(f"+ label {STAGE9_LABEL}")
    else:
        print(f"! label {STAGE9_LABEL}: {r.stderr.strip()}")


# --------------------------------------------------------------------------- #
# REST id и линковка (sub-issues + dependencies)                               #
# --------------------------------------------------------------------------- #
_id_cache = {}


def rest_id(num):
    """REST integer id issue по его номеру (с кешированием)."""
    if num not in _id_cache:
        _id_cache[num] = ci.gh_json(["api", f"repos/{REPO}/issues/{num}", "--jq", ".id"])
    return _id_cache[num]


def _safe_list(path):
    """GET list-эндпоинт, возвращает [] при ошибке (например, пустой связке)."""
    r = ci.sh(["gh", "api", path])
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []


def link_sub_issue(parent_num, child_num):
    """Делает child_num нативным sub-issue parent_num (идемпотентно)."""
    present = [s.get("number") for s in _safe_list(f"repos/{REPO}/issues/{parent_num}/sub_issues")]
    if child_num in present:
        print(f"  = sub-issue #{child_num} ⊂ #{parent_num}")
        return True
    cid = rest_id(child_num)
    r = ci.sh([
        "gh", "api", "--method", "POST",
        f"repos/{REPO}/issues/{parent_num}/sub_issues",
        "-F", f"sub_issue_id={cid}",
    ])
    if r.returncode == 0:
        print(f"  + sub-issue #{child_num} ⊂ #{parent_num}")
        return True
    print(f"  ! sub-issue #{child_num} ⊂ #{parent_num}: {r.stderr.strip()}")
    return False


def link_blocked_by(num, blocker_num):
    """Помечает num как blocked_by blocker_num (идемпотентно)."""
    present = [d.get("number") for d in _safe_list(f"repos/{REPO}/issues/{num}/dependencies/blocked_by")]
    if blocker_num in present:
        print(f"  = #{num} blocked_by #{blocker_num}")
        return True
    bid = rest_id(blocker_num)
    r = ci.sh([
        "gh", "api", "--method", "POST",
        f"repos/{REPO}/issues/{num}/dependencies/blocked_by",
        "-F", f"issue_id={bid}",
    ])
    if r.returncode == 0:
        print(f"  + #{num} blocked_by #{blocker_num}")
        return True
    print(f"  ! #{num} blocked_by #{blocker_num}: {r.stderr.strip()}")
    return False


def all_nodes():
    """Перебор всех узлов (эпики + задачи)."""
    for epic in PLAN2:
        yield epic
        for t in epic.get("children", []):
            yield t


# --------------------------------------------------------------------------- #
# main                                                                          #
# --------------------------------------------------------------------------- #
def main():
    ensure_milestone()
    ensure_label()

    if not DRY:
        ci.load_existing()

    # Создание эпиков и их задач (задачи создаются первыми внутри build_and_create).
    for epic in PLAN2:
        ci.build_and_create(epic)

    nums = dict(ci.created)  # key -> issue number

    if DRY:
        print("\n— (dry) Планируемые нативные sub-issues —")
        for epic in PLAN2:
            print(f"  #{PARENT_ISSUE} ⊃ {epic['key']}  ({epic['title']})")
            for t in epic.get("children", []):
                print(f"      {epic['key']} ⊃ {t['key']}  ({t['title']})")
        print("\n— (dry) Планируемые зависимости blocked_by —")
        for node in all_nodes():
            for bk in node.get("bdeps", []):
                print(f"  {node['key']} blocked_by {bk}")
        total = sum(1 for _ in all_nodes())
        print(f"\n(dry) Всего узлов: {total} ({len(PLAN2)} эпиков + задачи).")
        return

    # Нативные sub-issues: эпики под #213, задачи под их эпиком.
    print("\n— Линковка нативных sub-issues —")
    for epic in PLAN2:
        link_sub_issue(PARENT_ISSUE, nums[epic["key"]])
        for t in epic.get("children", []):
            link_sub_issue(nums[epic["key"]], nums[t["key"]])

    # Зависимости blocked_by.
    print("\n— Проставление зависимостей blocked_by —")
    for node in all_nodes():
        for bk in node.get("bdeps", []):
            if bk not in nums:
                print(f"  ! пропуск {node['key']} blocked_by {bk}: ключ не найден")
                continue
            link_blocked_by(nums[node["key"]], nums[bk])

    # Карта результатов.
    out = {k: {"number": nums[k], "id": rest_id(nums[k])} for k in nums}
    with open("experiments/issue_map_stage9.json", "w", encoding="utf-8") as f:
        json.dump(
            {"parent": PARENT_ISSUE, "milestone": MILESTONE_TITLE, "issues": out},
            f, ensure_ascii=False, indent=2,
        )
    print(f"\nГотово: {len(out)} issue. Карта: experiments/issue_map_stage9.json")


if __name__ == "__main__":
    main()
