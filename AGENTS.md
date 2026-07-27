# AGENTS.md — instructions for AI coding agents

You are helping a user run or extend the **AI Usage Dashboard**. Read this first; it's the fast path.

## What this is
A local, zero-dependency dashboard that reads the interaction logs your AI coding tools
already write to this machine and shows usage analytics — tokens, estimated cost, and
breakdowns by model / day / tool / project / hour. **Everything runs locally; no data
leaves the machine.** Python **standard library only** — there is nothing to `pip install`.

## Run it (this is the whole setup)
```bash
python3 dashboard.py          # then open http://127.0.0.1:7878
```
- Requires Python 3.8+. macOS or Linux. No dependencies, no API keys, no config.
- **First run** parses all local logs and can take ~30–60s if there are large Codex logs;
  it writes a cache (`.usage_cache.json`) and every later refresh is incremental (~ms).
- Useful flags: `--port 9000`, `--rebuild` (ignore cache & full re-parse), `--interval 20`
  (background refresh seconds). Or `./run.sh [flags]`.
- The page auto-refreshes; a session you run *right now* appears within seconds.

If the user just says "run/launch the dashboard": check if it's already up
(`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7878/api/data`); if not, start it.

## How it works (architecture)
- `dashboard.py` — stdlib `http.server`. Serves `/` (the HTML), `/chart.js` (vendored
  Chart.js), `/api/data` (aggregated JSON), `/api/refresh`. Owns the cache, the per-file
  aggregate merge, and cost computation (`_cost`).
- `parser.py` — discovers each tool's log files and parses them into per-file aggregates.
  `discover()` lists sources; `update_file()` routes each to a `parse_*` function;
  incremental (append-only `.jsonl` read by byte offset; rewritten stores re-read on change).
  `PRICING` (USD per 1M tokens) and the model-name normalizers live here.
- `index.html` — single-page frontend (vanilla JS + Chart.js). `SRC`/`ORDER` define the
  tools; filtering/aggregation happen client-side from `/api/data`.

## Sources covered
Claude Code (`~/.claude/projects`), Claude Desktop agent mode, Codex (`~/.codex/sessions`),
GitHub Copilot (VS Code/Insiders/Cursor `chatSessions`), Cursor native AI (`state.vscdb`),
and opencode (`~/.local/share/opencode/storage/message`). Missing tools simply contribute
nothing. Paths are derived from `$HOME` / XDG, so it works on any user's machine.

## Common tasks
- **A new model shows $0 / an unknown name** → add/fix it in `parser.py`:
  1. `PRICING["<Display Name>"] = (input, output, cache_write_5m, cache_write_1h, cache_read)`
     — USD per 1M tokens. For OpenAI rows, cache-write tiers are `0, 0`; put the cached
     rate in the last slot. **Verify prices against the vendor's docs — do not guess.**
  2. Make sure the model-name normalizer maps the raw id to that display name
     (`normalize_claude` / `normalize_codex` / `_canonicalize` / `_normalize_opencode`).
  3. Pricing is applied at request time, so no re-parse is needed after a `PRICING` edit;
     a normalizer change needs a `--rebuild` (bump `CACHE_VERSION` in `dashboard.py`).
- **Add a whole new tool source** → in `parser.py`: add its path(s), emit entries from
  `discover()`, write a `parse_<tool>()`, route it in `update_file()`. Then add the tool to
  `SRC` and `ORDER` (+ a chip and `--var`/`.s-`/`.b-` CSS classes) in `index.html`, and bump
  `CACHE_VERSION`. Attribute by *which tool's log the record came from*, never by model name.

## Rules
- **Never commit `.usage_cache.json`** (or `server.log`). They contain the user's own usage
  data and are gitignored. A fresh clone must start empty so each user sees only their own.
- Costs are **estimates** (API-equivalent list prices); subscription users don't pay per token.
  Keep that framing in any UI/text you change. Anthropic + OpenAI GPT-5.4/5.5/5.6 rates are
  verified from vendor docs; other models are estimates.
- Keep it dependency-free (stdlib only) and offline (Chart.js is vendored).
