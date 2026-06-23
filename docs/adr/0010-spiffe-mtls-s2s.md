# ADR-0010: Целевой переход S2S на SPIFFE/SPIRE и mTLS

- **Статус:** Accepted
- **Дата:** 2026-06-23
- **Связанный issue:** [#245](https://github.com/xlabtg/Media_Center/issues/245)

## Контекст

Эпик D Этапа 9 уже вводит S2S baseline без обязательной mesh-инфраструктуры:
Kubernetes projected ServiceAccount token, RSA JWT и shared secret fallback.
Это закрывает ближайшие требования `/admin/*` и внутренних вызовов, но оставляет
production-риски:

- долгоживущие shared secrets требуют отдельной ротации и создают secret sprawl;
- RSA key material нужно доставлять и ротировать вне приложения;
- Kubernetes ServiceAccount token подтверждает workload в кластере, но сам по себе
  не даёт mTLS и единого cross-cluster trust domain;
- in-process replay cache не синхронизируется между replica.

Для промышленной эксплуатации нужен стандарт workload identity с короткоживущими
автоматически ротируемыми credential'ами, пригодный для mTLS и policy-based
authorization.

## Решение

Принимаем SPIFFE/SPIRE как целевую архитектуру S2S identity:

1. Текущая цепочка `kubernetes_sa -> rsa_key -> shared_secret` остаётся baseline
   для bootstrap, локальной разработки и окружений без SPIRE.
2. Для production-k8s вводится SPIRE Server/Agent с trust domain
   `spiffe://media-center.local` или доменом конкретной инсталляции.
3. SPIRE Agent в Kubernetes аттестуется через PSAT, то есть projected
   ServiceAccount token, проверяемый SPIRE Server.
4. Сервисная identity задаётся SPIFFE ID вида
   `spiffe://media-center.local/ns/<namespace>/sa/<service-account>`.
5. Для HTTP/REST без прозрачного mTLS используется `JWT-SVID` как bearer token,
   валидируемый принимающим сервисом или Envoy/OPA policy layer.
6. Для прямых внутренних вызовов и mesh-сценариев используется `X.509-SVID` и
   mTLS; peer identity берётся из URI SAN SPIFFE ID.
7. Authorization policy строится по SPIFFE ID, HTTP method/path и service role,
   а не по произвольному `X-S2S-Service`.

## План миграции

| Фаза | Суть | Gate |
| --- | --- | --- |
| 0 | Текущий baseline: k8s/RSA/secret, replay, timing-safe HMAC. | `tests/test_s2s_auth_issue245_contract.py` зелёный. |
| 1 | Развернуть SPIRE в dev/stage k8s и описать trust domain, registration entries, ServiceAccount mapping. | Workloads получают SVID через Workload API. |
| 2 | Добавить валидатор `JWT-SVID` рядом с текущими методами S2S. | Принимающий сервис проверяет issuer/audience/trust bundle и SPIFFE ID. |
| 3 | Включить `X.509-SVID` mTLS для внутренних вызовов через Envoy/SDS или native TLS client/server. | Peer SPIFFE ID виден в access/audit logs, policy tests проходят. |
| 4 | Запретить `shared_secret` в production и оставить RSA только как break-glass fallback. | CI/config gate блокирует production deploy с shared secret. |

## Последствия

Плюсы:

- короткоживущие автоматически ротируемые SVID снижают риск утечки credential'ов;
- mTLS даёт проверку peer identity на транспортном уровне;
- SPIFFE ID унифицирует identity между сервисами и кластерами;
- policy layer может авторизовать не только сервис, но и конкретный method/path.

Минусы и риски:

- появляется операционная зависимость от SPIRE Server/Agent;
- нужны registration entries, trust bundle rollout и мониторинг срока жизни SVID;
- sidecar/mesh может добавить latency и усложнить отладку;
- rollout требует периода двойной поддержки текущего S2S и SPIFFE.

## Не входит в это решение

- Развёртывание SPIRE Server/Agent в текущем PR.
- Выбор конкретного service mesh.
- Удаление существующих k8s/RSA/shared-secret методов до завершения production
  миграции.

## Связанные документы

- [docs/S2S_AUTH.md](../S2S_AUTH.md)
- [issue-213/03-research-and-libraries.md](../case-studies/issue-213/03-research-and-libraries.md)
- [issue-213/05-solution-plan.md](../case-studies/issue-213/05-solution-plan.md)
- [SPIFFE Concepts](https://spiffe.io/docs/latest/spiffe-about/spiffe-concepts/)
- [SPIFFE Working with SVIDs](https://spiffe.io/docs/latest/deploying/svids/)
- [SPIRE Configuring PSAT node attestation](https://spiffe.io/docs/latest/deploying/configuring/)
