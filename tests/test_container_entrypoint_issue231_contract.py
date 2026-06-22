from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker/entrypoint.sh"


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_issue_231_service_dockerfile_uses_ready_entrypoint_script() -> None:
    dockerfile = read_text("infra/docker/service.Dockerfile")

    required_markers = [
        "COPY pyproject.toml /tmp/media-center-pyproject.toml",
        'pyproject["project"]["dependencies"]',
        "python -m pip install -r /tmp/requirements-runtime.txt",
        "COPY docker/entrypoint.sh /build/app/entrypoint.sh",
        (
            "COPY --from=builder --chown=1000:1000 "
            "/build/app/entrypoint.sh /app/entrypoint.sh"
        ),
        "chmod 0755 /app/entrypoint.sh",
        'ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]',
        'CMD ["serve"]',
    ]
    missing = [marker for marker in required_markers if marker not in dockerfile]

    assert not missing
    assert "image is ready" not in dockerfile


def test_issue_231_entrypoint_script_is_executable_and_documented() -> None:
    docs = read_text("infra/README.md")
    mode = ENTRYPOINT.stat().st_mode

    required_markers = [
        "docker/entrypoint.sh",
        "APP_MODULE",
        "SERVICE_NAME",
        "APP_HOST",
        "APP_PORT",
        "7700",
        "docker run",
    ]
    missing = [marker for marker in required_markers if marker not in docs]

    assert mode & stat.S_IXUSR
    assert not missing


def test_issue_231_entrypoint_launches_uvicorn_on_default_port(
    tmp_path: Path,
) -> None:
    args_file = tmp_path / "uvicorn-args.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "uvicorn",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$UVICORN_ARGS_FILE"\n',
    )

    env = _entrypoint_env(bin_dir)
    env["UVICORN_ARGS_FILE"] = str(args_file)

    subprocess.run(
        [str(ENTRYPOINT), "serve"],
        check=True,
        env=env,
        timeout=5,
    )

    assert args_file.read_text(encoding="utf-8").splitlines() == [
        "contribution_ledger_app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "7700",
        "--log-level",
        "info",
    ]


def test_issue_231_entrypoint_allows_command_override(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(ENTRYPOINT), "python", "-c", "print('override-ok')"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.stdout == "override-ok\n"


def test_issue_231_entrypoint_execs_server_for_sigterm(tmp_path: Path) -> None:
    pid_file = tmp_path / "uvicorn.pid"
    term_file = tmp_path / "uvicorn.term"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "uvicorn",
        "#!/bin/sh\n"
        'printf "%s\\n" "$$" > "$UVICORN_PID_FILE"\n'
        "trap 'printf term > \"$UVICORN_TERM_FILE\"; exit 0' TERM\n"
        "while :; do sleep 1; done\n",
    )

    env = _entrypoint_env(bin_dir)
    env["UVICORN_PID_FILE"] = str(pid_file)
    env["UVICORN_TERM_FILE"] = str(term_file)

    process = subprocess.Popen([str(ENTRYPOINT), "serve"], env=env)
    try:
        _wait_for_file(pid_file)
        process.terminate()
        return_code = process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert return_code == 0
    assert term_file.read_text(encoding="utf-8") == "term"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _entrypoint_env(bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "SERVICE_NAME": "contribution-ledger",
        }
    )
    for name in ("APP_HOST", "APP_PORT", "APP_MODULE", "LOG_LEVEL"):
        env.pop(name, None)

    return env


def _wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)

    raise AssertionError(f"{path} was not created")
