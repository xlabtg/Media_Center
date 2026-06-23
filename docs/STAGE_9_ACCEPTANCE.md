# Этап 9 - Acceptance Snapshot

Этот snapshot фиксирует закрытие эпика C из issue #241: CI/CD и публикация
сервисных образов в GHCR, эпика D из issue #246: Service-to-service
авторизация для внутренних вызовов и `/admin/*`, а также задачи E1 из issue
#247: локальный docker-compose с приложенческими сервисами, задачи E2 из
issue #248: k8s/Helm-манифесты для раскатки сервисов, и задачи E3 из issue
#249: раскатка единого runtime-контракта на все 14 продуктовых сервисов.
Остальные задачи Этапа 9 ведутся отдельными родительскими issue из плана #213.

## Статус эпика C

| Задача | Статус | Проверяемые артефакты |
| --- | --- | --- |
| C1 | Выполнено: semver и build metadata берутся из git-тегов. | `.github/scripts/resolve-build-metadata.sh`, `.github/workflows/build-service.yml`, `tests/test_semver_git_tags_issue234_contract.py` |
| C2 | Выполнено: GHCR-публикация использует `media-center-<service>`, semver, major.minor, sha и latest для релизных tag push. | `.github/workflows/build-service.yml`, `docs/adr/0009-ghcr-image-naming.md`, `tests/test_ghcr_publish_issue235_contract.py` |
| C3 | Выполнено: сервисные образы собираются как `linux/amd64,linux/arm64` через QEMU/buildx с gha-cache. | `.github/workflows/build-service.yml`, `tests/test_multiarch_build_issue236_contract.py` |
| C4 | Выполнено: SBOM генерируется Syft в SPDX JSON и публикуется как artifact/attestation. | `.github/workflows/build-service.yml`, `tests/test_sbom_issue237_contract.py` |
| C5 | Выполнено: digest опубликованного образа подписывается cosign keyless, SLSA provenance прикрепляется через GitHub attestations. | `.github/workflows/build-service.yml`, `docs/operations/image-signing-verification.md`, `tests/test_cosign_slsa_issue238_contract.py` |
| C6 | Выполнено: Trivy image scan запускается до GHCR login/build-push и блокирует HIGH/CRITICAL. | `.github/workflows/build-service.yml`, `tests/test_trivy_image_scan_issue239_contract.py` |
| C7 | Выполнено: сборка сервиса вынесена в reusable workflow `workflow_call`, matrix покрывает все продуктовые сервисы. | `.github/workflows/ci.yml`, `.github/workflows/build-service.yml`, `tests/test_reusable_build_service_issue240_contract.py` |

## Статус эпика D

| Задача | Статус | Проверяемые артефакты |
| --- | --- | --- |
| D1 | Выполнено: `libs/shared/s2s_auth.py` выбирает fallback chain `kubernetes_sa` -> `rsa_key` -> `shared_secret`, использует полноразмерный HMAC-SHA256, `hmac.compare_digest`, `timestamp + nonce` replay guard и серверную проверку Kubernetes token через TokenReview/OIDC. | `libs/shared/s2s_auth.py`, `tests/test_s2s_auth_issue242.py` |
| D2 | Выполнено: S2S настраивается через env/settings, включая метод, secret provider, пути ServiceAccount token/RSA key, issuer/audience, TTL и replay window. | `libs/shared/config.py`, `tests/test_config_settings.py` |
| D3 | Выполнено: `create_base_app()` защищает все `/admin/*` через `require_s2s`, а Gateway умеет подписывать downstream-запросы S2S-заголовками. | `libs/shared/server.py`, `libs/shared/gateway.py`, `tests/test_base_server_issue222.py`, `tests/test_api_gateway_routing.py` |
| D4 | Выполнено: threat model, тесты всех методов/replay/timing и ADR перехода к SPIFFE/SPIRE + mTLS зафиксированы в документации. | `docs/S2S_AUTH.md`, `docs/adr/0010-spiffe-mtls-s2s.md`, `tests/test_s2s_auth_issue245_contract.py` |

## Статус эпика E

| Задача | Статус | Проверяемые артефакты |
| --- | --- | --- |
| E1 | Выполнено: `infra/local/docker-compose.yml` добавляет все 14 продуктовых app-сервисов с образами `media-center-<service>`, build через `infra/docker/service.Dockerfile`, внутренним `APP_PORT=7700`, `expose: 7700`, healthcheck `/health`, `read_only`, `tmpfs`, `security_opt: no-new-privileges:true`, `cap_drop: ALL` и `depends_on` на healthy-инфраструктуру. | `infra/local/docker-compose.yml`, `infra/local/.env.local.example`, `infra/local/README.md`, `services/api-gateway/api_gateway_app/main.py`, `services/messenger-adapter/messenger_adapter_app/main.py`, `tests/test_local_app_compose_issue247_contract.py` |
| E2 | Выполнено: `deploy/helm/media-center` добавляет Helm chart для всех 14 продуктовых сервисов: Deployment с `/health` liveness и `/ready` readiness probes, resources, securityContext non-root/read-only/no privilege escalation/drop caps, ServiceAccount с projected ServiceAccount token для S2S, TokenReview RBAC и Service на `7700`. | `deploy/helm/media-center/Chart.yaml`, `deploy/helm/media-center/values.yaml`, `deploy/helm/media-center/templates/`, `infra/README.md`, `experiments/validate_issue248_helm.sh`, `tests/test_helm_k8s_issue248_contract.py` |
| E3 | Выполнено: все 14 продуктовых `*_app.main` используют `BaseAppConfig` + `create_base_app` через доменные фабрики, экспортируют ASGI `app`, поддерживают `python -m <service_app>.main`, стартуют на `APP_PORT=7700` и отвечают на `/health`, `/ready`, `/info`, `/metrics`. | `libs/shared/server.py`, `services/*/*_app/main.py`, `services/README.md`, `tests/test_stage9_epic_e_issue249_contract.py` |

## Release gate

Основной workflow `.github/workflows/ci.yml` запускает quality/security jobs,
job `kubernetes` с `helm lint`/`helm template`/`kubeconform` для chart #248 и
job `images`, который матрицей вызывает `.github/workflows/build-service.yml`.
Reusable workflow выполняет полный порядок release gate:

1. checkout с `fetch-depth: 0`;
2. вычисление build metadata и Docker metadata из git-тегов;
3. локальную сборку amd64-образа для Trivy image scan;
4. Trivy gate по HIGH/CRITICAL с SARIF artifact;
5. GHCR login через `secrets.GITHUB_TOKEN`;
6. финальную multi-arch сборку `linux/amd64,linux/arm64` и публикацию;
7. cosign keyless signature по digest;
8. SLSA build provenance attestation;
9. SBOM SPDX generation и SBOM attestation.

Публикация в GHCR выполняется только для `push` в `main` и semver tag push.
Pull request запускает сборку и security gate без публикации, подписи и
registry attestations.

## Контрактные проверки

Сквозной контракт эпика C закреплён в
`tests/test_stage9_epic_c_issue241_contract.py`. Он проверяет, что:

- release pipeline содержит semver, build-args, GHCR, multi-arch, SBOM, cosign,
  SLSA, Trivy и reusable `workflow_call`;
- Trivy image scan стоит перед GHCR login и финальным build/push;
- matrix покрывает все продуктовые сервисы из `services/`, кроме
  `service-template`;
- этот acceptance snapshot ссылается на workflow, resolver script и тесты.

Сквозной контракт эпика D закреплён в
`tests/test_stage9_epic_d_issue246_contract.py`. Он проверяет, что:

- fallback chain выбирает `kubernetes_sa`, затем `rsa_key`, затем
  `shared_secret`;
- все методы отклоняют replay через `timestamp + nonce`;
- shared-secret подпись остаётся полноразмерной и timing-safe;
- `/admin/*` недоступен без валидной S2S identity;
- snapshot и `docs/S2S_AUTH.md` ссылаются на код, тесты и ADR.

Контракт E1 по issue #247 закреплён в
`tests/test_local_app_compose_issue247_contract.py`. Он проверяет, что:

- compose содержит все продуктовые сервисы из `services/`, кроме
  `service-template`;
- app-сервисы собираются тем же `infra/docker/service.Dockerfile`, что и
  release pipeline, и используют `ghcr.io/${GHCR_OWNER}/media-center-<service>`;
- каждый контейнер слушает внутренний порт `7700`, публикуется на отдельный
  локальный host-порт, имеет `/health` healthcheck и ASGI entrypoint;
- runtime hardening включает `read_only: true`, `tmpfs` для `/tmp` и
  `/app/logs`, `no-new-privileges:true` и `cap_drop: ALL`;
- зависимости на PostgreSQL, Redis, RabbitMQ, ChromaDB, MinIO и
  OpenTelemetry Collector ожидают `condition: service_healthy`.

Контракт E2 по issue #248 закреплён в
`tests/test_helm_k8s_issue248_contract.py`. Он проверяет, что:

- chart `deploy/helm/media-center` содержит все продуктовые сервисы из
  `services/`, кроме `service-template`;
- values задают образы `ghcr.io/xlabtg/media-center-<service>`, Service на
  `7700` и probes `/health`/`/ready`;
- Deployment template включает resources и securityContext с non-root,
  read-only root filesystem, `allowPrivilegeEscalation: false`, drop caps и
  writable `emptyDir` только для `/tmp` и `/app/logs`;
- ServiceAccount использует projected ServiceAccount token для S2S,
  `automountServiceAccountToken: false`, env для `kubernetes_sa` и TokenReview
  RBAC;
- validation script запускает `helm lint`, `helm template` и `kubeconform`, а
  `.github/workflows/ci.yml` выполняет этот script в job `kubernetes`.

Контракт E3 по issue #249 закреплён в
`tests/test_stage9_epic_e_issue249_contract.py`. Он проверяет, что:

- каждый продуктовый `*_app.main` импортируется как ASGI entrypoint и хранит
  `app.state.base_app` с сервисным именем и портом `7700`;
- все 14 сервисов отвечают на `/health`, `/ready`, `/info` и `/metrics`;
- каждый entrypoint содержит `run()` для запуска через
  `python -m <service_app>.main`;
- snapshot и `services/README.md` перечисляют все сервисы и соответствующие
  entrypoint-модули.

Локальная проверка:

```bash
python -m pytest \
  tests/test_stage9_epic_c_issue241_contract.py \
  tests/test_stage9_epic_d_issue246_contract.py \
  tests/test_local_app_compose_issue247_contract.py \
  tests/test_helm_k8s_issue248_contract.py \
  tests/test_stage9_epic_e_issue249_contract.py

bash experiments/validate_issue248_helm.sh
```
