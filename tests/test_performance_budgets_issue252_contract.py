from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/check_service_performance_budget.py"
CONFIG = ROOT / "docs/operations/service-performance-budgets.json"
PRODUCT_SERVICES = [
    "activity-command-center",
    "analytics-engine",
    "api-gateway",
    "blockchain-auditor",
    "cglr",
    "contribution-ledger",
    "hitl-payout-gateway",
    "messenger-adapter",
    "neuro-agent-orchestrator",
    "notification-gateway",
    "policy-manager",
    "voice-to-chain",
    "wallet",
    "web-cabinet",
]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def load_budget_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_service_performance_budget",
        SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_issue_252_budget_config_defines_size_and_cold_start_gates() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    assert config["schema_version"] == 1
    assert config["issue"] == 252
    assert config["metrics"]["image_size"] == {
        "source": "docker image ls",
        "budget_bytes": 250_000_000,
        "budget_mb": 250,
        "stretch_budget_bytes": 200_000_000,
        "stretch_budget_mb": 200,
        "comparison": "less_than",
        "adr": "docs/adr/0008-container-image-size-optimization.md",
    }
    assert config["metrics"]["cold_start"] == {
        "source": "container start to first HTTP 200",
        "endpoint": "/ready",
        "port": 7700,
        "budget_ms": 3_000,
        "timeout_ms": 10_000,
        "comparison": "less_than_or_equal",
    }

    services = {service["name"]: service for service in config["services"]}
    assert sorted(services) == PRODUCT_SERVICES
    for service in services.values():
        assert service["image_size_budget_bytes"] == 250_000_000
        assert service["cold_start_budget_ms"] == 3_000


def test_issue_252_reusable_workflow_runs_budget_gate_after_local_build() -> None:
    workflow = yaml.safe_load(read_text(".github/workflows/build-service.yml"))
    steps = workflow["jobs"]["build"]["steps"]
    names = [step["name"] for step in steps]

    build_index = names.index("Build image for Trivy scan")
    budget_index = names.index("Check service performance budgets")
    trivy_index = names.index("Trivy image scan")
    upload_index = names.index("Upload service performance budget report")

    assert build_index < budget_index < upload_index < trivy_index
    budget_step = steps[budget_index]
    assert "check_service_performance_budget.py" in budget_step["run"]
    assert '--service "${{ inputs.service }}"' in budget_step["run"]
    assert (
        '--image "media-center-${{ inputs.service }}:trivy-scan"' in budget_step["run"]
    )
    assert (
        "--config docs/operations/service-performance-budgets.json"
        in budget_step["run"]
    )
    assert "--report-dir performance-reports" in budget_step["run"]

    upload_step = steps[upload_index]
    assert upload_step["if"] == "always()"
    assert upload_step["with"]["name"] == "service-performance-${{ inputs.service }}"
    assert upload_step["with"]["path"] == "performance-reports/"


def test_issue_252_budget_script_parses_docker_image_ls_sizes() -> None:
    module = load_budget_script()

    assert module.parse_docker_size("249MB") == 249_000_000
    assert module.parse_docker_size("1.5GB") == 1_500_000_000
    assert module.parse_docker_size("123kB") == 123_000
    assert module.parse_docker_size("42B") == 42
    assert module.parse_docker_size("10.5MiB") == 11_010_048


def test_issue_252_budget_script_fails_on_budget_exceedance() -> None:
    module = load_budget_script()
    budget = module.ServiceBudget(
        name="contribution-ledger",
        image_size_budget_bytes=250_000_000,
        cold_start_budget_ms=3_000,
    )

    passing = module.evaluate_budget(
        budget,
        image_size_bytes=249_999_999,
        cold_start_ms=3_000,
    )
    failing = module.evaluate_budget(
        budget,
        image_size_bytes=250_000_000,
        cold_start_ms=3_001,
    )

    assert passing.image_size_ok
    assert passing.cold_start_ok
    assert passing.passed
    assert not failing.image_size_ok
    assert not failing.cold_start_ok
    assert not failing.passed


def test_issue_252_runtime_dependencies_are_split_for_budgeted_images() -> None:
    pyproject = read_text("pyproject.toml")
    dockerfile = read_text("infra/docker/service.Dockerfile")

    assert "[project.optional-dependencies]" in pyproject
    assert "runtime-core = [" in pyproject
    assert "runtime-messenger-adapter = [" in pyproject
    assert "chromadb-client==1.5.9" in pyproject
    assert "boto3==1.43.32" in pyproject

    required_markers = [
        "runtime-core",
        'f"runtime-{service_name}"',
        'pyproject["project"].get("optional-dependencies", {})',
        "RUNTIME_REQUIREMENT_GROUPS",
        "/tmp/requirements-runtime.txt",
        "from compileall import compile_dir",
        'for package in ("fastapi", "starlette", "pydantic", "pydantic_settings")',
        "service_package_name",
        'Path("/build/app/libs/shared")',
        "COPY services/ /build/services-source/",
        'package_dir / "__init__.py"',
        "shutil.copytree(package_dir, target_dir)",
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing
    assert 'pyproject["project"]["dependencies"]' not in dockerfile
    assert "PYTHONPYCACHEPREFIX" not in dockerfile


def test_issue_252_docs_record_operational_budget_gate() -> None:
    budget_doc = read_text("docs/operations/image-size-budget.md")
    acceptance = read_text("docs/STAGE_9_ACCEPTANCE.md")

    budget_markers = [
        "docs/operations/service-performance-budgets.json",
        ".github/scripts/check_service_performance_budget.py",
        "docker image ls",
        "/ready",
        "< 3 с",
        "performance-reports",
        "GITHUB_STEP_SUMMARY",
        "runtime-core",
        "peer service packages",
    ]
    acceptance_markers = [
        "F2 / #252",
        "service-performance-budgets.json",
        "check_service_performance_budget.py",
        "tests/test_performance_budgets_issue252_contract.py",
    ]

    assert not [marker for marker in budget_markers if marker not in budget_doc]
    assert not [marker for marker in acceptance_markers if marker not in acceptance]
