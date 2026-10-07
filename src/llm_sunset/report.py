"""Render findings as text, JSON, Markdown, SARIF or GitHub annotations."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from typing import Dict, List, Optional

from . import __version__
from .scanner import Finding

STATUS_ORDER = {"retired": 0, "retiring": 1, "deprecated": 2}


def group_findings(findings: List[Finding], by: str, sev_of=None):
    """Group findings by model or file, worst-severity first.

    Returns a list of (key, [finding]) with errors before warnings.
    `by` is "model" (provider + model_id) or "file" (path).
    """
    groups: Dict = {}
    for f in findings:
        key = (f.deprecation.provider, f.deprecation.model_id) if by == "model" else (f.path,)
        groups.setdefault(key, []).append(f)

    ranked = []
    for key, items in groups.items():
        sev = 0 if (sev_of is not None and any(sev_of(i) == "error" for i in items)) else 1
        ranked.append(((sev, [str(k) for k in key]), key, items))
    ranked.sort(key=lambda r: r[0])
    return [(key, items) for _, key, items in ranked]


def severity(f: Finding, today: date, fail_within: int) -> str:
    left = f.deprecation.days_left(today)
    if left is not None and left <= fail_within:
        return "error"
    return "warning"


def describe(f: Finding, today: date) -> str:
    d = f.deprecation
    left = d.days_left(today)
    if left is None:
        when = "deprecated (no shutdown date announced)"
    elif left <= 0:
        when = f"RETIRED on {d.shutdown_date} ({-left} days ago)"
    else:
        when = f"retires {d.shutdown_date} ({left} days left)"
    msg = f"{d.provider} model '{d.model_id}' {when}"
    if d.replacements:
        msg += f"; replace with: {', '.join(d.replacements)}"
    return msg


_RESET = "\033[0m"
_CODES = {
    "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "grey": "90",
    "brightred": "91", "brightgreen": "92", "brightyellow": "93", "brightcyan": "96",
}


def _use_color(stream, mode: str = "auto") -> bool:
    """Should we emit ANSI codes? auto = only for a real terminal."""
    if os.environ.get("NO_COLOR"):
        return False
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def _visible_len(s: str) -> int:
    """Length of a string ignoring ANSI escape sequences."""
    n, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = len(s) if j == -1 else j + 1
            continue
        n += 1
        i += 1
    return n


def _pad(s: str, width: int, align: str = "left") -> str:
    gap = max(0, width - _visible_len(s))
    if align == "right":
        return " " * gap + s
    return s + " " * gap


def _truncate(s: str, width: int) -> str:
    if width <= 1 or _visible_len(s) <= width:
        return s
    return s[: width - 1] + "…"


def status_parts(f: Finding, today: date) -> tuple:
    """(text, colour) describing the lifecycle status of a finding."""
    d = f.deprecation
    left = d.days_left(today)
    if left is None:
        return "deprecated", "magenta"
    if left < 0:
        return f"retired {abs(left)}d ago", "brightred"
    if left == 0:
        return "retired today", "brightred"
    if left <= 30:
        return f"in {left}d", "brightred"
    if left <= 90:
        return f"in {left}d", "brightyellow"
    return f"in {left}d", "yellow"


def _summary_line(findings: List[Finding], today: date, fail_within: int, color_fn=None) -> str:
    errors = sum(1 for f in findings if severity(f, today, fail_within) == "error")
    warnings = len(findings) - errors
    models = {(f.deprecation.provider, f.deprecation.model_id) for f in findings}
    files = {f.path for f in findings}
    c = color_fn or (lambda code, s: s)
    return (
        f"{len(models)} model(s) in {len(files)} file(s), {len(findings)} location(s): "
        f"{c('31;1', str(errors) + ' error(s)')}, "
        f"{c('33', str(warnings) + ' warning(s)')}"
    )


def _rel(path: str, root: Optional[str]) -> str:
    if not root:
        return path
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


class _Ink:
    """Tiny ANSI helper that stays a no-op when colour is disabled."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, colour: str, s: str, bold: bool = False) -> str:
        if not self.enabled:
            return s
        code = _CODES.get(colour, "0")
        if bold:
            code = "1;" + code
        return f"\033[{code}m{s}{_RESET}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _render_summary(findings: List[Finding], today: date, fail_within: int,
                    c: _Ink, source: str, root: Optional[str]) -> str:
    errors = sum(1 for f in findings if severity(f, today, fail_within) == "error")
    warnings = len(findings) - errors
    models = {(f.deprecation.provider, f.deprecation.model_id) for f in findings}
    files = {f.path for f in findings}
    bit = [c("bold", str(len(models))) + " models in " + c("bold", str(len(files))) + " files"]
    if errors:
        bit.append(c("brightred", _plural(errors, "error"), bold=True))
    if warnings:
        bit.append(c("brightyellow", _plural(warnings, "warning")))
    tail = f"fail-within {fail_within}d · data: {source}"
    return "  ·  ".join(bit) + c("grey", "  ·  " + tail)


def _render_by_model(findings, today, fail_within, c, root, max_width) -> List[str]:
    sev_of = lambda f: severity(f, today, fail_within)  # noqa: E731
    rows = []
    for _key, items in group_findings(findings, "model", sev_of):
        f0 = items[0]
        d = f0.deprecation
        st, st_col = status_parts(f0, today)
        worst = "error" if any(sev_of(i) == "error" for i in items) else "warning"
        locs = []
        for i in items:
            at = _rel(i.path, root) + f":{i.line}" + (f":{i.column}" if i.column != 1 else "")
            locs.append((at, sev_of(i)))
        rows.append((worst, d.model_id, d.provider, st, st_col, list(d.replacements), locs, f0))

    w_sev = max(len(r[0]) for r in rows)
    budget = max_width or 100
    w_model = max(len(r[1]) for r in rows)
    w_prov = max(len(r[2]) for r in rows)
    # Shrink the widest column before letting the terminal wrap.
    while w_model + w_prov + w_sev > budget - 22 and w_model > 10:
        w_model -= 1

    lines = []
    for sev, model_id, provider, st, st_col, repls, locs, f0 in rows:
        sev_col = "brightred" if sev == "error" else "brightyellow"
        model_txt = _truncate(model_id, w_model)
        status = c(st_col, _pad(st, 15))
        if f0.deprecation.shutdown_date:
            status += c("grey", " " + str(f0.deprecation.shutdown_date))
        lines.append(
            f"  {c(sev_col, _pad(sev, w_sev), bold=True)}  "
            f"{c('bold', _pad(model_txt, w_model))}  "
            f"{c('cyan', _pad(provider, w_prov))}  {status}"
        )
        sub = " " * (2 + w_sev + 2)
        if repls:
            extra = c("grey", f"   +{len(repls) - 1} more") if len(repls) > 1 else ""
            lines.append(f"{sub}{c('grey', '→')} {c('green', repls[0], bold=True)}{extra}")
        loc_parts = [c("brightred" if s == "error" else "yellow", a) for a, s in locs]
        loc_w = max(_visible_len(a) for a, _ in locs)
        room = max(40, (max_width or 100) - (2 + w_sev + 2) - 2)
        # Wrap the location list instead of letting one 3000-char line run away.
        line, line_w = [], 0
        for part, (raw, _) in zip(loc_parts, locs):
            add = len(raw) + (3 if line else 0)
            if line and line_w + add > room:
                lines.append(sub + c("grey", " · ").join(line))
                line, line_w = [], 0
                add = len(raw)
            line.append(part)
            line_w += add
        if line:
            joined = sub + c("grey", " · ").join(line)
            if len(locs) > 6:
                joined += c("grey", f"   ({len(locs)} locations)")
            lines.append(joined)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_by_file(findings, today, fail_within, c, root, max_width) -> List[str]:
    sev_of = lambda f: severity(f, today, fail_within)  # noqa: E731
    rows = []
    for key, items in group_findings(findings, "file", sev_of):
        for i in items:
            st, st_col = status_parts(i, today)
            rows.append((key[0], i, st, st_col))
    w_at = max(len(f"{i.line}:{i.column}") for _, i, _, _ in rows)
    w_model = min(max(len(i.deprecation.model_id) for _, i, _, _ in rows),
                  max(14, (max_width or 100) // 4))
    w_prov = max(len(i.deprecation.provider) for _, i, _, _ in rows)

    lines = []
    current = None
    for path, i, st, st_col in rows:
        rel = _rel(path, root)
        if rel != current:
            lines.append("")
            current = rel
            lines.append(f"  {c('cyan', '▸')} {c('bold', rel)}")
        sev = sev_of(i)
        at = f"{i.line}:{i.column}"
        lines.append(
            f"      {c('brightred' if sev == 'error' else 'yellow', _pad(at, w_at))}  "
            f"{c('bold', _pad(_truncate(i.deprecation.model_id, w_model), w_model))}  "
            f"{c('cyan', _pad(i.deprecation.provider, w_prov))}  {c(st_col, st)}"
        )
    return lines


def _render_flat(findings, today, fail_within, c, root, max_width) -> List[str]:
    lines = []
    for f in findings:
        d = f.deprecation
        st, st_col = status_parts(f, today)
        sev = severity(f, today, fail_within)
        sev_col = "brightred" if sev == "error" else "brightyellow"
        at = _rel(f.path, root) + f":{f.line}:{f.column}"
        repl = f"  {c('grey', '→')} {c('green', ', '.join(d.replacements))}" if d.replacements else ""
        lines.append(
            f"  {c(sev_col, _pad(sev, 7), bold=True)}  {c('bold', at)}  "
            f"{d.model_id}  {c('cyan', d.provider)}  {c(st_col, st)}{repl}"
        )
    return lines


def render_text(findings: List[Finding], today: date, fail_within: int, source: str,
                group_by: str = "model", color_mode: str = "auto",
                root: Optional[str] = None, max_width: int = 0) -> str:
    c = _Ink(_use_color(sys.stdout, color_mode))
    if not max_width:
        try:
            import shutil
            max_width = shutil.get_terminal_size((100, 24)).columns - 1
        except Exception:
            max_width = 100
    max_width = max(60, min(max_width, 200))
    if not findings:
        return (c("green", "✔", bold=True) + "  No deprecated AI model IDs found   "
                + c("grey", f"data: {source}"))

    renderer = {"model": _render_by_model, "file": _render_by_file}.get(group_by, _render_flat)
    lines = renderer(findings, today, fail_within, c, root, max_width)
    body = "\n".join(lines)
    return body + "\n\n" + _render_summary(findings, today, fail_within, c, source, root)


def _as_dict(f: Finding, today: date, fail_within: int) -> Dict:
    d = f.deprecation
    return {
        "path": f.path,
        "line": f.line,
        "column": f.column,
        "match": f.text,
        "provider": d.provider,
        "model_id": d.model_id,
        "status": f.status(today),
        "severity": severity(f, today, fail_within),
        "shutdown_date": d.shutdown_date.isoformat() if d.shutdown_date else None,
        "deprecation_date": d.deprecation_date.isoformat() if d.deprecation_date else None,
        "days_left": d.days_left(today),
        "replacements": list(d.replacements),
        "replacement_note": (
            "Provider-suggested successor; verify it supports the features you use "
            "(context window, reasoning, vision, fine-tuning, price tier) before switching."
            if d.replacements else ""
        ),
        "confidence": f.confidence,
        "line_text": f.line_text,
        "url": d.url,
    }


def render_json(findings: List[Finding], today: date, fail_within: int, source: str,
                baselined: int = 0) -> str:
    return json.dumps(
        {
            "tool": "llm-sunset",
            "version": __version__,
            "date": today.isoformat(),
            "data_source": source,
            "suppressed_by_baseline": baselined,
            "findings": [_as_dict(f, today, fail_within) for f in findings],
        },
        indent=2,
    )


def render_markdown(findings: List[Finding], today: date, fail_within: int, source: str,
                    group_by: str = "model") -> str:
    if not findings:
        return "### 🌅 llm-sunset\n\n✅ No deprecated AI model IDs found.\n"
    sev_of = lambda f: severity(f, today, fail_within)  # noqa: E731
    if group_by == "none":
        out = [
            "### 🌅 llm-sunset: deprecated AI models found",
            "",
            "| | Location | Model | Provider | Shutdown | Replace with |",
            "|---|---|---|---|---|---|",
        ]
        for f in findings:
            d = f.deprecation
            left = d.days_left(today)
            if left is None:
                when = "deprecated"
            elif left <= 0:
                when = f"**retired** {d.shutdown_date}"
            else:
                when = f"{d.shutdown_date} ({left}d)"
            icon = "🔴" if sev_of(f) == "error" else "🟡"
            model = f"[`{d.model_id}`]({d.url})" if d.url else f"`{d.model_id}`"
            repl = ", ".join(f"`{r}`" for r in d.replacements) or "—"
            out.append(f"| {icon} | `{f.path}:{f.line}` | {model} | {d.provider} | {when} | {repl} |")
        out += ["", f"<sub>Data: [deprecations.info](https://deprecations.info) ({source}) · llm-sunset {__version__}</sub>", ""]
        return "\n".join(out)
    out = ["### 🌅 llm-sunset: deprecated AI models found", ""]
    for key, items in group_findings(findings, group_by, sev_of):
        f0 = items[0]
        icon = "🔴" if any(sev_of(i) == "error" for i in items) else "🟡"
        if group_by == "model":
            d = f0.deprecation
            left = d.days_left(today)
            if left is None:
                when = "deprecated"
            elif left <= 0:
                when = f"**retired** {d.shutdown_date}"
            else:
                when = f"{d.shutdown_date} ({left}d)"
            model = f"[`{d.model_id}`]({d.url})" if d.url else f"`{d.model_id}`"
            out.append(f"#### {icon} {model} ({d.provider}) — {when}")
            out.append("")
            if d.replacements:
                out.append(f"Replace with: {', '.join(f'`{r}`' for r in d.replacements)}")
                out.append("")
            out.append("Found in: " + ", ".join(f"`{i.path}:{i.line}`" for i in items))
        else:
            out.append(f"#### {icon} `{key[0]}`")
            out.append("")
            for i in items:
                d = i.deprecation
                repl = f" → {', '.join(f'`{r}`' for r in d.replacements)}" if d.replacements else ""
                out.append(f"- line `{i.line}`: `{d.model_id}` ({d.provider}){repl}")
        out.append("")
    out += [f"<sub>{_summary_line(findings, today, fail_within)} · "
            f"Data: [deprecations.info](https://deprecations.info) ({source}) · "
            f"llm-sunset {__version__}</sub>", ""]
    return "\n".join(out)


def render_github(findings: List[Finding], today: date, fail_within: int) -> str:
    def esc(s: str) -> str:
        return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    lines = []
    for f in findings:
        level = severity(f, today, fail_within)
        title = f"Deprecated model: {f.deprecation.model_id}"
        lines.append(
            f"::{level} file={esc(f.path)},line={f.line},col={f.column},title={esc(title)}::{esc(describe(f, today))}"
        )
    return "\n".join(lines)


def _uri(path: str) -> str:
    p = path.replace(os.sep, "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def render_sarif(findings: List[Finding], today: date, fail_within: int) -> str:
    rules = {}
    results = []
    for f in findings:
        d = f.deprecation
        rule_id = f"{d.provider}/{d.model_id}"
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "name": "DeprecatedAIModel",
                "shortDescription": {"text": f"Deprecated AI model {d.model_id} ({d.provider})"},
                "helpUri": d.url or "https://deprecations.info",
            }
        results.append(
            {
                "ruleId": rule_id,
                "level": severity(f, today, fail_within),
                "message": {"text": describe(f, today)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": _uri(f.path)},
                            "region": {"startLine": f.line, "startColumn": f.column},
                        }
                    }
                ],
            }
        )
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "llm-sunset",
                        "version": __version__,
                        "informationUri": "https://github.com/Ashveil1/llm-sunset",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)
