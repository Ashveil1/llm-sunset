"""Baseline support: record current findings so CI only reports new ones.

A baseline file looks like::

    {"tool": "llm-sunset", "version": 1, "entries": ["<sha1>", ...]}

Each entry fingerprints one finding: its path (relative to the working
directory when the baseline was written), the model ID and provider, and a
hash of the matched line. Moving a line keeps it baselined; editing the line
or adding a new use reports it as new.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, List, Set

from .scanner import Finding

BASELINE_VERSION = 1


def fingerprint(f: Finding, root: str = "") -> str:
    root = root or os.getcwd()
    try:
        rel = os.path.relpath(f.path, root)
    except ValueError:
        rel = f.path
    line_hash = hashlib.sha1(f.line_text.strip().encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"{rel}\x00{f.deprecation.provider}\x00{f.text}\x00{line_hash}"


def load_baseline(path: str) -> Set[str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    entries = data.get("entries", []) if isinstance(data, dict) else []
    return {e for e in entries if isinstance(e, str)}


def write_baseline(path: str, findings: Iterable[Finding], root: str = "") -> int:
    entries = sorted({fingerprint(f, root) for f in findings})
    payload = {"tool": "llm-sunset", "version": BASELINE_VERSION, "entries": entries}
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def split_baselined(findings: List[Finding], baseline: Set[str], root: str = "") -> tuple:
    """Return (new_findings, baselined_count)."""
    if not baseline:
        return list(findings), 0
    new = [f for f in findings if fingerprint(f, root) not in baseline]
    return new, len(findings) - len(new)
