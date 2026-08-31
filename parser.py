"""
parser.py — Incremental usage-log parser for local AI coding tools.

Sources: Claude Code, Claude Desktop (agent mode), Codex CLI, GitHub Copilot
(VS Code / Insiders / Cursor), Cursor native AI, opencode, and Hermes Agent. Each tool stores
interaction logs locally; this module discovers those files, parses them
incrementally (append-only .jsonl read from a byte offset; rewritten stores
and SQLite databases re-read on change), and produces per-file aggregates the
server merges into one dataset. Everything is keyed off the user's own home
dir — nothing is hardcoded to a machine or account, so it works on any
Mac/Linux install of the same tools.

No third-party dependencies — stdlib only.
"""
import os, sys, json, glob, re, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def _leaf(path):
    """Last path component of a cwd recorded on ANY OS — a macOS/Linux log read on
    Windows (or vice versa) still has the other platform's separator in it."""
    if not path:
        return ""
    return os.path.basename(str(path).replace("\\", "/").rstrip("/"))

# ---------------------------------------------------------------------------
# Source locations
# ---------------------------------------------------------------------------
CLAUDE_GLOBS = [os.path.join(HOME, ".claude", "projects", "**", "*.jsonl")]
CODEX_GLOBS = [
    os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl"),
    os.path.join(HOME, ".codex", "archived_sessions", "**", "*.jsonl"),
]
# Claude Desktop "local agent mode" runs Claude Code in a sandbox; it writes
# standard Claude-format transcripts under a nested .claude/projects/ tree
# (the sibling audit.jsonl mirrors the same sessions, so we deliberately skip it).
def _app_support_roots(name):
    """Per-user application-data dirs for `name` on macOS, Windows and Linux."""
    out, seen = [], set()
    for base in (os.path.join(HOME, "Library", "Application Support"),   # macOS
                 os.environ.get("APPDATA"),                              # Windows
                 os.environ.get("LOCALAPPDATA"),                         # Windows
                 os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")):
        if not base:
            continue
        r = os.path.join(base, name)
        if r not in seen:
            seen.add(r); out.append(r)
    return out


CLAUDE_DESKTOP_GLOBS = [
    os.path.join(r, "local-agent-mode-sessions", "**", ".claude", "projects", "**", "*.jsonl")
    for r in _app_support_roots("Claude")
]
# Gemini CLI persists ONLY user prompts locally (no model / no tokens / no responses),
# so this source contributes activity (prompts/sessions/days/projects) but no token data.
GEMINI_GLOBS = [
    os.path.join(HOME, ".gemini", "tmp", "*", "chats", "*.jsonl"),
]
# Cursor's native AI: one SQLite key-value store. Bubbles hold messages; token counts
# are present on only a few and no reliable per-message model is stored.
# (CURSOR_DBS is derived portably from the editor roots below.)
def _editor_roots(names):
    """VS Code-family app-support dirs across macOS, Linux and Windows."""
    bases = [
        os.path.join(HOME, "Library", "Application Support"),          # macOS
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),  # Linux
        os.environ.get("APPDATA", ""),                                 # Windows
    ]
    out = []
    for base in bases:
        if not base:
            continue
        for n in names:
            out.append(os.path.join(base, n))
    return out


# VS Code forks that ship Copilot Chat and therefore write the same chatSessions
# store. Adding a fork here is all it takes to cover it — the on-disk format is
# identical because they all inherit VS Code's chat storage. Verified present on
# this machine: Code, Code - Insiders, Cursor, Puku. The rest are included because
# they are the same shape and cost nothing when absent (glob on a missing dir is
# simply empty), NOT because they were tested here.
COPILOT_ROOTS = _editor_roots([
    "Code", "Code - Insiders", "VSCodium", "Cursor", "Puku",
    "Windsurf", "Trae", "Positron",
])
CURSOR_DBS = [os.path.join(r, "User", "globalStorage", "state.vscdb")
              for r in _editor_roots(["Cursor"])]

# opencode (SST) — per-message JSON at storage/message/{sessionID}/msg_*.json.
# cost is stored as 0, so we compute it from tokens like every other source.
def _opencode_roots():
    roots, seen = [], set()
    win = [os.path.join(b, "opencode")
           for b in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")) if b]
    for r in [os.environ.get("OPENCODE_DATA_DIR"),
              os.path.join(os.environ.get("XDG_DATA_HOME") or
                           os.path.join(HOME, ".local", "share"), "opencode"),
              *win,
              os.path.join(HOME, ".opencode")]:
        if r and r not in seen:
            seen.add(r); roots.append(r)
    return roots


OPENCODE_ROOTS = _opencode_roots()
# Current opencode stores interaction history in a single SQLite database.
OPENCODE_DBS = [os.path.join(r, "opencode.db") for r in OPENCODE_ROOTS]

# Hermes Agent (NousResearch) — one SQLite state.db under $HERMES_HOME (default
# ~/.hermes, or %LOCALAPPDATA%\hermes on native Windows) holding every session,
# same "one store, many sessions" shape as Cursor's state.vscdb.
def _hermes_home():
    override = os.environ.get("HERMES_HOME", "").strip()
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return os.path.join(base, "hermes")
    return os.path.join(HOME, ".hermes")


HERMES_DB = os.path.join(_hermes_home(), "state.db")

EDITOR_LABEL = {
    "Code": "VS Code",
    "Code - Insiders": "VS Code Insiders",
    "VSCodium": "VSCodium",
    "Cursor": "Cursor",
    "Puku": "Puku",
    "Windsurf": "Windsurf",
    "Trae": "Trae",
    "Positron": "Positron",
}

# ---------------------------------------------------------------------------
# Pricing — USD per 1,000,000 tokens:
#   (input, output, cache_write_5m, cache_write_1h, cache_read)
# Anthropic prices verified against the current model table (Opus 4.x = $5/$25,
# NOT the old $15/$75 — Opus pricing dropped with 4.5). Cache write = 1.25x input
# (5-min TTL) / 2x input (1-hour TTL); cache read = 0.1x input. OpenAI rows have
# no cache-write tier (cw5/cw1 = 0); cached input goes in the cache_read slot.
# Codex/Copilot/Cursor are subscription-billed, so their $ is an API-equivalent
# estimate, not an actual charge. Costs are computed at request time — edit
# freely, no re-parse needed.
# ---------------------------------------------------------------------------
PRICING = {
    # Anthropic Claude 5 family
    "Claude Fable 5": (10, 50, 12.5, 20, 1.0),
    "Claude Mythos 5": (10, 50, 12.5, 20, 1.0),
    # Sonnet 5 STANDARD rate ($3/$15); a $2/$10 intro rate applies through
    # 2026-08-31 and is handled date-aware in dashboard._cost.
    "Claude Sonnet 5": (3, 15, 3.75, 6, 0.30),
    "Claude Mythos Preview": (10, 50, 12.5, 20, 1.0),
    # Anthropic Opus 4.5+ — $5/$25 (current pricing)
    "Claude Opus 5": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.8": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.7": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.6": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.5": (5, 25, 6.25, 10, 0.50),
    # Anthropic Opus 4.1 / 4.0 — legacy $15/$75 pricing
    "Claude Opus 4.1": (15, 75, 18.75, 30, 1.50),
    "Claude Opus 4": (15, 75, 18.75, 30, 1.50),
    # Anthropic Sonnet — $3/$15
    "Claude Sonnet 4.6": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 4.5": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 4": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 3.7": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 3.5": (3, 15, 3.75, 6, 0.30),
    # Anthropic Haiku — $1/$5
    "Claude Haiku 4.5": (1, 5, 1.25, 2, 0.10),
    "Claude Haiku 3.5": (0.80, 4, 1.0, 1.6, 0.08),
    # OpenAI GPT-5.6 series (Sol/Terra/Luna) — verified from OpenAI docs (2026-07).
    # cache read = 0.1x input; a >272K-input surcharge (2x in/1.5x out) is not modeled.
    "GPT-5.6 Sol": (5, 30, 0, 0, 0.50),
    "GPT-5.6 Terra": (2.5, 15, 0, 0, 0.25),
    "GPT-5.6 Luna": (1, 6, 0, 0, 0.10),
    # OpenAI GPT-5.4 / 5.5 — verified from OpenAI API pricing docs (2026-07).
    # NOTE: GPT-5.5 has a >272K-input surcharge (2x in / 1.5x out for the session)
    # not modeled here, so heavy-context Codex sessions may cost somewhat more.
    "GPT-5.5": (5, 30, 0, 0, 0.50),
    "GPT-5.5 Pro": (30, 180, 0, 0, 3.0),
    "GPT-5.4": (2.5, 15, 0, 0, 0.25),
    "GPT-5.4 Mini": (0.75, 4.5, 0, 0, 0.075),
    "GPT-5.4 Nano": (0.20, 1.25, 0, 0, 0.02),
    # older GPT-5.x — delisted from OpenAI's current table, kept as estimates
    "GPT-5.3": (2.5, 15, 0, 0, 0.25),
    "GPT-5.3 Codex": (2.5, 15, 0, 0, 0.25),
    "GPT-5.2": (1.25, 10, 0, 0, 0.125),
    "GPT-5.1": (1.25, 10, 0, 0, 0.125),
    "GPT-5": (1.25, 10, 0, 0, 0.125),
    "GPT-5 Mini": (0.25, 2, 0, 0, 0.025),
    # OpenAI legacy (estimates)
    "GPT-4.1": (2, 8, 0, 0, 0.5),
    "GPT-4.1 Mini": (0.40, 1.6, 0, 0, 0.10),
    "GPT-4o": (2.5, 10, 0, 0, 1.25),
    "o4-mini": (1.1, 4.4, 0, 0, 0.275),
    "o3": (2, 8, 0, 0, 0.5),
}


def price_of(display):
    return PRICING.get(display, (0, 0, 0, 0, 0))


def vendor_of(display):
    d = display.lower()
    if d.startswith("claude"):
        return "Anthropic"
    if d.startswith(("gpt", "o3", "o4", "o1")):
        return "OpenAI"
    if "gemini" in d or "gemma" in d:
        return "Google"
    if display in ("Auto", "(synthetic)", "Unknown"):
        return "Other"
    return "Other"


# ---------------------------------------------------------------------------
# Model-name normalization → a single display name shared across all tools
# ---------------------------------------------------------------------------
def normalize_claude(raw):
    if not raw or raw == "<synthetic>":
        return "(synthetic)"
    base = re.sub(r"-\d{8}$", "", raw)               # strip trailing date snapshot
    if "mythos-preview" in base:
        return "Claude Mythos Preview"
    # handles both "claude-opus-4-8" (X.Y) and "claude-sonnet-5" (single version),
    # and the newer fable/mythos tiers of the Claude 5 family
    m = re.match(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?$", base)
    if m:
        tier = m.group(1).capitalize()
        ver = f"{m.group(2)}.{m.group(3)}" if m.group(3) else m.group(2)
        return f"Claude {tier} {ver}"
    return raw


def normalize_codex(raw):
    if not raw:
        return "Unknown"
    # capture version + any named/size suffix (mini, nano, codex, sol, terra, luna, pro...)
    m = re.match(r"gpt-([\d.]+)(?:-([a-z]+))?", raw)
    if m:
        ver, suffix = m.group(1), m.group(2)
        name = f"GPT-{ver}"
        if suffix:
            name += " " + suffix.capitalize()
        return name
    if raw.startswith("o"):
        return raw
    return raw


def normalize_copilot(model_id, details):
    """Copilot's `details` string ("Claude Sonnet 4.5 • 0x") gives the cleanest
    display name; fall back to parsing the modelId ("anthropic/claude-sonnet-4-5-...")."""
    if details:
        name = details.split("•")[0].strip()
        if name:
            return _canonicalize(name)
    if model_id:
        base = model_id.split("/")[-1]
        base = re.sub(r"-\d{8}$", "", base)  # drop trailing date stamp
        return _canonicalize(base)
    return "Unknown"


def _canonicalize(name):
    """Map any display spelling, deployment alias, or raw model id to a canonical
    name. Copilot exposes the same model under many labels — e.g. 'Azure GPT-5.5',
    '(ai-foundry-10ms)gpt-5.5', 'OpenAI: GPT-5.5', 'azure-gpt-5.5' — all → 'GPT-5.5'."""
    n = re.sub(r"\([^)]*\)", "", name).strip()        # drop "(...)" qualifiers
    low = n.lower().replace("_", "-")
    # strip a leading provider / deployment tag
    low = re.sub(r"^(openai|azure|anthropic|google|microsoft|github(\.copilot[-\w]*)?|"
                 r"copilot|ai-foundry[-\w]*)\b[\s:/\-]*", "", low).strip()
    if low in ("auto", "copilot/auto", ""):
        return "Auto" if "auto" in low else "Unknown"
    if "raptor" in low:
        return "Raptor Mini"

    # Claude — "Claude Sonnet 4.5", "claude-sonnet-4-5", "claude-3.5-sonnet"
    tier = next((t.capitalize() for t in ("opus", "sonnet", "haiku") if t in low), None)
    if "claude" in low and tier:
        nums = re.findall(r"\d+(?:\.\d+)?", low)
        ver = ""
        if len(nums) >= 2 and "." not in nums[0] and "." not in nums[1] and len(nums[1]) == 1:
            ver = f"{nums[0]}.{nums[1]}"      # collapse "4-5" → "4.5"
        elif nums:
            ver = nums[0]
        return f"Claude {tier} {ver}".strip()

    # GPT with a version number → GPT-x.y (keep named/size variants distinct)
    mg = re.match(r"gpt-?\s*(\d+(?:\.\d+)?)", low)
    if mg:
        suffix = ""
        for s in ("sol", "terra", "luna", "mini", "nano", "pro", "codex"):
            if s in low:
                suffix = " " + s.capitalize()
                break
        return f"GPT-{mg.group(1)}{suffix}".strip()
    if low.startswith("gpt-oss"):
        return "GPT-OSS"

    # o-series (o3, o4-mini)
    mo = re.match(r"o\d+(?:-mini)?", low)
    if mo:
        return mo.group(0)

    return n or "Unknown"


# ---------------------------------------------------------------------------
# Timestamp helpers — everything is bucketed in the machine's LOCAL timezone
# ---------------------------------------------------------------------------
def _from_iso(ts):
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


def _from_ms(ms):
    try:
        return datetime.fromtimestamp(ms / 1000.0)
    except Exception:
        return None


def _buckets(dt):
    """Return (date 'YYYY-MM-DD', hour 0-23, day-of-week 0=Mon)."""
    return dt.strftime("%Y-%m-%d"), dt.hour, dt.weekday()


# ---------------------------------------------------------------------------
# Per-file aggregate container
# ---------------------------------------------------------------------------
def _blank_agg(source, path):
    return {
        "source": source,
        "path": path,
        "size": 0,
        "mtime": 0.0,
        "offset": 0,           # bytes parsed (jsonl only)
        "records": {},          # "date\tmodel" -> token/count dict
        "tools": {},
        # date\tskill -> tokens attributed to a Skill (Claude Code's attributionSkill)
        "skills": {},
        # date\tbucket -> tokens sent at that per-request context size
        "ctx": {},            # "date\ttool name" -> count
        "hourly": {},           # "date\thour" -> {tokens, msgs}  (day-of-week is
                                #   derived from the date, so it needs no bucket)
        "project": "(unknown)",
        "editor": None,
        "title": None,          # human-readable session name, when the tool logs one
        "branch": None,         # git branch the work happened on
        "entry": None,          # entrypoint / originator (CLI vs IDE)
        "cliver": None,         # tool version that wrote the log
        "totals": {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0,
                   "reason": 0, "asst": 0, "user": 0, "req": 0, "prem": 0.0,
                   "tools": 0, "side": 0},
        "first_ts": None,
        "last_ts": None,
        # transient stream state for incremental codex parsing
        "state": {"cur_model": None},
        "sessions": [],
    }


def _rec(agg, date, model):
    key = f"{date}\t{model}"
    r = agg["records"].get(key)
    if r is None:
        r = {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
              "asst": 0, "user": 0, "req": 0, "prem": 0.0, "tools": 0, "cost": 0.0}
        agg["records"][key] = r
    return r


def _tool(agg, date, name):
    """Count one tool/function call, keyed by day so the UI can date-filter it."""
    k = f"{date}\t{name}"
    agg["tools"][k] = agg["tools"].get(k, 0) + 1
    agg["totals"]["tools"] += 1


def _bump_time(agg, dt, tokens, msgs):
    date, hour, _dow = _buckets(dt)
    h = agg["hourly"].setdefault(f"{date}\t{hour}", {"tokens": 0, "msgs": 0})
    h["tokens"] += tokens
    h["msgs"] += msgs
    iso = dt.isoformat()
    if agg["first_ts"] is None or iso < agg["first_ts"]:
        agg["first_ts"] = iso
    if agg["last_ts"] is None or iso > agg["last_ts"]:
        agg["last_ts"] = iso


_TITLE_NOISE = re.compile(
    r"^\s*([-*>|]|#|<|```|\[|Context from my IDE|Files mentioned by the user|"
    r"Active file:|Screenshot|Caveat:|Distinguish instructions|<system-reminder)", re.I)


def _clean_prompt(text):
    """First line of a prompt that is actual user intent, not IDE/tool preamble."""
    if not text:
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if not line or _TITLE_NOISE.match(line):
            continue
        return line
    return ""


# Title sources, weakest to strongest. Tools APPEND a new title record every
# time the session is re-titled, so within a rank the LAST one seen wins —
# otherwise a session keeps the first name it was ever auto-given and a manual
# rename is silently ignored.
TITLE_RANK = {"prompt": 1, "ai": 2, "custom": 3}


def _set_title(agg, text, kind="prompt"):
    rank = TITLE_RANK.get(kind, 1)
    if kind == "prompt":
        text = _clean_prompt(text)
    if not text:
        return
    t = " ".join(str(text).split())[:90]
    if not t:
        return
    have = agg.get("_title_rank", 0)
    if rank < have:
        return                      # never let a weaker source overwrite
    if kind == "prompt" and agg.get("title"):
        return                      # the FIRST prompt, not the latest
    agg["title"] = t
    agg["_title_rank"] = rank


def _first_text(content):
    """First plain-text chunk of a Claude/Codex message content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, str):
                return b
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                return b["text"]
    return ""


# ===========================================================================
# CLAUDE CODE
# ===========================================================================
def _is_subagent_path(path):
    """Claude Code writes each subagent's transcript to
    <session-id>/subagents/agent-<id>.jsonl. Those files are 100% isSidechain."""
    p = str(path or "").replace("\\", "/")
    return "/subagents/" in p and _leaf(p).startswith("agent-")


def parse_claude(agg, lines):
    project = agg["project"]
    model_tokens = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        cwd = o.get("cwd")
        if cwd:
            project = _leaf(cwd) or cwd
        # session metadata Claude Code writes on every entry
        if o.get("gitBranch"):
            agg["branch"] = o["gitBranch"]
        if o.get("entrypoint"):
            agg["entry"] = o["entrypoint"]
        if o.get("version"):
            agg["cliver"] = o["version"]
        # the tool's own name for the session beats a prompt snippet
        if o.get("customTitle"):
            _set_title(agg, o["customTitle"], "custom")
        elif o.get("aiTitle"):
            _set_title(agg, o["aiTitle"], "ai")
        side = bool(o.get("isSidechain"))
        msg = o.get("message") if isinstance(o.get("message"), dict) else None
        dt = _from_iso(o.get("timestamp", "")) if o.get("timestamp") else None

        if t == "assistant" and msg:
            model = normalize_claude(msg.get("model"))
            u = msg.get("usage") or {}
            inp = int(u.get("input_tokens", 0) or 0)
            out = int(u.get("output_tokens", 0) or 0)
            cr = int(u.get("cache_read_input_tokens", 0) or 0)
            cc = int(u.get("cache_creation_input_tokens", 0) or 0)
            # Thinking tokens are a SUBSET of output_tokens (never additive) — the
            # same convention Codex's reasoning_output_tokens uses, and what the UI
            # assumes when it shows "of which reasoning" without stacking it.
            # Without this Claude's extended thinking is invisible: the token
            # composition card and the Optimize "thinking" finding only ever saw Codex.
            reason = int((u.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0)
            ccd = u.get("cache_creation") or {}
            cc5 = int(ccd.get("ephemeral_5m_input_tokens", 0) or 0)
            cc1 = int(ccd.get("ephemeral_1h_input_tokens", 0) or 0)
            if cc and not (cc5 or cc1):   # older logs without the tier split
                cc5 = cc                  # assume 5-min when untiered
            if dt:
                r = _rec(agg, _buckets(dt)[0], model)
                r["in"] += inp; r["out"] += out; r["cr"] += cr; r["cc"] += cc
                r["cc5"] += cc5; r["cc1"] += cc1
                r["reason"] += reason
                r["asst"] += 1
                # count tool_use blocks
                tools = 0
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            _tool(agg, _buckets(dt)[0], blk.get("name", "tool"))
                            tools += 1
                r["tools"] += tools
                _bump_time(agg, dt, inp + out + cr + cc, 1)
                date0 = _buckets(dt)[0]
                tok = inp + out + cr + cc
                # Which Skill was driving this request, if any. Claude Code stamps
                # attributionSkill on the records a skill produced.
                sk = o.get("attributionSkill")
                if sk:
                    k = f"{date0}\t{sk}"
                    e = agg["skills"].setdefault(k, {"tok": 0, "asst": 0,
                                                     "in": 0, "out": 0, "cr": 0, "cc": 0})
                    e["tok"] += tok; e["asst"] += 1
                    e["in"] += inp; e["out"] += out; e["cr"] += cr; e["cc"] += cc
                # How big the context was for THIS request: everything that had to be
                # sent, cached or not. Long conversations cost more even when cached.
                ctx = inp + cr + cc
                b = ("0-50k" if ctx < 50_000 else "50-150k" if ctx < 150_000
                     else "150-400k" if ctx < 400_000 else "400k+")
                ck = f"{date0}\t{b}"
                ce = agg["ctx"].setdefault(ck, {"tok": 0, "n": 0})
                ce["tok"] += tok; ce["n"] += 1
                T = agg["totals"]
                T["in"] += inp; T["out"] += out; T["cr"] += cr; T["cc"] += cc
                T["reason"] += reason
                T["cc5"] += cc5; T["cc1"] += cc1
                T["asst"] += 1
                if side:                      # spawned subagent, not the main loop
                    T["side"] += inp + out + cr + cc
                model_tokens[model] = model_tokens.get(model, 0) + inp + out
        elif t == "user" and msg:
            # only count genuine user turns (not tool_result echoes)
            content = msg.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if not is_tool_result and dt:
                r = _rec(agg, _buckets(dt)[0], "(user)")
                r["user"] += 1
                agg["totals"]["user"] += 1
                # Normally a sidechain turn is a subagent talking inside a parent
                # session and must not retitle it. But a subagents/agent-*.jsonl file
                # is nothing BUT sidechain, so its first prompt is the task it was
                # given — without this the row has no title at all.
                if not side or agg.get("subagent"):
                    _set_title(agg, _first_text(content), "prompt")

    agg["project"] = project
    agg["editor"] = "Claude Code (CLI)"
    if model_tokens:
        agg["state"]["dom_model"] = max(model_tokens, key=model_tokens.get)


# ===========================================================================
# CODEX CLI
# ===========================================================================
# substrings that mark the giant lines we can skip without json.loads
_CODEX_SKIP = ('"function_call_output"', '"custom_tool_call_output"', '"type": "reasoning"')


def parse_codex(agg, lines):
    cur_model = agg["state"].get("cur_model")
    project = agg["project"]
    for line in lines:
        if not line.strip():
            continue
        # cheap skip for the multi-MB tool-output / reasoning lines
        if any(s in line for s in _CODEX_SKIP):
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = o.get("type")
        pl = o.get("payload") or {}
        pt = pl.get("type")
        dt = _from_iso(o.get("timestamp", "")) if o.get("timestamp") else None

        if t == "session_meta":
            cwd = pl.get("cwd")
            if cwd:
                project = _leaf(cwd) or cwd
            if pl.get("originator"):
                agg["entry"] = pl["originator"]
            if pl.get("cli_version"):
                agg["cliver"] = pl["cli_version"]
            # A spawned subagent's OWN rollout file self-identifies — no cross-file
            # correlation needed, unlike Claude Code's isSidechain records which live
            # inside the parent's log.
            # full thread id — the filename is truncated to 8 chars in the session
            # dict, but the editor-state lookup needs the whole UUID
            agg["_session_id"] = pl.get("id") or pl.get("session_id") or agg.get("_session_id")
            if pl.get("thread_source") == "subagent":
                agg["subagent"] = True
                # Stashed, not titled yet: many subagent transcripts carry no
                # UserMessage of their own (the task was handed to them at spawn
                # time, not as an in-band turn), so this is a last-resort label —
                # applied in _finalize_session only if nothing better ever showed up.
                agg["_agent_path"] = pl.get("agent_path")
            g = pl.get("git")
            if isinstance(g, dict) and g.get("branch"):
                agg["branch"] = g["branch"]
        elif t == "turn_context":
            m = pl.get("model")
            if m:
                cur_model = normalize_codex(m)
            cwd = pl.get("cwd")
            if cwd:
                project = _leaf(cwd) or cwd
        elif t == "event_msg":
            if pt == "token_count":
                info = pl.get("info") or {}
                last = info.get("last_token_usage") or {}
                inp = int(last.get("input_tokens", 0) or 0)
                cached = int(last.get("cached_input_tokens", 0) or 0)
                out = int(last.get("output_tokens", 0) or 0)
                reason = int(last.get("reasoning_output_tokens", 0) or 0)
                model = cur_model or "Unknown"
                if dt and (inp or out):
                    r = _rec(agg, _buckets(dt)[0], model)
                    # store non-cached input in "in", cached in "cr"
                    r["in"] += max(0, inp - cached)
                    r["cr"] += cached
                    r["out"] += out
                    r["reason"] += reason
                    _bump_time(agg, dt, inp + out, 0)
                    # Codex reports the FULL context it sent as input_tokens (cached
                    # or not), which is exactly the per-request context size — bucket
                    # it the same way as Claude Code so the finding is cross-tool.
                    date0 = _buckets(dt)[0]
                    b = ("0-50k" if inp < 50_000 else "50-150k" if inp < 150_000
                         else "150-400k" if inp < 400_000 else "400k+")
                    ce = agg["ctx"].setdefault(f"{date0}\t{b}", {"tok": 0, "n": 0})
                    ce["tok"] += inp + out; ce["n"] += 1
                    T = agg["totals"]
                    T["in"] += max(0, inp - cached); T["cr"] += cached
                    T["out"] += out; T["reason"] += reason
            elif pt == "agent_message":
                model = cur_model or "Unknown"
                if dt:
                    _rec(agg, _buckets(dt)[0], model)["asst"] += 1
                    agg["totals"]["asst"] += 1
            elif pt == "user_message":
                if dt:
                    _rec(agg, _buckets(dt)[0], "(user)")["user"] += 1
                    agg["totals"]["user"] += 1
                    _set_title(agg, pl.get("message") or _first_text(pl.get("content")), "prompt")
            elif pt == "item_completed":
                # Recent Codex CLI builds (0.151.x alpha) stopped emitting the flat
                # agent_message/user_message payloads above. Every turn's user text,
                # assistant text, tool activity and reasoning now arrives as ONE
                # item_completed event wrapping an `item` whose OWN `type` names the
                # real kind (UserMessage, AgentMessage, Reasoning, CommandExecution,
                # FileChange, SubAgentActivity, ...). Without this branch every session
                # written by the new format silently has 0 prompts and 0 messages —
                # tokens/cost/tools stay correct because those come from the separate
                # token_count and response_item events, which this format still emits
                # unchanged.
                item = pl.get("item") or {}
                it = item.get("type")
                if it == "UserMessage" and dt:
                    _rec(agg, _buckets(dt)[0], "(user)")["user"] += 1
                    agg["totals"]["user"] += 1
                    _set_title(agg, _first_text(item.get("content")), "prompt")
                elif it == "AgentMessage" and dt:
                    model = cur_model or "Unknown"
                    _rec(agg, _buckets(dt)[0], model)["asst"] += 1
                    agg["totals"]["asst"] += 1
                elif it == "SubAgentActivity" and item.get("kind") == "started":
                    # Counted on the PARENT's own file — a spawn marker, not a token
                    # or message event — so this session's own "delegated to a
                    # subagent" count is known without reading any other file.
                    agg["_spawned"] = agg.get("_spawned", 0) + 1
            elif pt in ("web_search_call", "web_search_end"):
                if pt == "web_search_call" and dt:
                    _tool(agg, _buckets(dt)[0], "web_search")
                    _rec(agg, _buckets(dt)[0], cur_model or "Unknown")["tools"] += 1
        elif t == "response_item" and dt:
            if pt in ("function_call", "custom_tool_call"):
                date = _buckets(dt)[0]
                nm = pl.get("name") or ("function" if pt == "function_call" else "custom_tool")
                # Codex does not prefix MCP tools the way Claude Code does — it keeps
                # the bare tool name and puts the server in `namespace` ("mcp__azure").
                # Normalise to mcp__<server>__<tool> so MCP usage is attributable and
                # comparable across tools; without this an MCP tool is indistinguishable
                # from a built-in and every Codex server looks unused.
                ns = pl.get("namespace") or ""
                if ns.startswith("mcp__"):
                    nm = f"{ns}__{nm}"
                _tool(agg, date, nm)
                _rec(agg, date, cur_model or "Unknown")["tools"] += 1

    agg["state"]["cur_model"] = cur_model
    agg["project"] = project
    agg["editor"] = "Codex (CLI)"


# ===========================================================================
# GITHUB COPILOT (VS Code / Insiders / Cursor chat sessions)
# ===========================================================================
def _copilot_text_len(response):
    """Approximate the assistant response length in characters."""
    total = 0
    if isinstance(response, list):
        for part in response:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                v = part.get("value")
                if isinstance(v, str):
                    total += len(v)
                elif isinstance(v, dict) and isinstance(v.get("value"), str):
                    total += len(v["value"])
                c = part.get("content")
                if isinstance(c, dict) and isinstance(c.get("value"), str):
                    total += len(c["value"])
    return total


def _copilot_apply_request(agg, r, fallback_ts=None):
    """Aggregate a single Copilot request record. Shared by the .json (whole-object)
    and .jsonl (mutation-log) parsers. Returns the normalized model name."""
    ts = r.get("timestamp") or fallback_ts
    dt = _from_ms(ts) if ts else None
    if not dt:
        return None
    details = (r.get("result") or {}).get("details") or ""
    model = normalize_copilot(r.get("modelId"), details)
    # premium multiplier from "... • 1x"
    mult = 0.0
    mm = re.search(r"([0-9.]+)x", details)
    if mm:
        try:
            mult = float(mm.group(1))
        except Exception:
            mult = 0.0
    # estimated tokens from text length (Copilot logs no real token counts)
    msg = r.get("message") or {}
    in_chars = len(msg.get("text", "")) if isinstance(msg, dict) else 0
    if isinstance(msg, dict) and msg.get("text"):
        _set_title(agg, msg["text"], "prompt")
    out_chars = _copilot_text_len(r.get("response"))
    est_in = in_chars // 4
    est_out = out_chars // 4
    meta = (r.get("result") or {}).get("metadata") or {}
    ntools = 0
    date = _buckets(dt)[0]
    for round_ in (meta.get("toolCallRounds") or []):
        for tc in (round_.get("toolCalls") or []):
            _tool(agg, date, tc.get("name") or "tool")
            ntools += 1
    rec = _rec(agg, date, model)
    rec["in"] += est_in; rec["out"] += est_out
    rec["req"] += 1; rec["user"] += 1; rec["asst"] += 1
    rec["prem"] += mult; rec["tools"] += ntools
    _bump_time(agg, dt, est_in + est_out, 1)
    T = agg["totals"]
    T["in"] += est_in; T["out"] += est_out
    T["req"] += 1; T["user"] += 1; T["asst"] += 1
    T["prem"] += mult
    return model


def parse_copilot(agg, obj):
    """Older Copilot format: one whole-JSON document (rewritten each save)."""
    mr = {}
    for r in (obj.get("requests") or []):
        m = _copilot_apply_request(agg, r, obj.get("lastMessageDate"))
        if m:
            mr[m] = mr.get(m, 0) + 1
    if mr:
        agg["state"]["dom_model"] = max(mr, key=mr.get)


def _find_copilot_requests(o):
    """Yield request dicts (have modelId + requestId) anywhere in a parsed structure,
    without descending into a matched request."""
    stack = [o]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "modelId" in cur and "requestId" in cur:
                yield cur
            else:
                stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def parse_copilot_jsonl(agg, lines):
    """Newer Copilot format: append-only mutation log. Requests appear as nested
    objects carrying modelId+requestId; dedupe by requestId across incremental reads."""
    seen = set(agg["state"].get("seen_req") or [])
    for line in lines:
        if '"modelId"' not in line:   # skip the huge streaming-content lines cheaply
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        for r in _find_copilot_requests(o):
            rid = r.get("requestId")
            if not rid or rid in seen:
                continue
            seen.add(rid)
            _copilot_apply_request(agg, r)
    agg["state"]["seen_req"] = list(seen)


# ===========================================================================
# GEMINI CLI  (prompts only — no model, no tokens, no responses persisted)
# ===========================================================================
def parse_gemini(agg, path):
    agg["source"] = "gemini"
    agg["editor"] = "Gemini CLI"
    parts = path.split(os.sep)
    try:
        agg["project"] = parts[parts.index("tmp") + 1]
    except (ValueError, IndexError):
        agg["project"] = "gemini"
    # the log is a series of MongoDB-style {"$set":{"messages":[...]}} snapshots;
    # the snapshot with the most messages is the full conversation
    best = None
    try:
        with open(path) as f:
            for line in f:
                if '"messages"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                st = o.get("$set")
                if isinstance(st, dict) and isinstance(st.get("messages"), list):
                    if best is None or len(st["messages"]) > len(best):
                        best = st["messages"]
    except Exception:
        return
    if not best:
        return
    model = "Gemini"
    for m in best:
        if not isinstance(m, dict) or m.get("type") != "user":
            continue
        # skip the auto-injected session context preamble
        txt = ""
        c = m.get("content")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            txt = c[0].get("text", "") or ""
        if txt.startswith("<session_context>"):
            continue
        dt = _from_iso(m.get("timestamp", "")) if m.get("timestamp") else None
        if not dt:
            continue
        date = _buckets(dt)[0]
        r = _rec(agg, date, model)
        r["asst"] += 1   # 1 prompt ≈ 1 model turn (Gemini logs no responses/tokens)
        r["user"] += 1
        _bump_time(agg, dt, 0, 1)
        agg["totals"]["asst"] += 1
        agg["totals"]["user"] += 1


# ===========================================================================
# CURSOR  (native AI chat bubbles in a SQLite key-value store)
# ===========================================================================
# Home-relative source dirs on any OS: /Users/x/... (macOS), /home/x/... (Linux)
# and C:\Users\x\... (Windows). Either separator, either drive — a Cursor DB can
# be read on a different machine than the one that wrote it.
_CURSOR_PATH_RE = re.compile(
    r'(?:/Users/|/home/|[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2})[^/\\"]+[\\/]{1,2}'
    r'(?:Documents[\\/]{1,2}GitHub|Documents|Desktop|repos?|code|dev|projects|src|work)'
    r'[\\/]{1,2}([^/\\"\s]+)', re.I)


def _normalize_cursor_model(raw):
    """Cursor names models its own way ("claude-4.6-opus-high-thinking",
    "gpt-5-nano", "composer-1"). Map them onto the display names every other
    source already uses so one model reads the same everywhere."""
    if not raw:
        return "Cursor (default)"
    first = str(raw).split(",")[0].strip()          # multi-model sessions list them
    if not first or first == "default":
        return "Cursor (default)"
    base = re.sub(r"-(?:high-)?(?:thinking|reasoning|max|fast)$", "", first.lower())
    m = re.match(r"claude-(\d+(?:\.\d+)?)-(opus|sonnet|haiku)$", base)
    if m:
        return f"Claude {m.group(2).capitalize()} {m.group(1)}"
    if base.startswith("composer"):
        n = base.split("-", 1)[1] if "-" in base else ""
        return ("Cursor Composer " + n).strip()
    canon = _canonicalize(base)
    return canon or first


def _cursor_ai_lines(con):
    """Cursor's own AI-code accounting: lines it suggested vs. lines you kept,
    per day, split by tab-completion and composer. No other tool records this."""
    out = {}
    try:
        rows = con.execute(
            "SELECT key, value FROM ItemTable WHERE key LIKE 'aiCodeTracking.dailyStats%'"
        ).fetchall()
    except Exception:
        return out
    for _k, v in rows:
        try:
            o = json.loads(v)
        except Exception:
            continue
        d = o.get("date")
        if not d:
            continue
        out[d] = {
            "tab_suggested": int(o.get("tabSuggestedLines", 0) or 0),
            "tab_accepted": int(o.get("tabAcceptedLines", 0) or 0),
            "composer_suggested": int(o.get("composerSuggestedLines", 0) or 0),
            "composer_accepted": int(o.get("composerAcceptedLines", 0) or 0),
        }
    return out


def _open_ro_sqlite(db_path):
    """Open a tool's live SQLite store read-only, correctly, on any filesystem.

    Neither flag alone is safe:
      * `mode=ro` alone reads the -wal, so a tool that is RUNNING has its recent
        activity visible — but SQLite must create a -shm alongside the db, so it
        raises "attempt to write a readonly database" on read-only media.
      * `immutable=1` needs no -shm and works there — but it tells SQLite the file
        can never change, so it ignores the -wal entirely. While the tool is
        running its newest sessions are invisible, or the open fails outright.
    Prefer correctness, fall back to availability.
    """
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&busy_timeout=5000", uri=True)
        # connect() is lazy — on read-only media it succeeds and only fails when a
        # query forces the -shm to be created. Probe before trusting it.
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.Error:
        try:
            con.close()
        except Exception:
            pass
        return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def parse_cursor(agg, db_path):
    import sqlite3
    agg["source"] = "cursor"
    agg["editor"] = "Cursor"
    agg["project"] = "Cursor"
    try:
        con = _open_ro_sqlite(db_path)
    except Exception:
        return
    cur = con.cursor()

    def _loads(v):
        try:
            return json.loads(v) if v else None
        except Exception:
            return None

    # ---- session metadata -------------------------------------------------
    # composerData holds most sessions; newer Cursor builds migrate the header
    # into its own table, so read both and let composerData win on conflicts.
    comp = {}
    try:
        for cid, created, updated, archived, val in cur.execute(
                "SELECT composerId, createdAt, lastUpdatedAt, isArchived, value "
                "FROM composerHeaders").fetchall():
            o = _loads(val) or {}
            comp[cid] = {"created": created, "updated": updated or created,
                         "name": o.get("name") or o.get("subtitle"),
                         "model": None, "maxmode": False,
                         "mode": o.get("unifiedMode"),
                         "added": int(o.get("totalLinesAdded") or 0),
                         "removed": int(o.get("totalLinesRemoved") or 0),
                         "archived": bool(archived or o.get("isArchived")),
                         "subs": int(o.get("numSubComposers") or 0)}
    except Exception:
        pass                                    # table absent on older builds
    try:
        rows = cur.execute("SELECT value FROM cursorDiskKV "
                           "WHERE key LIKE 'composerData:%'").fetchall()
    except Exception:
        con.close()
        return
    for (v,) in rows:
        o = _loads(v)
        if not o:
            continue
        cid = o.get("composerId")
        created = o.get("createdAt") or o.get("lastUpdatedAt")
        if not cid or not created:
            continue
        mc = o.get("modelConfig") if isinstance(o.get("modelConfig"), dict) else {}
        c = comp.setdefault(cid, {})
        c.update({
            "created": created,
            "updated": o.get("lastUpdatedAt") or created,
            "name": o.get("name") or c.get("name"),
            "model": mc.get("modelName"),
            "maxmode": bool(mc.get("maxMode")),
            "mode": o.get("unifiedMode") or c.get("mode"),
            "added": int(o.get("totalLinesAdded") or 0),
            "removed": int(o.get("totalLinesRemoved") or 0),
            "archived": bool(o.get("isArchived")),
            "subs": len(o.get("subComposerIds") or []) or c.get("subs", 0),
        })

    sess = {}
    path_re = _CURSOR_PATH_RE

    def _top(d):
        return max(d, key=d.get) if d else None

    # ---- messages ---------------------------------------------------------
    try:
        rows = cur.execute("SELECT key, value FROM cursorDiskKV "
                           "WHERE key LIKE 'bubbleId:%'")
        for k, v in rows:
            kp = k.split(":")
            cid = kp[1] if len(kp) >= 3 else None
            c = comp.get(cid)
            if not c:
                continue
            o = _loads(v)
            if not o:
                continue
            typ = o.get("type")            # 1 = user, 2 = AI
            tc = o.get("tokenCount") or {}
            it = int(tc.get("inputTokens", 0) or 0)
            ot = int(tc.get("outputTokens", 0) or 0)
            # Messages carry their own ISO timestamp; only fall back to the
            # session's creation time when one is genuinely missing, otherwise
            # a months-long session lands entirely on the day it started.
            dt = _from_iso(o["createdAt"]) if isinstance(o.get("createdAt"), str) else None
            if dt is None:
                dt = _from_ms(c["created"])
            if not dt:
                continue
            date = _buckets(dt)[0]
            model = _normalize_cursor_model(c.get("model"))
            r = _rec(agg, date, model)
            r["in"] += it
            r["out"] += ot
            T = agg["totals"]
            T["in"] += it
            T["out"] += ot
            s = sess.setdefault(cid, {"in": 0, "out": 0, "asst": 0, "user": 0, "tools": 0,
                                      "think": 0, "proj": {}, "days": {},
                                      "start": dt.isoformat(), "end": dt.isoformat()})
            dd = s["days"].setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                             "asst": 0, "user": 0, "tools": 0})
            dd["in"] += it; dd["out"] += ot
            iso = dt.isoformat()
            if iso < s["start"]:
                s["start"] = iso
            if iso > s["end"]:
                s["end"] = iso
            s["in"] += it
            s["out"] += ot
            if typ == 2:
                r["asst"] += 1; T["asst"] += 1; s["asst"] += 1; dd["asst"] += 1
            elif typ == 1:
                r["user"] += 1; T["user"] += 1; s["user"] += 1; dd["user"] += 1
            s["think"] += int(o.get("thinkingDurationMs") or 0)
            # tool calls — Cursor persists each as toolFormerData on the bubble
            tf = o.get("toolFormerData")
            if isinstance(tf, dict):
                name = tf.get("name") or tf.get("tool")
                if name:
                    _tool(agg, date, name)
                    r["tools"] += 1
                    s["tools"] += 1; dd["tools"] += 1
            # infer project from paths in the bubble's context fields
            for fld in ("attachedFolders", "attachedFoldersNew", "relevantFiles",
                        "recentlyViewedFiles", "gitDiffs", "context"):
                fv = o.get(fld)
                if fv:
                    for m in path_re.finditer(json.dumps(fv)):
                        s["proj"][m.group(1)] = s["proj"].get(m.group(1), 0) + 1
            _bump_time(agg, dt, it + ot, 1 if typ == 2 else 0)
        agg["state"]["ai_lines"] = _cursor_ai_lines(con)
    finally:
        con.close()

    # dominant inferred project across sessions drives the Projects-chart bucket
    tally = {}
    for s in sess.values():
        p = _top(s["proj"])
        if p:
            tally[p] = tally.get(p, 0) + 1
    agg["project"] = _top(tally) or "Cursor"

    out = []
    for cid, s in sess.items():
        c = comp.get(cid, {})
        mode = {1: "chat", 2: "agent"}.get(c.get("mode"), c.get("mode"))
        out.append({
            "id": (cid or "")[:8], "source": "cursor", "ide": IDE_FIXED["cursor"], "editor": "Cursor",
            "title": c.get("name"),   # Cursor stores the current name
            "project": _top(s["proj"]) or "Cursor",
            "model": _normalize_cursor_model(c.get("model")),
            "start": s["start"], "end": s["end"],
            "in": s["in"], "out": s["out"], "cr": 0, "cc": 0, "cc5": 0, "cc1": 0,
            "asst": s["asst"], "user": s["user"], "req": 0, "prem": 0.0,
            "tools": s["tools"], "side": 0, "days": s["days"],
            "mode": ("max " + mode) if (mode and c.get("maxmode")) else mode,
            "lines_add": c.get("added", 0), "lines_del": c.get("removed", 0),
            "think_ms": s["think"], "subagents": c.get("subs", 0),
            "archived_session": bool(c.get("archived")),
        })
    agg["sessions"] = out


# ===========================================================================
# OPENCODE  (SST) — per-message JSON files; real token counts + model + provider
# ===========================================================================
def _from_ms_or_s(v):
    """Epoch ms (opencode's time.created) or seconds (Hermes' REAL timestamps) — accept both."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v > 1e12:        # milliseconds
        return _from_ms(v)
    if v > 1e9:         # seconds
        return _from_ms(v * 1000)
    return None


def _normalize_opencode(model_id, provider):
    """opencode modelID is the provider's raw id. Cloudflare Workers AI aliases use
    the @cf/<publisher>/<model> namespace; opencode's own providers expose a mix
    of bare ids. Map both onto readable display names."""
    if not model_id:
        return "Unknown"
    # Cloudflare IDs look like @cf/moonshotai/kimi-k2.7-code — the meaningful
    # part is the last path segment, which is also what bare opencode ids use.
    base = model_id.split("/")[-1]
    low = base.lower()

    # Kimi (Moonshot) — @cf/moonshotai/kimi-k2.7-code or kimi-k3
    m = re.match(r"^kimi-k([0-9.]+)(?:-code)?$", low)
    if m:
        suffix = " Code" if "code" in low else ""
        return f"Kimi K{m.group(1)}{suffix}"

    # Google Gemma via Cloudflare — @cf/google/gemma-4-26b-a4b-it
    m = re.match(r"^gemma-(\d+(?:\.\d+)?)-(\d+b)(?:-[a-z0-9\-]+)*$", low)
    if m:
        return f"Gemma {m.group(1)} {m.group(2).upper()}"

    # DeepSeek — deepseek-v4-flash[-free]
    m = re.match(r"^deepseek-v?([0-9.]+)-flash(-free)?$", low)
    if m:
        free = " Free" if m.group(2) else ""
        return f"DeepSeek V{m.group(1)} Flash{free}"

    # Qwen — qwen3.7-max
    m = re.match(r"^qwen(\d+(?:\.\d+)?)(?:-(max|plus|coder))?$", low)
    if m:
        suffix = " " + m.group(2).capitalize() if m.group(2) else ""
        return f"Qwen {m.group(1)}{suffix}"

    # Zhipu GLM via Cloudflare — @cf/zai-org/glm-5.2
    m = re.match(r"^glm-(\d+(?:\.\d+)?)$", low)
    if m:
        return f"GLM {m.group(1)}"

    # opencode-hosted aliases without a recognised family
    if low == "big-pickle":
        return "Big Pickle"
    if low == "north-mini-code" or low == "north-mini-code-free":
        return "North Mini Code"
    if low.startswith("x-preview"):
        return "X Preview"

    # Claude/GPT/o-series handled by the shared canonicalizer
    name = _canonicalize(model_id)
    if name and name != model_id:
        return name

    return base


def _opencode_project(session_dir, sid):
    """Best-effort project label from the session file's directory/title; opencode
    keeps it under storage/session/*/{sid}.json a couple levels up."""
    try:
        storage = os.path.dirname(os.path.dirname(session_dir))   # .../storage
        for sf in glob.glob(os.path.join(storage, "session", "*", sid + ".json")):
            o = json.load(open(sf))
            d = o.get("directory") or o.get("cwd") or ""
            if d:
                return _leaf(d) or d
            if o.get("title"):
                return str(o["title"])[:40]
            break
    except Exception:
        pass
    return "opencode"


def parse_opencode(agg, session_dir, msg_files):
    agg["source"] = "opencode"
    agg["editor"] = "opencode"
    sid = os.path.basename(session_dir)
    agg["project"] = _opencode_project(session_dir, sid)
    model_tokens = {}
    for mf in msg_files:
        try:
            o = json.load(open(mf))
        except Exception:
            continue
        if o.get("role") != "assistant":
            continue
        t = o.get("tokens") or {}
        cache = t.get("cache") or {}
        inp = int(t.get("input", 0) or 0)
        out = int(t.get("output", 0) or 0)
        reason = int(t.get("reasoning", 0) or 0)
        cr = int(cache.get("read", 0) or 0)
        cw = int(cache.get("write", 0) or 0)
        model = _normalize_opencode(o.get("modelID"), o.get("providerID"))
        dt = _from_ms_or_s((o.get("time") or {}).get("created"))
        if not dt or not (inp or out or cr or cw):
            continue
        date = _buckets(dt)[0]
        # tool invocations ride along as typed parts on the message; the shape has
        # moved between opencode versions, so accept either spelling defensively
        ntools = 0
        for part in (o.get("parts") or o.get("content") or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("tool", "tool-invocation", "tool_use", "tool-call"):
                name = (part.get("tool") or part.get("name")
                        or (part.get("toolInvocation") or {}).get("toolName") or "tool")
                _tool(agg, date, str(name))
                ntools += 1
        r = _rec(agg, date, model)
        r["tools"] += ntools
        r["in"] += inp; r["out"] += out; r["reason"] += reason
        r["cr"] += cr; r["cc"] += cw; r["cc5"] += cw  # untiered cache write -> 5m rate
        r["asst"] += 1
        _bump_time(agg, dt, inp + out + cr + cw, 1)
        T = agg["totals"]
        T["in"] += inp; T["out"] += out; T["reason"] += reason
        T["cr"] += cr; T["cc"] += cw; T["cc5"] += cw; T["asst"] += 1
        model_tokens[model] = model_tokens.get(model, 0) + inp + out
    if model_tokens:
        agg["state"]["dom_model"] = max(model_tokens, key=model_tokens.get)


# ===========================================================================
# OPENCODE SQLite database (current opencode stores messages in opencode.db)
# ===========================================================================
def parse_opencode_db(agg, db_path):
    """Parse the current opencode SQLite database. One DB holds many sessions,
    messages and parts; we aggregate tokens per day/model and return a session
    list similar to Cursor's composer breakdown."""
    import sqlite3
    agg["source"] = "opencode"
    agg["editor"] = "opencode"

    def _loads(v):
        try:
            return json.loads(v) if v else None
        except Exception:
            return None

    sessions_meta = {}
    sess = {}          # sid -> running totals
    sess_days = {}     # sid -> {date -> per-day token/cost breakdown}
    model_tokens = {}
    try:
        con = _open_ro_sqlite(db_path)
    except Exception:
        return
    cur = con.cursor()
    try:
        # ---- session metadata ---------------------------------------------
        for row in cur.execute(
            "SELECT id, directory, title, agent, model, version, "
            "parent_id, time_created, time_updated FROM session"):
            sid, directory, title, agent, model_json, version, parent_id, tc, tu = row
            model = _loads(model_json) or {}
            sessions_meta[sid] = {
                "directory": directory or "",
                "title": title or "",
                "agent": agent or "",
                "model_id": model.get("id"),
                "provider_id": model.get("providerID"),
                "variant": model.get("variant"),
                "version": version or "",
                "parent_id": parent_id,
                "start": tc,
                "end": tu,
            }

        # dominant project for aggregate-level project bucket
        proj_tally = {}
        for s in sessions_meta.values():
            p = _leaf(s["directory"]) or s["directory"] or "opencode"
            proj_tally[p] = proj_tally.get(p, 0) + 1
        agg["project"] = max(proj_tally, key=proj_tally.get) if proj_tally else "opencode"

        # opencode's DB contains many sessions across many projects. Records are
        # keyed by date+model, so a single aggregate would otherwise force every
        # token into the single dominant project. Track a parallel set of records
        # keyed by project so the dashboard can attribute each day's tokens to the
        # project that actually produced them.
        agg["project_records"] = {}
        def _rec_proj(project, date, model):
            key = f"{project}\t{date}\t{model}"
            r = agg["project_records"].get(key)
            if r is None:
                r = {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0,
                     "reason": 0, "asst": 0, "user": 0, "req": 0, "prem": 0.0,
                     "tools": 0, "cost": 0.0}
                agg["project_records"][key] = r
            return r

        # ---- tool parts (batch) --------------------------------------------
        # tool rows in part have {"type":"tool", "tool":"<name>", ...}
        tools_by_msg = {}
        for mid, data in cur.execute(
                "SELECT message_id, data FROM part WHERE json_extract(data,'$.type')='tool'"):
            o = _loads(data)
            if not o:
                continue
            name = o.get("tool") or o.get("name") or "tool"
            tools_by_msg.setdefault(mid, []).append(str(name))

        # ---- messages -------------------------------------------------------
        for row in cur.execute(
                "SELECT id, session_id, time_created, data FROM message"):
            mid, sid, tc, data = row
            o = _loads(data)
            if not o:
                continue
            meta = sessions_meta.get(sid)
            role = o.get("role")
            ts = (o.get("time") or {}).get("created") or tc
            dt = _from_ms_or_s(ts)
            if not dt:
                continue
            date = _buckets(dt)[0]

            project = (_leaf(meta["directory"]) if meta and meta.get("directory") else None) or "opencode"
            if role == "user":
                r = _rec(agg, date, "(user)")
                r["user"] += 1
                rp = _rec_proj(project, date, "(user)")
                rp["user"] += 1
                agg["totals"]["user"] += 1
                _bump_time(agg, dt, 0, 0)
                # weak title from first user prompt of the session
                if meta and not meta.get("_weak_title_set"):
                    text = ""
                    for part in (o.get("parts") or o.get("content") or []):
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            break
                    if text:
                        # kind="prompt" is the lowest title rank, so a real
                        # session title from the DB still wins over this.
                        _set_title(agg, text)
                        meta["_weak_title_set"] = True
                s = sess.setdefault(sid, _blank_opencode_session())
                sd = sess_days.setdefault(sid, {})
                day = sd.setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                              "asst": 0, "user": 0, "tools": 0,
                                              "prem": 0.0, "cost": 0.0})
                if "end" not in s or dt.isoformat() > s["end"]:
                    s["end"] = dt.isoformat()
                s["user"] += 1
                day["user"] += 1
                continue

            if role != "assistant":
                continue

            t = o.get("tokens") or {}
            cache = t.get("cache") or {}
            inp = int(t.get("input", 0) or 0)
            out = int(t.get("output", 0) or 0)
            reason = int(t.get("reasoning", 0) or 0)
            cr = int(cache.get("read", 0) or 0)
            cw = int(cache.get("write", 0) or 0)
            cost = float(o.get("cost") or 0.0)
            if not (inp or out or cr or cw):
                # token-less assistant bookkeeping rows (flow control, etc.)
                continue
            provider = o.get("providerID") or (meta.get("provider_id") if meta else None)
            model = _normalize_opencode(o.get("modelID"), provider)

            model_tokens[model] = model_tokens.get(model, 0) + inp + out + cr + cw

            # tool calls from preloaded part rows
            ntools = 0
            for name in tools_by_msg.get(mid, []):
                _tool(agg, date, name)
                ntools += 1

            r = _rec(agg, date, model)
            r["tools"] += ntools
            r["in"] += inp; r["out"] += out; r["reason"] += reason
            r["cr"] += cr; r["cc"] += cw; r["cc5"] += cw
            r["asst"] += 1
            r["cost"] += cost

            rp = _rec_proj(project, date, model)
            rp["tools"] += ntools
            rp["in"] += inp; rp["out"] += out; rp["reason"] += reason
            rp["cr"] += cr; rp["cc"] += cw; rp["cc5"] += cw
            rp["asst"] += 1
            rp["cost"] += cost

            _bump_time(agg, dt, inp + out + cr + cw, 1)
            T = agg["totals"]
            T["in"] += inp; T["out"] += out; T["reason"] += reason
            T["cr"] += cr; T["cc"] += cw; T["cc5"] += cw; T["asst"] += 1
            T["cost"] = T.get("cost", 0.0) + cost
            if meta and meta.get("parent_id"):
                T["side"] = T.get("side", 0) + inp + out + cr + cw

            s = sess.setdefault(sid, _blank_opencode_session())
            sd = sess_days.setdefault(sid, {})
            day = sd.setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                        "asst": 0, "user": 0, "tools": 0,
                                        "prem": 0.0, "cost": 0.0})
            iso = dt.isoformat()
            if "start" not in s or iso < s["start"]:
                s["start"] = iso
            if "end" not in s or iso > s["end"]:
                s["end"] = iso
            s["in"] += inp; s["out"] += out; s["reason"] += reason
            s["cr"] += cr; s["cc"] += cw; s["cc5"] += cw
            s["asst"] += 1
            s["tools"] += ntools
            s["cost"] += cost
            day["in"] += inp; day["out"] += out; day["cr"] += cr; day["cc"] += cw
            day["asst"] += 1
            day["tools"] += ntools
            day["cost"] += cost

        # ---- build per-session summaries ----------------------------------
        out = []
        for sid, s in sess.items():
            meta = sessions_meta.get(sid, {})
            model_id = meta.get("model_id")
            provider_id = meta.get("provider_id")
            model = _normalize_opencode(model_id, provider_id)
            directory = meta.get("directory") or "opencode"
            title = meta.get("title") or agg.get("title")
            agent = meta.get("agent")
            start_dt = _from_ms_or_s(meta.get("start"))
            end_dt = _from_ms_or_s(meta.get("end"))
            out.append({
                "id": sid or "",
                "source": "opencode", "ide": _ide_of("opencode", agg),
                "editor": "opencode",
                "title": title,
                "project": _leaf(directory) or directory or "opencode",
                "model": model,
                "start": start_dt.isoformat() if start_dt else s.get("start"),
                "end": end_dt.isoformat() if end_dt else s.get("end"),
                "in": s["in"], "out": s["out"], "cr": s["cr"], "cc": s["cc"],
                "cc5": s["cc5"], "cc1": s["cc1"],
                "asst": s["asst"], "user": s["user"], "req": 0,
                "prem": 0.0, "tools": s["tools"], "side": 0,
                "cost": s["cost"],
                "days": sess_days.get(sid, {}),
                "cliver": meta.get("version"),
                "mode": agent,
            })
        agg["sessions"] = sorted(out, key=lambda x: x.get("end") or "", reverse=True)
        if model_tokens:
            agg["state"]["dom_model"] = max(model_tokens, key=model_tokens.get)
    finally:
        con.close()


def _blank_opencode_session():
    return {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
            "asst": 0, "user": 0, "tools": 0, "cost": 0.0}


# ===========================================================================
# HERMES AGENT (NousResearch) — one SQLite state.db, many sessions.
# `sessions` carries per-session metadata, `session_model_usage` carries a real
# input/output/cache/reasoning token breakdown per (session, model) pair (a
# session can switch models mid-way, same as Codex), and `messages` carries a
# per-turn timestamp/role/tool_calls used only for day-bucketed counts.
# ===========================================================================
def _normalize_hermes(model_id):
    """Hermes routes through many providers verbatim (Anthropic/OpenAI/OpenRouter/
    Nous's own Hermes models); _canonicalize already maps the Claude/GPT/o-series
    spellings, same as opencode's normalizer."""
    if not model_id:
        return "Unknown"
    name = _canonicalize(model_id)
    if name and name != model_id:
        return name
    return model_id.split("/")[-1]


def parse_hermes(agg, db_path):
    import sqlite3
    agg["source"] = "hermes"
    agg["editor"] = "Hermes Agent"
    try:
        con = _open_ro_sqlite(db_path)
    except Exception:
        return
    cur = con.cursor()

    sess = {}
    try:
        rows = cur.execute(
            "SELECT id, cwd, git_branch, title, model, started_at, ended_at, "
            "archived, source FROM sessions").fetchall()
    except Exception:
        con.close()
        return
    for sid, cwd, branch, title, model, started, ended, archived, chan in rows:
        sess[sid] = {
            "cwd": cwd, "branch": branch, "title": title, "model": model,
            "started": started, "ended": ended or started,
            "archived": bool(archived), "channel": chan,
            "days": {}, "asst": 0, "user": 0, "tools": 0, "req": 0,
            "in": 0, "out": 0, "cr": 0, "cc": 0, "reason": 0,
        }

    try:
        urows = cur.execute(
            "SELECT session_id, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, reasoning_tokens, api_call_count, first_seen, last_seen "
            "FROM session_model_usage").fetchall()
    except Exception:
        urows = []
    model_tokens = {}   # session_id -> {display_model: tokens}
    for sid, model, it, ot, cr, cw, reason, calls, first, last in urows:
        s = sess.get(sid)
        if s is None:
            continue
        dt = _from_ms_or_s(first or last or s["started"])
        if not dt:
            continue
        date = _buckets(dt)[0]
        disp = _normalize_hermes(model)
        it, ot, cr, cw = int(it or 0), int(ot or 0), int(cr or 0), int(cw or 0)
        reason, calls = int(reason or 0), int(calls or 0)
        r = _rec(agg, date, disp)
        r["in"] += it; r["out"] += ot; r["cr"] += cr; r["cc"] += cw
        r["cc5"] += cw          # untiered cache write -> 5m rate, like opencode
        r["reason"] += reason; r["req"] += calls
        T = agg["totals"]
        T["in"] += it; T["out"] += ot; T["cr"] += cr; T["cc"] += cw
        T["cc5"] += cw; T["reason"] += reason; T["req"] += calls
        _bump_time(agg, dt, it + ot + cr + cw, 0)
        s["in"] += it; s["out"] += ot; s["cr"] += cr; s["cc"] += cw
        s["reason"] += reason; s["req"] += calls
        dd = s["days"].setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                          "asst": 0, "user": 0, "tools": 0})
        dd["in"] += it; dd["out"] += ot; dd["cr"] += cr; dd["cc"] += cw
        model_tokens.setdefault(sid, {})
        model_tokens[sid][disp] = model_tokens[sid].get(disp, 0) + it + ot

    dom_model = {sid: max(mt, key=mt.get) for sid, mt in model_tokens.items() if mt}

    try:
        mrows = cur.execute(
            "SELECT session_id, role, timestamp, tool_calls FROM messages").fetchall()
    except Exception:
        mrows = []
    for sid, role, ts, tool_calls_json in mrows:
        s = sess.get(sid)
        if s is None or not ts:
            continue
        dt = _from_ms_or_s(ts)
        if not dt:
            continue
        date = _buckets(dt)[0]
        model = dom_model.get(sid) or _normalize_hermes(s.get("model")) or "Unknown"
        r = _rec(agg, date, model)
        dd = s["days"].setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                          "asst": 0, "user": 0, "tools": 0})
        if role == "user":
            r["user"] += 1; agg["totals"]["user"] += 1; s["user"] += 1; dd["user"] += 1
        elif role == "assistant":
            r["asst"] += 1; agg["totals"]["asst"] += 1; s["asst"] += 1; dd["asst"] += 1
            _bump_time(agg, dt, 0, 1)
            if tool_calls_json:
                try:
                    calls = json.loads(tool_calls_json) or []
                except Exception:
                    calls = []
                for tc in calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = fn.get("name") or tc.get("name") or "tool"
                    _tool(agg, date, name)
                    r["tools"] += 1; s["tools"] += 1; dd["tools"] += 1
    con.close()

    tally = {}
    for s in sess.values():
        p = _leaf(s["cwd"]) or s["cwd"] or "(unknown)"
        w = s["in"] + s["out"] + s["asst"] + s["user"]
        if w:
            tally[p] = tally.get(p, 0) + w
    agg["project"] = (max(tally, key=tally.get) if tally else "Hermes Agent")

    out = []
    for sid, s in sess.items():
        mt = model_tokens.get(sid, {})
        ranked = [m for m, _ in sorted(mt.items(), key=lambda kv: -kv[1])]
        dom = dom_model.get(sid) or _normalize_hermes(s["model"])
        models = [dom] + [m for m in ranked if m != dom]
        start = _from_ms_or_s(s["started"])
        end = _from_ms_or_s(s["ended"]) or start
        out.append({
            "id": (sid or "")[:8], "source": "hermes", "ide": IDE_FIXED["hermes"], "editor": "Hermes Agent",
            "title": s.get("title"), "project": _leaf(s["cwd"]) or s["cwd"] or "(unknown)",
            "model": dom, "models": models[:6], "nmodels": len(mt),
            "branch": s.get("branch"), "entry": s.get("channel"),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "in": s["in"], "out": s["out"], "cr": s["cr"], "cc": s["cc"],
            "cc5": s["cc"], "cc1": 0,
            "asst": s["asst"], "user": s["user"], "req": s["req"], "prem": 0.0,
            "tools": s["tools"], "side": 0, "days": s["days"],
            "archived_session": bool(s.get("archived")),
        })
    agg["sessions"] = out


# ===========================================================================
# Incremental file scanning
# ===========================================================================
def _read_new_bytes(path, offset):
    """Return (list_of_complete_lines, new_offset). Reads only bytes past offset
    and stops at the last newline so a partial trailing line isn't parsed."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    if not data:
        return [], offset
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return [], offset  # no complete line yet
    chunk = data[:last_nl + 1]
    new_offset = offset + len(chunk)
    text = chunk.decode("utf-8", errors="replace")
    return text.splitlines(), new_offset


def _uri_to_path(uri):
    """file:///Users/me/My%20Repo -> /Users/me/My Repo (also passes plain paths)."""
    from urllib.parse import unquote, urlparse
    if not uri:
        return ""
    if uri.startswith("file://"):
        pr = urlparse(uri)
        return unquote(pr.path)
    return unquote(uri)


def _copilot_project_map():
    """Map workspaceStorage hash -> friendly workspace folder name."""
    m = {}
    for root in COPILOT_ROOTS:
        for wj in glob.glob(os.path.join(root, "User", "workspaceStorage", "*", "workspace.json")):
            try:
                o = json.load(open(wj))
            except Exception:
                continue
            path = _uri_to_path(o.get("folder") or o.get("workspace") or "")
            if not path:
                continue
            name = _leaf(path)
            # A MULTI-ROOT window stores a pointer to a workspace *file*, whose
            # basename is literally "workspace.json" — read it for the real roots.
            if name in ("workspace.json",) or path.endswith(".code-workspace"):
                try:
                    wo = json.load(open(path))
                    roots = [_leaf(_uri_to_path(f.get("path") or f.get("uri") or ""))
                             for f in (wo.get("folders") or [])]
                    roots = [r for r in roots if r]
                    name = " + ".join(roots[:2]) + ("…" if len(roots) > 2 else "")
                except Exception:
                    name = ""
                if not name:
                    name = "(multi-root workspace)"
            if name:
                m[os.path.dirname(wj)] = name
    return m


def discover():
    """Return list of (source, path, editor_hint)."""
    out = []
    for g in CLAUDE_GLOBS:
        for p in glob.glob(g, recursive=True):
            out.append(("claude", p, None))
    for g in CODEX_GLOBS:
        for p in glob.glob(g, recursive=True):
            out.append(("codex", p, None))
    for g in CLAUDE_DESKTOP_GLOBS:
        for p in glob.glob(g, recursive=True):
            out.append(("claude-desktop", p, "Claude Desktop (agent mode)"))
    # NOTE: Gemini CLI is intentionally NOT discovered — its local chat logs persist
    # only a re-written session-context preamble (no prompts/responses/tokens/model),
    # so there is no usable usage data to report. See GEMINI_GLOBS / parse_gemini.
    for db in CURSOR_DBS:
        if os.path.exists(db):
            out.append(("cursor", db, "Cursor"))
    if os.path.exists(HERMES_DB):
        out.append(("hermes", HERMES_DB, "Hermes Agent"))
    # opencode: current versions keep everything in opencode.db; older versions
    # used storage/message/<session>/msg_*.json. Discover both so upgrades and
    # legacy installs are both covered.
    seen_db_inodes = set()
    for db in OPENCODE_DBS:
        if not os.path.exists(db):
            continue
        try:
            st = os.stat(db)
            inode = (st.st_dev, st.st_ino)
        except Exception:
            continue
        if inode in seen_db_inodes:
            continue
        seen_db_inodes.add(inode)
        out.append(("opencode", db, "opencode"))
    for root in OPENCODE_ROOTS:
        for d in glob.glob(os.path.join(root, "storage", "message", "*")):
            if os.path.isdir(d):
                out.append(("opencode", d, "opencode"))
    for root in COPILOT_ROOTS:
        editor = EDITOR_LABEL.get(os.path.basename(root), os.path.basename(root))
        # older VS Code stores chat sessions as *.json (whole-object); newer builds use
        # *.jsonl (an append-only mutation log) — parse both.
        pats = [
            os.path.join(root, "User", "workspaceStorage", "*", "chatSessions", "*.json"),
            os.path.join(root, "User", "workspaceStorage", "*", "chatSessions", "*.jsonl"),
            os.path.join(root, "User", "globalStorage", "emptyWindowChatSessions", "*.json"),
            os.path.join(root, "User", "globalStorage", "emptyWindowChatSessions", "*.jsonl"),
            # Newer builds nest each session in its own directory as
            # chatSessions/<uuid>/index.json instead of a flat file. Seen on Puku;
            # the flat globs above miss it entirely because it is one level deeper.
            os.path.join(root, "User", "workspaceStorage", "*", "chatSessions", "*", "index.json"),
            os.path.join(root, "User", "globalStorage", "emptyWindowChatSessions", "*", "index.json"),
        ]
        for pat in pats:
            for p in glob.glob(pat):
                out.append(("copilot", p, editor))
    return out


def update_file(agg, source, path, editor_hint, proj_map):
    """(Re)parse a single file incrementally. Returns the updated agg."""
    try:
        st = os.stat(path)
    except OSError:
        return agg
    size, mtime = st.st_size, st.st_mtime

    if source == "copilot" and path.endswith(".json"):
        # older whole-object format, rewritten on each save → full reparse on change
        if agg and agg.get("size") == size and agg.get("mtime") == mtime:
            return agg
        fresh = _blank_agg(source, path)
        # project name from workspaceStorage hash
        ws_dir = os.path.dirname(os.path.dirname(path))  # .../<hash>
        fresh["project"] = proj_map.get(ws_dir, "(no folder)")
        fresh["editor"] = editor_hint
        try:
            obj = json.load(open(path))
            parse_copilot(fresh, obj)
        except Exception:
            pass
        fresh["size"], fresh["mtime"] = size, mtime
        _finalize_session(fresh, source, path)
        return fresh

    if source in ("gemini", "cursor", "hermes"):
        # rewritten stores → full reparse when the file/DB changes
        if agg and agg.get("size") == size and agg.get("mtime") == mtime:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        try:
            if source == "gemini":
                parse_gemini(fresh, path)
                _finalize_session(fresh, source, path)
            elif source == "cursor":
                parse_cursor(fresh, path)   # sets its own per-composer sessions
            else:
                parse_hermes(fresh, path)   # sets its own per-session sessions
        except Exception as e:
            sys.stderr.write(f"[{source}] {path}: {type(e).__name__}: {e}\n")
        fresh["size"], fresh["mtime"] = size, mtime
        return fresh

    if source == "opencode":
        if path.endswith(".db"):
            # current opencode: a single SQLite DB for all sessions. Re-parse when
            # the DB or its WAL changes; parse_opencode_db builds its own session
            # list so don't run the generic _finalize_session over the top.
            wal = path + "-wal"
            wal_size = os.path.getsize(wal) if os.path.exists(wal) else 0
            wal_mtime = os.path.getmtime(wal) if os.path.exists(wal) else 0
            sig = [size, mtime, wal_size, wal_mtime]
            if agg and agg.get("_sig") == sig:
                return agg
            fresh = _blank_agg(source, path)
            fresh["editor"] = editor_hint
            fresh["size"] = size + wal_size
            try:
                parse_opencode_db(fresh, path)
            except Exception as e:
                # Keep a bad DB from taking the whole refresh down, but never
                # fail silently: a swallowed error here looks identical to
                # "you don't use opencode".
                sys.stderr.write(f"[opencode] {path}: {type(e).__name__}: {e}\n")
            fresh["_sig"] = sig
            fresh["mtime"] = max(mtime, wal_mtime)
            return fresh

        # older opencode: a directory of msg_*.json for one session
        msgs = sorted(glob.glob(os.path.join(path, "msg_*.json")))
        try:
            sig_mtime = max((os.path.getmtime(m) for m in msgs), default=0.0)
        except OSError:
            sig_mtime = mtime
        sig = [len(msgs), sig_mtime]
        if agg and agg.get("_sig") == sig:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        try:                                  # size first: _finalize_session reads it
            fresh["size"] = sum(os.path.getsize(m) for m in msgs)
        except OSError:
            fresh["size"] = 0
        try:
            parse_opencode(fresh, path, msgs)
            _finalize_session(fresh, source, path)
        except Exception:
            pass
        fresh["_sig"] = sig
        fresh["mtime"] = sig_mtime
        return fresh

    # jsonl (claude / codex) — incremental append
    if not agg or agg.get("size", 0) > size:
        agg = _blank_agg(source, path)  # new or truncated → reparse fully
    offset = agg["offset"]
    if size == offset and agg.get("mtime") == mtime:
        return agg  # unchanged
    lines, new_offset = _read_new_bytes(path, offset)
    if lines:
        if source in ("claude", "claude-desktop"):
            agg["subagent"] = _is_subagent_path(path)
            parse_claude(agg, lines)
        elif source == "copilot":
            parse_copilot_jsonl(agg, lines)
        else:
            parse_codex(agg, lines)
    if source == "copilot":
        ws_dir = os.path.dirname(os.path.dirname(path))   # .../<workspace hash>
        agg["project"] = proj_map.get(ws_dir, "(no folder)")
    if source == "claude-desktop":
        # nested transcripts live under a VM scratch cwd; give them a clean label
        agg["project"] = "Claude Desktop"
        agg["editor"] = "Claude Desktop (agent mode)"
    agg["offset"] = new_offset
    agg["size"], agg["mtime"] = size, mtime
    agg["editor"] = agg.get("editor") or editor_hint
    _finalize_session(agg, source, path)
    return agg


# ---------------------------------------------------------------------------
# Which IDE / surface a session actually ran in.
#
# Every source records this differently and none of them agree on spelling:
#   Copilot  — implicit in WHICH editor's storage the file came from ("Code")
#   Claude   — an `entrypoint` field on each record ("claude-vscode", "cli")
#   Codex    — an `originator` in session_meta ("codex_vscode", "Codex Desktop")
#   Cursor / Claude Desktop / opencode / Hermes — one surface by definition
# so they are collapsed to a shared vocabulary before anything groups by them.
#
# CAVEAT worth keeping in mind: "claude-vscode" and "codex_vscode" name the VS Code
# *extension*, not the fork hosting it. Run either inside Cursor, Windsurf or
# Antigravity and the log still says vscode — the host is genuinely not recorded, so
# those land under "VS Code" and cannot be split further from the log alone.
# ---------------------------------------------------------------------------
# Codex's own log says only "vscode" — it never records WHICH VS Code variant
# hosted it, so Insiders work is indistinguishable from stable from the rollout
# alone. The editor itself does know: its globalStorage/state.vscdb carries the
# Codex extension's per-thread UI state under "openai.chatgpt", and a thread id
# appearing there means that editor opened it. Build {thread id -> editor} from
# every known editor and use it to recover the variant.
#
# A thread present in TWO editors' state is genuinely ambiguous (it was opened in
# both) and is deliberately left unattributed rather than guessed.
_VSCODE_THREADS = {"at": 0.0, "map": {}}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _vscode_thread_owners():
    """{codex thread id: editor label} for threads owned by exactly one editor."""
    if time.time() - _VSCODE_THREADS["at"] < 60:
        return _VSCODE_THREADS["map"]
    per = {}
    for root in COPILOT_ROOTS:
        db = os.path.join(root, "User", "globalStorage", "state.vscdb")
        if not os.path.exists(db):
            continue
        label = EDITOR_LABEL.get(os.path.basename(root), os.path.basename(root))
        try:
            con = _open_ro_sqlite(db)
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key='openai.chatgpt'").fetchone()
            con.close()
        except Exception as e:
            sys.stderr.write(f"[ide] {db}: {type(e).__name__}: {e}\n")
            continue
        if not row or not row[0]:
            continue
        v = row[0]
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        for tid in set(_UUID_RE.findall(v)):
            per.setdefault(tid, set()).add(label)
    out = {t: next(iter(owners)) for t, owners in per.items() if len(owners) == 1}
    _VSCODE_THREADS.update(at=time.time(), map=out)
    return out


IDE_FROM_ENTRY = {
    "claude-vscode": "VS Code",
    "codex_vscode": "VS Code",
    "vscode": "VS Code",
    "cli": "CLI",
    "codex_cli": "CLI",
    "codex_exec": "CLI",
    "codex desktop": "Codex Desktop",
    "codex_desktop": "Codex Desktop",
    "codex_work_desktop": "Codex Desktop",
    "local-agent": "Claude Desktop",
}

# Sources that only ever run in one place — no per-record signal needed.
IDE_FIXED = {
    "cursor": "Cursor",
    "claude-desktop": "Claude Desktop",
    "opencode": "CLI",
    "hermes": "CLI",
    "gemini": "CLI",
}


def _ide_of(source, agg, editor_hint=None):
    """Normalised IDE/surface for one aggregate. Never guesses: an unrecognised
    entrypoint is passed through as-is rather than being forced into a bucket, so a
    new host shows up as itself instead of silently becoming 'VS Code'."""
    if source in IDE_FIXED:
        # opencode is a terminal tool; when the dashboard itself is running inside
        # the VS Code integrated terminal, opencode sessions almost certainly are
        # too. Use that as a best-effort host signal since opencode's logs do not
        # record which terminal launched them.
        if source == "opencode" and os.environ.get("TERM_PROGRAM") == "vscode":
            return "VS Code"
        return IDE_FIXED[source]
    if source == "copilot":
        # the editor whose storage this file came out of
        return agg.get("editor") or editor_hint or "VS Code"
    entry = (agg.get("entry") or "").strip()
    ide = IDE_FROM_ENTRY.get(entry.lower(), entry) if entry else "CLI"
    # Recover the VS Code variant for Codex, which only ever logs "vscode".
    if source == "codex" and ide == "VS Code":
        owner = _vscode_thread_owners().get(agg.get("_session_id") or "")
        if owner:
            return owner
    return ide


def _finalize_session(agg, source, path):
    """Roll the file's totals into a single session summary."""
    T = agg["totals"]
    # Last-resort title for a Codex subagent that never had a UserMessage of its
    # own — its task arrived at spawn time, not as an in-band turn. Only applied
    # if nothing stronger (a real prompt) ever set agg["title"].
    if agg.get("subagent") and not agg.get("title") and agg.get("_agent_path"):
        # agent_path is namespaced ("/root/science_audit"); every sample seen has
        # a constant, uninformative leading segment, so use the leaf only.
        leaf = agg["_agent_path"].strip("/").split("/")[-1].replace("_", " ").replace("-", " ")
        if leaf:
            agg["title"] = (leaf[:1].upper() + leaf[1:])[:90]
    # rank the models used in this session by tokens (a session — especially a
    # resumed Codex rollout — can switch models mid-way)
    mt = {}
    for k, r in agg["records"].items():
        mdl = k.split("\t", 1)[1]
        if mdl == "(user)":
            continue
        mt[mdl] = mt.get(mdl, 0) + r["in"] + r["out"]
    ranked = [m for m, _ in sorted(mt.items(), key=lambda kv: -kv[1])]
    dom = agg["state"].get("dom_model") or (ranked[0] if ranked else "Unknown")
    models = [dom] + [m for m in ranked if m != dom]   # dominant first
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"rollout-\d{4}-\d{2}-\d{2}T[\d-]+-([0-9a-f]{8})", base)
    if m:
        base = m.group(1)
    agg["sessions"] = [{
        "id": base[:8],
        "source": source,
        "subagent": bool(agg.get("subagent")),
        "editor": agg.get("editor"),
        "project": agg.get("project"),
        "model": dom,
        "models": models[:6],          # for the "+N" indicator / tooltip
        "nmodels": len(mt),
        "title": agg.get("title"),
        "branch": agg.get("branch"),
        "entry": agg.get("entry"),
        "cliver": agg.get("cliver"),
        "start": agg.get("first_ts"),
        "end": agg.get("last_ts"),
        "in": T["in"], "out": T["out"], "cr": T["cr"], "cc": T["cc"],
        "cc5": T["cc5"], "cc1": T["cc1"],
        "asst": T["asst"], "user": T["user"], "req": T["req"],
        "prem": T["prem"], "tools": T["tools"], "side": T.get("side", 0),
        # Reuses Cursor's "subagents" (a count) — here, how many SubAgentActivity
        # "started" markers this Codex session's own file recorded. 0 for anyone
        # who didn't spawn any, so it renders identically to Cursor's absence case.
        "subagents": agg.get("_spawned", 0),
        "ide": _ide_of(source, agg),
        "bytes": agg.get("size", 0), "archived": bool(agg.get("archived")),
    }]
