# Case study: issue #213 — «Полный детальный план разработки 2»

Глубокий разбор issue [#213](https://github.com/xlabtg/Media_Center/issues/213) и подготовка «Плана 2» — нового набора детальных GitHub-issues для доведения контейнеризации НМЦ до production-grade уровня.

## Что просили (issue #213)

1. Детально изучить материалы из чата Qwen перед планированием.
2. Создать полный профессиональный «релиз»: все этапы, привязанные к тегам и меткам, чтобы команда могла начать полноценную разработку.
3. Задачи отражают полный цикл разработки, шаг за шагом; запланировать максимум issues, чтобы система «побила конкурентов по всем метрикам».
4. Собрать данные в `./docs/case-studies/issue-{id}`, провести глубокий case-study (+ онлайн-факты), перечислить все требования, предложить решения по каждому.
5. Проверить существующие компоненты/библиотеки.
6. Все issues — нативные sub-issues #213, со связями `blocked_by` через `gh`.
7. Каждый issue максимально детален (чтобы справился даже слабый AI-агент).
8. Сделать всё в рамках одной ревизии.

## Структура case-study

| Файл | Содержание |
| --- | --- |
| [`01-requirements.md`](01-requirements.md) | Полный разбор требований: REQ-M1..M12 (процесс), REQ-1..12 (функциональные, 30 атомарных пунктов), REQ-N1..N5 (нефункциональные) |
| [`02-gap-analysis.md`](02-gap-analysis.md) | Текущее состояние репозитория vs требования, со ссылками на файлы и статусами ✅/🟡/❌ |
| [`03-research-and-libraries.md`](03-research-and-libraries.md) | Онлайн-исследование (multi-stage, hardening, S2S/SPIFFE, supply-chain, FastAPI health/logging, semver/GHCR, DORA) + реестр переиспользуемых компонентов |
| [`04-competitive-analysis.md`](04-competitive-analysis.md) | Конкуренты, оси сравнения, целевые метрики превосходства (REQ-N1..N5) |
| [`05-solution-plan.md`](05-solution-plan.md) | План «Этап 9»: 6 эпиков → 33 задачи, граф зависимостей, матрица трассируемости REQ→issue |
| [`metrics/dora-data-sources.md`](metrics/dora-data-sources.md) | Контракт источников данных DORA для issue #251: CI/CD events, incidents и Prometheus recording rules |
| [`metrics/competitive-metrics-matrix.md`](metrics/competitive-metrics-matrix.md) | Живая матрица конкурентных метрик для F3 / #253 в составе Эпик F / issue #255: текущие и целевые значения, F1/F2 evidence, SLO evidence и release-процесс обновления |
| [`sources/`](sources/) | Первоисточники: транскрипт чата Qwen (`qwen-chat-transcript.md`) и сырой дамп (`qwen-chat-raw-innertext.txt`) |

## Итог планирования

- **6 эпиков (A–F)** и **33 задачи** = **39 новых issue**, milestone **«Этап 9»**, метка `stage:9-prod-containerization`.
- Иерархия: `#213 → эпики → задачи` (нативные sub-issues GitHub).
- Зависимости: связи `blocked_by` по графу `A → {B→C, D}; E ⊃ {A,B,D}; F ⊃ {B,C,E}`.
- Покрытие: все 12 функциональных требований источника + 5 нефункциональных + 12 мета-требований процесса.
- Эпик F / issue #255 закрывает Операционное превосходство через DORA, F2
  budget gates, competitive metrics matrix и SLO/error budget; сквозной
  contract-тест закреплён в
  `tests/test_stage9_epic_f_issue255_contract.py`.

## Генерация issues

Скрипты в [`../../../experiments/`](../../../experiments/):
- `plan2_data.py` — данные узлов плана (эпики/задачи: контекст, файлы, критерии, тесты, DoD, зависимости).
- `create_stage9_issues.py` — идемпотентное создание milestone/метки/issues, линковка sub-issues и `blocked_by`, запись `issue_map_stage9.json`.

Предпросмотр без записи: `DRY=1 python experiments/create_stage9_issues.py`.

## Ключевые источники (полный список — в `03-research-and-libraries.md`)

- DORA-метрики 2025: [dora.dev](https://dora.dev/guides/dora-metrics/), [RDEL #115](https://rdel.substack.com/p/rdel-115-what-are-the-2025-benchmarks)
- Multi-stage/distroless: [Data Build Company](https://databuildcompany.com/reducing-docker-image-sizes-with-multi-stage-builds-and-distroless/), [OneUptime](https://oneuptime.com/blog/post/2026-01-16-docker-reduce-image-size/view)
- Hardening: [BetterLink non-root](https://eastondev.com/blog/en/posts/dev/20251218-docker-security-nonroot/), [HackTricks no-new-privs](https://hacktricks.wiki/en/linux-hardening/privilege-escalation/container-security/protections/no-new-privileges.html)
- S2S/SPIFFE: [spiffe.io](https://spiffe.io/docs/latest/spire-about/spire-concepts/), [Red Hat](https://www.redhat.com/en/topics/security/spiffe-and-spire)
- Supply-chain: [Sigstore/SLSA (AquilaX)](https://aquilax.ai/blog/supply-chain-artifact-signing-slsa), [SLSA L3 (OneUptime)](https://oneuptime.com/blog/post/2026-02-09-slsa-level3-build-provenance/view)
