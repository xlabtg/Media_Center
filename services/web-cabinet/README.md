# Web Cabinet

Сервис личного кабинета пайщика для #67, панели Совета для #68 и дашборда KPI
для #69. Он собирает tenant-scoped проекцию вклада, баланса МСЦ, истории
операций, контента, реферальных ссылок, HITL-очереди и метрик Analytics Engine
в формат, удобный для клиентского веб-экрана.

## Интерфейсы

- `create_web_cabinet_app(config)` собирает FastAPI-приложение Web Cabinet.
- `GET /cabinet/overview?period=<YYYY-MM>` возвращает JSON-сводку текущего
  пайщика или, для ролей Совета/Президиума/Правления, указанного `member_id`.
- `GET /cabinet?period=<YYYY-MM>` возвращает адаптивный HTML-экран с теми же
  данными.
- `GET /council/panel/overview` возвращает JSON-сводку панели Совета: очередь
  HITL, risk-флаги, окно вето, политики, 2FA и audit timeline.
- `GET /council/panel` возвращает адаптивный HTML-экран "Панель Совета".
- `POST /council/payouts/{payout_id}/veto` накладывает вето в открытом окне.
- `POST /council/payouts/{payout_id}/confirm` подтверждает выплату через TOTP.
- `PUT /council/policies/{key}` меняет политику/порог tenant решением Совета.
- `GET /analytics/dashboard/overview?period=<YYYY-MM|YYYY-Www>` возвращает
  JSON-сводку "Дашборд KPI" с фильтром `category`.
- `GET /analytics/dashboard?period=<YYYY-MM|YYYY-Www>` возвращает адаптивный
  HTML-экран "Дашборд KPI".
- `GET /analytics/dashboard/export?period=<YYYY-MM|YYYY-Www>` выгружает CSV
  отчёт по KPI и агрегатам.

## Данные

`InMemoryWebCabinetRepository` хранит кабинетные projection-записи для ранней
интеграции и тестов:

- `CabinetContributionRecord` — баллы, Кв, доля распределения и количество
  событий вклада за период;
- `CabinetContentRecord` — контент участника, платформы, баллы и
  реферальные ссылки L1/L2/L3.
- `InMemoryCouncilPanelRepository` хранит tenant-scoped аннотации выплат
  (`CouncilPanelPayoutAnnotation`) и audit timeline (`CouncilPanelAuditRecord`)
  для интерфейса Совета.
- `InMemoryAnalyticsRepository` подключается из Analytics Engine и даёт
  дашборду #69 те же KPI и агрегаты, что `GET /analytics/kpi` и
  `GET /analytics/aggregates`.

Баланс и история операций читаются из `InMemoryWalletRepository`, поэтому
значения МСЦ в кабинете соответствуют контракту Wallet Module.
Очередь, вето и 2FA подтверждения переиспользуют managers HITL Payout Gateway,
а политики — `PolicyManager`.

## Безопасность

- Все запросы проходят через JWT tenant context и `X-Tenant-Id`.
- `member_full` и `member_assoc` читают только собственный кабинет.
- `council`, `presidium`, `board` могут читать кабинет указанного участника в
  пределах своего tenant.
- Tenant-isolation контракт #67: подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`, а данные другого tenant не попадают в
  JSON и HTML.
- Панель Совета доступна только роли `council`.
- Tenant-isolation контракт #68: подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`, а HITL/audit данные другого tenant не
  попадают в JSON и HTML.
- Подтверждение выплат из панели требует 2FA-код и не принимает действие без
  `totp_code`.
- Дашборд KPI доступен ролям `council`, `presidium`, `board`, `member_full`,
  `member_assoc`.
- Tenant-isolation контракт #69: подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`, а данные другого tenant не попадают в JSON,
  HTML и CSV.
