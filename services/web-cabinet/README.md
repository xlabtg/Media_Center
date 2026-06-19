# Web Cabinet

Сервис личного кабинета пайщика для #67. Он собирает tenant-scoped проекцию
вклада, баланса МСЦ, истории операций, контента и реферальных ссылок в формат,
удобный для клиентского веб-экрана.

## Интерфейсы

- `create_web_cabinet_app(config)` собирает FastAPI-приложение Web Cabinet.
- `GET /cabinet/overview?period=<YYYY-MM>` возвращает JSON-сводку текущего
  пайщика или, для ролей Совета/Президиума/Правления, указанного `member_id`.
- `GET /cabinet?period=<YYYY-MM>` возвращает адаптивный HTML-экран с теми же
  данными.

## Данные

`InMemoryWebCabinetRepository` хранит кабинетные projection-записи для ранней
интеграции и тестов:

- `CabinetContributionRecord` — баллы, Кв, доля распределения и количество
  событий вклада за период;
- `CabinetContentRecord` — контент участника, платформы, баллы и
  реферальные ссылки L1/L2/L3.

Баланс и история операций читаются из `InMemoryWalletRepository`, поэтому
значения МСЦ в кабинете соответствуют контракту Wallet Module.

## Безопасность

- Все запросы проходят через JWT tenant context и `X-Tenant-Id`.
- `member_full` и `member_assoc` читают только собственный кабинет.
- `council`, `presidium`, `board` могут читать кабинет указанного участника в
  пределах своего tenant.
- Tenant-isolation контракт #67: подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`, а данные другого tenant не попадают в
  JSON и HTML.
