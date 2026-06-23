# Проверка подписей и provenance сервисных образов

Этот runbook закрывает #238: сервисные образы, опубликованные в GHCR, должны
иметь keyless-подпись cosign по digest и SLSA build-provenance attestation.

## Что создаёт CI

Workflow `.github/workflows/ci.yml` вызывает reusable workflow
`.github/workflows/build-service.yml` только для матрицы продуктовых сервисов.
Публикация образов выполняется только для `push` в `main` или для semver-тегов.
Для каждого сервиса reusable job `Build service image` выполняет:

- сборку multi-arch manifest list через `docker/build-push-action`;
- keyless-подпись `cosign sign --yes` для
  `ghcr.io/${owner}/media-center-${service}@${digest}`;
- SLSA build-provenance через `actions/attest-build-provenance`;
- SBOM SPDX и SBOM-attestation.

Keyless-подпись использует GitHub OIDC, Fulcio-сертификат и запись в Rekor.
Секретные ключи для подписи не создаются и не хранятся в репозитории.

## Входные данные для проверки

Возьмите digest опубликованного образа из job `Build service image` или из GHCR.
Дальше используйте digest, а не mutable tag:

```bash
IMAGE="ghcr.io/xlabtg/media-center-api-gateway@sha256:<digest>"
```

## Проверить cosign-подпись

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/xlabtg/Media_Center/\.github/workflows/build-service\.yml@refs/(heads/main|tags/.+)$' \
  "$IMAGE"
```

Успешная проверка подтверждает, что digest подписан reusable workflow
`Build Service` из репозитория `xlabtg/Media_Center`, а сертификат выпущен
через GitHub OIDC.

## Проверить SLSA provenance через cosign

```bash
cosign verify-attestation \
  --type slsaprovenance \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/xlabtg/Media_Center/\.github/workflows/build-service\.yml@refs/(heads/main|tags/.+)$' \
  "$IMAGE"
```

Проверьте в payload поля `subject`, `predicateType`, `builder`, source commit и
workflow ref. Для machine-readable проверки можно добавить `--output json` и
политики поверх JSON.

## Проверить GitHub artifact attestation

Так как provenance прикрепляется к OCI registry, для `gh` используйте
`--bundle-from-oci`:

```bash
gh attestation verify \
  "oci://ghcr.io/xlabtg/media-center-api-gateway@sha256:<digest>" \
  --repo xlabtg/Media_Center \
  --bundle-from-oci \
  --signer-workflow xlabtg/Media_Center/.github/workflows/build-service.yml
```

По умолчанию `gh attestation verify` ожидает predicate type
`https://slsa.dev/provenance/v1`, поэтому отдельный флаг для SLSA provenance не
нужен.
