# Каталог tenant'ов и самостоятельное подключение

Статус: baseline для issue #100, этап 8 — масштабирование и эксплуатация.

Документ фиксирует минимальный backend-контракт Tenant Marketplace: публичный
каталог опубликованных tenant-профилей, самостоятельную заявку кооператива на
подключение и обязательную модерацию перед созданием tenant в платформе.
Базовая реализация находится в `libs.shared.tenant_marketplace` и покрыта
контрактом `tests/test_tenant_marketplace_issue100_acceptance_contract.py`.

## Цели #100

| Критерий | Контракт |
|----------|----------|
| Новый tenant подключается по сценарию | Кооператив отправляет `TenantMarketplaceSubmission`, заявка получает статус `submitted`, проходит checklist и после `approve` создаёт tenant-профиль с `resource_plan`. |
| Каталог отображает tenant'ов | `InMemoryTenantMarketplace.list_catalog()` возвращает только `published` профили и не раскрывает `contact_ref`, ПДн или секреты. |
| Подключение проходит модерацию | Только роли `council`, `presidium` и `board` могут принять `TenantMarketplaceDecision`; `request_changes` возвращает заявку на доработку, `reject` закрывает, `approve` переводит в `provisioned`. |

## Публичный API

Целевой REST-контур может быть размещён за API Gateway или выделенным tenant
foundation service. Синхронный контракт:

- GET /tenants/catalog — публичный список `published` профилей с фильтрами
  `region` и `cooperative_type`;
- POST /tenants/applications — самостоятельная подача заявки кооперативом;
- GET /tenants/applications/{application_id} — чтение заявки заявителем или
  модератором;
- POST /tenants/applications/{application_id}/moderation — решение Совета:
  `approve`, `request_changes` или `reject`.

Поля заявки соответствуют `TenantMarketplaceSubmission`: `slug`, `name`,
`region`, `cooperative_type`, `description`, `expected_members`, `capabilities`,
`contact_ref`, `requested_plan` и `checklist`. `contact_ref` обязан ссылаться на
секретный контур (`vault://...`), а публичный каталог публикует только безопасные
поля профиля.

## Сценарий подключения

1. Кооператив заполняет профиль, контакты, политику данных и подтверждает
   предварительную проверку Советом в checklist.
2. Marketplace проверяет уникальность `slug` среди опубликованных профилей и
   активных заявок.
3. До модерации заявка не попадает в каталог.
4. Модератор с ролью `council`, `presidium` или `board` принимает
   `TenantMarketplaceDecision`.
5. При `approve` создаётся tenant profile, статус заявки становится
   `provisioned`, а `InMemoryTenantResourceManager.configure_tenant()` получает
   выбранный `resource_plan`.
6. При `request_changes` заявка остаётся вне каталога; при `reject` slug можно
   использовать в новой заявке.

## Безопасность

- Каталог не публикует `contact_ref`, ПДн, токены, email, телефоны или raw
  evidence: политика `no_pdn_no_secrets`.
- `tenant_id` нового tenant задаётся только доверенным backend-контуром при
  одобрении, а не телом заявки.
- Все resource quotas после одобрения tenant-local и наследуют контракт
  `TenantResourcePlan`.
- Модерация фиксирует `reviewer_subject`, решение, время и комментарий без
  раскрытия секретов.

## Проверка

```bash
pytest tests/test_tenant_marketplace_issue100_acceptance_contract.py
```

Полный gate перед PR остаётся стандартным:

```bash
ruff check .
ruff format --check .
black --check .
mypy .
pytest
```
