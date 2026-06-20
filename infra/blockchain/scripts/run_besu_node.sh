#!/usr/bin/env sh
set -eu

node_id="${BESU_NODE_ID:?BESU_NODE_ID is required}"
network_dir="${BESU_NETWORK_DIR:-/network}"
data_path="${BESU_DATA_PATH:-/var/lib/besu}"
node_dir="${network_dir}/nodes/${node_id}"

if [ ! -f "${network_dir}/ready" ]; then
  echo "Besu QBFT network files are not bootstrapped yet" >&2
  exit 1
fi

if [ ! -f "${node_dir}/key" ]; then
  echo "Missing private node key for ${node_id}" >&2
  exit 1
fi

mkdir -p "${data_path}"
cp "${node_dir}/key" "${data_path}/key"
cp "${network_dir}/static-nodes.json" "${data_path}/static-nodes.json"
cp "${network_dir}/permissions_config.toml" "${data_path}/permissions_config.toml"

bootnodes="$(cat "${network_dir}/bootnodes.txt")"

exec besu \
  --data-path="${data_path}" \
  --genesis-file=/network/genesis.json \
  --node-private-key-file=/var/lib/besu/key \
  --network-id="${BESU_NETWORK_ID:-20260679}" \
  --p2p-host=0.0.0.0 \
  --p2p-port="${BESU_P2P_PORT:-30303}" \
  --discovery-enabled=true \
  --bootnodes="${bootnodes}" \
  --permissions-nodes-config-file-enabled=true \
  --rpc-http-enabled=${BESU_RPC_HTTP_ENABLED:-false} \
  --rpc-http-host=0.0.0.0 \
  --rpc-http-port="${BESU_RPC_HTTP_PORT:-8545}" \
  --rpc-http-api="${BESU_RPC_HTTP_API:-ETH,NET,QBFT,WEB3}" \
  --rpc-http-cors-origins="${BESU_RPC_HTTP_CORS_ORIGINS:-none}" \
  --host-allowlist="${BESU_HOST_ALLOWLIST:-localhost,127.0.0.1}" \
  --metrics-enabled=true \
  --metrics-host=0.0.0.0 \
  --metrics-port="${BESU_METRICS_PORT:-9545}" \
  --min-gas-price=0 \
  --logging="${BESU_LOGGING:-INFO}"
