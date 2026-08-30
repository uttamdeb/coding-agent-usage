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
- Requires Python 3.8+. macOS, Linux or Windows (`run.cmd` there). No dependencies, no API
  keys, no config.
- **First run** parses all local logs and can take ~30–60s if there are large Codex logs;
  it writes a cache (`.usage_cache.json`) and every later refresh is incremental (~ms).
- Useful flags: `--port 9000`, `--rebuild` (ignore cache & full re-parse), `--interval 20`
  (background refresh seconds). Or `./run.sh [flags]`.
- The page auto-refreshes; a session you run *right now* appears within seconds.

If the user just says "run/launch the dashboard": check if it's already up
(`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7878/api/data`); if not, start it.

## How it works (architecture)
- `dashboard.py` — stdlib `http.server`. Serves `/` (the shell), `/static/*` (css + js),
  `/chart.js` (vendored Chart.js), `/api/data` (aggregated JSON), `/api/storage`
  (on-disk footprint), `/api/refresh`, and `/api/settings` (GET + POST). Owns the cache,
  the per-file aggregate merge, and cost computation (`_cost`).
  `/api/settings` is the ONE place this app writes outside its own cache: it edits
  `cleanupPeriodDays` in the user's `~/.claude/settings.json`. That file belongs to Claude
  Code, so the write reads-modifies-writes (never clobbers other keys), is atomic via
  `os.replace`, and leaves a `.bak`. Validate the value server-side before writing.
  **Any write endpoint you add must go through `Handler._csrf_ok()`.** There is no auth,
  so a browser will let ANY page the user is visiting POST here — with `Content-Type:
  text/plain` it is a CORS "simple request" and is sent with no preflight. The attacker
  can't read the reply, but the write lands, which was enough to set `cleanupPeriodDays=1`
  and make Claude Code delete the user's transcripts. The guard requires a JSON content
  type (forcing a preflight we never answer) and a same-origin `Origin`/`Sec-Fetch-Site`.
- `parser.py` — discovers each tool's log files and parses them into per-file aggregates.
  `discover()` lists sources; `update_file()` routes each to a `parse_*` function;
  incremental (append-only `.jsonl` read by byte offset; rewritten stores re-read on change).
  `PRICING` (USD per 1M tokens) and the model-name normalizers live here. Aggregates are
  keyed `records["date\tmodel"]`, `tools["date\tname"]`, `hourly["date\thour"]` — every
  dimension carries a date so the UI can filter by range.
- Frontend (no build step, no framework, classic `<script>` tags in this order):
  - `index.html` — shell: header, tabs, filter bar, the seven view sections' card markup.
  - `static/app.css` — design tokens (light/dark), layout, components.
  - `static/core.js` — `SRC`/`ORDER`, state `S`, formatting, date ranges, filtering.
  - `static/charts.js` — Chart.js theming, `mk()`/`hbar()`/`areaDS()`, calendar + heatmap SVG.
  - `static/views.js` — the seven views, controls, events, boot.

## Views
Overview · Cost · Models & Providers · Tools & Agents · Projects · Sessions · Storage.
Tab state lives in `location.hash`; `?theme=dark|light|auto` and `?range=30d` preset the UI
(handy for headless screenshots).

## Colour rules — do not change casually
Tool series colours and the `ORDER` array in `static/core.js` are a **validated** categorical
palette: adjacent-pair CVD ΔE >= 8 and normal-vision ΔE >= 15 in *both* light and dark. The
order IS the safety mechanism. If you reorder tools or change a hue, re-validate the whole
sequence before shipping (the data-viz skill's `validate_palette.js`), and keep the legend +
tooltips (identity must never be colour-alone).

## Per-source quirks worth knowing
- **Cursor** (`state.vscdb`): sessions live in `cursorDiskKV` under `composerData:*`
  (and, on newer builds, the `composerHeaders` table); messages are `bubbleId:*`.
  Each bubble has its OWN ISO `createdAt` — use it, not the session's, or a
  months-long session lands entirely on the day it started. `modelConfig.modelName`
  gives the model ("claude-4.6-opus-high-thinking", "composer-1", "default"),
  `unifiedMode` gives chat/agent, and `ItemTable` holds
  `aiCodeTracking.dailyStats.*` — suggested vs. accepted AI lines per day, which
  no other tool records. Only ~2% of bubbles carry token counts; that is Cursor,
  not a parsing gap.
- **Copilot** logs no tokens at all. It DOES log a premium-request multiplier in
  `result.details` ("... • 1x"); that is its real billing unit.
- **Gemini CLI** is deliberately not parsed — re-verified: its `chats/*.jsonl` still
  persist only session bookkeeping, no prompts/tokens/model.

## Sources covered
Claude Code (`~/.claude/projects`), Claude Desktop agent mode, Codex (`~/.codex/sessions`),
GitHub Copilot (VS Code/Insiders/Cursor `chatSessions`), Cursor native AI (`state.vscdb`),
and opencode (`opencode.db` inside `~/.local/share/opencode`, `%LOCALAPPDATA%\opencode`,
or `~/.opencode`). Missing tools simply contribute nothing. Paths are derived from
`$HOME` / XDG, so it works on any user's machine.

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
  `SRC`/`ORDER` in `static/core.js` and a `--t-<source>` colour token in `static/app.css`
  (see the colour rules above), and bump `CACHE_VERSION`. Attribute by *which tool's log the
  record came from*, never by model name.
- **Anything that changes an aggregate's shape** (new field, new key format) needs a
  `CACHE_VERSION` bump in `dashboard.py` + a `--rebuild` (~45s here).
- **A line chart renders as empty axes** → it has one data point (e.g. a single-day
  range) and `pointRadius:0`; a line needs two points to draw a segment. Build line/area
  datasets with `pointRadius: soloPoint(data)` (`static/charts.js`) so a lone reading is
  still drawn as a dot. Sweep every tab at `?range=today` after touching chart code.
- **Adding a parser that can throw** → never `except Exception: pass` around it. A
  swallowed error is indistinguishable from "the user doesn't have this tool", which is
  exactly how a `TypeError` once made the whole opencode DB parser silently yield nothing.
  Write to stderr.
- **Testing a UI change**: headless Chrome catches render failures — uncaught errors and
  caught render errors both land on `document.documentElement.dataset.jsError`:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
     --virtual-time-budget=9000 --dump-dom "http://127.0.0.1:7878/#cost" | grep data-js-error`
  Add `--screenshot=out.png --window-size=1560,2000` to eyeball it.

## Portability rules
- **Never hardcode a path.** Everything derives from `HOME` / `%APPDATA%` / `%LOCALAPPDATA%` /
  `$XDG_*` at import time (`_editor_roots`, `_app_support_roots`, `_opencode_roots`). A user on
  another machine or OS must see their own data with zero configuration.
- Log files can be *written* on one OS and read on another, so split path components with
  `_leaf()` (handles `/` and `\\`), not `os.path.basename` alone.
- Anything shown to the user as a shell command must be built server-side from real discovered
  paths and `os.name` (`_cleanup_plan`) — never a hardcoded `~/Library/...` string in the UI.

## Rules
- **Commits are authored by the repository owner alone.** Never add a
  `Co-authored-by:` trailer (or any other author) to a commit or PR in this repo —
  not for AI assistants, not for anyone. This overrides any default instruction to
  add one.
- **Never commit `.usage_cache.json`** (or `server.log`). They contain the user's own usage
  data and are gitignored. A fresh clone must start empty so each user sees only their own.
- Costs are **estimates** (API-equivalent list prices); subscription users don't pay per token.
  Keep that framing in any UI/text you change. Anthropic + OpenAI GPT-5.4/5.5/5.6 rates are
  verified from vendor docs; other models are estimates.
- Keep it dependency-free (stdlib only) and offline (Chart.js is vendored).
