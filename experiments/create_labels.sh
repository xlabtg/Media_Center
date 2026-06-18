#!/usr/bin/env bash
# Создание полной таксономии меток (labels) для плана разработки НМЦ.
# Идемпотентно: --force обновляет существующие метки.
set -euo pipefail
REPO="xlabtg/Media_Center"

create() { # name color description
  gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force >/dev/null && echo "✓ $1"
}

# --- type: вид работы (синий) ---
create "type:epic"     "1d76db" "Крупный блок работ: этап или модуль"
create "type:feature"  "1d76db" "Новая функциональность"
create "type:task"     "1d76db" "Конкретная задача"
create "type:research" "1d76db" "Исследование / spike / проектирование"
create "type:docs"     "1d76db" "Документация"
create "type:chore"    "1d76db" "Инфраструктура / обслуживание"
create "type:test"     "1d76db" "Тестирование"
create "type:bug"      "1d76db" "Дефект"

# --- priority: приоритет ---
create "priority:critical" "b60205" "P0 — критический, блокирует"
create "priority:high"     "d93f0b" "P1 — высокий"
create "priority:medium"   "fbca04" "P2 — средний"
create "priority:low"      "0e8a16" "P3 — низкий"

# --- stage: этап SDLC (фиолетовый) ---
create "stage:0-discovery"        "5319e7" "Этап 0 — Discovery и фундамент"
create "stage:1-foundation"       "5319e7" "Этап 1 — Базовая инфраструктура и мультитенантность"
create "stage:2-core-services"    "5319e7" "Этап 2 — Ключевые микросервисы"
create "stage:3-extended-modules" "5319e7" "Этап 3 — Расширенные модули"
create "stage:4-clients-ux"       "5319e7" "Этап 4 — Клиентские приложения и UX"
create "stage:5-integrations"     "5319e7" "Этап 5 — Интеграции"
create "stage:6-qa-security"      "5319e7" "Этап 6 — QA, безопасность, нагрузка"
create "stage:7-pilot"            "5319e7" "Этап 7 — Пилотный запуск"
create "stage:8-scale-ops"        "5319e7" "Этап 8 — Масштабирование и эксплуатация"

# --- area: дисциплина (бирюзовый) ---
create "area:backend"    "006b75" "Backend-разработка"
create "area:frontend"   "006b75" "Frontend-разработка"
create "area:devops"     "006b75" "DevOps / инфраструктура"
create "area:ai-ml"      "006b75" "AI / ML / агенты"
create "area:data"       "006b75" "Данные / БД / модель данных"
create "area:security"   "006b75" "Безопасность"
create "area:compliance" "006b75" "Правовое соответствие"
create "area:design"     "006b75" "Дизайн / UX"
create "area:qa"         "006b75" "Тестирование / контроль качества"
create "area:product"    "006b75" "Продукт / требования"

# --- component: модуль системы (голубой) ---
create "component:contribution-ledger" "c5def5" "Contribution Ledger & Weight Engine"
create "component:cglr"                "c5def5" "Content Generator & Link Router"
create "component:hitl-payout"         "c5def5" "HITL Payout Gateway"
create "component:messenger-adapter"   "c5def5" "Unified Messenger Adapter"
create "component:blockchain-auditor"  "c5def5" "Private Blockchain Auditor"
create "component:voice-to-chain"      "c5def5" "Voice-to-Chain Module"
create "component:neuro-agent"         "c5def5" "Neuro-Agent Orchestrator"
create "component:activity-center"     "c5def5" "Activity Command Center"
create "component:wallet"              "c5def5" "Wallet Module"
create "component:web-cabinet"         "c5def5" "Веб-кабинет / клиентские приложения"
create "component:api-gateway"         "c5def5" "API Gateway / аутентификация"
create "component:tenant-core"         "c5def5" "Мультитенантность / изоляция"
create "component:analytics"           "c5def5" "Analytics Engine"
create "component:notification"        "c5def5" "Notification Gateway"
create "component:infra"               "c5def5" "Инфраструктура / CI/CD / наблюдаемость"

echo "--- Готово ---"
