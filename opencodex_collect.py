"""Independent opt-in oneshot; no scheduler, startup hook or credential discovery.

HTTP mode reads only /api/usage on an explicitly configured literal-loopback
OpenCodex API. No credentials are read/sent and 401/403 fail closed. File/CLI
export is the fallback when upstream management authorization is required.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
import sqlite3
import socket
import threading
import sys
import time
from urllib.parse import urlencode, urlsplit

import opencodex_usage as usage
import project_config

RESPONSE_LIMIT = 1024 * 1024


def _loopback_target(base_url):
    url = urlsplit(base_url)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', '::1') or
            url.username is not None or url.password is not None or
            url.path not in ('', '/') or url.query or url.fragment or url.port is None):
        raise usage.InvalidSnapshot('Use a literal loopback local HTTP API with an explicit port and no URL credentials')
    return url.hostname, url.port


def _fetch(host, port, query, deadline):
    """One bounded read. No redirects, proxy environment, cookies or auth headers."""
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Aggregate collection deadline exceeded')
        return value
    conn = http.client.HTTPConnection(host, port, timeout=remaining())
    timer = None
    try:
        conn.connect()
        sock = conn.sock
        def interrupt():
            # Also bound slow status/header lines inside http.client parsing.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(remaining(), interrupt)
        timer.daemon = True
        timer.start()
        conn.request('GET', '/api/usage?' + urlencode(query), headers={'Accept': 'application/json'})
        sock.settimeout(remaining())
        response = conn.getresponse()
        if response.status != 200:
            # Never print upstream bodies, redirect locations, headers or addresses.
            raise usage.InvalidSnapshot('Aggregate endpoint returned a non-200 response')
        if response.getheader('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise usage.InvalidSnapshot('Aggregate endpoint did not return JSON')
        if response.getheader('Content-Encoding', 'identity') != 'identity':
            raise usage.InvalidSnapshot('Encoded aggregate responses are not supported')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > RESPONSE_LIMIT):
            raise usage.InvalidSnapshot('Invalid aggregate response size')
        raw = bytearray()
        while not response.isclosed():
            # read1 makes at most one underlying buffered read, so a slow trickle
            # cannot reset a per-recv timeout forever inside read(MAX_BYTES).
            sock.settimeout(remaining())
            remaining()
            chunk = response.read1(min(65536, RESPONSE_LIMIT + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > RESPONSE_LIMIT:
                raise usage.InvalidSnapshot('Aggregate response exceeds 1 MiB')
        remaining()
        if length is not None and len(raw) != int(length):
            raise usage.InvalidSnapshot('Truncated aggregate HTTP response')
        return usage.decode_json(raw)
    finally:
        if timer is not None:
            timer.cancel()
        conn.close()


def fetch_export(base_url, start, end, *, identity='private-mac', timeout=15):
    host, port = _loopback_target(base_url)
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 60:
        raise usage.InvalidSnapshot('Collection deadline must be between 0 and 60 seconds')
    deadline = time.monotonic() + timeout
    windows = []
    for day in usage.dates(start, end):
        since, until = usage.bounds(day)
        body = _fetch(host, port, {'range': 'all', 'surface': 'all',
                                  'since': since, 'until': until}, deadline)
        # Upstream /api/usage also includes accounts and other metadata. Keep
        # only the closed allowlist in RAM; never persist/log raw responses.
        # Direct mode is same-host loopback only, not a forwarded Mac admin API.
        body = usage.project_window(body)
        usage.normalize_export(dict(schema=2, upstream_sha=usage.UPSTREAM_SHA,
            source_id=usage.source_id(identity), start_date=day, end_date=day,
            windows=[dict(date=day, response=body)]))
        windows.append(dict(date=day, response=body))
    return dict(schema=2, upstream_sha=usage.UPSTREAM_SHA,
                source_id=usage.source_id(identity), start_date=start, end_date=end, windows=windows)


def collect(*, input_path=None, base_url=None, path=None, identity=None,
            days=7, end_date=None, timeout=15):
    if not usage.enabled():
        raise usage.InvalidSnapshot('OpenCodex collection is not enabled')
    if input_path and base_url:
        raise usage.InvalidSnapshot('Select either aggregate file or local API')
    if not input_path:
        base_url = base_url or os.environ.get('OPENCODEX_API_BASE_URL', '')
        if not base_url:
            raise usage.InvalidSnapshot('Configure the local API address or select an aggregate file')
    identity = usage.source_id(identity)
    if input_path:
        body = usage.read_export(input_path)
    else:
        if type(days) is not int or not 1 <= days <= usage.MAX_DAYS:
            raise usage.InvalidSnapshot('Expected 1 through 30 UTC days')
        end = end_date or datetime.now(timezone.utc).date().isoformat()
        start = (datetime.fromisoformat(end).date() - timedelta(days=days - 1)).isoformat()
        body = fetch_export(base_url, start, end, identity=identity, timeout=timeout)
    return usage.import_snapshot(body, path=path, expected_source=identity)


def main(argv=None):
    project_config.load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument('--input', help='Private, aggregate-only JSON export')
    transport.add_argument('--base-url', help='Local loopback API base; defaults to OPENCODEX_API_BASE_URL in project .env')
    parser.add_argument('--db', help='Dashboard-owned OpenCodex SQLite cache')
    parser.add_argument('--source-id', default=os.environ.get('OPENCODEX_USAGE_SOURCE_ID', 'private-mac'))
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--end-date', help='Last UTC day, defaults to today')
    parser.add_argument('--timeout', type=float, default=15)
    args = parser.parse_args(argv)
    try:
        result = collect(input_path=args.input, base_url=args.base_url, path=args.db,
                         identity=args.source_id, days=args.days,
                         end_date=args.end_date, timeout=args.timeout)
    except (OSError, ValueError, TypeError, sqlite3.Error, http.client.HTTPException):
        # Last admitted snapshot is untouched. No raw exception/body/credentials.
        print('OpenCodex collection refused or unavailable; saved observations unchanged.', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
