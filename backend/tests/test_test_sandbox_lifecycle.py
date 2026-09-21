"""Test-only filesystem sandboxes must disappear when their owner process exits."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_conftest_sandboxes_cleanup_on_process_exit():
    probe = (
        "import sys; "
        "sys.path.insert(0, 'tests'); "
        "import conftest as c; "
        "print(c._TMP_REPORT_DIR); "
        "print(c._DATA_SANDBOX)"
    )
    run = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = [Path(line.strip()) for line in run.stdout.splitlines() if line.strip()]
    assert len(paths) == 2
    assert all(not path.exists() for path in paths), paths
