# Container Runtime Hardening

Документ фиксирует контракт issue #227 для сервисных образов Media Center:
образ запускается от non-root пользователя `1000:1000`, использует `tini` как
PID 1 и готов к `read-only` rootfs. Runtime-флаги применяются в compose/k8s
слоях, потому что Dockerfile не может сам включить `no-new-privileges`, drop
capabilities или read-only rootfs.

## Image baseline

- `infra/docker/service.Dockerfile` создает пользователя `app` с UID/GID
  `1000:1000` и shell `/usr/sbin/nologin`.
- `ENTRYPOINT ["/usr/bin/tini", "--"]` делает `tini` PID 1, чтобы SIGTERM
  корректно проксировался дочернему процессу, а zombie-процессы reaped.
- Writable-контракт ограничен путями `/tmp` и `/app/logs`.
- `PYTHONDONTWRITEBYTECODE=1` предотвращает запись bytecode в read-only
  runtime; нужный для cold-start bytecode выборочно создаётся в build stage и
  копируется как read-only артефакт вместе с кодом.

## Docker Compose contract

Для app-сервисов в `infra/local/docker-compose.yml` и production compose
используется такой baseline:

```yaml
services:
  api-gateway:
    image: ghcr.io/xlabtg/media-center-api-gateway:${IMAGE_TAG}
    user: "1000:1000"
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,nodev,mode=1777
      - /app/logs:rw,noexec,nosuid,nodev,mode=0775,uid=1000,gid=1000
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
```

Порт приложения `7700`, поэтому capability `NET_BIND_SERVICE` не нужна.
Исключения должны быть оформлены отдельным ADR с указанием сервиса, причины и
срока пересмотра.

## Kubernetes contract

Для Deployment/Pod securityContext используется тот же набор ограничений:

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      securityContext:
        runAsUser: 1000
        runAsGroup: 1000
        runAsNonRoot: true
        fsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: api-gateway
          image: ghcr.io/xlabtg/media-center-api-gateway:${IMAGE_TAG}
          securityContext:
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: app-logs
              mountPath: /app/logs
      volumes:
        - name: tmp
          emptyDir:
            medium: Memory
        - name: app-logs
          emptyDir:
            medium: Memory
```

`emptyDir` можно заменить на другой writable volume только если он сохраняет
права записи для UID/GID `1000:1000` и не расширяет writable surface за пределы
`/tmp` и `/app/logs`.
