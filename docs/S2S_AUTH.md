# Service-to-service авторизация

- **Связанные issue:** [#246](https://github.com/xlabtg/Media_Center/issues/246), [#245](https://github.com/xlabtg/Media_Center/issues/245)
- **Код:** [`libs/shared/s2s_auth.py`](../libs/shared/s2s_auth.py)
- **ADR эволюции:** [ADR-0010](adr/0010-spiffe-mtls-s2s.md)

Документ фиксирует production baseline для внутренних вызовов между сервисами
НМЦ и для `/admin/*` endpoint'ов. Цель: каждый сервисный запрос имеет
проверяемую workload identity, привязку к методу и пути, защиту от Replay и
timing-safe проверку подписи.

## Текущий контракт

`get_s2s_auth()` выбирает метод явно через `S2S_AUTH_METHOD` или автоматически
по доступным credential'ам:

| Метод | `S2S_AUTH_METHOD` | Credential | Основная проверка |
| --- | --- | --- | --- |
| Kubernetes ServiceAccount | `kubernetes_sa` | projected ServiceAccount token | `TokenReview` Kubernetes API или локальная проверка JWT через `OIDC issuer` и public key |
| RSA JWT | `rsa_key` | private/public key по путям из env | RS256 JWT с `iss`, `aud`, `exp`, `iat`, `nonce`, `method`, `path` |
| Shared secret | `shared_secret` | общий секрет из env/secret provider | HMAC-SHA256 по `method`, `path`, `timestamp`, `nonce`, `service_name` |

Все методы используют заголовки:

| Заголовок | Роль |
| --- | --- |
| `X-S2S-Method` | Явный метод: `kubernetes_sa`, `rsa_key` или `shared_secret`. |
| `X-S2S-Service` | Имя вызывающего сервиса; для k8s итоговая identity берётся из проверенного token subject. |
| `X-S2S-Timestamp` | Unix timestamp запроса. Проверяется окном `S2S_REPLAY_WINDOW_SECONDS`. |
| `X-S2S-Nonce` | Одноразовый nonce внутри окна replay. |
| `Authorization: Bearer ...` | Projected SA token или RS256 JWT для `kubernetes_sa`/`rsa_key`. |
| `X-S2S-Signature` | Полноразмерная 64-hex HMAC-SHA256 подпись для `shared_secret`. |

`require_s2s()` кладёт проверенную `S2SIdentity` в `request.state.s2s_identity`.
`create_base_app()` устанавливает эту проверку на `/admin/*`, включая
`/admin/log-level`.

## Метод `kubernetes_sa`

Kubernetes baseline использует короткоживущий projected ServiceAccount token,
смонтированный в pod. При проверке сервис не доверяет факту наличия заголовка:

1. читает bearer token из `Authorization`;
2. валидирует token через `TokenReview` с ожидаемой `audience`, либо через
   локальный JWT decode с `OIDC issuer` и `S2S_K8S_OIDC_PUBLIC_KEY_PATH`;
3. проверяет `audience` и, если настроен, `issuer`;
4. извлекает service identity из `system:serviceaccount:<namespace>:<name>`;
5. применяет `timestamp + nonce` replay guard.

Минимальные env:

```text
S2S_AUTH_METHOD=kubernetes_sa
S2S_K8S_TOKEN_PATH=/var/run/secrets/kubernetes.io/serviceaccount/token
S2S_AUDIENCE=nmc-services
S2S_K8S_ISSUER=https://kubernetes.default.svc
S2S_K8S_TOKENREVIEW_URL=https://kubernetes.default.svc/apis/authentication.k8s.io/v1/tokenreviews
S2S_K8S_CA_PATH=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
```

## Метод `rsa_key`

RSA fallback предназначен для окружений, где Kubernetes identity ещё недоступна,
но можно безопасно доставить key material. Исходящий сервис подписывает RS256 JWT
с коротким TTL (`S2S_TOKEN_TTL_SECONDS`). Входящий сервис проверяет:

- public key, `iss`, `aud`, `exp`, `nbf` и `iat`;
- совпадение JWT claims `method`, `path`, `nonce` с HTTP-запросом;
- `timestamp + nonce` replay guard.

Минимальные env:

```text
S2S_AUTH_METHOD=rsa_key
S2S_RSA_PRIVATE_KEY_PATH=/run/secrets/s2s-private.pem
S2S_RSA_PUBLIC_KEY_PATH=/run/secrets/s2s-public.pem
S2S_RSA_ISSUER=nmc-s2s
S2S_RSA_AUDIENCE=nmc-services
S2S_TOKEN_TTL_SECONDS=60
```

## Метод `shared_secret`

Shared secret остаётся последним fallback для локальной разработки и простых
standalone-окружений. Подпись строится как HMAC-SHA256 по canonical payload:

```text
METHOD
/path
timestamp
nonce
service-name
```

Требования:

- секрет хранится только в env/secret provider, не в репозитории;
- подпись всегда полноразмерная HMAC-SHA256 (`64` hex-символа);
- сравнение выполняется через `hmac.compare_digest`, а не через `==`;
- replay отклоняется по `timestamp + nonce`.

## Модель угроз STRIDE

| STRIDE | Угроза S2S | Контрмеры baseline |
| --- | --- | --- |
| Spoofing | Атакующий подделывает сервис или присылает произвольный `X-S2S-Service`. | Проверка bearer token/RSA/HMAC; для k8s service identity берётся из TokenReview/JWT claims, а не из доверенного заголовка. |
| Tampering | Меняется HTTP method, path, nonce или подпись при повторной отправке. | RSA и HMAC подписывают `method` и `path`; неверный `nonce`, `timestamp` или signature даёт `401`. |
| Repudiation | Сервис отрицает административный вызов или смену runtime-настроек. | `S2SIdentity` доступна handler'у и audit/log слою; `/admin/*` не исполняется без валидной identity. |
| Information Disclosure | Утечка SA token, RSA private key или shared secret из env, логов, образа. | Секреты передаются через runtime secret store; `.env.example` содержит только плейсхолдеры; токены и подписи не пишутся в audit-chain. |
| Denial of Service | Злоумышленник перегружает TokenReview или replay cache большим числом nonce. | `S2S_K8S_TOKENREVIEW_TIMEOUT_SECONDS`, короткое replay window, очистка expired nonce; внешние rate limits остаются на Gateway/mesh. |
| Elevation of Privilege | Сервис с широкой ServiceAccount ролью получает доступ к чужим `/admin/*`. | Отдельные ServiceAccount на сервис, Kubernetes RBAC least privilege, deny-by-default policy на уровне service endpoint'ов. |
| Replay | Перехваченный запрос повторяется в пределах TTL. | `X-S2S-Timestamp` ограничивает окно, `X-S2S-Nonce` запоминается на стороне принимающего процесса и повторно отклоняется. |

## Операционные ограничения

- `InMemoryS2SReplayCache` защищает процесс. При нескольких replica нужен внешний
  shared cache или переход на mesh/SPIFFE, где replay и peer identity
  централизуются в инфраструктуре.
- Shared secret не должен быть production-default. Он допустим только как
  fallback до Kubernetes/RSA/SPIFFE и требует регулярной ротации.
- RSA key material должен приходить через secret store и иметь план ротации.
- Kubernetes TokenReview требует сетевой доступ к API server и отдельные RBAC
  права для reviewer token.

## Тестовый контракт

| Файл | Что закрепляет |
| --- | --- |
| `tests/test_s2s_auth_issue242.py` | Реализацию D1: выбор метода, k8s/RSA/secret, replay и базовую timing-safe проверку. |
| `tests/test_config_settings.py` | Env/config D2: методы, secret provider, пути token/key, issuer/audience и TTL. |
| `tests/test_base_server_issue222.py` | D3: `/admin/*` требует валидную S2S подпись. |
| `tests/test_s2s_auth_issue245_contract.py` | D4: все методы, replay, `hmac.compare_digest`, threat model и ADR SPIFFE/mTLS. |
| `tests/test_stage9_epic_d_issue246_contract.py` | Сквозной контракт #246: fallback chain, replay/timing controls, защита `/admin/*` и acceptance snapshot эпика D. |

Локальная проверка:

```bash
python -m pytest tests/test_s2s_auth_issue242.py tests/test_s2s_auth_issue245_contract.py tests/test_stage9_epic_d_issue246_contract.py
```

## Эволюция к SPIFFE/SPIRE и mTLS

Текущая цепочка закрывает REQ-10 без обязательного service mesh. Целевой
production-путь описан в [ADR-0010](adr/0010-spiffe-mtls-s2s.md):

- SPIRE выдаёт workload identity как `JWT-SVID` для HTTP bearer-flow или как
  `X.509-SVID` для прямого mTLS;
- SPIFFE ID становится stable service identity вместо ручного `X-S2S-Service`;
- mTLS с короткоживущими и автоматически ротируемыми SVID убирает долгоживущие
  shared secrets из production-контура;
- текущие методы остаются bootstrap/fallback, пока не завершена rollout-миграция.

## Источники

- [SPIFFE Concepts](https://spiffe.io/docs/latest/spiffe-about/spiffe-concepts/)
- [SPIFFE Working with SVIDs](https://spiffe.io/docs/latest/deploying/svids/)
- [SPIRE Configuring PSAT node attestation](https://spiffe.io/docs/latest/deploying/configuring/)
- [Kubernetes TokenReview API](https://kubernetes.io/docs/reference/kubernetes-api/definitions/token-review-v1-authentication/)
- [Kubernetes ServiceAccount administration](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/)
