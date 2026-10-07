# Launch posts (copy/paste)

---

## Hacker News — "Show HN"

**Title:** Show HN: llm-sunset – find AI model IDs in your code that are about to be shut down

**Text:**
OpenAI is retiring gpt-4, gpt-3.5-turbo, o1, o3-mini and gpt-4o-2024-05-13 on Oct 23. Anthropic, Google, Groq and others retire models every few months too. Usually the model ID is hard-coded in some config and nobody notices until prod starts returning model_not_found.

llm-sunset is a small zero-dependency CLI and GitHub Action. It scans your repo for model IDs and checks them against the official deprecation pages (via the daily-updated deprecations.info feed). It prints the shutdown date, the days left, and the recommended replacement, and it can fail CI when a model is within N days of shutdown.

It matches exact IDs (gpt-4o-mini is not gpt-4o, gpt-4.1 is not gpt-4), skips docs and lockfiles, works offline, and never sends your code anywhere.

`pipx run llm-sunset` in any repo.

https://github.com/Ashveil1/llm-sunset

---

## Reddit (r/OpenAI, r/LocalLLaMA, r/Python, r/devops, r/ClaudeAI)

**Title:** gpt-4 / gpt-3.5-turbo / o1 shut down on Oct 23. I made a one-command checker for your codebase

Run `pipx run llm-sunset` in your repo. It lists every OpenAI/Anthropic/Gemini/Groq/Cohere/xAI model ID that is retired or retiring, with the date and the official replacement. It's free and open source (MIT), has no dependencies, and needs no API key. There's also a GitHub Action so you get warned about the next round of deprecations before it bites.

https://github.com/Ashveil1/llm-sunset

---

## X / Twitter / Bluesky / Threads

OpenAI shuts down gpt-4, gpt-3.5-turbo, o1 and o3-mini on Oct 23.

Is your code still using them? One command:

pipx run llm-sunset

→ every deprecated model ID in your repo, its shutdown date, and the replacement.
Free, open source, works in CI.

https://github.com/Ashveil1/llm-sunset

---

## Where else to submit
- Pull request to "awesome" lists: awesome-llmops, awesome-ai-tools, awesome-python (Code Analysis), awesome-github-actions
- GitHub Marketplace (publish the Action from the release page; it's free)
- dev.to / Medium / Hashnode article: "How to audit your codebase before OpenAI's Oct 23 model shutdown"
- Product Hunt (free)
- Comment helpfully in GitHub issues/discussions where people report `model_not_found` errors
