"""Version-pinned, bounded SQLite aggregate reader; no OpenClaw imports or RPC.

Reads only cache_entries headers and matching numeric quarter blobs. It cannot
certify freshness against transcripts, so coverage is always explicitly partial.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from collections import Counter
import math
from pathlib import Path
import sqlite3
import struct
import time

from openclaw_snapshot import FIELDS, validate

ROLLUP = 'session-cost-usage-rollup-v3'
QUARTER = 'session-cost-usage-quarters-v1'
PARTITION = 'session-cost-usage-projection-v2'
WIDTHS = (25, 4, 11, 3, 3, 3)
OFFSETS = (0, 25, 29, 40, 43, 46)
DERIVED = {7: (3, 4, 5, 6), 8: (9, 10, 11, 12), 14: (15, 16),
           33: (29, 30, 31, 32), 34: (35, 36, 37, 38)}
MAX_ROWS = 20000
MAX_READ_BYTES = 64 * 1024 * 1024


def decode(month, header, blob):
    h = json.loads(header)
    counts, codes = h['rows'], h['columns']
    if (h['format'] != 2 or len(counts) != 6 or len(codes) != 49 or
            any(type(n) is not int or not 0 <= n <= 100000 for n in counts)):
        raise ValueError('Unsupported quarter format')
    columns = []
    int_count = sum(counts[t] for t, w in enumerate(WIDTHS)
                    for c in codes[OFFSETS[t]:OFFSETS[t]+w] if c == 'i')
    ip, fp = 0, ((int_count * 4 + 7) // 8) * 8
    for t, width in enumerate(WIDTHS):
        for _ in range(width):
            index = len(columns)
            code, count = codes[index], counts[t]
            if code == '0':
                values = [0] * count
            elif code == 's' and index in DERIVED:
                values = None
            elif code in ('i', 'd'):
                pos, size = (ip, 4) if code == 'i' else (fp, 8)
                if pos + size * count > len(blob):
                    raise ValueError('Truncated quarter data')
                values = list(struct.unpack_from('<' + code.replace('i', 'I') * count, blob, pos))
                if code == 'i': ip += count * size
                else: fp += count * size
            else:
                raise ValueError('Invalid quarter column')
            columns.append(values)
    if fp != len(blob) or ip != int_count * 4:
        raise ValueError('Invalid quarter size')
    for index, parts in DERIVED.items():
        if columns[index] is None:
            columns[index] = [sum(values) for values in zip(*(columns[p] for p in parts))]
    if any(not math.isfinite(v) or v < 0 for col in columns for v in col):
        raise ValueError('Invalid quarter numbers')
    start = datetime.combine(date.fromisoformat(month), datetime.min.time(), timezone.utc)
    owned, previous_q = 0, -1
    for i in range(counts[1]):
        q, model_id, flag = columns[25][i], columns[26][i], columns[28][i]
        if (int(q) != q or not 0 <= q < counts[0] or q < previous_q or
                int(model_id) != model_id or not 0 <= model_id < len(h['models']) or flag not in (0, 1)):
            raise ValueError('Invalid quarter attribution')
        q, model_id = int(q), int(model_id)
        previous_q = q
        pair = h['models'][model_id]
        if not isinstance(pair, list) or len(pair) != 2 or any(p is not None and not isinstance(p, str) for p in pair):
            raise ValueError('Invalid model dictionary')
        provider, model = (part or 'unknown' for part in pair)
        base, row = (29, owned) if flag else (3, q)
        if flag: owned += 1
        if row >= counts[2] and flag:
            raise ValueError('Missing owned model totals')
        slot = columns[0][q]
        if int(slot) != slot or not 0 <= slot < 31 * 96:
            raise ValueError('Invalid quarter time')
        day = (start + timedelta(minutes=15 * slot)).date().isoformat()
        if day[:7] != month[:7]:
            raise ValueError('Quarter outside month')
        values = {field: columns[base + j][row] for j, field in enumerate(FIELDS)}
        yield dict(date=day, provider=provider, model=model, **values)
    if owned != counts[2]:
        raise ValueError('Unconsumed owned model totals')


def collect(root, *, now=None, deadline=None):
    now = now or datetime.now(timezone.utc)
    deadline = deadline if deadline is not None else time.monotonic() + 25
    start, end = (now.date() - timedelta(days=89)).isoformat(), now.date().isoformat()
    root = Path(root).resolve()
    paths = sorted(root.glob('*/agent/openclaw-agent.sqlite'))
    if not paths or len(paths) > 64:
        raise ValueError('Missing or excessive agent databases')
    rows, stamps, seen = {}, [], set()
    budget, rollups, months, excluded_old, supported = MAX_READ_BYTES, 0, 0, 0, 0
    excluded_archive = 0
    excluded_ambiguous = 0
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('Aggregate read deadline exceeded')
    for path in paths:
        check()
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Unexpected agent database locator')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as conn:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('PRAGMA cache_size=-1024')
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            # No transcript tables, schema repair or runtime bootstrap may be queried.
            conn.set_authorizer(lambda op, table, column, *_:
                sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_READ and table != 'cache_entries'
                else sqlite3.SQLITE_OK)
            conn.execute('BEGIN')
            query = 'SELECT key,value_json,updated_at FROM cache_entries WHERE scope=? LIMIT ?'
            plan = conn.execute('EXPLAIN QUERY PLAN ' + query, (ROLLUP, MAX_ROWS + 1)).fetchall()
            if not any('SEARCH' in p[3] and 'scope=?' in p[3] for p in plan):
                raise ValueError('Aggregate scope index unavailable')
            headers = list(conn.execute(query, (ROLLUP, MAX_ROWS + 1)))
            if len(headers) + rollups > MAX_ROWS or sum(len(h or '') + len(k) for k, h, _ in headers) > budget:
                raise ValueError('Aggregate inventory budget exceeded')
            def instance_id(key):
                prefix = 'sqlite:' + path.parent.parent.name + ':'
                suffix = ':' + str(path)
                if key.startswith(prefix) and key.endswith(suffix):
                    return key[len(prefix):-len(suffix)]
                legacy = Path(key)
                if legacy.parent == path.parent.parent / 'sessions' and '.jsonl' in legacy.name:
                    return legacy.name.split('.jsonl', 1)[0]
                return None
            identities = Counter(instance_id(key) for key, _, _ in headers)
            for key, header, updated in headers:
                check()
                rollups += 1
                if rollups > MAX_ROWS:
                    raise ValueError('Aggregate row budget exceeded')
                budget -= len(header or '') + len(key)
                if budget < 0: raise ValueError('Aggregate byte budget exceeded')
                envelope = json.loads(header)
                projection = envelope.get('projection', {})
                if envelope.get('version') in (6, 8) and envelope.get('format') == 1:
                    excluded_old += 1
                    continue
                if (envelope.get('version') != 11 or envelope.get('format') != 1 or
                        projection.get('version') != 3 or not isinstance(projection.get('dates'), list)):
                    raise ValueError('Unsupported committed rollup version')
                # Uniquely bind SQLite keys to this database, not aliases/import copies.
                prefix = 'sqlite:' + path.parent.parent.name + ':'
                suffix = ':' + str(path)
                if key.startswith(prefix) and key.endswith(suffix):
                    identity = key[len(prefix):-len(suffix)]
                else:
                    # Historic JSONL-generated aggregates are read as numbers only.
                    # No stat/open of the transcript. Refuse aliases of one instance.
                    legacy = Path(key)
                    if legacy.parent.name == 'session-archive-read-cache':
                        # Temporary materializations can alias original instance rows.
                        excluded_archive += 1
                        continue
                    if legacy.parent != path.parent.parent / 'sessions' or '.jsonl' not in legacy.name:
                        raise ValueError('Foreign aggregate identity')
                    identity = legacy.name.split('.jsonl', 1)[0]
                if identities[identity] > 1:
                    # Several artifacts claim the same session instance. Without a
                    # content/inventory read we cannot prove disjointness; omit all.
                    excluded_ambiguous += 1
                    continue
                supported += 1
                if not identity or (path.parent.parent.name, identity) in seen:
                    raise ValueError('Duplicate session aggregate')
                seen.add((path.parent.parent.name, identity))
                stamp = envelope['scannedAt']
                if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= stamp <= max(now.timestamp(), time.time())*1000:
                    raise ValueError('Invalid computed timestamp')
                stamps.append(stamp)
                dates = projection['dates']
                if len(dates) != len(set(dates)) or len(dates) > 1200:
                    raise ValueError('Invalid committed month inventory')
                for month in dates:
                    if date.fromisoformat(month).day != 1:
                        raise ValueError('Invalid aggregate month')
                    if not start[:7] <= month[:7] <= end[:7]: continue
                    check()
                    qkey = json.dumps(key, ensure_ascii=False, separators=(',', ':')) + ':' + month
                    found = conn.execute('''SELECT q.value_json,q.blob FROM cache_entries q
                        JOIN cache_entries p ON p.scope=? AND p.key=q.key AND p.updated_at=q.updated_at
                        WHERE q.scope=? AND q.key=? AND length(q.blob)<=?''',
                        (PARTITION, QUARTER, qkey, min(budget, 4*1024*1024))).fetchone()
                    if found is None:
                        raise ValueError('Committed quarter/partition pair missing')
                    h, blob = found
                    budget -= len(h or '') + len(blob)
                    if budget < 0: raise ValueError('Aggregate byte budget exceeded')
                    months += 1
                    for row in decode(month, h, blob):
                        if not start <= row['date'] <= end: continue
                        ident = tuple(row[k] for k in ('date', 'provider', 'model'))
                        target = rows.setdefault(ident, dict(date=row['date'], provider=row['provider'],
                                                       model=row['model'], **dict.fromkeys(FIELDS, 0)))
                        for field in FIELDS: target[field] += row[field]
                    check()
            conn.rollback()
    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='seconds')
    check()
    if supported == 0:
        raise ValueError('No supported committed aggregates')
    return validate(dict(schema=1, source='openclaw-committed-quarters-v2', startDate=start,
        endDate=end, collectedAt=now.isoformat(timespec='seconds'), daily=list(rows.values()),
        coverage=dict(complete=False, strategy='committed-only-no-refresh',
            liveFreshnessVerified=False, committedRowsComplete=not (excluded_old or excluded_archive or excluded_ambiguous), agents=len(paths),
            rollups=rollups, supportedRollups=supported, excludedOldFormatRollups=excluded_old,
            excludedArchiveMaterializations=excluded_archive, excludedAmbiguousRollups=excluded_ambiguous,
            months=months, aggregateBytes=MAX_READ_BYTES-budget,
            oldestComputedAtUtc=iso(min(stamps)) if stamps else None,
            newestComputedAtUtc=iso(max(stamps)) if stamps else None,
            categoryMismatchModelDays=sum(r['totalTokens'] != sum(r[f] for f in FIELDS[:4]) for r in rows.values()),
            reason='Committed numeric usage only; old-format/ambiguous/archive-copy rows excluded; uncached/live/untimestamped activity unknown')))
