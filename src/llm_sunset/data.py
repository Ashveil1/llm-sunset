"""Load the AI model deprecation dataset.

Data comes from https://deprecations.info (MIT licensed, refreshed daily by
https://github.com/deprecations/deprecations-rss). We try, in order:

1. a fresh local cache (< 24h old)
2. the live feed
3. a stale local cache
4. the snapshot bundled with this package (works fully offline)
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

FEED_URLS = (
    "https://deprecations.info/v1/deprecations.json",
    "https://raw.githubusercontent.com/deprecations/deprecations-rss/main/data.json",
)
CACHE_TTL_SECONDS = 24 * 60 * 60
SNAPSHOT_PATH = Path(__file__).with_name("snapshot.json")


@dataclass(frozen=True)
class Deprecation:
    provider: str
    model_id: str
    shutdown_date: Optional[date]
    deprecation_date: Optional[date]
    replacements: Tuple[str, ...] = field(default_factory=tuple)
    url: str = ""

    def days_left(self, today: date) -> Optional[int]:
        if self.shutdown_date is None:
            return None
        return (self.shutdown_date - today).days


def _parse_date(value: object) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "llm-sunset" / "deprecations.json"


def _download(timeout: float) -> Optional[list]:
    for url in FEED_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llm-sunset"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                return data
        except Exception:
            continue
    return None


def _read_json(path: Path) -> Optional[list]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else None
    except Exception:
        return None


def load_raw(offline: bool = False, data_file: Optional[str] = None, timeout: float = 10.0) -> Tuple[list, str]:
    """Return (records, source_description)."""
    if data_file:
        data = _read_json(Path(data_file))
        if data is None:
            raise SystemExit(f"llm-sunset: could not read data file {data_file}")
        return data, data_file

    cache = cache_path()
    if not offline:
        fresh = cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL_SECONDS
        if fresh:
            data = _read_json(cache)
            if data:
                return data, "cache"
        data = _download(timeout)
        if data:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                pass
            return data, "live"
        data = _read_json(cache)
        if data:
            return data, "cache (stale)"

    data = _read_json(SNAPSHOT_PATH)
    if not data:
        raise SystemExit("llm-sunset: bundled snapshot missing and feed unreachable")
    return data, "bundled snapshot"


def normalize(records: Iterable[dict]) -> List[Deprecation]:
    """Turn raw records into Deprecation objects, one per (provider, model_id)."""
    best: Dict[Tuple[str, str], Tuple[str, Deprecation]] = {}
    for r in records:
        model_id = (r.get("model_id") or "").strip()
        provider = (r.get("provider") or "Unknown").strip()
        if not model_id:
            continue
        dep = Deprecation(
            provider=provider,
            model_id=model_id,
            shutdown_date=_parse_date(r.get("shutdown_date")),
            deprecation_date=_parse_date(r.get("deprecation_date")),
            replacements=tuple(x for x in (r.get("replacement_models") or []) if isinstance(x, str)),
            url=r.get("url") or "",
        )
        key = (provider, model_id)
        seen = r.get("last_observed") or r.get("announcement_date") or ""
        if key not in best or seen >= best[key][0]:
            best[key] = (seen, dep)
    return [d for _, d in best.values()]


def load(offline: bool = False, data_file: Optional[str] = None) -> Tuple[List[Deprecation], str]:
    raw, source = load_raw(offline=offline, data_file=data_file)
    return normalize(raw), source
