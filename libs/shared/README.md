# Shared Library

**Статус:** каркас общей библиотеки, реализация запланирована в этапе 1.

## Назначение

`libs/shared` будет содержать общий Python-код, который нужен нескольким
сервисам и не принадлежит одному домену. Библиотека не должна становиться
скрытым монолитом: доменная логика остаётся в соответствующих `services/*`.

## Будущие области

- tenant context и middleware contracts;
- общий error envelope, включая `tenant_isolation_violation`;
- audit utilities для SHA256-хэшей и correlation metadata;
- Pydantic-модели, используемые в межсервисных контрактах;
- базовые helpers для конфигурации, логов и observability.

## Правила

1. Новый код попадает сюда только после проверки, что он нужен двум и более
   сервисам.
2. Shared API должен быть стабильнее внутренних API сервисов.
3. Любой helper для tenant или audit обязан сохранять инварианты из
   [SECURITY.md](../../docs/SECURITY.md) и [DATA_MODEL.md](../../docs/DATA_MODEL.md).
