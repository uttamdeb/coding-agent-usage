"""
parser.py — Incremental usage-log parser for local AI coding tools.

Sources: Claude Code, Claude Desktop (agent mode), Codex CLI, GitHub Copilot
(VS Code / Insiders / Cursor), Cursor native AI, and opencode. Each tool stores
interaction logs locally; this module discovers those files, parses them
incrementally (append-only .jsonl read from a byte offset; rewritten stores
re-read on change), and produces per-file aggregates the server merges into one
dataset. Everything is keyed off the user's own home dir — nothing is hardcoded
to a machine or account, so it works on any Mac/Linux install of the same tools.

No third-party dependencies — stdlib only.
"""
import os, json, glob, re, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")

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
CLAUDE_DESKTOP_GLOBS = [
    os.path.join(HOME, "Library", "Application Support", "Claude",
                 "local-agent-mode-sessions", "**", ".claude", "projects", "**", "*.jsonl"),
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


COPILOT_ROOTS = _editor_roots(["Code", "Code - Insiders", "VSCodium", "Cursor"])
CURSOR_DBS = [os.path.join(r, "User", "globalStorage", "state.vscdb")
              for r in _editor_roots(["Cursor"])]

# opencode (SST) — per-message JSON at storage/message/{sessionID}/msg_*.json.
# cost is stored as 0, so we compute it from tokens like every other source.
def _opencode_roots():
    roots, seen = [], set()
    for r in [os.environ.get("OPENCODE_DATA_DIR"),
              os.path.join(os.environ.get("XDG_DATA_HOME") or
                           os.path.join(HOME, ".local", "share"), "opencode"),
              os.path.join(HOME, ".opencode")]:
        if r and r not in seen:
            seen.add(r); roots.append(r)
    return roots


OPENCODE_ROOTS = _opencode_roots()

EDITOR_LABEL = {
    "Code": "VS Code",
    "Code - Insiders": "VS Code Insiders",
    "VSCodium": "VSCodium",
    "Cursor": "Cursor",
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
    # Anthropic Opus 4.5+ — $5/$25 (current pricing)
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
    if "gemini" in d:
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
        "tools": {},            # tool name -> count
        "hourly": {},           # "hour" -> {tokens, msgs}
        "dow": {},              # "dow" -> {tokens, msgs}
        "project": "(unknown)",
        "editor": None,
        "totals": {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0,
                   "reason": 0, "asst": 0, "user": 0, "req": 0, "prem": 0.0,
                   "tools": 0},
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
              "asst": 0, "user": 0, "req": 0, "prem": 0.0, "tools": 0}
        agg["records"][key] = r
    return r


def _bump_time(agg, dt, tokens, msgs):
    date, hour, dow = _buckets(dt)
    h = agg["hourly"].setdefault(str(hour), {"tokens": 0, "msgs": 0})
    h["tokens"] += tokens
    h["msgs"] += msgs
    d = agg["dow"].setdefault(str(dow), {"tokens": 0, "msgs": 0})
    d["tokens"] += tokens
    d["msgs"] += msgs
    iso = dt.isoformat()
    if agg["first_ts"] is None or iso < agg["first_ts"]:
        agg["first_ts"] = iso
    if agg["last_ts"] is None or iso > agg["last_ts"]:
        agg["last_ts"] = iso


# ===========================================================================
# CLAUDE CODE
# ===========================================================================
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
            project = os.path.basename(cwd.rstrip("/")) or cwd
        msg = o.get("message") if isinstance(o.get("message"), dict) else None
        dt = _from_iso(o.get("timestamp", "")) if o.get("timestamp") else None

        if t == "assistant" and msg:
            model = normalize_claude(msg.get("model"))
            u = msg.get("usage") or {}
            inp = int(u.get("input_tokens", 0) or 0)
            out = int(u.get("output_tokens", 0) or 0)
            cr = int(u.get("cache_read_input_tokens", 0) or 0)
            cc = int(u.get("cache_creation_input_tokens", 0) or 0)
            ccd = u.get("cache_creation") or {}
            cc5 = int(ccd.get("ephemeral_5m_input_tokens", 0) or 0)
            cc1 = int(ccd.get("ephemeral_1h_input_tokens", 0) or 0)
            if cc and not (cc5 or cc1):   # older logs without the tier split
                cc5 = cc                  # assume 5-min when untiered
            if dt:
                r = _rec(agg, _buckets(dt)[0], model)
                r["in"] += inp; r["out"] += out; r["cr"] += cr; r["cc"] += cc
                r["cc5"] += cc5; r["cc1"] += cc1
                r["asst"] += 1
                # count tool_use blocks
                tools = 0
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            name = blk.get("name", "tool")
                            agg["tools"][name] = agg["tools"].get(name, 0) + 1
                            tools += 1
                r["tools"] += tools
                agg["totals"]["tools"] += tools
                _bump_time(agg, dt, inp + out + cr + cc, 1)
                T = agg["totals"]
                T["in"] += inp; T["out"] += out; T["cr"] += cr; T["cc"] += cc
                T["cc5"] += cc5; T["cc1"] += cc1
                T["asst"] += 1
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
                project = os.path.basename(cwd.rstrip("/")) or cwd
        elif t == "turn_context":
            m = pl.get("model")
            if m:
                cur_model = normalize_codex(m)
            cwd = pl.get("cwd")
            if cwd:
                project = os.path.basename(cwd.rstrip("/")) or cwd
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
            elif pt in ("web_search_call", "web_search_end"):
                if pt == "web_search_call":
                    agg["tools"]["web_search"] = agg["tools"].get("web_search", 0) + 1
                    agg["totals"]["tools"] += 1
        elif t == "response_item":
            if pt == "function_call":
                name = pl.get("name") or "function"
                agg["tools"][name] = agg["tools"].get(name, 0) + 1
                agg["totals"]["tools"] += 1
            elif pt == "custom_tool_call":
                name = pl.get("name") or "custom_tool"
                agg["tools"][name] = agg["tools"].get(name, 0) + 1
                agg["totals"]["tools"] += 1

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
    out_chars = _copilot_text_len(r.get("response"))
    est_in = in_chars // 4
    est_out = out_chars // 4
    meta = (r.get("result") or {}).get("metadata") or {}
    ntools = 0
    for round_ in (meta.get("toolCallRounds") or []):
        for tc in (round_.get("toolCalls") or []):
            name = tc.get("name") or "tool"
            agg["tools"][name] = agg["tools"].get(name, 0) + 1
            ntools += 1
    date = _buckets(dt)[0]
    rec = _rec(agg, date, model)
    rec["in"] += est_in; rec["out"] += est_out
    rec["req"] += 1; rec["user"] += 1; rec["asst"] += 1
    rec["prem"] += mult; rec["tools"] += ntools
    _bump_time(agg, dt, est_in + est_out, 1)
    T = agg["totals"]
    T["in"] += est_in; T["out"] += est_out
    T["req"] += 1; T["user"] += 1; T["asst"] += 1
    T["prem"] += mult; T["tools"] += ntools
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
def parse_cursor(agg, db_path):
    import sqlite3
    agg["source"] = "cursor"
    agg["editor"] = "Cursor"
    agg["project"] = "Cursor"
    model = "Cursor"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except Exception:
        return
    cur = con.cursor()
    # composerId -> session timestamps (bubbles carry no timestamp of their own)
    comp = {}
    try:
        for (v,) in cur.execute("SELECT value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"):
            try:
                o = json.loads(v)
            except Exception:
                continue
            cid = o.get("composerId")
            created = o.get("createdAt") or o.get("lastUpdatedAt")
            if cid and created:
                comp[cid] = {"created": created, "updated": o.get("lastUpdatedAt") or created}
    except Exception:
        con.close()
        return
    sess = {}
    # infer the repo/project name from any absolute path in a bubble
    path_re = re.compile(r'/Users/[^/"\\]+/(?:Documents/GitHub|Documents|Desktop|'
                         r'repos?|code|dev|projects|src|work)/([^/"\\\s]+)')

    def _top(d):
        return max(d, key=d.get) if d else None

    try:
        rows = cur.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'")
        for k, v in rows:
            kp = k.split(":")
            cid = kp[1] if len(kp) >= 3 else None
            c = comp.get(cid)
            if not c:
                continue
            try:
                o = json.loads(v)
            except Exception:
                continue
            typ = o.get("type")            # 1 = user, 2 = AI
            tc = o.get("tokenCount") or {}
            it = int(tc.get("inputTokens", 0) or 0)
            ot = int(tc.get("outputTokens", 0) or 0)
            dt = _from_ms(c["created"])
            if not dt:
                continue
            date = _buckets(dt)[0]
            r = _rec(agg, date, model)
            r["in"] += it
            r["out"] += ot
            T = agg["totals"]
            T["in"] += it
            T["out"] += ot
            if typ == 2:
                r["asst"] += 1
                T["asst"] += 1
            elif typ == 1:
                r["user"] += 1
                T["user"] += 1
            s = sess.setdefault(cid, {"in": 0, "out": 0, "asst": 0, "user": 0, "tools": 0,
                                      "proj": {},
                                      "start": dt.isoformat(),
                                      "end": (_from_ms(c["updated"]) or dt).isoformat()})
            s["in"] += it
            s["out"] += ot
            if typ == 2:
                s["asst"] += 1
            elif typ == 1:
                s["user"] += 1
            # tool calls — Cursor persists each as toolFormerData on the bubble
            tf = o.get("toolFormerData")
            if isinstance(tf, dict):
                name = tf.get("name") or tf.get("tool")
                if name:
                    agg["tools"][name] = agg["tools"].get(name, 0) + 1
                    r["tools"] += 1
                    T["tools"] += 1
                    s["tools"] += 1
            # infer project from paths in the bubble's context fields
            for fld in ("attachedFolders", "attachedFoldersNew", "relevantFiles",
                        "recentlyViewedFiles", "gitDiffs", "context"):
                fv = o.get(fld)
                if fv:
                    for m in path_re.finditer(json.dumps(fv)):
                        p = m.group(1)
                        s["proj"][p] = s["proj"].get(p, 0) + 1
            _bump_time(agg, dt, it + ot, 1 if typ == 2 else 0)
    finally:
        con.close()

    # dominant inferred project across sessions drives the Projects-chart bucket
    tally = {}
    for s in sess.values():
        p = _top(s["proj"])
        if p:
            tally[p] = tally.get(p, 0) + 1
    agg["project"] = _top(tally) or "Cursor"

    agg["sessions"] = [{
        "id": (cid or "")[:8], "source": "cursor", "editor": "Cursor",
        "project": _top(s["proj"]) or "Cursor",
        "model": model, "start": s["start"], "end": s["end"],
        "in": s["in"], "out": s["out"], "cr": 0, "cc": 0, "cc5": 0, "cc1": 0,
        "asst": s["asst"], "user": s["user"], "req": 0, "prem": 0.0, "tools": s["tools"],
    } for cid, s in sess.items()]


# ===========================================================================
# OPENCODE  (SST) — per-message JSON files; real token counts + model + provider
# ===========================================================================
def _from_ms_or_s(v):
    """opencode time.created is epoch ms; tolerate seconds just in case."""
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
    """opencode modelID is the provider's raw id (claude-sonnet-4-5, gpt-5.6-sol,
    gemini-2.5-pro, ...). _canonicalize already handles Claude/GPT/o-series; other
    providers pass through with light cleanup."""
    if not model_id:
        return "Unknown"
    name = _canonicalize(model_id)
    if name and name != model_id:
        return name
    # prettify a bare provider id we didn't canonicalize (e.g. gemini-2.5-pro)
    base = model_id.split("/")[-1]
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
                return os.path.basename(d.rstrip("/")) or d
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
        r = _rec(agg, date, model)
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


def _copilot_project_map():
    """Map workspaceStorage hash -> friendly workspace folder name."""
    m = {}
    for root in COPILOT_ROOTS:
        for wj in glob.glob(os.path.join(root, "User", "workspaceStorage", "*", "workspace.json")):
            try:
                o = json.load(open(wj))
                folder = o.get("folder") or o.get("workspace") or ""
                name = os.path.basename(folder.rstrip("/")) if folder else None
                if name:
                    m[os.path.dirname(wj)] = name
            except Exception:
                pass
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
    # opencode: one session = one directory of msg_*.json files
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

    if source in ("gemini", "cursor"):
        # rewritten stores → full reparse when the file/DB changes
        if agg and agg.get("size") == size and agg.get("mtime") == mtime:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        try:
            if source == "gemini":
                parse_gemini(fresh, path)
                _finalize_session(fresh, source, path)
            else:
                parse_cursor(fresh, path)   # sets its own per-composer sessions
        except Exception:
            pass
        fresh["size"], fresh["mtime"] = size, mtime
        return fresh

    if source == "opencode":
        # a directory of msg_*.json for one session; re-parse when it changes
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
        try:
            parse_opencode(fresh, path, msgs)
            _finalize_session(fresh, source, path)
        except Exception:
            pass
        fresh["_sig"] = sig
        fresh["size"], fresh["mtime"] = 0, sig_mtime
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


def _finalize_session(agg, source, path):
    """Roll the file's totals into a single session summary."""
    T = agg["totals"]
    dom = agg["state"].get("dom_model")
    if not dom:
        # pick model with most tokens from records
        best, bestv = None, -1
        for k, r in agg["records"].items():
            mdl = k.split("\t", 1)[1]
            if mdl in ("(user)",):
                continue
            v = r["in"] + r["out"]
            if v > bestv:
                bestv, best = v, mdl
        dom = best or "Unknown"
    agg["sessions"] = [{
        "id": os.path.splitext(os.path.basename(path))[0][:8],
        "source": source,
        "editor": agg.get("editor"),
        "project": agg.get("project"),
        "model": dom,
        "start": agg.get("first_ts"),
        "end": agg.get("last_ts"),
        "in": T["in"], "out": T["out"], "cr": T["cr"], "cc": T["cc"],
        "cc5": T["cc5"], "cc1": T["cc1"],
        "asst": T["asst"], "user": T["user"], "req": T["req"],
        "prem": T["prem"], "tools": T["tools"],
    }]
