# AI Usage Dashboard

A **live, local** analytics dashboard for your AI coding-assistant usage. It reads the
interaction logs your tools already write to your own machine and serves an interactive
dashboard — tokens, estimated cost, cache efficiency, an **Anthropic vs OpenAI vs Copilot**
provider comparison, a GitHub-style activity calendar, and breakdowns by **model, provider,
day, hour, weekday, tool, project and session** — plus how much **disk** all these logs eat.

**Your data never leaves your machine.** No account, no API key, no telemetry, no
dependencies — just Python's standard library and a vendored copy of Chart.js.

Covers **Claude Code · Claude Desktop · Codex · GitHub Copilot · Cursor · opencode · Hermes Agent**.

---

## Quick start

```bash
git clone https://github.com/uttamdeb/coding-agent-usage.git
cd coding-agent-usage
python3 dashboard.py
```

Then open **http://127.0.0.1:7878**. That's it — no `pip install`, no setup.

> First run parses your local logs (can take ~30–60s if you have large Codex logs), writes
> a cache, and is instant thereafter. The page auto-refreshes every ~15s, so a session
> you're running *right now* shows up within seconds.

Options: `python3 dashboard.py --port 9000` · `--rebuild` (ignore cache, full re-parse) ·
`--interval 20` (background refresh seconds). Or `./run.sh [flags]`.

**Requirements:** Python 3.8+ on **macOS, Linux or Windows**. On Windows run
`python dashboard.py` (or `run.cmd`); on macOS/Linux `python3 dashboard.py` (or `./run.sh`).

It works on anyone's machine because **nothing is hardcoded** — every location is derived at
runtime from your own `$HOME` / `%APPDATA%` / `%LOCALAPPDATA%` / `$XDG_*`, the numbers are
read live from your own logs on every refresh, and the disk figures come from your own drive.
Two people running this see two completely different dashboards.

---

## Everyone sees *their own* usage

This is the important part if you're sharing it: the dashboard has **no bundled data**.
On each machine it scans that user's own logs (`~/.claude`, `~/.codex`,
`~/Library/Application Support/…`, `~/.local/share/opencode`, `~/.hermes`, …) and builds a **fresh**
`.usage_cache.json` locally. That cache is **gitignored and never committed**, so a clone
starts empty and shows only the cloning user's numbers. (If you ever *copy the folder*
instead of cloning, delete `.usage_cache.json` first — that file is your personal data.)

---

## Data sources

| Tool | Where it reads | Tokens |
|---|---|---|
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | exact (in/out/cache read+write, 5m/1h tiers) |
| **Claude Desktop** (agent mode) | `Claude/local-agent-mode-sessions/**` under App Support / `%APPDATA%` / `~/.config` | exact |
| **Codex** | `~/.codex/sessions/**`, `~/.codex/archived_sessions/**` | exact (in/cached/out/reasoning) |
| **GitHub Copilot** | VS Code / Insiders / Cursor `workspaceStorage/*/chatSessions/*.{json,jsonl}` | estimated from message text (Copilot logs no token counts) |
| **Cursor** (native AI) | `Cursor/User/globalStorage/state.vscdb` under App Support / `%APPDATA%` / `~/.config` | partial — model, mode, timestamps, tool calls and AI-line stats are exact; tokens are on only ~2% of messages |
| **opencode** | `~/.local/share/opencode`, `%LOCALAPPDATA%\opencode`, `~/.opencode` (or `$OPENCODE_DATA_DIR`) | exact (in/out/reasoning/cache) |
| **Hermes Agent** | `~/.hermes/state.db` (or `$HERMES_HOME`, `%LOCALAPPDATA%\hermes`) | exact (in/out/cache/reasoning, per model) |

A tool you don't use simply contributes nothing. **Attribution is by tool, not by model** —
a Claude or GPT model used *inside* Copilot/Cursor/opencode/Hermes counts under that tool, and the
Models table lists each `model × tool` row separately.

---

## What you get

Seven tabs, light + dark theme, everything date-filterable.

**Overview** — KPI cards with sparklines and period-over-period deltas · Highlights (biggest
day, priciest session, longest streak, busiest hour) · GitHub-style activity calendar (click a
day to zoom to it) · daily activity stacked by tool · share by tool · **hour × weekday
heatmap** · token composition.

**Cost** — total / per active day / 30-day run rate / per session / per prompt ·
**cache hit rate and what caching saved you** · blended rate by model, toggleable between
*all tokens* (cost ÷ every token, cache reads included — a low bar means heavily cached)
and *per output* (cost ÷ generated tokens, the one that's comparable across providers) ·
cumulative and daily cost by tool.

**Models & Providers** — **Anthropic vs OpenAI vs Google head-to-head** (independent of which
tool ran the model) · concentric provider→model doughnut · provider share over time ·
model-adoption timeline · **provider × tool matrix** · sortable `model × tool` table with a
⚠ on any model missing a price row.

**Tools & Agents** — tool calls per prompt / per message, context amplification, subagent
token share · top tool calls · calls by category (read / edit / execute / web / agents / MCP)
· **MCP server usage** · full sortable tool list.

**Projects** — by tokens / cost / messages, concentration stats, and a table where clicking a
row filters everything to that project.

**Sessions** — real session titles (not hashes), tool, project, model, tokens, cost, prompts,
messages, tool calls, cache % — click any row for a detail panel with git branch, entrypoint,
tool version, token breakdown and log size.

**Storage** — see below.

**Filters** — flexible date range (14 presets incl. this week / month / quarter / year, plus a
custom start–end picker), **compare vs. previous period**, multi-select dropdowns for tool,
provider, project and model, search, and an "exact tokens only" toggle that drops the sources
whose token counts are estimated. Filter state shows as removable pills.

**Keyboard** — `1`/`7`/`3`/`9`/`a` ranges, `m` month-to-date, `/` search, `t` theme, `r` refresh.

---

## Storage — what these logs cost you in disk

The tools you use write a *lot* to disk, and nothing else tells you how much. The **Storage**
tab shows total footprint and per-tool bytes, a free-space gauge that warns when the drive is
nearly full, storage accumulation over time, the largest individual log files, **bytes per 1M
tokens** (which tool stores its history most expensively), AI data on disk the dashboard does
*not* analyse, and copy-paste cleanup commands **generated for your own paths and your own
shell** (`find` on macOS/Linux, PowerShell on Windows). The dashboard never deletes anything
itself.

Deleting old logs does **not** shrink your analytics — the dashboard keeps every session it has
already parsed, so the cleanup is safe.

---

## Cost notes (read this)

Costs are **estimates** computed as `tokens × price` — the tools store token counts, **not
dollars**, so cost is always derived. Rates live in `parser.py → PRICING` as
`(input, output, cache_write_5m, cache_write_1h, cache_read)` per 1M tokens; edit freely
(recomputed on each request, no re-parse needed).

- **Anthropic** rates are current list prices (Opus 5 & 4.x $5/$25, Sonnet $3/$15 — Sonnet 5 at
  its $2/$10 intro, date-aware — Haiku $1/$5; cache write 1.25×/2× input for 5-min/1-hour,
  cache read 0.1×). **OpenAI** GPT-5.4/5.5/5.6 are verified from OpenAI docs; older/other
  models are estimates.
- **These are API-equivalent values.** If you're on a subscription (Claude Max/Pro, Codex,
  Copilot), you don't pay per token — the $ is "what this would cost at API rates."
- **Copilot / Cursor** don't log real token counts, so their tokens (and thus $) are rough.
  Copilot's honest metric is **request count** and its **premium-request** total (both shown);
  Cursor's is **messages, tool calls and AI lines kept** (also shown).
- A model with no price row reads as **$0** — add it to `PRICING` (see below).

## Note on log retention

Some tools delete old logs. **Claude Code** prunes transcripts after `cleanupPeriodDays`
(default **30**); **Codex** keeps everything. The dashboard also keeps parsed sessions in
its cache even after a tool deletes the on-disk log, so totals don't silently shrink once seen.

You can change Claude Code's retention window from the dashboard itself — the **⚙** button
in the header edits `cleanupPeriodDays` in your own `~/.claude/settings.json` (leave it blank
to fall back to the tool's default). The write is atomic and keeps a `.bak`; every other
setting in the file is preserved untouched. It's the only file outside its own cache that the
dashboard ever writes.

---

## Extending it

- **Add a model's price / fix an unknown model:** edit `PRICING` (and, if needed, the
  model-name normalizer) in `parser.py`. Verify rates against the vendor's docs.
- **Add a new tool:** add its paths + a `parse_*` function in `parser.py`, wire `discover()`
  and `update_file()`, then add it to `SRC`/`ORDER` in `static/core.js` and give it a
  `--t-<source>` colour in `static/app.css`. The tool colours are a colour-blind-validated
  palette whose *order* is the safety mechanism — see AGENTS.md before changing them.

See **[AGENTS.md](AGENTS.md)** for a concise, agent-oriented guide (any coding agent can run
and extend this from that file).

## Files

`dashboard.py` (server + cache + cost + `/api/storage`) · `parser.py` (log parsers + pricing) ·
`index.html` (shell) · `static/app.css` · `static/core.js` · `static/charts.js` ·
`static/views.js` · `chart.umd.min.js` (vendored Chart.js) · `AGENTS.md` · `run.sh`.

## License

MIT — see [LICENSE](LICENSE).
