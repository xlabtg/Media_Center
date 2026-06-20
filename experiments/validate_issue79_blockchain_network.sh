#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

required_files=(
  "infra/blockchain/docker-compose.yml"
  "infra/blockchain/qbft-network.json"
  "infra/blockchain/scripts/bootstrap_qbft.sh"
  "infra/blockchain/scripts/run_besu_node.sh"
  "infra/blockchain/README.md"
  "infra/observability/prometheus/prometheus.blockchain.yml"
  "infra/observability/prometheus/rules/blockchain-auditor.yml"
)

for path in "${required_files[@]}"; do
  test -f "${path}"
done

grep -q 'hyperledger/besu:${BESU_IMAGE_TAG:-26.6.1}' infra/blockchain/docker-compose.yml
grep -q 'besu-validator-4:9545' infra/observability/prometheus/prometheus.blockchain.yml
grep -q 'NmcPrivateBlockchainQuorumAtRisk' infra/observability/prometheus/rules/blockchain-auditor.yml
grep -q 'BLOCKCHAIN_AUDITOR_URL=grpc://besu-auditor.internal:50051' infra/local/.env.local.example

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose \
    --project-name "${LOCAL_PROJECT_NAME:-media-center-local}" \
    --env-file "${LOCAL_ENV_FILE:-infra/local/.env.local.example}" \
    -f infra/local/docker-compose.yml \
    -f infra/blockchain/docker-compose.yml \
    --profile blockchain \
    config --quiet
fi

printf '%s\n' "issue #79 blockchain network contract is valid"
