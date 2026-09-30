"""Runs tools/workflow-generator/tests/approval_parity_test.js: n8n's decision code and this
service's planner on the same cases (see that file for the cases and the documented differences).

Needs node (NODE env, else `node` on PATH) and the monorepo layout; skipped with the reason otherwise.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "tools" / "workflow-generator" / "tests" / "approval_parity_test.js"
WORKFLOW = ROOT / "72-control-room" / "n8n" / "workflow.json"


def test_same_decisions_as_n8n():
    node = os.environ.get("NODE") or shutil.which("node")
    if not node:
        pytest.skip("node not found (set NODE or put node on PATH) - parity with n8n not checked")
    if not TEST.exists() or not WORKFLOW.exists():
        pytest.skip("workflow generator or built 72 workflow not found (parity needs the monorepo)")
    r = subprocess.run([node, str(TEST)], cwd=TEST.parent.parent, capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHON": sys.executable})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "approval parity:" in r.stdout and "(ok)" in r.stdout, r.stdout
