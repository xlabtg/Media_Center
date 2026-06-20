#!/usr/bin/env sh
set -eu

config_file="${BESU_QBFT_CONFIG_FILE:-/config/qbft-network.json}"
output_dir="${BESU_QBFT_OUTPUT_DIR:-/network}"
generated_dir="${output_dir}/generated"
nodes_dir="${output_dir}/nodes"
ready_file="${output_dir}/ready"
validator_count="${BESU_VALIDATOR_COUNT:-4}"

if [ -f "${ready_file}" ] && [ -f "${output_dir}/genesis.json" ]; then
  echo "Besu QBFT network files already exist in ${output_dir}"
  exit 0
fi

rm -rf "${generated_dir}"
mkdir -p "${generated_dir}" "${nodes_dir}"

besu operator generate-blockchain-config \
  --config-file="${config_file}" \
  --to="${generated_dir}" \
  --private-key-file-name=key \
  --public-key-file-name=key.pub

cp "${generated_dir}/genesis.json" "${output_dir}/genesis.json"

nodes_file="${output_dir}/nodes-allowlist.txt"
static_nodes_file="${output_dir}/static-nodes.json"
permissions_file="${output_dir}/permissions_config.toml"
bootnodes_file="${output_dir}/bootnodes.txt"

: > "${nodes_file}"
index=1
for key_dir in $(find "${generated_dir}/keys" -mindepth 1 -maxdepth 1 -type d | sort); do
  if [ "${index}" -gt "${validator_count}" ]; then
    break
  fi

  node_id="validator-${index}"
  node_dir="${nodes_dir}/${node_id}"
  mkdir -p "${node_dir}"
  cp "${key_dir}/key" "${node_dir}/key"

  if [ -f "${key_dir}/key.pub" ]; then
    cp "${key_dir}/key.pub" "${node_dir}/key.pub"
  else
    besu --data-path="${node_dir}" public-key export --to="${node_dir}/key.pub" >/dev/null
  fi

  public_key="$(tr -d ' \r\n' < "${node_dir}/key.pub" | sed 's/^0x//')"
  printf '%s\n' "enode://${public_key}@besu-validator-${index}:30303" >> "${nodes_file}"
  index=$((index + 1))
done

if [ "${index}" -le "${validator_count}" ]; then
  echo "Expected ${validator_count} validator keys, generated only $((index - 1))" >&2
  exit 1
fi

line_count="$(wc -l < "${nodes_file}" | tr -d ' ')"
line_number=0
bootnodes=""

{
  printf '[\n'
  while IFS= read -r enode; do
    line_number=$((line_number + 1))
    if [ -z "${bootnodes}" ]; then
      bootnodes="${enode}"
    else
      bootnodes="${bootnodes},${enode}"
    fi

    if [ "${line_number}" -lt "${line_count}" ]; then
      printf '  "%s",\n' "${enode}"
    else
      printf '  "%s"\n' "${enode}"
    fi
  done < "${nodes_file}"
  printf ']\n'
} > "${static_nodes_file}"

line_number=0
{
  printf 'nodes-allowlist=[\n'
  while IFS= read -r enode; do
    line_number=$((line_number + 1))
    if [ "${line_number}" -lt "${line_count}" ]; then
      printf '  "%s",\n' "${enode}"
    else
      printf '  "%s"\n' "${enode}"
    fi
  done < "${nodes_file}"
  printf ']\n'
} > "${permissions_file}"

printf '%s\n' "${bootnodes}" > "${bootnodes_file}"
touch "${ready_file}"
echo "Besu QBFT genesis, static nodes and node permissioning files are ready"
