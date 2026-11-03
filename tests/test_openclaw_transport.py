"""Exercise the actual public-SDK request builder with a deterministic RPC peer."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node required')
def test_exact_aggregate_transport_contract():
    script = Path(__file__).with_name('openclaw_gateway_read_test.mjs')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
