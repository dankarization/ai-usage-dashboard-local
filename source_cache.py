"""Bounded in-process single-flight cache for aggregate reads, never credentials.

Keys include the exact UTC window and projection. Fetch timestamps belong to
stored results, not to the cache hit. Invalidation fences in-flight readers so
an old read cannot repopulate a new refresh generation.
"""
from collections import OrderedDict
import threading
import time


class SourceCache:
    def __init__(self, ttl=30, failure_ttl=5, limit=32):
        self.ttl, self.failure_ttl, self.limit = ttl, failure_ttl, limit
        self._condition = threading.Condition()
        self._entries = OrderedDict()
        self._inflight = set()
        self._generation = 0

    def invalidate(self):
        with self._condition:
            self._generation += 1
            self._entries.clear()
            self._condition.notify_all()

    def get(self, key, collect, *, force=False):
        with self._condition:
            generation = self._generation
            flight = (generation, key)
            waited = False
            while flight in self._inflight:
                waited = True
                self._condition.wait()
                if generation != self._generation:
                    generation = self._generation
                    flight = (generation, key)
            entry = self._entries.get(key)
            if entry and (not force or waited) and time.monotonic() < entry[0]:
                self._entries.move_to_end(key)
                return entry[1], 'coalesced' if waited else 'cached'
            self._inflight.add(flight)
        try:
            value = collect()
            meta = value.get('meta', {})
            failed = any(v in ('unavailable', 'cached', 'partial', 'stale', 'refreshing')
                         for k, v in meta.items() if k.endswith('_status'))
            failed |= any(row.get('status') in ('unavailable', 'stale')
                          for row in value.get('quotas', []))
            with self._condition:
                if generation == self._generation:
                    lifetime = 1 if meta.get('openclaw_refreshing') else self.failure_ttl if failed else self.ttl
                    self._entries[key] = (time.monotonic() + lifetime, value)
                    self._entries.move_to_end(key)
                    while len(self._entries) > self.limit:
                        self._entries.popitem(last=False)
            return value, 'fresh'
        finally:
            with self._condition:
                self._inflight.discard(flight)
                self._condition.notify_all()
