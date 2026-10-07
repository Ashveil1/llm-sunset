import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_sunset import report  # noqa: E402
from llm_sunset.cli import main  # noqa: E402
from llm_sunset.data import normalize  # noqa: E402
from llm_sunset.scanner import Matcher, scan  # noqa: E402

RAW = [
    {"provider": "OpenAI", "model_id": "gpt-4o-2024-05-13", "shutdown_date": "2026-10-23",
     "replacement_models": ["gpt-5"], "url": "https://example.com/a", "last_observed": "2026-10-01"},
    {"provider": "OpenAI", "model_id": "davinci", "shutdown_date": "2024-01-04",
     "replacement_models": ["davinci-002"], "last_observed": "2024-01-01"},
    {"provider": "OpenAI", "model_id": "ada", "shutdown_date": "2024-01-04", "last_observed": "2024-01-01"},
    {"provider": "Cohere", "model_id": "command", "shutdown_date": None, "last_observed": "2026-01-01"},
    {"provider": "Cohere", "model_id": "command-r", "shutdown_date": None, "last_observed": "2026-01-01"},
    {"provider": "Anthropic", "model_id": "claude-2.0", "shutdown_date": "2025-07-21", "last_observed": "2025-07-01"},
    {"provider": "Azure", "model_id": "gpt-4o", "shutdown_date": "2027-06-01", "last_observed": "2026-10-01"},
    {"provider": "Google", "model_id": "gemini-1.0-pro", "shutdown_date": None, "last_observed": "2025-01-01"},
    # older duplicate must lose to the newer record above
    {"provider": "Azure", "model_id": "gpt-4o", "shutdown_date": "2026-01-01", "last_observed": "2025-01-01"},
]
TODAY = date(2026, 10, 7)


def matcher(providers=None):
    return Matcher(normalize(RAW), providers)


class MatchTests(unittest.TestCase):
    def hits(self, line, providers=None):
        return [(t, [d.provider for d in deps]) for _, t, deps in matcher(providers).match_line(line)]

    def test_exact_quoted(self):
        self.assertEqual(self.hits('model="gpt-4o-2024-05-13"'), [("gpt-4o-2024-05-13", ["OpenAI"])])

    def test_no_partial_match(self):
        # gpt-4o-mini must not be reported as gpt-4o
        self.assertEqual(self.hits('model="gpt-4o-mini"'), [])

    def test_unquoted_yaml_and_env(self):
        self.assertEqual(self.hits("MODEL=claude-2.0"), [("claude-2.0", ["Anthropic"])])
        self.assertEqual(self.hits("model: gpt-4o", providers=["azure"]), [("gpt-4o", ["Azure"])])

    def test_platforms_off_by_default(self):
        self.assertEqual(self.hits("model: gpt-4o"), [])
        self.assertEqual(self.hits("model: gpt-4o", providers=["all"]), [("gpt-4o", ["Azure"])])

    def test_version_suffix_not_matched(self):
        raw = RAW + [{"provider": "OpenAI", "model_id": "gpt-4", "shutdown_date": "2026-10-23"}]
        m = Matcher(normalize(raw))
        hits = lambda s: [t for _, t, _ in m.match_line(s)]  # noqa: E731
        self.assertEqual(hits('"gpt-4.1"'), [])
        self.assertEqual(hits('"gpt-4-turbo-x"'), [])
        self.assertEqual(hits('"my-gpt-4"'), [])
        self.assertEqual(hits('model = "gpt-4".'), ["gpt-4"])

    def test_prefixes(self):
        self.assertEqual(self.hits('"openai/gpt-4o-2024-05-13"')[0][0], "gpt-4o-2024-05-13")
        self.assertEqual(self.hits('"models/gemini-1.0-pro"')[0][0], "gemini-1.0-pro")
        self.assertEqual(self.hits('"ft:gpt-4o-2024-05-13:acme::abc"')[0][0], "gpt-4o-2024-05-13")

    def test_plain_words_need_quotes(self):
        self.assertEqual(self.hits("# Charles Babbage and davinci were smart"), [])
        self.assertEqual(self.hits("engine = 'davinci'"), [("davinci", ["OpenAI"])])

    def test_risky_ids_from_issue(self):
        # https://github.com/Ashveil1/llm-sunset FP report: "command" is the
        # standard MCP-server key, "ada" is often just a variable name.
        hits = lambda s: [t for _, t, _ in matcher().match_line(s)]  # noqa: E731
        # JSON/YAML mapping keys are never model refs ...
        self.assertEqual(hits('{"command": "uvx mcp-server"}'), [])
        self.assertEqual(hits('"command": "npx foo"'), [])
        self.assertEqual(hits('command: foo'), [])
        # ... but values next to a model key are.
        self.assertEqual(hits('{"model": "command"}'), ["command"])
        self.assertEqual(hits('model: command'), ["command"])
        self.assertEqual(hits('MODEL=command'), ["command"])
        self.assertEqual(hits('cohere.create(model="command-r")'), ["command-r"])
        # bare words without any model context stay silent ...
        self.assertEqual(hits('x = "ada"'), [])
        self.assertEqual(hits('x = "command"'), [])
        # ... and gain a voice with model context.
        self.assertEqual(hits('model = "ada"'), ["ada"])
        self.assertEqual(hits('openai.engine = "ada"'), ["ada"])
        # distinctive hyphenated IDs still match anywhere.
        self.assertEqual(hits('x = "command-r"'), ["command-r"])
        self.assertEqual(hits('x = "gpt-4o-2024-05-13"'), ["gpt-4o-2024-05-13"])
        # prose that merely contains "deployment" is not model context ...
        self.assertEqual(hits('suite: test deployment command, args, and labels'), [])
        # ... including docstrings that mention "models" ...
        self.assertEqual(hits('"""Test the models list command with JSON output"""'), [])
        # ... and prose about "/slash commands".
        self.assertEqual(hits('use the /bashes command to check output'), [])
        # ... but a real mapping value is.
        self.assertEqual(hits('deployment: command'), ["command"])
        self.assertEqual(hits('MODEL_NAME=command'), ["command"])
        # provider words inside file paths don't count as context ...
        trace_line = '"stringValue": "[{\\"type\\":\\"command\\"}]" /workspace/.claude/x'
        self.assertEqual(hits(trace_line), [])
        # ... while a real provider prefix does.
        self.assertEqual(hits('"openai/command"'), ["command"])
        # tokenizer vocabs / mapping keys stay silent even when the same
        # line mentions "model" (single-line JSON).
        self.assertEqual(hits('{"command":2339,"model":1234}'), [])
        self.assertEqual(hits('{"model": "command"}'), ["command"])

    def test_provider_filter(self):
        self.assertEqual(self.hits("model: gpt-4o", providers=["openai"]), [])
        self.assertEqual(self.hits("MODEL=claude-2.0", providers=["openai"]), [])

    def test_dedup_keeps_newest(self):
        d = [x for x in normalize(RAW) if x.provider == "Azure"][0]
        self.assertEqual(d.shutdown_date, date(2027, 6, 1))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "app.py").write_text(
            'a = "gpt-4o-2024-05-13"\n'
            'b = "claude-2.0"  # llm-sunset: ignore\n'
            'model = "davinci"\n'  # generic-word IDs need model context
            'c = "davinci"\n'  # ... so this bare one stays silent
        )
        (root / "README.md").write_text("we used gpt-4o-2024-05-13\n")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "x.js").write_text('"claude-2.0"\n')
        (root / "skip.py").write_text('# llm-sunset: ignore-file\n"claude-2.0"\n')
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_defaults(self):
        found = scan([str(self.root)], matcher())
        self.assertEqual([(Path(f.path).name, f.line, f.text) for f in found],
                         [("app.py", 1, "gpt-4o-2024-05-13"), ("app.py", 3, "davinci")])

    def test_include_docs(self):
        found = scan([str(self.root)], matcher(), include_docs=True)
        self.assertIn("README.md", {Path(f.path).name for f in found})

    def test_severity(self):
        found = scan([str(self.root)], matcher())
        sev = {f.text: report.severity(f, TODAY, 90) for f in found}
        self.assertEqual(sev, {"gpt-4o-2024-05-13": "error", "davinci": "error"})
        self.assertEqual(report.severity(found[0], TODAY, 10), "warning")

    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / "data.json"
            data.write_text(json.dumps(RAW))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([*args, "--data-file", str(data), "--today", TODAY.isoformat()])
        return code, buf.getvalue()

    def test_cli_exit_codes_and_formats(self):
        code, out = self.run_cli(str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("gpt-4o-2024-05-13", out)
        code, out = self.run_cli(str(self.root), "--format", "json", "--no-fail")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["findings"]), 2)
        code, out = self.run_cli("scan", str(self.root), "--format", "sarif")
        self.assertEqual(json.loads(out)["version"], "2.1.0")
        code, out = self.run_cli("scan", str(self.root), "--format", "markdown")
        self.assertIn("| 🔴 |", out)

    def test_cli_info_and_upcoming(self):
        code, out = self.run_cli("info", "gpt-4o-2024-05-13")
        self.assertEqual(code, 1)
        self.assertIn("16 days left", out)
        code, out = self.run_cli("upcoming", "--days", "30")
        self.assertIn("gpt-4o-2024-05-13", out)
        self.assertNotIn("2027-06-01", out)

    def test_skips_dot_dirs_logs_and_jsonl(self):
        m = matcher()
        zcode = self.root / ".zcode" / "cli"
        zcode.mkdir(parents=True)
        (zcode / "config.json").write_text('{"model": "gpt-4o-2024-05-13"}\n')
        (self.root / ".qwen").mkdir()
        (self.root / ".qwen" / "tmp.py").write_text('a = "gpt-4o-2024-05-13"\n')
        (self.root / "debug.log").write_text('model="gpt-4o-2024-05-13"\n')
        (self.root / "events.jsonl").write_text('{"model": "gpt-4o-2024-05-13"}\n')
        (self.root / ".mcp.json").write_text('{"mcpServers": {"x": {"command": "uvx"}}}\n')
        found = scan([str(self.root)], m)
        names = {Path(f.path).name for f in found}
        self.assertNotIn("config.json", names)
        self.assertNotIn("tmp.py", names)
        self.assertNotIn("debug.log", names)
        self.assertNotIn("events.jsonl", names)
        self.assertNotIn(".mcp.json", names)  # "command" key, no model context
        self.assertIn("app.py", names)  # real source still scanned

    def test_gitignore_respected(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git not available")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "kept.py").write_text('a = "gpt-4o-2024-05-13"\n')
        (root / "ignored.py").write_text('a = "gpt-4o-2024-05-13"\n')
        (root / ".gitignore").write_text("ignored.py\n")
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
        m = matcher()
        self.assertEqual([Path(f.path).name for f in scan([str(root)], m)], ["kept.py"])
        self.assertEqual(
            sorted(Path(f.path).name for f in scan([str(root)], m, use_gitignore=False)),
            ["ignored.py", "kept.py"],
        )


class SnapshotTests(unittest.TestCase):
    def test_bundled_snapshot_loads(self):
        from llm_sunset.data import SNAPSHOT_PATH
        data = json.loads(SNAPSHOT_PATH.read_text())
        self.assertGreater(len(normalize(data)), 50)


if __name__ == "__main__":
    unittest.main()
