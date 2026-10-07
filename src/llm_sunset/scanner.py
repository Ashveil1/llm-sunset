"""Find references to deprecated AI model IDs in source files."""

from __future__ import annotations

import bisect
import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set

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
    # Tool state, logs and event streams are not source code. They caused the
    # bulk of false positives (e.g. *.log / *.jsonl transcripts that mention
    # old model names in prose).
    "*.log", "*.log.*", "*.jsonl", "*.ndjson", "*.sarif",
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

# --- Risky (generic-word) model IDs -------------------------------------------
# IDs like "command", "ada", "whisper", "davinci", "o1" are ordinary words or
# plausible variable names. Reporting every quoted occurrence produces thousands
# of false positives (e.g. the "command" key in .mcp.json / plugin.json). These
# are only reported when the same line shows model intent (a model/engine key,
# a provider or SDK name, or a structural prefix like "openai/" or "ft:") and
# the match is not a mapping key ("command": ...).
# A model/engine key that is actually used as a key ("model:", "MODEL=",
# "'engine' :", "MODEL_NAME=", "modelName:"). Stops prose like "suite: test
# deployment command ..." from counting as context.
_MODEL_KEY_POS_RE = re.compile(
    r"(?i)(?<![A-Za-z])(model|models|engine|deployment)(?![A-Za-z])"
    r"(?:_[A-Za-z0-9_]*|[A-Z][a-z]*)*\s*[\"']?\s*[:=]"
)
_PROVIDER_HINT_RE = re.compile(
    r"(?i)(?<![A-Za-z])"
    r"(openai|anthropic|claude|cohere|gemini|google|vertex|bedrock|azure|groq|xai|grok|"
    r"mistral|deepseek|llama|qwen|phi|gemma|openrouter|litellm|langchain|llamaindex|"
    r"transformers|huggingface|replicate|together|fireworks|perplexity|vllm|ollama|"
    r"lmstudio|genai|instructor|dspy|autogen|crewai)(?![A-Za-z])"
)
_QUOTED_SPAN_RE = re.compile(r'''"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\\n])*`''')


def _is_risky(model_id: str) -> bool:
    """True when a model ID looks like an ordinary word/short token."""
    if not model_id or any(c.isspace() for c in model_id):
        return False  # not scannable anyway (see Matcher); kept for safety
    if len(model_id) <= 4:
        return True  # ada, o1, o3, tts, ...
    if any(c.isdigit() for c in model_id):
        return False
    if any(c in model_id for c in "-_./:@"):
        return False  # structured/distinctive: command-r, gpt-realtime, groq/compound, ...
    return True  # command, whisper, babbage, davinci, curie, ...


def ctx(line: str, start: int, end: int, radius: int = 200) -> str:
    """The match plus its neighbourhood.

    On ordinary source lines this is the whole line. On kilobyte-long
    embedded-prose/data lines (agent instructions, tokenizer vocabs, bundles)
    a distant, coincidental "model" must not bless an unrelated word, so the
    window shrinks.
    """
    if len(line) > 2000:
        radius = 50
    return line[max(0, start - radius):min(len(line), end + radius)]


def _provider_hinted(line: str) -> bool:
    """True when a provider/SDK name on the line is not just part of a path.

    ".claude/hooks/notify.sh" is a directory, not a model reference, so a
    provider word preceded by "." or "/" (or followed by "/") doesn't count.
    "openai/gpt-4" style refs still pass via the "/" structural check below.
    """
    for m in _PROVIDER_HINT_RE.finditer(line):
        s, e = m.span()
        before = line[s - 1] if s > 0 else ""
        after = line[e] if e < len(line) else ""
        if before in "./\\" or after == "/":
            continue
        return True
    return False


def _quoted_span(line: str, start: int, end: int):
    for m in _QUOTED_SPAN_RE.finditer(line):
        if m.start() <= start and end <= m.end():
            return m
    return None


def _within_quotes(line: str, start: int, end: int) -> bool:
    return _quoted_span(line, start, end) is not None


def _risky_ok(line: str, start: int, end: int) -> bool:
    """Decide whether a generic-word match on one line is a real model ref."""
    span = _quoted_span(line, start, end)
    if span is None:
        # Unquoted values only count as the value of an explicit model key:
        #   model: command   /   MODEL=command
        # ("suite: test deployment command ..." has no such key.)
        # A bare word followed by ":" is a mapping key, never a model id:
        #   command: foo
        j = end
        while j < len(line) and line[j] in " \t":
            j += 1
        if j < len(line) and line[j] == ":":
            return False
        return bool(_MODEL_KEY_POS_RE.search(line))
    # Inside quotes: a string followed by ":" is a mapping key, not a model
    # id ("command": "uvx ...", tokenizer vocab "command":2339, ...). The
    # check must look past the closing quote, not just past the match.
    j = span.end()
    while j < len(line) and line[j] in " \t":
        j += 1
    if j < len(line) and line[j] == ":":
        return False
    if _MODEL_KEY_POS_RE.search(ctx(line, start, end)) or _provider_hinted(ctx(line, start, end)):
        return True
    lowered = ctx(line, start, end).lower()
    if "models/" in lowered or "ft:" in lowered:
        return True
    if re.search(r"[A-Za-z0-9_.-]+/$", line[max(0, start - 30):start]):
        return True  # provider prefix, e.g. "openai/command" (not "/bashes cmd")
    return False


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

        # IDs containing whitespace are product/feature labels (e.g. Azure's
        # "Agent Builder"), not IDs anyone puts in code. Keep them for the
        # `info`/`upcoming` commands but never match them in scans.
        scannable = {i for i in self.by_id if not any(c.isspace() for c in i)}
        self._risky: Set[str] = {i for i in scannable if _is_risky(i)}
        self._safe: Set[str] = scannable - self._risky
        self._safe_tokenizable = frozenset(i for i in self._safe if _TOKEN_RE.fullmatch(i))
        self._untokenizable_safe = sorted(i for i in self._safe if not _TOKEN_RE.fullmatch(i))

    def find(self, text: str) -> Iterator[tuple]:
        """Yield (offset, matched_id, [Deprecation]) for every hit in text."""
        if not text:
            return
        # Fast path: tokenize + set intersection both run in C.
        hits = self._safe_tokenizable.intersection(_TOKEN_RE.findall(text))
        if hits:
            rx = re.compile(_LEFT + "(" + "|".join(map(re.escape, sorted(hits, key=len, reverse=True))) + ")" + _RIGHT)
            for m in rx.finditer(text):
                yield m.start(1), m.group(1), self.by_id[m.group(1)]
        for uid in self._untokenizable_safe:
            if uid in text:
                rx = re.compile(_LEFT + "(" + re.escape(uid) + ")" + _RIGHT)
                for m in rx.finditer(text):
                    yield m.start(1), m.group(1), self.by_id[m.group(1)]
        risky_hits = self._risky.intersection(_TOKEN_RE.findall(text))
        if not risky_hits:
            return
        rx = re.compile(_LEFT + "(" + "|".join(map(re.escape, sorted(risky_hits, key=len, reverse=True))) + ")" + _RIGHT)
        line_starts = None
        for m in rx.finditer(text):
            if line_starts is None:
                line_starts = [0] + [n.end() for n in re.finditer(r"\n", text)]
            s, e = m.span(1)
            idx = bisect.bisect_right(line_starts, s) - 1
            ls = line_starts[idx]
            nl = text.find("\n", ls)
            line = text[ls:] if nl == -1 else text[ls:nl]
            if _risky_ok(line, s - ls, e - ls):
                yield s, m.group(1), self.by_id[m.group(1)]

    def match_line(self, line: str) -> Iterator[tuple]:
        return self.find(line)


def _compile_globs(globs: Iterable[str]):
    globs = list(globs)
    if not globs:
        return lambda rel, name: False
    rx = re.compile("|".join(fnmatch.translate(g) for g in globs))
    return lambda rel, name: bool(rx.match(name) or rx.match(rel.replace(os.sep, "/")))


def _git_toplevel(path: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    return None


def _apply_gitignore(files: List[Path], dir_roots: List[Path]) -> List[Path]:
    """Drop files ignored by git (respects nested .gitignore files).

    Explicitly named files are filtered by the caller before this runs, so
    everything here came from directory walks and is safe to filter.
    """
    if not files or not dir_roots:
        return files
    # Group candidate files by enclosing git work tree.
    by_top: Dict[str, List[str]] = {}
    top_cache: Dict[str, Optional[str]] = {}
    for f in files:
        parent = str(f.parent)
        top = top_cache.get(parent)
        if parent not in top_cache:
            top = _git_toplevel(parent)
            top_cache[parent] = top
        if not top:
            continue
        try:
            rel = os.path.relpath(str(f), top)
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        by_top.setdefault(top, []).append(rel)
    if not by_top:
        return files
    ignored: Set[str] = set()
    for top, rel_paths in by_top.items():
        try:
            proc = subprocess.run(
                ["git", "-C", top, "check-ignore", "--stdin", "-z"],
                input="\0".join(rel_paths) + "\0",
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return files  # git went away; scan everything
        if proc.returncode not in (0, 1):
            continue  # not a repo / git error: keep files
        if proc.stdout:
            ignored.update(a for a in proc.stdout.split("\0") if a)
    if not ignored:
        return files
    out = []
    for f in files:
        drop = False
        for top, rel_paths in by_top.items():
            try:
                rel = os.path.relpath(str(f), top)
            except ValueError:
                continue
            if rel in ignored:
                drop = True
                break
        if not drop:
            out.append(f)
    return out


def iter_files(
    paths: Sequence[str],
    exclude: Sequence[str],
    include_docs: bool,
    use_gitignore: bool = True,
) -> Iterator[Path]:
    globs = list(DEFAULT_EXCLUDE_GLOBS) + list(exclude)
    if not include_docs:
        globs += DOC_GLOBS
    file_excluded = _compile_globs(globs)
    dir_excluded = _compile_globs(exclude)
    explicit: List[Path] = []
    walked: List[Path] = []
    dir_roots: List[Path] = []
    for p in paths:
        root = Path(p)
        if root.is_file():
            explicit.append(root)  # explicitly named files are always scanned
            continue
        if not root.is_dir():
            continue
        dir_roots.append(root)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = [
                d for d in dirnames
                if d not in DEFAULT_EXCLUDE_DIRS
                and not d.startswith(".")  # tool state dirs: .zcode, .qwen, .cache, ...
                and not dir_excluded(os.path.normpath(os.path.join(rel_dir, d)), d)
            ]
            for f in filenames:
                if not file_excluded(os.path.normpath(os.path.join(rel_dir, f)), f):
                    walked.append(Path(dirpath) / f)
    if use_gitignore and walked:
        walked = _apply_gitignore(walked, dir_roots)
    yield from explicit
    yield from walked


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


def scan(
    paths: Sequence[str],
    matcher: Matcher,
    exclude: Sequence[str] = (),
    include_docs: bool = False,
    use_gitignore: bool = True,
) -> List[Finding]:
    out: List[Finding] = []
    for f in iter_files(paths, exclude, include_docs, use_gitignore):
        out.extend(scan_file(f, matcher))
    out.sort(key=lambda x: (x.path, x.line, x.column, x.deprecation.provider))
    return out
