from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: tuple[str, ...]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue_79_private_blockchain_compose_declares_qbft_network() -> None:
    compose = read_text("infra/blockchain/docker-compose.yml")

    required_markers = (
        "hyperledger/besu:${BESU_IMAGE_TAG:-26.6.1}",
        "besu-qbft-bootstrap:",
        "besu-validator-1:",
        "besu-validator-2:",
        "besu-validator-3:",
        "besu-validator-4:",
        "profiles:",
        "blockchain",
        "besu-rpc",
        "besu-qbft-network:",
        "besu-validator-1-data:",
        "besu-validator-2-data:",
        "besu-validator-3-data:",
        "besu-validator-4-data:",
        'BESU_RPC_HTTP_ENABLED: "false"',
        'BESU_RPC_HTTP_ENABLED: "true"',
        "besu-auditor.internal",
    )
    missing = [marker for marker in required_markers if marker not in compose]

    assert not missing
    assert "8545:8545" not in compose
    assert "30303:30303" not in compose
    assert ":latest" not in compose


def test_issue_79_besu_bootstrap_and_runtime_enforce_access_contract() -> None:
    assert_markers(
        "infra/blockchain/qbft-network.json",
        (
            '"qbft"',
            '"chainId": 20260679',
            '"blockperiodseconds": 4',
            '"requesttimeoutseconds": 8',
            '"count": 4',
        ),
    )
    assert_markers(
        "infra/blockchain/scripts/bootstrap_qbft.sh",
        (
            "operator generate-blockchain-config",
            "--private-key-file-name=key",
            "static-nodes.json",
            "permissions_config.toml",
            "nodes-allowlist",
            "besu-validator-${index}:30303",
        ),
    )
    assert_markers(
        "infra/blockchain/scripts/run_besu_node.sh",
        (
            "--genesis-file=/network/genesis.json",
            "--node-private-key-file=/var/lib/besu/key",
            "--permissions-nodes-config-file-enabled=true",
            "--rpc-http-enabled=${BESU_RPC_HTTP_ENABLED:-false}",
            "--metrics-enabled=true",
            "--metrics-host=0.0.0.0",
        ),
    )


def test_issue_79_auditor_endpoint_monitoring_and_runbook_are_declared() -> None:
    assert_markers(
        "infra/local/.env.local.example",
        (
            "BLOCKCHAIN_AUDITOR_URL=grpc://besu-auditor.internal:50051",
            "BESU_IMAGE_TAG=26.6.1",
            "BESU_NETWORK_ID=20260679",
            "BESU_RPC_HTTP_URL=http://besu-rpc:8545",
        ),
    )
    assert_markers(
        "infra/observability/prometheus/prometheus.blockchain.yml",
        (
            "private-blockchain-besu",
            "besu-validator-1:9545",
            "besu-validator-4:9545",
            "tenant_id: system",
            "service: private-blockchain",
        ),
    )
    assert_markers(
        "infra/observability/prometheus/rules/blockchain-auditor.yml",
        (
            "NmcPrivateBlockchainNodeDown",
            "NmcPrivateBlockchainQuorumAtRisk",
            'job="private-blockchain-besu"',
            "severity: critical",
        ),
    )
    assert_markers(
        "infra/blockchain/README.md",
        (
            "# Приватная блокчейн-сеть",
            "issue #79",
            "Hyperledger Besu 26.6.1",
            "QBFT",
            "make blockchain-up",
            "make blockchain-config",
            "BLOCKCHAIN_AUDITOR_URL=grpc://besu-auditor.internal:50051",
            "доступ только для Совета",
            "snapshot",
            "restore",
        ),
    )
    assert_markers(
        "Makefile",
        (
            "BLOCKCHAIN_COMPOSE_FILE ?= infra/blockchain/docker-compose.yml",
            "blockchain-up:",
            "blockchain-down:",
            "blockchain-config:",
            "experiments/validate_issue79_blockchain_network.sh",
        ),
    )


def test_issue_79_documentation_links_network_to_blockchain_auditor() -> None:
    docs = "\n".join(
        [
            read_text("docs/modules/blockchain-auditor.md"),
            read_text("services/blockchain-auditor/README.md"),
            read_text("infra/README.md"),
        ]
    )

    for marker in (
        "infra/blockchain",
        "besu-auditor.internal",
        "private-blockchain-besu",
        "#79",
        "Развёртывание приватной блокчейн-сети",
    ):
        assert marker in docs
