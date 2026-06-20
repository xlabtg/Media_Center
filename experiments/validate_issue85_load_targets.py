#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_load_testing_issue85_acceptance_contract.py::"
            "test_issue85_load_targets_are_reproducible_and_met",
            "-q",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
