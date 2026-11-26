"""Proposed Mac-local read-only CLI producer. Never transfers raw CLI JSON.

Not installed or scheduled here. Requires owner authorization on the Mac, a verified
2.99.0 CLI/runtime, and a private destination. Stdout contains only safe receipts.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

import opencodex_usage as usage

CLI_LIMIT = 2 * 1024 * 1024


def cli_usage(ocx, start=None, end=None, *, deadline):
    """Capture bounded stdout in RAM only; stderr is never persisted or relayed."""
    command = [ocx, 'usage', '--range', 'all', '--surface', 'all', '--json']
    if start is not None:
        command += ['--since', str(start), '--until', str(end)]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL) as child:
        try:
            raw = bytearray()
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError('CLI aggregate deadline exceeded')
                    part = os.read(child.stdout.fileno(), min(65536, CLI_LIMIT + 1 - len(raw)))
                    if not part:
                        break
                    raw.extend(part)
                    if len(raw) > CLI_LIMIT:
                        raise usage.InvalidSnapshot('Oversized CLI aggregates')
            remaining = deadline - time.monotonic()
            if remaining <= 0 or child.wait(timeout=remaining) != 0:
                raise usage.InvalidSnapshot('CLI aggregate unavailable')
            return usage.decode_json(raw)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


def envelope(windows, identity):
    body = dict(schema=2, upstream_sha=usage.UPSTREAM_SHA, source_id=usage.source_id(identity),
                start_date=windows[0]['date'], end_date=windows[-1]['date'], windows=windows)
    usage.normalize_export(body, expected_source=identity)
    return body


def retained_start(body):
    """Use full-ledger scalar bounds, NOT capped/local-time days[]."""
    projected = usage.project_window(body)
    for key in ('historyTruncated', 'entriesTruncated'):
        if projected.get(key) is not False:
            raise usage.InvalidSnapshot('Truncated retained ledger discovery')
    for key in ('entriesDropped', 'truncatedPrefixBytes'):
        if usage._counter(projected.get(key)):
            raise usage.InvalidSnapshot('Truncated retained ledger discovery')
    if body.get('range') != 'all' or body.get('surface') != 'all' or body.get('customWindow'):
        raise usage.InvalidSnapshot('Wrong discovery scope')
    first, last = body.get('snapshotWindowStart'), body.get('snapshotWindowEnd')
    if first is None and last is None and body.get('summary', {}).get('requests') == 0:
        return None
    first, last = usage._counter(first), usage._counter(last)
    if first > last or last > int(datetime.now(timezone.utc).timestamp() * 1000) + 60000:
        raise usage.InvalidSnapshot('Invalid retained-ledger bounds')
    return datetime.fromtimestamp(first / 1000, timezone.utc).date().isoformat()


def atomic_export(path, body):
    target = Path(path).absolute()
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise usage.InvalidSnapshot('Symlinked export paths are refused')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.stat().st_mode & 0o077:
        raise usage.InvalidSnapshot('Select a private export directory (0700)')
    raw = json.dumps(body, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > usage.MAX_BYTES:
        raise usage.InvalidSnapshot('Oversized aggregate export')
    fd, name = tempfile.mkstemp(prefix='.aggregate-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def export_batches(output_dir, *, identity='private-mac', ocx='ocx', start_date=None,
                   end_date=None, batch_days=7, max_batches=1, timeout=60, reader=cli_usage):
    if (type(batch_days) is not int or not 1 <= batch_days <= usage.MAX_DAYS or
            type(max_batches) is not int or not 1 <= max_batches <= 1000 or
            type(timeout) not in (int, float) or not 0 < timeout <= 300):
        raise usage.InvalidSnapshot('Invalid bounded export budget')
    identity = usage.source_id(identity)
    end = date.fromisoformat(end_date or datetime.now(timezone.utc).date().isoformat())
    if start_date is None:
        start_date = retained_start(reader(ocx, deadline=time.monotonic() + timeout))
        if start_date is None:
            return dict(batches_written=0, complete=True, next_start_date=None)
    cursor = date.fromisoformat(start_date)
    if cursor > end or end > datetime.now(timezone.utc).date():
        raise usage.InvalidSnapshot('Invalid export date interval')
    count = 0
    while cursor <= end and count < max_batches:
        stop = min(end, cursor + timedelta(days=batch_days - 1))
        deadline = time.monotonic() + timeout
        windows = []
        for day in usage.dates(cursor.isoformat(), stop.isoformat()):
            start_ms, end_ms = usage.bounds(day)
            raw = reader(ocx, start_ms, end_ms, deadline=deadline)
            projected = usage.project_window(raw)
            usage.normalize_day(day, projected)
            windows.append(dict(date=day, response=projected))
        body = envelope(windows, identity)
        atomic_export(Path(output_dir) / f'{identity}-{cursor}-{stop}.json', body)
        count += 1
        cursor = stop + timedelta(days=1)
    return dict(batches_written=count, complete=cursor > end,
                next_start_date=cursor.isoformat() if cursor <= end else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--source-id', default='private-mac')
    parser.add_argument('--ocx', default='ocx', help='Verified Mac-local 2.99.0 CLI executable')
    parser.add_argument('--start-date', help='First UTC date; omitted = full retained-ledger discovery')
    parser.add_argument('--end-date', help='Last UTC date; omitted = today')
    parser.add_argument('--batch-days', type=int, default=7)
    parser.add_argument('--max-batches', type=int, default=1)
    parser.add_argument('--timeout', type=float, default=60, help='Whole batch deadline, maximum 300s')
    args = parser.parse_args(argv)
    try:
        result = export_batches(args.output_dir, identity=args.source_id, ocx=args.ocx,
                                start_date=args.start_date, end_date=args.end_date,
                                batch_days=args.batch_days, max_batches=args.max_batches,
                                timeout=args.timeout)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, OverflowError):
        print('OpenCodex aggregate export refused; prior completed files unchanged.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
