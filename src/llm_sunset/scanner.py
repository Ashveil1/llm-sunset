"""Find references to deprecated AI model IDs in source files."""

from __future__ import annotations

import bisect
import fnmatch
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

from .data import Deprecation

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor",
    ".venv", "venv", "env", ".env.d", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".nox", "dist", "build", "out", "target", ".next",
    ".nuxt", ".svelte-kit", ".turbo", ".cache", "coverage", ".idea", ".vscode",
    "site-packages",
}
DEFAULT_EXCLUDE_GLOBS = [
    "*.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock",
    "uv.lock", "Cargo.lock", "go.sum", "*.min.js", "*.map", "*.svg",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico", "*.pdf", "*.zip",
    "*.gz", "*.tar", "*.woff", "*.woff2", "*.ttf", "*.mp3", "*.mp4", "*.wasm",
    "*.so", "*.dylib", "*.dll", "*.exe", "*.bin", "*.pyc", "*.ipynb_checkpoints",
]
# Prose files mention old models all the time ("we migrated from gpt-3.5-turbo").
DOC_GLOBS = ["*.md", "*.mdx", "*.rst", "*.txt", "*.adoc", "CHANGELOG*", "HISTORY*"]

MAX_FILE_BYTES = 2 * 1024 * 1024
IGNORE_LINE = "llm-sunset: ignore"
IGNORE_FILE = "llm-sunset: ignore-file"

# Characters that may legally sit right before / after a model ID. "/" ":" and
# "@" are allowed so that "openai/gpt-4", "ft:gpt-4o:org::id" and
# "claude-3-5-sonnet@20240620" still match.
_LEFT = r"(?<![A-Za-z0-9._+-])"
_RIGHT = r"(?![A-Za-z0-9_+-])(?!\.[A-Za-z0-9])"

# Same character classes as _LEFT/_RIGHT; a token never ends with ".".
_TOKEN_RE = re.compile(r"[A-Za-z0-9_+-](?:[A-Za-z0-9._+-]*[A-Za-z0-9_+-])?")

# Platforms that resell models under their own (often very different) lifecycle
# dates. Off by default so OpenAI/Anthropic users don't get Azure dates.
PLATFORM_PROVIDERS = ("azure", "google vertex", "bedrock")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    text: str  # exact text matched in the file
    deprecation: Deprecation

    def status(self, today: date) -> str:
        left = self.deprecation.days_left(today)
        if left is None:
            return "deprecated"
        return "retired" if left <= 0 else "retiring"


class Matcher:
    def __init__(self, deprecations: Sequence[Deprecation], providers: Optional[Sequence[str]] = None):
        wanted = [p.lower() for p in providers] if providers else []
        everything = "all" in wanted
        self.by_id: Dict[str, List[Deprecation]] = {}
        for d in deprecations:
            prov = d.provider.lower()
            if everything:
                pass
            elif wanted:
                if not any(w in prov for w in wanted):
                    continue
            elif any(p in prov for p in PLATFORM_PROVIDERS):
                continue
            self.by_id.setdefault(d.model_id, []).append(d)

        self._ids = frozenset(self.by_id)
        # IDs like "babbage", "davinci" or "command" are ordinary words; only
        # match them when they are the entire contents of a string literal.
        self._words = frozenset(i for i in self.by_id if not any(c.isdigit() for c in i))

    def find(self, text: str) -> Iterator[tuple]:
        """Yield (offset, matched_id, [Deprecation]) for every hit in text."""
        # Fast path: tokenize + set intersection both run in C.
        hits = self._ids.intersection(_TOKEN_RE.findall(text))
        if not hits:
            return
        ids = sorted(hits - self._words, key=len, reverse=True)
        words = sorted(hits & self._words, key=len, reverse=True)
        if ids:
            rx = re.compile(_LEFT + "(" + "|".join(map(re.escape, ids)) + ")" + _RIGHT)
            for m in rx.finditer(text):
                yield m.start(1), m.group(1), self.by_id[m.group(1)]
        if words:
            rx = re.compile(r"""(["'`])(""" + "|".join(map(re.escape, words)) + r")\1")
            for m in rx.finditer(text):
                yield m.start(2), m.group(2), self.by_id[m.group(2)]

    def match_line(self, line: str) -> Iterator[tuple]:
        return self.find(line)


def _compile_globs(globs: Iterable[str]):
    globs = list(globs)
    if not globs:
        return lambda rel, name: False
    rx = re.compile("|".join(fnmatch.translate(g) for g in globs))
    return lambda rel, name: bool(rx.match(name) or rx.match(rel.replace(os.sep, "/")))


def iter_files(paths: Sequence[str], exclude: Sequence[str], include_docs: bool) -> Iterator[Path]:
    globs = list(DEFAULT_EXCLUDE_GLOBS) + list(exclude)
    if not include_docs:
        globs += DOC_GLOBS
    file_excluded = _compile_globs(globs)
    dir_excluded = _compile_globs(exclude)
    for p in paths:
        root = Path(p)
        if root.is_file():
            yield root  # explicitly named files are always scanned
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                d for d in dirnames
                if d not in DEFAULT_EXCLUDE_DIRS
                and not dir_excluded(os.path.normpath(os.path.join(rel_dir, d)), d)
            ]
            for f in filenames:
                if not file_excluded(os.path.normpath(os.path.join(rel_dir, f)), f):
                    yield Path(dirpath) / f


def scan_file(path: Path, matcher: Matcher) -> List[Finding]:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
    except OSError:
        return []
    if len(raw) > MAX_FILE_BYTES:
        return []
    if b"\0" in raw[:8192]:
        return []
    text = raw.decode("utf-8", errors="replace")
    if IGNORE_FILE in text:
        return []
    findings = []
    line_starts = None
    for offset, matched, deps in matcher.find(text):
        if line_starts is None:
            line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
        idx = bisect.bisect_right(line_starts, offset) - 1
        start = line_starts[idx]
        end = text.find("\n", start)
        line = text[start:] if end == -1 else text[start:end]
        if IGNORE_LINE in line:
            continue
        for d in deps:
            findings.append(Finding(str(path), idx + 1, offset - start + 1, matched, d))
    return findings


def scan(paths: Sequence[str], matcher: Matcher, exclude: Sequence[str] = (), include_docs: bool = False) -> List[Finding]:
    out: List[Finding] = []
    for f in iter_files(paths, exclude, include_docs):
        out.extend(scan_file(f, matcher))
    out.sort(key=lambda x: (x.path, x.line, x.column, x.deprecation.provider))
    return out
