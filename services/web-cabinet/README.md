# Web Cabinet

Сервис личного кабинета пайщика для #67, панели Совета для #68, дашборда KPI
для #69, онбординга для #70, UI голосового ассистента для #72 и
дизайн-системы для #73. Он собирает
tenant-scoped проекцию вклада,
баланса МСЦ, истории операций, контента, реферальных ссылок, HITL-очереди,
метрик Analytics Engine, шагов онбординга, AI-подсказок и Voice-to-Chain
receipt в формат, удобный для клиентского веб-экрана.
Дизайн-система и UI-кит реализованы в `web_cabinet.design_system`: токены,
компоненты, общий CSS layer и HTML showcase `nmc-ui`.

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
- `GET /onboarding/overview` возвращает JSON-сводку "Онбординг": прогресс
  обязательных шагов, согласия, AI-ответы и проверку готовности участника.
- `GET /onboarding` возвращает адаптивный HTML-экран самостоятельного
  онбординга участника.
- `POST /onboarding/assistant/answer` возвращает ответ AI-ассистента на
  типовой вопрос из tenant-scoped базы FAQ.
- `GET /voice-assistant` возвращает адаптивный HTML-экран голосового
  ассистента с записью через MediaRecorder.
- `POST /voice-assistant/transcribe` принимает audio payload из UI, передаёт
  его в `VoiceToChainService` и возвращает transcript, hash evidence,
  `raw_audio_status` и срок TTL-удаления исходного аудио.
- `GET /design-system/tokens` возвращает JSON-контракт "Дизайн-система и
  UI-кит": токены, компоненты, гайдлайны и accessibility baseline.
- `GET /design-system/ui-kit` возвращает HTML-витрину UI-kit с
  `data-design-system="nmc-ui"` и `data-component` markers.

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
- `OnboardingProfileRecord`, `OnboardingStepRecord`,
  `OnboardingConsentRecord` и `OnboardingAssistantAnswerRecord` хранят
  tenant-scoped projection онбординга: окно 12–36 ч, шаги, согласия, readiness
  и ответы AI-ассистента на типовые вопросы.
- UI голосового ассистента не хранит raw audio в Web Cabinet: endpoint
  `/voice-assistant/transcribe` передаёт аудио в `VoiceToChainService`, а
  клиент получает тот же receipt, что и Voice-to-Chain.
- `web_cabinet.design_system` хранит дизайн-токены, Pydantic-модель
  `DesignSystemResponse`, каталог компонентов и render helper-ы для общего
  HTML/CSS слоя.

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
- Онбординг доступен ролям `audience`, `member_assoc`, `member_full`,
  `council`, `presidium`, `board`; участник видит только собственный прогресс,
  а управляющие роли могут открыть участника по `member_id` внутри tenant.
- Tenant-isolation контракт #70: подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`, а шаги, согласия и ответы AI-ассистента
  другого tenant не попадают в JSON, HTML и ответ ассистента.
- Голосовой ассистент доступен ролям `member_assoc`, `member_full`, `board` и
  `council`, как Voice-to-Chain транскрипция.
- Tenant-isolation контракт #72: подмена `X-Tenant-Id` возвращает
  `403 tenant_isolation_violation`, а UI не сохраняет сырой звук в Web Cabinet.
