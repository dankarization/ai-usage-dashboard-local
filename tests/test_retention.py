"""Bounded dashboard-owned cache retention without touching unknown artifacts."""
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import antigravity_usage as ag
import nordrouter_usage as nr


def _entry(day, response_id):
    return {"model": "gemini-3-flash-a", "timestamp": day.isoformat(),
            "input": 10, "response_id": response_id}


def test_antigravity_retains_full_display_horizon_and_bounds_repeated_ingest(tmp_path):
    today = date.today()
    path = tmp_path / "antigravity_usage_cache.json"
    good = [_entry(today - timedelta(days=offset), str(offset)) for offset in (0, 6, 7, 29, 30, 89)]
    old = _entry(today - timedelta(days=90), "old")
    undated = {"model": "gemini-3-flash-a", "input": 99, "response_id": "undated"}
    load = lambda: ag.load_cache(str(path))
    save = lambda entries: ag.save_cache(str(path), entries)
    before = good + [old, undated]
    for _ in range(3):
        result = ag.ingest_entries(before, load_cached=load, save_cached=save)
    assert result["total_cache"] == len(good)
    assert {entry["response_id"] for entry in load()} == {entry["response_id"] for entry in good}
    # 7/30-day bucket totals are unchanged by pruning 90-day-old entries.
    for days in (7, 30):
        cutoff = today - timedelta(days=days - 1)
        total_before = sum(entry["input"] for entry in before
                           if ag.entry_to_date(entry) and ag.entry_to_date(entry) >= cutoff)
        total_after = sum(entry["input"] for entry in load()
                          if ag.entry_to_date(entry) >= cutoff)
        assert total_after == total_before
    assert len(load()) == len(good)


def test_antigravity_refresh_removes_expired_entries_without_new_data(tmp_path):
    today = date.today()
    path = tmp_path / "antigravity_usage_cache.json"
    path.write_text(json.dumps({"entries": [_entry(today - timedelta(days=91), "old")]}))
    saved = []
    result = ag.load_usage(
        load_cached=lambda: ag.load_cache(str(path)),
        save_cached=lambda entries: (saved.append(entries), ag.save_cache(str(path), entries)),
        load_sync=lambda: {}, save_sync=lambda _: None, discover=lambda: [], fetch=lambda _: [],
        rpc_call=lambda *args: {}, parse_usage=lambda *args: [], classify=lambda _: "gemini",
        conversation_dirs=[],
    )
    assert saved == [[]]
    assert ag.load_cache(str(path)) == []
    assert not any(result.values())


def test_antigravity_cleans_only_abandoned_owned_temp_files(tmp_path):
    abandoned = tmp_path / ".antigravity-cache-abandoned"
    abandoned.write_text("partial")
    outside = tmp_path / "outside"
    outside.write_text("keep")
    symlink = tmp_path / ".antigravity-cache-link"
    symlink.symlink_to(outside)
    unknown = tmp_path / "verification.png"
    unknown.write_text("keep")
    old = time.time() - 25 * 60 * 60
    os.utime(abandoned, (old, old))
    os.utime(unknown, (old, old))
    ag.save_cache(str(tmp_path / "antigravity_usage_cache.json"), [])
    assert not abandoned.exists()
    assert symlink.is_symlink() and outside.read_text() == "keep"
    assert unknown.read_text() == "keep"


def test_nordrouter_prunes_only_owned_variants_and_preserves_snapshots(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"balance_usd": 8, "currency": "USD"}
    monkeypatch.setattr(nr.requests, "get", lambda *args, **kwargs: Response())
    client = nr.Client("fake", tmp_path)
    client.root.mkdir()
    old = time.time() - nr.CACHE_RETENTION_SECONDS - 10
    expired = client.root / "analytics-days_1.json"
    expired.write_text("{}")
    os.utime(expired, (old, old))
    canonical = client.root / "analytics-days_30.json"
    canonical.write_text("{}")
    os.utime(canonical, (old, old))
    unknown = client.root / "notes.json"
    unknown.write_text("keep")
    target = tmp_path / "outside.json"
    target.write_text("outside")
    (client.root / "analytics-days_2.json").symlink_to(target)
    for day in range(100, 170):
        (client.root / f"analytics-days_{day}.json").write_text("{}")
    assert client.get("balance")[0]["balance_usd"] == 8
    assert not expired.exists()
    assert canonical.exists() and unknown.exists() and target.read_text() == "outside"
    assert (client.root / "analytics-days_2.json").is_symlink()
    variants = list(client.root.glob("analytics-days_1*.json"))
    assert len(variants) <= nr.CACHE_VARIANT_LIMIT
    before = sorted(path.name for path in client.root.iterdir())
    client.get("balance")
    assert sorted(path.name for path in client.root.iterdir()) == before


def test_nordrouter_rejects_symlink_root_and_query_escape(tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    client = nr.Client("fake", tmp_path / "cache")
    client.root.parent.mkdir()
    client.root.symlink_to(external, target_is_directory=True)
    try:
        client.get("balance")
    except ValueError:
        pass
    else:
        raise AssertionError("symlink cache root accepted")
    client.root.unlink()
    try:
        client.get("analytics", days="../outside")
    except ValueError:
        pass
    else:
        raise AssertionError("path-like query accepted")
    assert list(external.iterdir()) == []
