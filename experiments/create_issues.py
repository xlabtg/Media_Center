#!/usr/bin/env python3
"""Генератор GitHub-issue для плана разработки НМЦ.

Создаёт иерархию: мастер-эпик → эпики этапов → эпики сервисов → задачи.
Идемпотентно: при повторном запуске существующие issue (по точному заголовку)
обновляются (body/labels/milestone), а не дублируются.

Использование:
  DRY=1 python3 experiments/create_issues.py   # предпросмотр без создания
  python3 experiments/create_issues.py          # создание/обновление
"""
import json
import os
import subprocess
import tempfile

REPO = "xlabtg/Media_Center"
DOC_BASE = f"https://github.com/{REPO}/blob/main"
DRY = os.environ.get("DRY") == "1"

MS = {
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
STAGE_LABEL = {
    0: "stage:0-discovery", 1: "stage:1-foundation", 2: "stage:2-core-services",
    3: "stage:3-extended-modules", 4: "stage:4-clients-ux", 5: "stage:5-integrations",
    6: "stage:6-qa-security", 7: "stage:7-pilot", 8: "stage:8-scale-ops",
}


def sh(args):
    return subprocess.run(args, capture_output=True, text=True)


def gh_json(args):
    r = sh(["gh"] + args)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return json.loads(r.stdout)


def labels_for(n):
    labs = [f"type:{n['type']}", f"priority:{n['prio']}"]
    if n.get("stage") is not None:
        labs.append(STAGE_LABEL[n["stage"]])
    labs += [f"area:{a}" for a in n.get("areas", [])]
    labs += [f"component:{c}" for c in n.get("comps", [])]
    return labs


def doc_links(docs):
    return [f"- [{d}]({DOC_BASE}/{d})" for d in docs]


def task_body(n, parent_title):
    L = [n["desc"], ""]
    L += ["## 🎯 Цель", n["goal"], ""]
    L += ["## 📦 Объём работ"] + [f"- {x}" for x in n["scope"]] + [""]
    L += ["## ✅ Критерии приёмки"] + [f"- [ ] {x}" for x in n["acc"]] + [""]
    if n.get("tech"):
        L += ["## 🛠 Технические детали"] + [f"- {x}" for x in n["tech"]] + [""]
    if n.get("deps"):
        L += ["## 🧩 Зависимости"] + [f"- {x}" for x in n["deps"]] + [""]
    if n.get("docs"):
        L += ["## 🔗 Документация"] + doc_links(n["docs"]) + [""]
    pid = f" · Эпик: {parent_title}" if parent_title else ""
    st = MS[n["stage"]] if n.get("stage") is not None else "—"
    L += ["---", f"<sub>Внутренний ID: <code>{n['key']}</code> · Этап: {st}{pid}</sub>"]
    return "\n".join(L)


def epic_body(n, child_refs):
    L = [n["desc"], ""]
    L += ["## 🎯 Цель", n["goal"], ""]
    if n.get("extra"):
        L += [n["extra"], ""]
    if n.get("scope"):
        L += ["## 📦 Объём"] + [f"- {x}" for x in n["scope"]] + [""]
    if child_refs:
        L += ["## 📋 Состав задач"] + [f"- [ ] #{num} — {t}" for num, t in child_refs] + [""]
    L += ["## ✅ Критерии завершения"] + [f"- [ ] {x}" for x in n["exit"]] + [""]
    if n.get("docs"):
        L += ["## 🔗 Документация"] + doc_links(n["docs"]) + [""]
    st = MS[n["stage"]] if n.get("stage") is not None else "Мастер-план"
    L += ["---", f"<sub>Внутренний ID: <code>{n['key']}</code> · {st}</sub>"]
    return "\n".join(L)


existing = {}
created = {}


def load_existing():
    data = gh_json(["issue", "list", "--repo", REPO, "--state", "all",
                    "--limit", "500", "--json", "number,title"])
    for it in data:
        existing[it["title"]] = it["number"]


def write_tmp(body):
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(body)
    f.close()
    return f.name


def ensure(n, body):
    title = n["title"]
    labs = labels_for(n)
    if title in existing:
        num = existing[title]
        if not DRY:
            args = ["gh", "issue", "edit", str(num), "--repo", REPO,
                    "--body-file", write_tmp(body)]
            for l in labs:
                args += ["--add-label", l]
            if n.get("stage") is not None:
                args += ["--milestone", MS[n["stage"]]]
            r = sh(args)
            if r.returncode != 0:
                print("  EDIT ERR", title, r.stderr.strip())
        print(f"= #{num} {title}")
        return num
    if DRY:
        print(f"+ (dry) {title}  [{', '.join(labs)}]")
        return 0
    args = ["gh", "issue", "create", "--repo", REPO, "--title", title,
            "--body-file", write_tmp(body)]
    for l in labs:
        args += ["--label", l]
    if n.get("stage") is not None:
        args += ["--milestone", MS[n["stage"]]]
    r = sh(args)
    if r.returncode != 0:
        print("  CREATE ERR", title, r.stderr.strip())
        raise SystemExit(1)
    url = r.stdout.strip().splitlines()[-1]
    num = int(url.rsplit("/", 1)[-1])
    existing[title] = num
    print(f"+ #{num} {title}")
    return num


def build_and_create(n, parent_title=None):
    child_refs = []
    for c in n.get("children", []):
        cnum = build_and_create(c, n["title"])
        child_refs.append((cnum, c["title"]))
    body = epic_body(n, child_refs) if n["type"] == "epic" else task_body(n, parent_title)
    num = ensure(n, body)
    created[n["key"]] = num
    return num


# ============================ ДАННЫЕ ПЛАНА ============================
from plan_data import PLAN  # noqa: E402


def main():
    if not DRY:
        load_existing()
    build_and_create(PLAN)
    if not DRY:
        with open("experiments/issue_map.json", "w", encoding="utf-8") as f:
            json.dump(created, f, ensure_ascii=False, indent=2)
        print(f"\nСоздано/обновлено: {len(created)} issue. Карта: experiments/issue_map.json")


if __name__ == "__main__":
    main()
