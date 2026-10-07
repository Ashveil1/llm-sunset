"""Project config file support: `.llm-sunset.toml` or `[tool.llm-sunset]` in pyproject.toml.

Example `.llm-sunset.toml`:

    exclude = ["tests/*", "docs/*"]
    providers = ["openai", "anthropic"]
    fail_within = 60
    warn_within = 180
    format = "text"
    group_by = "model"
    include_docs = false

Scalar options are defaults: any CLI flag overrides them. `exclude` from the
config file and the CLI are combined. `providers` from the CLI replaces the
config file list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_NAME = ".llm-sunset.toml"
KNOWN_KEYS = {
    "exclude", "providers", "provider", "fail_within", "warn_within",
    "format", "group_by", "include_docs", "include_hidden",
    "gitignore", "default_excludes",
}


def _mini_toml_parse(text: str) -> Dict:
    """Parse the small TOML subset we use (for Python < 3.11 without tomllib).

    Supports top-level `key = value` pairs and a single `[tool.llm-sunset]`
    section. Values: booleans, integers, quoted strings and lists of strings
    (possibly multi-line). Anything else is ignored.
    """
    out: Dict[str, dict] = {}
    section: Optional[str] = None
    buf_key: Optional[str] = None
    buf_lines: List[str] = []

    def target() -> dict:
        if section in (None, "tool.llm-sunset"):
            return out.setdefault(section or "", {})
        return {}

    def strip_comment(s: str) -> str:
        in_str: Optional[str] = None
        esc = False
        for i, ch in enumerate(s):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == in_str:
                    in_str = None
            elif ch in "\"'":
                in_str = ch
            elif ch == "#":
                return s[:i]
        return s

    def parse_value(s: str):
        s = s.strip()
        if s in ("true", "false"):
            return s == "true"
        try:
            return int(s)
        except ValueError:
            pass
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            body = s[1:-1]
            if s[0] == '"':
                body = body.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
            return body
        if s.startswith("[") and s.endswith("]"):
            items = []
            inner = s[1:-1].strip()
            if not inner:
                return items
            cur, in_str, esc, q = "", None, False, ""
            for ch in inner:
                if in_str:
                    cur += ch
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == q:
                        in_str = None
                elif ch in "\"'":
                    in_str, q, cur = True, ch, cur + ch
                elif ch == ",":
                    v = parse_value(cur)
                    if isinstance(v, str):
                        items.append(v)
                    cur = ""
                else:
                    cur += ch
            v = parse_value(cur)
            if isinstance(v, str):
                items.append(v)
            return items
        return None

    for raw in text.splitlines():
        line = strip_comment(raw).strip()
        if not line:
            continue
        if buf_key is not None:
            buf_lines.append(line)
            if line.rstrip().endswith("]"):
                v = parse_value(" ".join(buf_lines))
                if isinstance(v, list):
                    target()[buf_key] = v
                buf_key, buf_lines = None, []
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        if value.startswith("[") and not value.rstrip().endswith("]"):
            buf_key, buf_lines = key, [value]
            continue
        v = parse_value(value)
        if isinstance(v, (bool, int, str, list)):
            target()[key] = v
    merged = dict(out.get("", {}))
    merged.update(out.get("tool.llm-sunset", {}))
    return {k: v for k, v in merged.items() if k in KNOWN_KEYS}


def load_toml_file(path: Path) -> Dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import tomllib  # Python 3.11+
        data = tomllib.loads(text)
        section = data.get("tool", {}).get("llm-sunset", {})
        top = {k: v for k, v in data.items() if k in KNOWN_KEYS}
        if isinstance(section, dict):
            top.update({k: v for k, v in section.items() if k in KNOWN_KEYS})
        return top
    except Exception:
        pass
    try:
        return _mini_toml_parse(text)
    except Exception:
        return {}


def find_config(start: Optional[str] = None) -> Optional[Path]:
    """Locate `.llm-sunset.toml` or a `pyproject.toml` with our section, upward from cwd."""
    here = Path(start or os.getcwd()).resolve()
    while True:
        cand = here / CONFIG_NAME
        if cand.is_file():
            return cand
        py = here / "pyproject.toml"
        if py.is_file() and load_toml_file(py):
            return py
        parent = here.parent
        if parent == here:
            return None
        here = parent


def load_config(start: Optional[str] = None) -> Dict:
    path = find_config(start)
    if path is None:
        return {}
    return load_toml_file(path)
