# AGENTS.md

A local, **stdlib-only** dashboard that reads the logs your AI coding tools already write
to this machine and shows tokens, estimated cost, and breakdowns by model / day / tool /
project / hour. Nothing leaves the machine. Nothing to install.

```bash
python3 dashboard.py            # http://127.0.0.1:7878
```
Flags: `--port`, `--host`, `--interval`, `--rebuild`. First run parses everything (~30–60s
with big Codex logs), then caches; later refreshes are incremental. If asked to "run the
dashboard", check `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7878/api/data`
first — it may already be up.

## Layout

| File | Role |
|---|---|
| `dashboard.py` | stdlib `http.server`. Serves `/`, `/static/*`, `/chart.js`, `/manifest.json`, `/sw.js`, `/api/{data,storage,refresh,settings,cache}`. Owns the cache, the aggregate merge and `_cost()`. |
| `parser.py` | `discover()` lists log files; `update_file()` routes each to a `parse_*`. Holds `PRICING` and the model-name normalizers. |
| `static/core.js` | `SRC`/`ORDER`, state `S`, formatting, date ranges, filtering. |
| `static/charts.js` | Chart.js theming, `mk()`/`hbar()`/`areaDS()`, calendar + heatmap SVG. |
| `static/views.js` | The seven views, controls, events, boot. |
| `index.html` | Shell only: header, tabs, filter bar, card markup. |

Eight tabs: Overview · Cost · Models & Providers · Tools & Agents · Projects · Sessions ·
**Optimize** · Storage. Tab lives in `location.hash`; `?theme=` and `?range=` preset the UI (handy for
headless screenshots).

Aggregates are keyed `records["date\tmodel"]`, `tools["date\tname"]`,
`hourly["date\thour"]` — **every dimension carries a date** so the UI can filter by range.

## Rules

1. **Stdlib only, offline.** No runtime dependencies. Vendor any JS (Chart.js already is).
2. **Never commit `.usage_cache.json`** or `server.log` — that's the user's own prompts,
   projects and costs. A fresh clone must start empty.
3. **Never hardcode a path.** Derive from `HOME` / `%APPDATA%` / `%LOCALAPPDATA%` /
   `$XDG_*`. Split path components with `_leaf()` (handles `/` and `\`) — logs written on
   one OS get read on another.
4. **Commits are authored by the repo owner alone.** Never add a `Co-authored-by:` trailer.
5. **Costs are estimates** at API list prices; subscription users pay nothing per token.
   Keep that framing. Verify any price against vendor docs — never guess.
6. **Attribute by tool, not by model.** A Claude model run inside Copilot counts as Copilot.
7. **Bump `CACHE_VERSION`** whenever an aggregate's shape changes.
8. **Never `except Exception: pass` around a parser.** A swallowed error is
   indistinguishable from "the user doesn't have this tool" — that's how a `TypeError`
   once made the whole opencode parser silently yield nothing. Write to stderr.
9. **Any write endpoint goes through `Handler._csrf_ok()`.** There's no auth, so any page
   the user visits can POST here; with `Content-Type: text/plain` it's a CORS simple
   request with no preflight. That was enough to set `cleanupPeriodDays=1` and make Claude
   Code delete transcripts. The guard demands a JSON content type and same-origin.
10. **Colours are a validated palette and `ORDER` in `core.js` is the safety mechanism** —
    adjacent pairs must clear CVD ΔE ≥ 8 and normal-vision ΔE ≥ 15 in *both* themes.
    Re-run the data-viz skill's `validate_palette.js` over the whole sequence after any
    reorder or hue change. Identity must never be colour-alone: keep legends and tooltips.

## The IDE dimension

`records` carry an `ide` — which editor/surface the work ran in — resolved once per
aggregate by `_ide_of()` and filterable like project or model. Each source records it
differently and none agree on spelling, so they collapse to a shared vocabulary:
Copilot's comes from *which editor's storage* the file sat in, Claude/Codex stamp an
`entrypoint`/`originator`, and Cursor/Claude Desktop/opencode/Hermes run in exactly one
place. An unrecognised entrypoint passes through **as itself** rather than being forced
into a bucket, so a new host appears rather than silently becoming "VS Code".

**A new `entrypoint` value can collide in spelling with an unrelated `source`.**
`entrypoint: "claude-desktop"` on `source: "claude"` (Claude Code launched from
inside the Desktop app) and `source: "claude-desktop"` (Desktop's own agent-mode
logs — a different file format) both mean "the Claude Desktop app" to the user, but
the first fell through `IDE_FROM_ENTRY` unmapped and showed as a separate, oddly-cased
row. Mapped alongside `local-agent`, the older entrypoint for the same app.

**Codex's VS Code variant is recovered from the editor, not the log.** Codex only ever
writes `vscode`, so Insiders work is indistinguishable from stable in the rollout itself.
But each editor's `globalStorage/state.vscdb` carries the Codex extension's per-thread UI
state under `openai.chatgpt`; a thread id appearing there means that editor opened it.
`_vscode_thread_owners()` builds {thread id → editor} across all known editors (60s TTL)
and `_ide_of` uses it. A thread present in TWO editors is genuinely ambiguous and is left
as plain "VS Code" rather than guessed — here that's 6 of 45.

**Still unrecoverable:** Claude Code. Its `Anthropic.claude-code` state holds only
settings and `hiddenSessionIds`, never a session list, and there is no per-editor
workspaceStorage for it — so `claude-vscode` stays "VS Code" whatever fork hosted it.
Same for any extension run inside Cursor/Windsurf/Antigravity that logs only "vscode".

Adding a VS Code fork is one entry in `COPILOT_ROOTS` + `EDITOR_LABEL`; the chat storage
format is identical across forks. Note newer builds nest sessions as
`chatSessions/<uuid>/index.json` instead of a flat file — both globs are needed.

## The durable ledger is load-bearing — treat it as data, not cache

Once a log is deleted from disk its aggregates exist ONLY in `.usage_cache.json`,
marked `archived`. The Storage tab actively tells users deleting old logs is safe,
so that file stops being a cache and becomes the sole record. Two consequences:

- `load_cache()` **keeps `archived` entries across a `CACHE_VERSION` bump** while
  re-parsing live logs normally. They cannot be re-derived — the source is gone — so
  discarding them on a version change would silently destroy history the UI promised
  to keep. Newer fields are read with `.get()` defaults, so an old-shape archived
  entry degrades rather than breaks.
- **`--rebuild` destroys archived sessions too**, not just the Settings buttons — it
  skips the cache entirely and re-parses from disk, and an archived session has no
  disk to parse. Back up `.usage_cache.json` before running it on a machine whose
  logs have been archived off.
- The Settings panel's **Rebuild / Delete cache** actions still drop archived
  sessions permanently; both warn about exactly this. Don't add a third path that
  clears the cache without the same warning.
- **One conversation can hold two ledger entries**, and each used to count in full:
  Codex's archive feature moves a rollout into `archived_sessions/` (the old path stays,
  archived, beside the new live one), and a Copilot chat can exist as both `<uuid>.json`
  and `<uuid>.jsonl` across its storage-format migration, or in both VS Code's and
  Cursor's storage after Cursor imported VS Code's. `build_payload` keeps one per
  conversation (`_one_per_conversation`: fullest, then live, then newest).

## What the logs cannot show

- **Claude Code bills calls it never writes down.** A `/compact` (manual or auto) runs a
  summarization request whose usage appears nowhere in the transcript, and Claude Code's
  background Haiku calls never do either. Its own `cost-state` records (rare) include
  them — where they exist, the transcript matches them exactly *except* for those calls,
  typically a few percent of cost. Don't estimate them from `compactMetadata` token sizes.
- **`codex-auto-review` has no public price**, so its tokens are counted but cost $0.

## Field conventions that differ by source (do not "fix" these)

- **One Claude response is several records, and a resume can replay them all.** Claude
  Code writes one `type:"assistant"` record per content block (thinking, text, each
  tool_use), each repeating the WHOLE response's `usage` — so tokens, cost and "assistant
  msgs" count once per `(message.id, requestId)`, and a later block adds only what grew
  (`output_tokens` streams upward: 1 on the first block, 388 by the last). Tool calls
  stay per record — each record carries its own block. Separately, resuming a session
  can rewrite its whole history into the same file (same `uuid`s and timestamps, newer
  `version`), so any record whose `uuid` was already seen in that file is skipped —
  `state.seen_uuids`, 12 hex chars per record, persisted for incremental parsing.
  Summing records instead inflated Claude usage ~2.3x (Sep 2026: 3.16B for 1.35B billed).
- **A Claude "prompt" is only what the user typed.** Newer builds stamp `origin.kind` on
  a user turn (`human`, `task-notification`); anything not `human` is dropped. So is every
  `isMeta` record — screenshot captions (older builds omit `turnCompanion`), skill bodies,
  slash-command expansions, "Continue from where you left off." after a limit stop — plus
  `isCompactSummary`, and records opening with a slash-command wrapper,
  `<task-notification>` or "[Request interrupted by user". Slash commands never count, a
  queued "/compact" included. Assistant records with model `<synthetic>` are Claude Code's
  own notices (zero usage), not replies. Before this, ~14% of prompts were never typed.
- **A prompt typed while Claude is working is not a `type:"user"` record.** Claude Code
  queues it and writes `type:"attachment"` with `attachment.type == "queued_command"`,
  so the user branch never sees it — that silently undercounted prompts by ~23%.
  Count it, but only `commandMode == "prompt"` (`task-notification` is the harness
  telling itself a background task finished), with non-empty text that isn't an
  `<ide_opened_file>` / `<system-reminder>` injection riding the same channel. Do NOT
  dedupe against `user` records by text: the same words seconds apart are usually the
  user pressing enter twice, and `source_uuid` does not link the two. Codex needs none
  of this — it logs a steer as an ordinary `UserMessage`.
- **User turns** land in two different places: Claude/Claude Desktop/Codex write them
  to a `(user)` marker row in `records`; Copilot/Cursor write them onto the model row.
  Neither writes both, so summing `r.user` across all records is correct — but a check
  that assumes one convention will report a phantom bug.
- **`reason` is a SUBSET of `out`, never additive** — Claude's
  `usage.output_tokens_details.thinking_tokens` and Codex's `reasoning_output_tokens`
  are both already inside `output_tokens`. The UI shows it as "of which reasoning"
  without stacking; anything that adds it to a token total is double-counting.
- **`cc5 + cc1` can disagree with `cc` by a few hundred tokens.** Anthropic itself
  occasionally logs `cache_creation_input_tokens: 0` alongside a non-zero
  `cache_creation.ephemeral_1h_input_tokens`. Both are recorded as-is; cost uses the
  tiered fields. Seen once in 813 rows, worth ~$0.003 — upstream, not ours.
- **`side` vs `subagents`**: Claude folds subagent tokens into the parent's stream
  (`side`); Codex spawns wholly separate sessions and only the parent's spawn COUNT
  (`subagents`) is knowable. Check both when asking "did this session delegate".

## Per-source quirks

- **Codex has (at least) two incompatible rollout schemas.** Older/stable CLIs emit
  flat `event_msg` payloads (`agent_message`, `user_message`, `token_count`). Recent
  alpha builds (seen: `0.151.x`) wrap turn content in one `item_completed` event
  whose own `item.type` names the real kind (`UserMessage`, `AgentMessage`,
  `Reasoning`, `CommandExecution`, `SubAgentActivity`, ...) — `token_count` and the
  `response_item` tool-call events are unchanged across both, which is exactly why a
  schema mismatch here degrades quietly: tokens/cost/tools keep working while
  prompts/messages silently zero out. `parse_codex` handles both; if Codex ships a
  third shape, check `event_msg` payload types in a fresh rollout file before
  assuming the existing branches still apply.
- **Codex usage comes from `token_usage_record` once a rollout has one.** Builds from
  2026-09 log one per model response (with a `response_id`), written before its
  `token_count` twin. `token_count` alone misses a compaction request (it logs zeros
  after `compacted`) and a response cut off mid-turn. Before the first record, and in
  older rollouts, `token_count` is used — but an event whose (`total_token_usage`,
  `last_token_usage`) pair was already seen in the last 32 is a re-emission, not new
  usage: Codex repeats the previous one when a turn starts, and two Codex processes on
  one thread interleave two running totals in the same file, so a repeat can land a few
  events late (776 had double-counted 116M tokens). To check the arithmetic, chain each
  event onto the counter it continues and compare to Codex's own totals — neither
  `threads.tokens_used` in `state_5.sqlite` (it resets, and omits compactions) nor a
  `guardian_review` rollout's total (forked from the session it reviews, it starts with
  that session's total on the clock) can be compared directly.
- **Codex's built-in tools are `response_item`s of their own type** —
  `web_search_call`, `tool_search_call`, `image_generation_call` — not function calls and
  never an `event_msg`. Web searches were uncounted until they were read from there.
- **Codex's session title lives outside the rollout.** `~/.codex/session_index.jsonl`
  maps thread id → `thread_name`, and that is the name Codex's own UI shows. It is
  append-only, so a renamed thread gets a NEW line and the LAST one wins — same
  shape as Claude's `aiTitle`. Applied at rank "ai" so it beats a prompt snippet.
  ~80% coverage here; threads with no entry keep the first-prompt title. Do NOT
  reach for the editor's `agentSessions.model.cache` instead: it holds the same
  string but appears and vanishes within seconds while VS Code runs.
- **Codex subagents self-identify** via `session_meta.thread_source == "subagent"`
  in the CHILD's own file (plus `parent_thread_id`, `agent_path`, `agent_nickname`)
  — no cross-file correlation needed, unlike Claude Code. A subagent's task is
  usually never an in-band `UserMessage` in its own log (it arrives at spawn time),
  so `_finalize_session` falls back to the leaf of `agent_path` as its title, and
  only when no real prompt was ever found. The PARENT's own file separately counts
  `SubAgentActivity` "started" markers into `subagents` (a count, the same field
  Cursor uses) — read that, not `side` (which Codex never sets: its subagents are
  wholly separate sessions, not sidechain records mixed into the parent's stream).
- **Cursor** (`state.vscdb`): sessions in `cursorDiskKV` under `composerData:*` (newer
  builds also `composerHeaders`); messages are `bubbleId:*`. Each bubble has its **own**
  `createdAt` — use it, not the session's, or a months-long session lands on day one.
  `ItemTable` holds `aiCodeTracking.dailyStats.*` (AI lines suggested vs accepted). Only
  ~2% of bubbles carry tokens; that's Cursor, not a parsing gap.
- **Copilot** logs no tokens at all. It does log a premium-request multiplier in
  `result.details` ("… • 1x") — that's its real billing unit.
- **opencode**: current versions use one SQLite `opencode.db`; older ones use
  `storage/message/<session>/msg_*.json`. Both are read. Only the DB records a real
  per-message cost, so cost routing keys on whether the aggregate's path ends `.db`.
- **Gemini CLI** is deliberately not parsed — its `chats/*.jsonl` hold only session
  bookkeeping, no prompts/tokens/model.
- **Hermes Agent** (`~/.hermes/state.db`, `$HERMES_HOME`, or `%LOCALAPPDATA%\hermes`): one
  SQLite store for all sessions. Unlike Cursor it logs a real per-model in/out/cache/
  reasoning breakdown in `session_model_usage` (hence `exact:true`), one row per model a
  session actually used — sessions can switch model mid-way, like Codex. `messages.tool_calls`
  is an OpenAI-shaped JSON array, read only for per-day tool and turn counts; message
  `content` is never read.
- **SQLite stores**: always open via `_open_ro_sqlite()`. Neither flag is safe alone —
  `mode=ro` reads the `-wal` (so a *running* tool's newest sessions are visible) but must
  create a `-shm`, which fails on read-only media; `immutable=1` needs no `-shm` but ignores
  the `-wal` entirely. The helper tries the first, probes it with a query (connect is lazy),
  and falls back to the second.

## Common tasks

**A model shows $0 / an unknown name** → in `parser.py`, add
`PRICING["<Display Name>"] = (input, output, cache_write_5m, cache_write_1h, cache_read)`
(USD per 1M; OpenAI rows use `0, 0` for the write tiers and put the cached rate last), and
make the normalizer map the raw id to that name. Pricing applies at request time — no
re-parse needed; a normalizer change needs `--rebuild` + a `CACHE_VERSION` bump.

**A vendor changed a price** → the new tuple goes in `PRICING` (always today's rate — the
Optimize tab re-prices savings from it) and the old one moves into `PRICE_HISTORY` with
the last date it applied. `price_of(model, date)` picks by each record's date, so a cut
never re-prices history. Record a change only once it has taken effect — never pre-encode
an announced future price: Sonnet 5's scheduled $3/$15 increase was encoded ahead of time,
then cancelled by Anthropic, and silently overbilled every day after 2026-08-31 by 50%.

**Add a tool source** → `parser.py`: paths, emit from `discover()`, write `parse_<tool>()`,
route in `update_file()`. Then `SRC`/`ORDER` in `core.js`, a `--t-<source>` colour in
`app.css` (re-validate the palette), bump `CACHE_VERSION`, update README + this file.

**Test a UI change** → headless Chrome catches render failures; both uncaught and caught
errors land on `document.documentElement.dataset.jsError`:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --virtual-time-budget=9000 --dump-dom "http://127.0.0.1:7878/#cost" | grep data-js-error
```

Sweep every tab at `?range=today` too: a line chart needs **two** points to draw a segment,
so single-day ranges render as empty axes unless the dataset uses `pointRadius: soloPoint(data)`.
Add `--screenshot=out.png --window-size=1560,2000` to eyeball it.

## The Optimize tab

Findings are computed client-side in `static/views.js` by the functions in
`OPT_FINDERS`, ranked by estimated saving. A finder returns `null` when it does not
apply — **never render a finding that isn't backed by the user's own numbers**, and
every one must carry the figure it came from plus something concrete to do.

**Never hardcode a model name, price or tool list into a finding.** Savings are
re-priced from `RAW.prices` (the real per-1M rates for the models that user actually
ran) and candidate swaps come from `cheaperPeer()`, which only ever suggests a model
they already use from the same maker. A finder that named specific models would go
stale and would be wrong for anyone whose mix differs.

A finding's left stripe carries **tool identity**, painted from the same
`--t-<source>` token as every chart and badge — orange is still Claude Code, green
still Codex. It is only painted when exactly one tool is in scope; a finding spanning
several has no single owner and stays neutral. It must never encode severity again:
`--accent` is byte-identical to `--t-opencode` and `--warn` collides with
`--t-claude-desktop`, so a severity stripe silently painted findings in the colour of
a tool they had nothing to do with.

Each finding sets `tools: [...]`. `scopeLabel()` renders a "<tool> only" chip when a
finding does not span every source present in the range — the MCP, Skills and
`/compact` advice is Claude Code's, and a Codex or Cursor user must not read it as
advice about their own setup. No chip is shown when it applies to everything, since
then the label is noise.

**MCP attribution differs by tool.** Claude Code names MCP tools
`mcp__<server>__<tool>`; Codex keeps the bare tool name and puts the server in a
separate `namespace` field (`"mcp__azure"`). `parse_codex` normalises to Claude's
shape — without that, an MCP tool is indistinguishable from a built-in and every
Codex server looks unused. Configured servers come from `~/.claude.json` and the
`[mcp_servers.*]` blocks of `~/.codex/config.toml` (parsed by regex, not tomllib,
which is 3.11+).

It leans on three signals the other tabs don't use: `attributionSkill` (which Skill
drove a request — this is how "/dataviz cost you $20" is possible), a per-request
context-size histogram (`agg["ctx"]`, bucketed 0-50k / 50-150k / 150-400k / 400k+),
and the MCP servers configured in `~/.claude.json` (`_mcp_servers()`) compared against
`mcp__<server>__*` tool calls. Server names are matched loosely — the same server
appears as `claude-in-chrome` and `Claude_in_Chrome` across versions, and
`google-workspace` shows up in tool names as `workspace`.

## Active time — a gap-capped estimate, not wall-clock length

`records`/`sessions` carry `active` seconds: the sum of gaps BETWEEN consecutive real
turns, counted only when the gap is <= `ACTIVE_GAP_CAP` (300s, `parser.py`) — the same
heuristic WakaTime/RescueTime use. It is a lower bound on time actually spent driving
the tool, deliberately not `end - start`: a Codex session resumed after three days away
must not report three days of "active" time. Formatted client-side by `fmtDur()`.

- **Computed at the same choke point every source already shares**: `_bump_time()`
  receives a real timestamp at every one of its 11 call sites, so `_active_gap()` sits
  right next to it rather than duplicating cap logic per source.
- **Single-session sources** (Claude/Claude Desktop, Codex, Copilot, legacy opencode —
  one file per session) persist the last-event timestamp on `agg["_active_last"]`,
  because Claude/Codex/Copilot-jsonl parse incrementally (byte-offset resume): the
  function only ever sees the NEW lines, so the previous timestamp must survive across
  calls or every incremental chunk looks like the start of a fresh burst. A fresh
  `_blank_agg` (full reparse) correctly resets it to `None`.
- **Multi-session sources** (Cursor, opencode's SQLite DB, Hermes — one store holding
  many unrelated sessions) use a LOCAL dict keyed by session id instead, never
  `agg`-level: these are always fully reparsed from scratch on change (see
  `update_file`), so nothing needs to persist, and persisting at the `agg` level would
  incorrectly bridge a gap across two different sessions in the same store.
- **Not every timestamp in a source is a real turn.** Hermes' `session_model_usage` is
  one SUMMARY row per (session, model) pair — its `first_seen`/`last_seen` span the
  whole pairing, not a single turn — so it must never feed a gap calculation; only the
  `messages` table's per-row timestamps do. Codex's `token_count` event and Copilot's
  `_copilot_apply_request` ARE legitimate per-turn signals (same events `_bump_time`
  already used for the activity heatmap), so both participate normally.
- **Copilot legitimately measures near-zero.** Its interactions are one-shot
  completions, not an agentic tool loop, so consecutive events are usually well over
  the 5-minute cap apart. That is the heuristic doing its job, not a parsing gap.
- **opencode's SQLite sessions inherit an existing imprecision**, not a new one: they
  have no per-session `days` breakdown (only Cursor and Hermes build one), so
  `dashboard.py` falls back to the whole-DB `file_days` for their per-day figures —
  true for every field, not just `active`. The session-level total (not per-day) is
  exact.
- The positional `s.days[date]` array gained a 10th slot, **appended, not inserted** —
  `[cost,in,out,cr,cc,asst,user,tools,prem,active]` — specifically so nothing that reads
  it by index elsewhere needs to change. `static/core.js`'s `clipSession()` is the one
  place that unpacks it.

## Gotchas

- The **PWA service worker is opt-in** (gear menu, `localStorage` `aiu.pwa`) and never
  registered without consent — a service worker controls the origin until unregistered,
  and localhost ports get reused by other tools. It is network-first: the cache is an
  offline fallback only, never preferred, or `git pull` wouldn't take effect until the
  second reload. `/api/` is never intercepted.
- **Durable ledger**: sessions pruned from disk stay counted and are marked `archived`, so
  totals never silently shrink.
- **`--rebuild` is genuinely destructive on a machine with archived history** — it is not
  just a Settings-panel-only hazard (see above). Forgetting this once already wiped 251
  archived Copilot sessions from the running cache mid-session; recovered from the same
  `~/ai-usage-archives/usage_cache_backup_*.json` this file already tells you to make.
  Check `ls ~/ai-usage-archives/` (or wherever archives were made) BEFORE running
  `--rebuild`, every time — not just the first time.
- A `.dd-panel` that can overflow its `max-height` needs its most important action
  (here: the range picker's "Apply custom range") wrapped in `.dd-pin`, a
  `position:sticky` footer pulled into the panel's own padding — otherwise it silently
  requires scrolling to reach, which is how the custom date range historically hid its
  own submit button.
- Anything shown as a shell command must be built server-side from real discovered paths
  and `os.name` (`_cleanup_plan`) — never a hardcoded `~/Library/...` string.
