"""Command line interface for llm-sunset."""

from __future__ import annotations

import argparse
import difflib
import os
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

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
  llm-sunset fix --dry-run           preview rewrites of deprecated model IDs
  llm-sunset fix --replace gpt-4o-2024-05-13=gpt-5.6-sol

Ignore a line with a comment containing:  llm-sunset: ignore
Ignore a whole file with:                  llm-sunset: ignore-file
"""


def _today(value: Optional[str]) -> date:
    return date.fromisoformat(value) if value else date.today()


def _config_for_command(argv: List[str]) -> tuple:
    """Load config-file defaults for scan-like commands.

    Returns (parser_defaults, providers). The parser defaults make CLI flags
    override the config file; --exclude lists are combined.
    """
    first = argv[0] if argv else "scan"
    if first.startswith("-"):
        first = "scan"
    if first not in ("scan", "fix", "baseline"):
        return {}, None
    from .config import load_config
    cfg = load_config()
    if not cfg:
        return {}, None
    defaults: dict = {}
    warns = []

    def want_int(key: str):
        if key in cfg:
            if isinstance(cfg[key], bool) or not isinstance(cfg[key], int):
                warns.append(f"ignoring config {key}={cfg[key]!r} (want an integer)")
            else:
                defaults[key] = cfg[key]

    want_int("fail_within")
    want_int("warn_within")
    for key, choices in (("format", ("text", "json", "markdown", "sarif", "github")),
                         ("group_by", ("model", "file", "none"))):
        if key in cfg:
            if cfg[key] in choices:
                defaults[key] = cfg[key]
            else:
                warns.append(f"ignoring config {key}={cfg[key]!r} (want one of {', '.join(choices)})")
    if isinstance(cfg.get("exclude"), list):
        defaults["exclude"] = [str(x) for x in cfg["exclude"]]
    for key in ("include_docs", "include_hidden"):
        if cfg.get(key) is True:
            defaults[key] = True
    for key in ("gitignore", "default_excludes"):
        if cfg.get(key) is False:
            defaults[key] = False
    providers = None
    if isinstance(cfg.get("providers"), list):
        providers = [str(x) for x in cfg["providers"] if str(x).strip()]
    elif isinstance(cfg.get("provider"), str) and cfg["provider"].strip():
        providers = [cfg["provider"].strip()]
    for w in warns:
        print(f"llm-sunset: {w}", file=sys.stderr)
    return defaults, providers


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--provider", action="append", metavar="NAME",
                   help="only consider this provider (repeatable; e.g. openai, anthropic, google, azure, groq). "
                        "Default: all direct providers; Azure/Vertex/Bedrock need an explicit "
                        "--provider (or --provider all)")
    p.add_argument("--offline", action="store_true", help="don't fetch live data; use cache or bundled snapshot")
    p.add_argument("--data-file", metavar="PATH", help="use a local deprecations JSON file")
    p.add_argument("--today", metavar="YYYY-MM-DD", help=argparse.SUPPRESS)


def _add_scan_options(s: argparse.ArgumentParser) -> None:
    s.add_argument("paths", nargs="*", default=["."])
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="exclude paths (repeatable)")
    s.add_argument("--include-docs", action="store_true", help="also scan .md/.rst/.txt files")
    s.add_argument("--no-gitignore", dest="gitignore", action="store_false", default=True,
                   help="do not respect .gitignore (by default, ignored files are skipped)")
    s.add_argument("--no-default-excludes", dest="default_excludes", action="store_false", default=True,
                   help="do not skip default non-source locations (caches, histories, backups, logs, lockfiles)")
    s.add_argument("--include-hidden", action="store_true",
                   help="also scan hidden directories like .zcode/ (skipped by default)")


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
    s.add_argument("--format", choices=["text", "json", "markdown", "sarif", "github"], default="text")
    s.add_argument("--group-by", choices=["model", "file", "none"], default="model",
                   help="group text/markdown output by model or file (default: model; 'none' = one line per finding)")
    s.add_argument("--fail-within", type=int, default=90, metavar="DAYS",
                   help="treat models retiring within DAYS as errors (default: 90)")
    s.add_argument("--warn-within", type=int, default=365, metavar="DAYS",
                   help="hide models retiring more than DAYS from now (default: 365, -1 = show all)")
    s.add_argument("--no-fail", action="store_true", help="always exit 0")
    s.add_argument("--baseline", metavar="FILE",
                   help="only report findings not recorded in FILE "
                        "(create it with 'llm-sunset baseline --write FILE')")
    s.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                   help="colorize output (default: auto, i.e. only when stdout is a terminal; "
                        "FORCE_COLOR and NO_COLOR are also honoured)")
    _add_scan_options(s)
    _common(s)

    f = sub.add_parser("fix", help="rewrite deprecated model IDs to their replacements")
    f.add_argument("--dry-run", action="store_true",
                   help="print a unified diff without changing any files")
    f.add_argument("--replace", action="append", default=[], metavar="OLD=NEW",
                   help="use NEW as the replacement for model OLD (repeatable; "
                        "default: the provider's first suggested replacement)")
    f.add_argument("--include-risky", action="store_true",
                   help="also rewrite medium-confidence matches (generic words like "
                        "'command' that needed model context; high-confidence only by default)")
    _add_scan_options(f)
    _common(f)

    i = sub.add_parser("info", help="show deprecation info for model IDs")
    i.add_argument("models", nargs="+")
    _common(i)

    u = sub.add_parser("upcoming", help="list upcoming shutdowns")
    u.add_argument("--days", type=int, default=180, help="look ahead this many days (default: 180)")
    _common(u)

    b = sub.add_parser("baseline", help="write a baseline file of current findings")
    b.add_argument("--write", required=True, metavar="FILE", help="write fingerprints of current findings to FILE")
    _add_scan_options(b)
    _common(b)
    return p


def _display_root(paths: List[str]) -> Optional[str]:
    """Show paths relative to the single directory being scanned, when there is one."""
    dirs = [p for p in paths if os.path.isdir(p)]
    if len(dirs) == 1 and not any(os.path.isfile(p) for p in paths):
        return os.path.abspath(dirs[0])
    return None


def cmd_scan(a: argparse.Namespace) -> int:
    today = _today(a.today)
    deps, source = load(offline=a.offline, data_file=a.data_file)
    if getattr(a, "provider", None) is None and getattr(a, "cfg_providers", None):
        a.provider = a.cfg_providers
    findings = scan(a.paths, Matcher(deps, a.provider), exclude=a.exclude, include_docs=a.include_docs,
                  use_gitignore=a.gitignore, use_default_excludes=a.default_excludes,
                  include_hidden=a.include_hidden)
    baselined = 0
    if getattr(a, "baseline", None):
        from .baseline import load_baseline, split_baselined
        skip_abs = os.path.abspath(a.baseline)
        findings = [f for f in findings if os.path.abspath(f.path) != skip_abs]
        bl = load_baseline(a.baseline)
        if not bl and not os.path.exists(a.baseline):
            print(f"llm-sunset: baseline {a.baseline} not found, reporting everything "
                  f"(create it with 'llm-sunset baseline --write {a.baseline}')", file=sys.stderr)
        findings, baselined = split_baselined(findings, bl)
    if a.warn_within >= 0:
        findings = [
            f for f in findings
            if f.deprecation.days_left(today) is None or f.deprecation.days_left(today) <= a.warn_within
        ]

    if a.format == "json":
        print(report.render_json(findings, today, a.fail_within, source, baselined))
    elif a.format == "markdown":
        print(report.render_markdown(findings, today, a.fail_within, source, a.group_by))
    elif a.format == "sarif":
        print(report.render_sarif(findings, today, a.fail_within))
    elif a.format == "github":
        out = report.render_github(findings, today, a.fail_within)
        if out:
            print(out)
        print(report.render_text(findings, today, a.fail_within, source, group_by=a.group_by,
                                 color_mode="never", root=_display_root(a.paths)))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(report.render_markdown(findings, today, a.fail_within, source, a.group_by) + "\n")
    else:
        print(report.render_text(findings, today, a.fail_within, source, group_by=a.group_by,
                                 color_mode=a.color, root=_display_root(a.paths)))
    if baselined:
        print(f"(baseline {a.baseline}: {baselined} known finding(s) hidden)", file=sys.stderr)

    if a.no_fail:
        return 0
    return 1 if any(report.severity(f, today, a.fail_within) == "error" for f in findings) else 0


def cmd_baseline(a: argparse.Namespace) -> int:
    deps, _source = load(offline=a.offline, data_file=a.data_file)
    if getattr(a, "provider", None) is None and getattr(a, "cfg_providers", None):
        a.provider = a.cfg_providers
    findings = scan(a.paths, Matcher(deps, a.provider), exclude=a.exclude, include_docs=a.include_docs,
                    use_gitignore=a.gitignore, use_default_excludes=a.default_excludes,
                    include_hidden=a.include_hidden)
    skip_abs = os.path.abspath(a.write)
    findings = [f for f in findings if os.path.abspath(f.path) != skip_abs]
    from .baseline import write_baseline
    n = write_baseline(a.write, findings)
    print(f"llm-sunset: wrote {n} finding(s) to {a.write}")
    return 0


def cmd_fix(a: argparse.Namespace) -> int:
    deps, source = load(offline=a.offline, data_file=a.data_file)
    if getattr(a, "provider", None) is None and getattr(a, "cfg_providers", None):
        a.provider = a.cfg_providers
    matcher = Matcher(deps, a.provider)
    findings = scan(a.paths, matcher, exclude=a.exclude, include_docs=a.include_docs,
                    use_gitignore=a.gitignore, use_default_excludes=a.default_excludes,
                    include_hidden=a.include_hidden)

    overrides: Dict[str, str] = {}
    for item in a.replace:
        if "=" not in item:
            print(f"llm-sunset: ignoring malformed --replace {item!r} (want OLD=NEW)", file=sys.stderr)
            continue
        old, new = item.split("=", 1)
        overrides[old.strip()] = new.strip()

    by_file: Dict[str, list] = {}
    skipped_no_repl = 0
    skipped_risky = 0
    for f in findings:
        target = overrides.get(f.text)
        if target is None:
            if not f.deprecation.replacements:
                skipped_no_repl += 1
                continue
            target = f.deprecation.replacements[0]
        if f.confidence != "high" and not a.include_risky and f.text not in overrides:
            skipped_risky += 1
            continue
        by_file.setdefault(f.path, []).append((f, target))

    changed_files = 0
    applied = 0
    for path, jobs in sorted(by_file.items()):
        try:
            original = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            print(f"llm-sunset: cannot read {path}: {e}", file=sys.stderr)
            continue
        lines = original.splitlines(keepends=True)
        pending = []
        for f, target in jobs:
            idx = f.line - 1
            if not (0 <= idx < len(lines)):
                continue
            col0 = f.column - 1
            if lines[idx][col0:col0 + len(f.text)] != f.text:
                print(f"llm-sunset: {path}:{f.line} changed since scan, skipping '{f.text}'",
                      file=sys.stderr)
                continue
            pending.append((idx, col0, f, target))
        if not pending:
            continue
        # Apply from the end of the file backwards so offsets stay valid.
        new_lines = list(lines)
        for idx, col0, f, target in sorted(pending, key=lambda j: (j[0], j[1]), reverse=True):
            line = new_lines[idx]
            new_lines[idx] = line[:col0] + target + line[col0 + len(f.text):]
        updated = "".join(new_lines)
        if updated == original:
            continue
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
        if a.dry_run:
            print(diff, end="")
        else:
            try:
                Path(path).write_text(updated, encoding="utf-8")
            except OSError as e:
                print(f"llm-sunset: cannot write {path}: {e}", file=sys.stderr)
                continue
            print(diff, end="")
            changed_files += 1
        applied += len(pending)

    print(f"llm-sunset fix: {applied} replacement(s) in {changed_files} file(s)"
          + (" (dry run, nothing written)" if a.dry_run else "")
          + f"; skipped: {skipped_no_repl} without known replacement, "
            f"{skipped_risky} medium-confidence (use --include-risky).")
    return 0


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
    known = {"scan", "info", "upcoming", "fix", "baseline", "-h", "--help", "--version"}
    if not argv or argv[0] not in known:
        argv = ["scan"] + argv
    parser = build_parser()
    cfg_defaults, cfg_providers = _config_for_command(argv)
    if cfg_defaults:
        parser.set_defaults(**cfg_defaults)
    a = parser.parse_args(argv)
    a.cfg_providers = cfg_providers
    handler = {"scan": cmd_scan, "info": cmd_info, "upcoming": cmd_upcoming,
               "fix": cmd_fix, "baseline": cmd_baseline}[a.command]
    try:
        return handler(a)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
