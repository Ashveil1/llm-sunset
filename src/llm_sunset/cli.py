"""Command line interface for llm-sunset."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from typing import List, Optional

from . import __version__, report
from .data import load
from .scanner import Matcher, scan

EPILOG = """examples:
  llm-sunset                         scan the current directory
  llm-sunset src/ config/            scan specific paths
  llm-sunset --provider openai       only check OpenAI deprecations
  llm-sunset --format sarif > out.sarif
  llm-sunset info gpt-4o-2024-05-13  look up a single model
  llm-sunset upcoming --days 90      list every shutdown in the next 90 days

Ignore a line with a comment containing:  llm-sunset: ignore
Ignore a whole file with:                  llm-sunset: ignore-file
"""


def _today(value: Optional[str]) -> date:
    return date.fromisoformat(value) if value else date.today()


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--provider", action="append", metavar="NAME",
                   help="only consider this provider (repeatable; e.g. openai, anthropic, google, azure, groq). "
                        "Default: all direct providers; Azure/Vertex/Bedrock need an explicit "
                        "--provider (or --provider all)")
    p.add_argument("--offline", action="store_true", help="don't fetch live data; use cache or bundled snapshot")
    p.add_argument("--data-file", metavar="PATH", help="use a local deprecations JSON file")
    p.add_argument("--today", metavar="YYYY-MM-DD", help=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llm-sunset",
        description="Find AI model IDs in your code that are deprecated or about to be shut down.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"llm-sunset {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("scan", help="scan files (default command)")
    s.add_argument("paths", nargs="*", default=["."])
    s.add_argument("--format", choices=["text", "json", "markdown", "sarif", "github"], default="text")
    s.add_argument("--fail-within", type=int, default=90, metavar="DAYS",
                   help="treat models retiring within DAYS as errors (default: 90)")
    s.add_argument("--warn-within", type=int, default=365, metavar="DAYS",
                   help="hide models retiring more than DAYS from now (default: 365, -1 = show all)")
    s.add_argument("--no-fail", action="store_true", help="always exit 0")
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="exclude paths (repeatable)")
    s.add_argument("--include-docs", action="store_true", help="also scan .md/.rst/.txt files")
    _common(s)

    i = sub.add_parser("info", help="show deprecation info for model IDs")
    i.add_argument("models", nargs="+")
    _common(i)

    u = sub.add_parser("upcoming", help="list upcoming shutdowns")
    u.add_argument("--days", type=int, default=180, help="look ahead this many days (default: 180)")
    _common(u)
    return p


def cmd_scan(a: argparse.Namespace) -> int:
    today = _today(a.today)
    deps, source = load(offline=a.offline, data_file=a.data_file)
    findings = scan(a.paths, Matcher(deps, a.provider), exclude=a.exclude, include_docs=a.include_docs)
    if a.warn_within >= 0:
        findings = [
            f for f in findings
            if f.deprecation.days_left(today) is None or f.deprecation.days_left(today) <= a.warn_within
        ]

    if a.format == "json":
        print(report.render_json(findings, today, a.fail_within, source))
    elif a.format == "markdown":
        print(report.render_markdown(findings, today, a.fail_within, source))
    elif a.format == "sarif":
        print(report.render_sarif(findings, today, a.fail_within))
    elif a.format == "github":
        out = report.render_github(findings, today, a.fail_within)
        if out:
            print(out)
        print(report.render_text(findings, today, a.fail_within, source))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(report.render_markdown(findings, today, a.fail_within, source) + "\n")
    else:
        print(report.render_text(findings, today, a.fail_within, source))

    if a.no_fail:
        return 0
    return 1 if any(report.severity(f, today, a.fail_within) == "error" for f in findings) else 0


def cmd_info(a: argparse.Namespace) -> int:
    today = _today(a.today)
    deps, source = load(offline=a.offline, data_file=a.data_file)
    matcher = Matcher(deps, a.provider)
    status = 0
    for model in a.models:
        hits = matcher.by_id.get(model, [])
        if not hits:
            print(f"{model}: no deprecation notice found")
            continue
        status = 1
        for d in hits:
            left = d.days_left(today)
            if left is None:
                when = "deprecated, no shutdown date announced"
            elif left <= 0:
                when = f"RETIRED on {d.shutdown_date}"
            else:
                when = f"retires {d.shutdown_date} ({left} days left)"
            print(f"{model} [{d.provider}]: {when}")
            if d.replacements:
                print(f"    replace with: {', '.join(d.replacements)}")
            if d.url:
                print(f"    source: {d.url}")
    print(f"(data: {source})", file=sys.stderr)
    return status


def cmd_upcoming(a: argparse.Namespace) -> int:
    today = _today(a.today)
    deps, source = load(offline=a.offline, data_file=a.data_file)
    matcher = Matcher(deps, a.provider)
    rows = []
    for items in matcher.by_id.values():
        for d in items:
            left = d.days_left(today)
            if left is not None and 0 < left <= a.days:
                rows.append((d.shutdown_date, d))
    rows.sort(key=lambda r: (r[0], r[1].provider, r[1].model_id))
    if not rows:
        print(f"No shutdowns in the next {a.days} days.")
    for when, d in rows:
        repl = f"  -> {', '.join(d.replacements)}" if d.replacements else ""
        print(f"{when}  {d.provider:<14} {d.model_id}{repl}")
    print(f"(data: {source})", file=sys.stderr)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"scan", "info", "upcoming", "-h", "--help", "--version"}
    if not argv or argv[0] not in known:
        argv = ["scan"] + argv
    a = build_parser().parse_args(argv)
    handler = {"scan": cmd_scan, "info": cmd_info, "upcoming": cmd_upcoming}[a.command]
    try:
        return handler(a)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
