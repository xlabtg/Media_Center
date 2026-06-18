#!/usr/bin/env bash
# Создание этапов (milestones) для плана разработки НМЦ. Идемпотентно.
set -euo pipefail
REPO="xlabtg/Media_Center"

# Существующие milestones (title -> number)
existing=$(gh api "repos/$REPO/milestones?state=all&per_page=100" --jq '.[].title')

create_ms() { # title description
  if echo "$existing" | grep -qxF "$1"; then
    echo "= уже есть: $1"
  else
    gh api "repos/$REPO/milestones" -f title="$1" -f description="$2" -f state="open" --jq '"✓ #\(.number) \(.title)"'
  fi
}

create_ms "Этап 0 — Discovery и фундамент" "Требования, юр. анализ, архитектура (C4/ADR), модель данных, CI/CD, среда разработки, threat model, UX-прототипы, глоссарий."
create_ms "Этап 1 — Базовая инфраструктура и мультитенантность" "Tenant Isolation Layer, JWT+2FA, RBAC, API Gateway, БД, Redis, RabbitMQ, ChromaDB, S3/MinIO, наблюдаемость, общая библиотека."
create_ms "Этап 2 — Ключевые микросервисы" "Contribution Ledger & Weight Engine, CGLR, HITL Payout Gateway, Unified Messenger Adapter, Private Blockchain Auditor."
create_ms "Этап 3 — Расширенные модули" "Activity Command Center, Neuro-Agent Orchestrator, Voice-to-Chain, Wallet, Analytics Engine, Notification Gateway, Policy Manager, Agentic RAG, XAI."
create_ms "Этап 4 — Клиентские приложения и UX" "Веб-кабинет пайщика, панель Совета (HITL), дашборды, онбординг/AI-ассистент, Telegram-клиент, голосовой UI, дизайн-система."
create_ms "Этап 5 — Интеграции" "Telegram, VK, Dzen, OK (top-10 РФ), платёжные шлюзы РФ, приватная блокчейн-сеть, реестр площадок, anti-blocking."
create_ms "Этап 6 — QA, безопасность, нагрузка" "Стратегия тестирования, тесты мультитенантной изоляции, нагрузочные тесты, pentest, аудит ФЗ-152, e2e HITL, отказоустойчивость."
create_ms "Этап 7 — Пилотный запуск" "Подготовка тенанта (15-25 чел.), сбор KPI, документация участников, поддержка, ретроспектива и план масштабирования."
create_ms "Этап 8 — Масштабирование и эксплуатация" "Мультитенантное масштабирование, SRE (runbooks/SLA/алертинг), backup/DR, маркетплейс тенантов, RL-KPI loop в проде, обучение."

echo "--- Список milestones ---"
gh api "repos/$REPO/milestones?state=all&per_page=100" --jq '.[] | "#\(.number)\t\(.title)"'
