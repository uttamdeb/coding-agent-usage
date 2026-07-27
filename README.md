# AI Usage Dashboard

A **live, local** analytics dashboard for your AI coding-assistant usage. It reads the
interaction logs your tools already write to your own machine and serves an interactive
dashboard — tokens, estimated cost, a GitHub-style activity calendar, token-type
composition, and breakdowns by **model, day, hour, day-of-week, tool, project, and session**.

**Your data never leaves your machine.** No account, no API key, no telemetry, no
dependencies — just Python's standard library and a vendored copy of Chart.js.

Covers **Claude Code · Claude Desktop · Codex · GitHub Copilot · Cursor · opencode**.

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

**Requirements:** Python 3.8+, macOS or Linux. It works on anyone's machine because every
path is derived from your home directory — the tools store logs in the same place for
every user.

---

## Everyone sees *their own* usage

This is the important part if you're sharing it: the dashboard has **no bundled data**.
On each machine it scans that user's own logs (`~/.claude`, `~/.codex`,
`~/Library/Application Support/…`, `~/.local/share/opencode`, …) and builds a **fresh**
`.usage_cache.json` locally. That cache is **gitignored and never committed**, so a clone
starts empty and shows only the cloning user's numbers. (If you ever *copy the folder*
instead of cloning, delete `.usage_cache.json` first — that file is your personal data.)

---

## Data sources

| Tool | Where it reads | Tokens |
|---|---|---|
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | exact (in/out/cache read+write, 5m/1h tiers) |
| **Claude Desktop** (agent mode) | `~/Library/Application Support/Claude/local-agent-mode-sessions/**` | exact |
| **Codex** | `~/.codex/sessions/**`, `~/.codex/archived_sessions/**` | exact (in/cached/out/reasoning) |
| **GitHub Copilot** | VS Code / Insiders / Cursor `workspaceStorage/*/chatSessions/*.{json,jsonl}` | estimated from message text (Copilot logs no token counts) |
| **Cursor** (native AI) | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | partial (few messages carry tokens) |
| **opencode** | `~/.local/share/opencode/storage/message/**` (or `$OPENCODE_DATA_DIR`) | exact (in/out/reasoning/cache) |

A tool you don't use simply contributes nothing. **Attribution is by tool, not by model** —
a Claude or GPT model used *inside* Copilot/Cursor/opencode counts under that tool, and the
Models table lists each `model × tool` row separately.

---

## What you get

- **KPI cards** — total tokens, estimated cost, messages, sessions, active days, tool calls.
- **Activity calendar** — GitHub-style contribution heatmap (last 12 months).
- **Daily activity** + **cumulative cost**, toggle Tokens / Cost / Messages.
- **Token composition** — prompt / cache-read / cache-write / output / **thinking**.
- **Model mix** — doughnut + sortable `model × tool` table.
- **Top tools**, **projects**, **time-of-day**, **day-of-week**, and a sortable **sessions** table.
- Filters: date range (Today / 7d / 30d / 90d / 1y / All), per-tool chips, live toggle.

---

## Cost notes (read this)

Costs are **estimates** computed as `tokens × price` — the tools store token counts, **not
dollars**, so cost is always derived. Rates live in `parser.py → PRICING` as
`(input, output, cache_write_5m, cache_write_1h, cache_read)` per 1M tokens; edit freely
(recomputed on each request, no re-parse needed).

- **Anthropic** rates are current list prices (Opus 4.x $5/$25, Sonnet $3/$15 — Sonnet 5 at
  its $2/$10 intro, date-aware — Haiku $1/$5; cache write 1.25×/2× input for 5-min/1-hour,
  cache read 0.1×). **OpenAI** GPT-5.4/5.5/5.6 are verified from OpenAI docs; older/other
  models are estimates.
- **These are API-equivalent values.** If you're on a subscription (Claude Max/Pro, Codex,
  Copilot), you don't pay per token — the $ is "what this would cost at API rates."
- **Copilot / Cursor** don't log real token counts, so their tokens (and thus $) are rough;
  their honest metric is **request count**.
- A model with no price row reads as **$0** — add it to `PRICING` (see below).

## Note on log retention

Some tools delete old logs. **Claude Code** prunes transcripts after `cleanupPeriodDays`
(default **30**); raise it in `~/.claude/settings.json` to keep more history. **Codex**
keeps everything. The dashboard also keeps parsed sessions in its cache even after a tool
deletes the on-disk log, so totals don't silently shrink once seen.

---

## Extending it

- **Add a model's price / fix an unknown model:** edit `PRICING` (and, if needed, the
  model-name normalizer) in `parser.py`. Verify rates against the vendor's docs.
- **Add a new tool:** add its paths + a `parse_*` function in `parser.py`, wire `discover()`
  and `update_file()`, then add it to `SRC`/`ORDER` in `index.html`.

See **[AGENTS.md](AGENTS.md)** for a concise, agent-oriented guide (any coding agent can run
and extend this from that file).

## Files

`dashboard.py` (server + cache + cost) · `parser.py` (log parsers + pricing) ·
`index.html` (frontend) · `chart.umd.min.js` (vendored Chart.js) · `AGENTS.md` · `run.sh`.

## License

MIT — see [LICENSE](LICENSE).
