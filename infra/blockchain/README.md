# Приватная блокчейн-сеть

Инфраструктурный контур для issue #79 разворачивает приватную
Hyperledger Besu 26.6.1 сеть с консенсусом QBFT для Private Blockchain
Auditor. В сеть передаются только hash-only audit records через существующую
границу `blockchain-auditor`; исходные payload, ПДн, суммы выплат и токены в
цепочку не пишутся.

## Состав

- `besu-qbft-bootstrap` генерирует `genesis.json`, node keys,
  `static-nodes.json` и `permissions_config.toml` в Docker volume
  `besu-qbft-network`.
- `besu-validator-1` ... `besu-validator-4` образуют 4-узловой QBFT-кворум.
  При одном недоступном валидаторе сеть сохраняет кворум.
- `besu-validator-1` имеет внутренний alias `besu-rpc` и
  `besu-auditor.internal`; HTTP RPC не публикуется на host-порты.
- Prometheus override `prometheus.blockchain.yml` добавляет scrape job
  `private-blockchain-besu` для метрик Besu.

Приватные node keys создаются только внутри Docker volume и не коммитятся.

## Запуск

Проверить compose и обязательные маркеры:

```bash
make blockchain-config
```

Запустить локальную среду вместе с blockchain-профилем:

```bash
make blockchain-up
```

Остановить стек:

```bash
make blockchain-down
```

`make up` по-прежнему поднимает только базовую локальную среду без Besu.

## Доступ

Auditor-сервис использует внутренний endpoint:

```text
BLOCKCHAIN_AUDITOR_URL=grpc://besu-auditor.internal:50051
BESU_RPC_HTTP_URL=http://besu-rpc:8545
```

Низкоуровневый RPC endpoint доступен только внутри docker-compose сети. На
host не публикуются `8545` и `30303`, а валидаторы принимают peer-соединения
только из сгенерированного `nodes-allowlist`. Операции записи и проверки audit
records остаются за `blockchain-auditor`, где включены tenant context, RBAC и
доступ только для Совета.

## Мониторинг

При запуске blockchain-профиля Prometheus получает конфигурацию
`infra/observability/prometheus/prometheus.blockchain.yml` и собирает метрики:

- `besu-validator-1:9545`
- `besu-validator-2:9545`
- `besu-validator-3:9545`
- `besu-validator-4:9545`

Rules в `infra/observability/prometheus/rules/blockchain-auditor.yml`
сигналят, если Besu-нода недоступна или живых валидаторов меньше трёх.

## Snapshot и restore

Снимок bootstrap volume содержит genesis, static nodes, permissions и node
keys. Храните snapshot как секретный операционный артефакт вне репозитория.

Создать snapshot:

```bash
mkdir -p backups
docker run --rm \
  -v media-center-local_besu-qbft-network:/network:ro \
  -v "$PWD/backups":/backup \
  alpine:3.22 \
  sh -ec 'tar -czf /backup/besu-qbft-network-$(date -u +%Y%m%dT%H%M%SZ).tgz -C /network .'
```

Восстановить snapshot перед запуском сети:

```bash
docker run --rm \
  -v media-center-local_besu-qbft-network:/network \
  -v "$PWD/backups":/backup:ro \
  alpine:3.22 \
  sh -ec 'rm -rf /network/* && tar -xzf /backup/<snapshot>.tgz -C /network'
```

После restore выполните `make blockchain-up` и проверьте Prometheus target
`private-blockchain-besu`.
