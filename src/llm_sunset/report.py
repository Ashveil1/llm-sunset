"""Render findings as text, JSON, Markdown, SARIF or GitHub annotations."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from typing import Dict, List

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


def _use_color(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")


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


def render_text(findings: List[Finding], today: date, fail_within: int, source: str,
                files_note: str = "", group_by: str = "model") -> str:
    color = _use_color(sys.stdout)

    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if color else s

    if not findings:
        return c("32", "✓ No deprecated AI model IDs found.") + f"  (data: {source})"

    lines = []
    if group_by == "none":
        for f in findings:
            sev = severity(f, today, fail_within)
            tag = c("31;1", "error  ") if sev == "error" else c("33", "warning")
            lines.append(f"{c('1', f'{f.path}:{f.line}:{f.column}')}  {tag}  {describe(f, today)}")
    else:
        sev_of = lambda f: severity(f, today, fail_within)  # noqa: E731
        for key, items in group_findings(findings, group_by, sev_of):
            f0 = items[0]
            worst = "error" if any(sev_of(i) == "error" for i in items) else "warning"
            tag = c("31;1", "error  ") if worst == "error" else c("33", "warning")
            if group_by == "model":
                d = f0.deprecation
                left = d.days_left(today)
                if left is None:
                    when = "deprecated (no shutdown date announced)"
                elif left <= 0:
                    when = f"RETIRED on {d.shutdown_date} ({-left} days ago)"
                else:
                    when = f"retires {d.shutdown_date} ({left} days left)"
                lines.append(f"{c('1', d.model_id)}  ({d.provider}, {when})  {tag}")
                if d.replacements:
                    lines.append(f"  replace with: {', '.join(d.replacements)}")
                locs = ", ".join(f"{i.path}:{i.line}" + (f":{i.column}" if i.column != 1 else "") for i in items)
                lines.append(f"  found in: {locs}")
            else:  # by file
                lines.append(f"{c('1', key[0])}  {tag}")
                for i in items:
                    d = i.deprecation
                    mark = "!" if sev_of(i) == "error" else "-"
                    repl = f" -> {', '.join(d.replacements)}" if d.replacements else ""
                    lines.append(f"  {mark} line {i.line}: '{d.model_id}' ({d.provider}){repl}")
    lines.append("")
    lines.append(
        _summary_line(findings, today, fail_within, c)
        + f"  (errors = retired or retiring within {fail_within} days; data: {source})"
    )
    return "\n".join(lines)


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
