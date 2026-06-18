#!/usr/bin/env python3
"""Генератор docs/DEVELOPMENT_PLAN.md из plan_data.PLAN и issue_map.json.

Создаёт детальную декомпозицию задач с реальными номерами созданных issue,
чтобы документ и GitHub-issue оставались согласованными.

Запуск из корня репозитория:
  python3 experiments/gen_dev_plan.py
"""
import json
import os

from plan_data import PLAN

REPO = "xlabtg/Media_Center"
ISSUE_BASE = f"https://github.com/{REPO}/issues"
MS_BASE = f"https://github.com/{REPO}/milestone"

# номер milestone в GitHub соответствует stage+1 (этап 0 → milestone #1)
MS_TITLE = {
    0: "Этап 0 — Discovery и фундамент",
    1: "Этап 1 — Базовая инфраструктура и мультитенантность",
    2: "Этап 2 — Ключевые микросервисы",
    3: "Этап 3 — Расширенные модули",
    4: "Этап 4 — Клиентские приложения и UX",
    5: "Этап 5 — Интеграции",
    6: "Этап 6 — QA, безопасность, нагрузка",
    7: "Этап 7 — Пилотный запуск",
    8: "Этап 8 — Масштабирование и эксплуатация",
}

PRIO_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}

with open("experiments/issue_map.json", encoding="utf-8") as f:
    IMAP = json.load(f)


def num(node):
    return IMAP.get(node["key"])


def link(node):
    n = num(node)
    return f"[#{n}]({ISSUE_BASE}/{n})" if n else "—"


def count_tasks(node):
    """Рекурсивно считает листовые задачи (не эпики) под узлом."""
    kids = node.get("children", [])
    if not kids:
        return 1 if node["type"] != "epic" else 0
    return sum(count_tasks(k) for k in kids)


def render_task_row(node, lines):
    prio = PRIO_EMOJI.get(node["prio"], "")
    comps = ", ".join(f"`{c}`" for c in node.get("comps", []))
    areas = ", ".join(f"`area:{a}`" for a in node.get("areas", []))
    meta = " · ".join(x for x in [areas, comps] if x)
    lines.append(f"| {link(node)} | {prio} {node['title']} | `type:{node['type']}` | {meta} |")


def render_epic(epic, lines, level=2):
    n = num(epic)
    hashes = "#" * level
    total = count_tasks(epic)
    lines.append(f"{hashes} {epic['title']} — {link(epic)}")
    lines.append("")
    lines.append(f"> {epic['desc']}")
    lines.append("")
    if epic.get("stage") is not None:
        ms_num = epic["stage"] + 1
        lines.append(f"**Milestone:** [{MS_TITLE[epic['stage']]}]({MS_BASE}/{ms_num}) · "
                     f"**Приоритет:** {PRIO_EMOJI.get(epic['prio'], '')} `{epic['prio']}` · "
                     f"**Задач:** {total}")
        lines.append("")

    # дочерние узлы: разделяем под-эпики и задачи
    sub_epics = [c for c in epic.get("children", []) if c["type"] == "epic"]
    tasks = [c for c in epic.get("children", []) if c["type"] != "epic"]

    if tasks:
        lines.append("| Issue | Задача | Тип | Область / Компонент |")
        lines.append("|-------|--------|-----|---------------------|")
        for t in tasks:
            render_task_row(t, lines)
        lines.append("")

    for se in sub_epics:
        render_epic(se, lines, level + 1)


def main():
    L = []
    L.append("# Детальный план разработки НМЦ")
    L.append("")
    L.append("Документ — единая декомпозиция работ платформы «Народный Медиацентр» (НМЦ) "
             "с прослеживаемостью **этап → эпик → задача**. Каждая строка ссылается на "
             "конкретный GitHub-issue с метками и milestone.")
    L.append("")
    L.append(f"> 🗺 **Точка входа:** {link(PLAN)} — {PLAN['title']}.")
    L.append("> Сводка этапов и сроки — в [ROADMAP.md](ROADMAP.md). "
             "Этот документ генерируется скриптом `experiments/gen_dev_plan.py` "
             "из дерева плана и карты issue.")
    L.append("")

    total = count_tasks(PLAN)
    n_epics = sum(1 for k in IMAP if True)  # placeholder
    L.append("## Сводка")
    L.append("")
    L.append(f"- **Всего issue:** {len(IMAP)}")
    L.append(f"- **Эпиков (этапы + сервисы):** {len(IMAP) - total}")
    L.append(f"- **Задач:** {total}")
    L.append("- **Меток:** type / priority / stage / area / component")
    L.append("- **Этапов (milestones):** 9 (0–8)")
    L.append("")
    L.append("Условные обозначения приоритета: "
             "🔴 critical · 🟠 high · 🟡 medium · 🟢 low.")
    L.append("")
    L.append("---")
    L.append("")

    # Этапы (прямые дети мастер-плана)
    for stage_epic in PLAN["children"]:
        render_epic(stage_epic, L, level=2)
        L.append("---")
        L.append("")

    L.append("<sub>Сгенерировано из `experiments/plan_data.py` и "
             "`experiments/issue_map.json`. Для обновления номеров issue "
             "перезапустите `experiments/gen_dev_plan.py`.</sub>")
    L.append("")

    out = "docs/DEVELOPMENT_PLAN.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"Записано: {out} ({len(L)} строк)")


if __name__ == "__main__":
    main()
