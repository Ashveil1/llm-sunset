# 🌅 llm-sunset

**Your app is about to break because an AI provider is shutting down the model it uses. `llm-sunset` tells you before it happens.**

OpenAI, Anthropic, Google, Groq, Cohere and xAI retire models constantly, often with only a few months' notice. The model ID sits hard-coded in some config file and nobody notices until production starts returning `404 model_not_found`.

`llm-sunset` scans your code, finds every AI model ID, and checks it against a daily-updated list of official deprecation notices.

```console
$ llm-sunset
app/llm.py:12:23     error    OpenAI model 'gpt-4o-2024-05-13' retires 2026-10-23 (16 days left); replace with: gpt-5.6-sol
app/summarize.py:8:11  error  OpenAI model 'gpt-3.5-turbo' retires 2026-10-23 (16 days left); replace with: gpt-5.6-terra
config/models.yaml:3:8 error  Anthropic model 'claude-2.0' RETIRED on 2025-07-21 (443 days ago); replace with: claude-opus-4-8
worker/.env:2:7        warning Anthropic model 'claude-sonnet-4-5-20250929' retires 2026-11-30 (54 days left)

4 finding(s): 3 error(s), 1 warning(s)
```

- **Zero dependencies.** Pure Python standard library, Python 3.8+.
- **Works offline.** Ships with a bundled snapshot; uses live data when it can reach it.
- **No API keys, and your code never leaves your machine.** It only downloads a public JSON file.
- **CI-ready.** Comes with a GitHub Action, a pre-commit hook, SARIF output for GitHub code scanning, JSON and Markdown.
- **Low noise.** It matches exact model IDs (so `gpt-4o-mini` is not reported as `gpt-4o`, and `gpt-4.1` is not reported as `gpt-4`). Generic one-word IDs (`command`, `ada`, `whisper`, `davinci`…) only count next to model context — a `model:`/`engine=` key, a provider or SDK name, or a `provider/` prefix — so everyday words like the `"command"` key in MCP configs don't trigger it. Dot-directories (`.zcode/`, `.qwen/`…), logs (`*.log`, `*.jsonl`), lockfiles and `node_modules` are skipped, and `.gitignore` is respected.

## Install

```bash
pipx install llm-sunset     # or: pip install llm-sunset / uvx llm-sunset
```

## Usage

```bash
llm-sunset                          # scan current directory
llm-sunset src/ config/             # scan specific paths
llm-sunset --provider openai        # only OpenAI notices (repeatable)
llm-sunset --fail-within 30         # only fail on models retiring within 30 days
llm-sunset --format json            # also: markdown, sarif, github
llm-sunset info gpt-4o-2024-05-13   # look up a model
llm-sunset upcoming --days 90       # every shutdown in the next 90 days
```

**Exit code** is `1` if any model is already retired or retires within `--fail-within` days (default 90). Use `--no-fail` to only report.

**Ignoring things:** put `llm-sunset: ignore` in a comment on a line, or `llm-sunset: ignore-file` anywhere in a file. Use `--exclude 'tests/*'` for paths. Markdown/RST/TXT files are skipped unless you pass `--include-docs`. Files ignored by `.gitignore` are skipped unless you pass `--no-gitignore`.

**Azure, Vertex AI and Bedrock** publish their own retirement dates for models they resell, which often differ from the original provider's dates. To avoid false alarms these are off by default. Turn them on with `--provider azure` or `--provider all`.

## GitHub Action

```yaml
# .github/workflows/llm-sunset.yml
name: llm-sunset
on:
  push:
  pull_request:
  schedule:
    - cron: "0 8 * * 1"   # also re-check weekly: deprecations are announced while your code sits still
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Ashveil1/llm-sunset@v0
        with:
          fail-within: 60          # optional
          # provider: "openai anthropic"
          # exclude: "tests/*"
          # gitignore: "false"     # scan git-ignored files too
```

Findings appear as inline annotations on the PR, along with a summary table on the run page.

## pre-commit

```yaml
repos:
  - repo: https://github.com/Ashveil1/llm-sunset
    rev: v0.2.0
    hooks:
      - id: llm-sunset
```

## GitHub code scanning (SARIF)

```yaml
      - run: pipx run llm-sunset --format sarif --no-fail > llm-sunset.sarif
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: llm-sunset.sarif
```

## Where the data comes from

Deprecation data comes from [deprecations.info](https://deprecations.info) ([source](https://github.com/deprecations/deprecations-rss), MIT). It scrapes the official deprecation pages of OpenAI, Anthropic, Google Gemini and Vertex AI, AWS Bedrock, Azure AI Foundry, Cohere, Groq and xAI every day. `llm-sunset` caches it for 24 hours in `~/.cache/llm-sunset/` and falls back to a bundled snapshot that is refreshed weekly.

If you find a missing or wrong entry, please report it upstream at deprecations-rss. For false positives or negatives in matching, open an issue here.

## Support the project

If `llm-sunset` saved you from a production outage, please consider [sponsoring](https://github.com/sponsors/Ashveil1) ❤️

## License

MIT
