"""Hourly oneshot entry point. Run through the installed systemd service only.

No threads, child processes, SDK, network connections or transcript reads.
systemd owns the whole nondelegated cgroup, including cleanup on every exit.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

from openclaw_committed import collect
from openclaw_snapshot import atomic_json, snapshot_path

UNIT = 'ai-usage-openclaw-collect.service'


def require_supervisor():
    cgroup = Path('/proc/self/cgroup').read_text().strip()
    if (not os.environ.get('INVOCATION_ID') or
            not cgroup.endswith('/' + UNIT)):
        raise RuntimeError('Start the collector with systemctl --user start ' + UNIT)
    members = {int(pid) for pid in (Path('/sys/fs/cgroup') / cgroup.split(':', 2)[2].lstrip('/') / 'cgroup.procs').read_text().split()}
    if members != {os.getpid()}:
        atomic_json(snapshot_path().with_suffix('.state.json'), dict(blocked=True, outcome='unclean_cgroup'))
        raise RuntimeError('Collector cgroup contains previous processes; collection blocked')


def run(root, output, *, now=None, reader=collect):
    """Locked, restart-safe rate limiting; interruption retains the prior artifact."""
    now = time.time() if now is None else now
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    state_path = output.with_suffix('.state.json')
    with output.with_suffix('.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'locked'
        try:
            state = json.loads(state_path.read_text())
        except FileNotFoundError:
            state = {}
        # Corrupt state fails closed (no immediate upstream retry).
        if state.get('blocked'):
            raise RuntimeError('Collector cleanup was not confirmed; operator inspection required')
        if now < state.get('next_allowed_at', 0):
            return 'rate_limited'
        failures = min(int(state.get('failures', 0)) + 1, 5)
        # Persist BEFORE any source access; timeout/crash/reboot cannot hot-loop.
        atomic_json(state_path, dict(next_allowed_at=now + min(3600 * 2**(failures-1), 14400),
                                    failures=failures, outcome='interrupted'))
        try:
            data = reader(root, deadline=time.monotonic() + 25)
            atomic_json(output, data)
        except BaseException as exc:
            atomic_json(state_path, dict(next_allowed_at=now + min(3600 * 2**(failures-1), 14400),
                                        failures=failures, outcome=type(exc).__name__))
            raise
        atomic_json(state_path, dict(next_allowed_at=now + 3600, failures=0, outcome='success'))
        return 'success'


def main():
    require_supervisor()
    def cancelled(signum, frame):
        raise TimeoutError('Collector deadline or cancellation')
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    signal.signal(signal.SIGALRM, cancelled)
    signal.setitimer(signal.ITIMER_REAL, 30)
    try:
        root = Path(os.environ.get('OPENCLAW_USAGE_AGENTS_DIR', str(Path.home() / '.openclaw/agents')))
        outcome = run(root, snapshot_path())
        print(json.dumps(dict(outcome=outcome, finishedAt=datetime.now(timezone.utc).isoformat())))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    main()
