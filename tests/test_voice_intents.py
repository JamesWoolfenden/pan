"""Runs the Node test suite for the browser-side voice intent parser (ui/voice-intents.js),
so CI's plain `uv run pytest` covers it too."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_voice_intents_node_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, "--test", "tests/voice_intents.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
