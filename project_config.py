"""Shared project .env loading; process environment takes precedence.

Keep the existing simple KEY=value syntax: no shell evaluation, expansion,
credential discovery, logging or network calls. Never write the private file.
"""
from pathlib import Path
import os


def load_env(path=None):
    target = Path(path) if path is not None else Path(__file__).resolve().parent / '.env'
    if not target.exists():
        return
    with target.open() as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key, value)
