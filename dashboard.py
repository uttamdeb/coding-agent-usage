#!/usr/bin/env python3
"""
dashboard.py — Live local usage analytics for your AI coding tools.

Covers Claude Code, Claude Desktop, Codex, GitHub Copilot, Cursor and opencode.
Parses your local interaction logs (no data leaves the machine), aggregates usage by
day / model / tool / project / hour, and serves an interactive dashboard.

    python3 dashboard.py            # serve at http://127.0.0.1:7878
    python3 dashboard.py --port 9000
    python3 dashboard.py --rebuild  # ignore cache, full re-parse

Stdlib only. First run parses everything (one large Codex log makes that take a
moment); results are cached, and subsequent refreshes are incremental & instant.
"""
import os, sys, json, time, threading, argparse, shutil, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import parser as P

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".usage_cache.json")
CACHE_VERSION = 22

# ---------------------------------------------------------------------------
# In-memory store of per-file aggregates, refreshed on a background interval.
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {"files": {}, "version": CACHE_VERSION}
_meta = {"last_refresh": 0.0, "last_duration": 0.0, "files": 0, "building": False}
_dirty = {"v": True}          # cache is only rewritten when a file actually changed


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return
    try:
        data = json.load(open(CACHE_PATH))
        if data.get("version") == CACHE_VERSION:
            _state["files"] = data.get("files", {})
    except Exception:
        pass


def save_cache():
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, CACHE_PATH)
    except Exception as e:
        sys.stderr.write(f"[cache] save failed: {e}\n")


def refresh(verbose=False):
    """Scan all sources and incrementally update changed files."""
    t0 = time.time()
    _meta["building"] = True
    proj_map = P._copilot_project_map()
    found = P.discover()
    files = _state["files"]
    seen = set()
    for i, (source, path, editor) in enumerate(found):
        seen.add(path)
        prev = files.get(path)
        try:
            updated = P.update_file(prev, source, path, editor, proj_map)
            if updated is not prev:
                _dirty["v"] = True
            files[path] = updated
        except Exception as e:
            if verbose:
                sys.stderr.write(f"[parse] {path}: {e}\n")
        if verbose and (i % 5 == 0 or i == len(found) - 1):
            sys.stderr.write(f"\r[scan] {i+1}/{len(found)} files...")
            sys.stderr.flush()
    # Durable ledger: do NOT drop files that disappeared from disk. Claude Code &
    # Codex prune old transcripts (default 30-day retention), but once we've parsed
    # a session its aggregates stay counted here forever. Mark them archived so the
    # UI can distinguish them; their cost/tokens continue to contribute to totals.
    gone = [p for p in files if p not in seen]
    for p in gone:
        if not files[p].get("archived"):
            _dirty["v"] = True
        files[p]["archived"] = True
    if verbose and gone:
        sys.stderr.write(f"\n[ledger] retaining {len(gone)} pruned-from-disk session(s)\n")
    if verbose:
        sys.stderr.write("\n")
    _meta.update(last_refresh=time.time(), last_duration=time.time() - t0,
                 files=len(files), building=False)
    # The cache is ~1MB; rewriting it every interval when nothing changed is a
    # pointless few GB of disk writes a day (and this machine is short on space).
    if _dirty["v"]:
        save_cache()
        _dirty["v"] = False


# ---------------------------------------------------------------------------
# Merge per-file aggregates → a single dataset payload for the frontend.
# ---------------------------------------------------------------------------
def _cost(source, model, inp, out, cr, cc5, cc1, cc_fallback=0, date=None):
    pin, pout, pcw5, pcw1, pcr = P.price_of(model)
    # Claude Sonnet 5 introductory pricing ($2/$10) through 2026-08-31
    if model == "Claude Sonnet 5" and date and date <= "2026-08-31":
        pin, pout, pcw5, pcw1, pcr = 2, 10, 2.5, 4, 0.20
    # if a record only has the untiered total (cc_fallback), bill it at the 5-min rate
    if cc_fallback and not (cc5 or cc1):
        cc5 = cc_fallback
    return (inp * pin + out * pout + cr * pcr
            + cc5 * pcw5 + cc1 * pcw1) / 1_000_000.0


def build_payload():
    records = {}      # (date, source, model, project) -> aggregates
    tools = {}        # (date, source, name) -> count
    projects = {}     # (project, source) -> {tokens, msgs, sessions, cost}
    hourly = {}       # (date, hour, source) -> {tokens, msgs}
    sessions = []
    model_meta = {}   # model -> vendor
    ai_lines = {}     # date -> Cursor's suggested/accepted line counts

    with _lock:
        files = list(_state["files"].values())

    for agg in files:
        source = agg["source"]
        project = agg.get("project") or "(unknown)"
        file_tokens = 0
        file_msgs = 0
        file_cost = 0.0
        for key, r in agg.get("records", {}).items():
            date, model = key.split("\t", 1)
            if model == "(user)":
                # only carries user-turn counts
                rk = (date, source, "(user)", project)
                slot = records.setdefault(rk, _zero())
                slot["user"] += r.get("user", 0)
                continue
            rk = (date, source, model, project)
            slot = records.setdefault(rk, _zero())
            for f in ("in", "out", "cr", "cc", "cc5", "cc1", "reason", "asst", "user", "req", "tools"):
                slot[f] += r.get(f, 0)
            slot["prem"] += r.get("prem", 0.0)
            c = _cost(source, model, r["in"], r["out"], r["cr"],
                      r.get("cc5", 0), r.get("cc1", 0), r.get("cc", 0), date)
            slot["cost"] += c
            model_meta[model] = P.vendor_of(model)
            file_tokens += r["in"] + r["out"] + r["cr"] + r["cc"]
            file_msgs += r.get("asst", 0)
            file_cost += c
        for tk, c in agg.get("tools", {}).items():
            date, _, name = tk.partition("\t")
            if not name:                      # pre-v16 cache shape — skip
                continue
            tools[(date, source, name)] = tools.get((date, source, name), 0) + c
        for hk, v in agg.get("hourly", {}).items():
            date, _, hour = hk.partition("\t")
            if not hour:                      # pre-v16 cache shape — skip
                continue
            slot = hourly.setdefault((date, int(hour), source), {"tokens": 0, "msgs": 0})
            slot["tokens"] += v["tokens"]; slot["msgs"] += v["msgs"]
        # project rollup
        pk = (project, source)
        pr = projects.setdefault(pk, {"tokens": 0, "msgs": 0, "sessions": 0, "cost": 0.0})
        pr["tokens"] += file_tokens; pr["msgs"] += file_msgs
        pr["sessions"] += 1; pr["cost"] += file_cost
        for day, v in (agg.get("state", {}).get("ai_lines") or {}).items():
            slot = ai_lines.setdefault(day, {"tab_suggested": 0, "tab_accepted": 0,
                                             "composer_suggested": 0, "composer_accepted": 0})
            for f in slot:
                slot[f] += v.get(f, 0)
        # sessions
        for s in agg.get("sessions", []):
            s2 = dict(s)
            # `archived` is stamped on the aggregate by refresh() after the parse,
            # so read it from the aggregate rather than the frozen session copy
            s2["archived"] = bool(agg.get("archived"))
            s2["cost"] = _cost(source, s["model"], s["in"], s["out"], s["cr"],
                               s.get("cc5", 0), s.get("cc1", 0), s.get("cc", 0),
                               (s.get("end") or s.get("start") or "")[:10])
            sessions.append(s2)

    rec_list = []
    for (date, source, model, project), v in records.items():
        rec_list.append({"date": date, "source": source, "model": model,
                         "project": project, **v})
    rec_list.sort(key=lambda x: (x["date"], x["source"]))

    # keep the payload bounded: the long tail of one-off tool names folds into
    # a single "(other)" row rather than shipping thousands of day rows
    name_tot = {}
    for (d, s, n), c in tools.items():
        name_tot[n] = name_tot.get(n, 0) + c
    keep = set(sorted(name_tot, key=lambda n: -name_tot[n])[:60])
    tl = {}
    for (d, s, n), c in tools.items():
        k = (d, s, n if n in keep else "(other)")
        tl[k] = tl.get(k, 0) + c
    tool_list = [{"date": d, "source": s, "name": n, "count": c}
                 for (d, s, n), c in tl.items()]
    tool_list.sort(key=lambda x: -x["count"])

    proj_list = [{"project": p, "source": s, **v} for (p, s), v in projects.items()]
    proj_list.sort(key=lambda x: -x["tokens"])

    hour_list = [{"date": d, "hour": h, "source": s, **v}
                 for (d, h, s), v in hourly.items()]

    sessions = [s for s in sessions if (s.get("asst") or s.get("req") or s.get("in"))]
    sessions.sort(key=lambda s: (s.get("end") or ""), reverse=True)

    return {
        "generated_at": time.time(),
        "meta": dict(_meta),
        "records": rec_list,
        "tools": tool_list,
        "projects": proj_list,
        "hourly": hour_list,
        "sessions": sessions[:2000],
        "sessions_total": len(sessions),
        "model_vendor": model_meta,
        "ai_lines": [{"date": d, **v} for d, v in sorted(ai_lines.items())],
        "pricing": {k: list(v) for k, v in P.PRICING.items()},
        "pricing_note": ("Anthropic costs use current list pricing (Fable 5 $10/$50, Opus 5 & 4.x "
                         "$5/$25, Sonnet 5 $2/$10 intro thru 2026-08-31 then $3/$15, Sonnet 4.x "
                         "$3/$15, Haiku $1/$5 per Mtok) with cache write billed at 1.25x "
                         "(5-min) / 2x (1-hour) input and cache read at 0.1x; OpenAI/Codex/"
                         "Copilot/Cursor prices are estimates. Codex GPT-5.4/5.5 use verified "
                         "OpenAI list rates (GPT-5.5 $5/$30, cached $0.50; a >272K-input "
                         "surcharge is not modeled, so heavy-context sessions may be higher). "
                         "Actual "
                         "billing may differ. Claude Code/Desktop & Codex can run on either "
                         "subscription or API billing and the logs don't record which, so $ is "
                         "shown as API-equivalent value (all such usage is included either way). "
                         "IMPORTANT — GitHub Copilot logs NO token counts: its tokens here are "
                         "estimated from visible message text only and EXCLUDE hidden context "
                         "(file/repo context, system prompts, tool outputs), so they are a large "
                         "undercount. Copilot's accurate usage metric is request count (the 'Msgs' "
                         "column) and the premium-request multiplier, not tokens. "
                         "Cursor: messages, tool calls, per-session model, mode and timestamps are "
                         "exact, but it records token counts on only ~2% of messages (it meters usage "
                         "server-side for its request-based plan) — so Cursor tokens/cost here are a "
                         "LOWER BOUND; see Cursor's own dashboard for real usage. Gemini is not shown: "
                         "its local logs persist no usable prompt/token/model data."),
    }


# ---------------------------------------------------------------------------
# Storage — how much of the drive these interaction logs occupy.
# The per-file aggregates already carry each log's byte size, so the per-tool
# rollup is free; only the "related but unparsed" dirs need a walk, and that is
# cached because it touches multi-GB trees.
# ---------------------------------------------------------------------------
HOME = os.path.expanduser("~")
_extras_cache = {"at": 0.0, "rows": []}
EXTRAS_TTL = 300


def _dir_bytes(path, cap=400000):
    """Recursive size of a directory. Skips symlinks; gives up past `cap` files."""
    total, files, stack, n = 0, 0, [path], 0
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir():
                            stack.append(e.path)
                        else:
                            total += e.stat().st_size
                            files += 1
                            n += 1
                    except OSError:
                        continue
        except OSError:
            continue
        if n > cap:
            break
    return total, files


def _extras():
    """AI-related data on disk that the dashboard does NOT parse into analytics."""
    if time.time() - _extras_cache["at"] < EXTRAS_TTL:
        return _extras_cache["rows"]
    cands = [
        (os.path.join(HOME, ".gemini", "tmp"), "Gemini CLI logs",
         "not parsed — Gemini persists no tokens/model, so this is pure dead weight"),
        (os.path.join(HOME, ".claude", "file-history"), "Claude Code file history",
         "edit snapshots used for undo"),
        (os.path.join(HOME, ".claude", "shell-snapshots"), "Claude Code shell snapshots", ""),
        (os.path.join(HOME, ".claude", "backups"), "Claude Code backups", ""),
        (os.path.join(HOME, ".claude", "cache"), "Claude Code cache", ""),
        (os.path.join(HOME, ".codex", "archived_sessions"), "Codex archived sessions",
         "parsed — counted in Codex above"),
    ]
    rows = []
    for path, label, note in cands:
        if not os.path.isdir(path):
            continue
        b, n = _dir_bytes(path)
        if b:
            rows.append({"label": label, "path": path, "bytes": b, "files": n, "note": note})
    rows.sort(key=lambda r: -r["bytes"])
    _extras_cache.update(at=time.time(), rows=rows)
    return rows


IS_WINDOWS = os.name == "nt"


def _cleanup_targets():
    """Directories the cleanup commands sweep, as (dir, path-glob, name-glob).

    Derived from the same globs `parser.discover()` scans, so the commands and the
    "reclaimable" figure are two views of ONE rule and cannot drift apart.
    Cursor is absent on purpose: it keeps a single live SQLite store, and deleting
    it would destroy its chat history rather than reclaim stale logs.
    """
    t = []
    for g in P.CLAUDE_GLOBS + P.CODEX_GLOBS:
        head = g.split("**")[0].rstrip(os.sep)
        if os.path.isdir(head):
            t.append((head, None, "*.jsonl"))
    for g in P.CLAUDE_DESKTOP_GLOBS:                 # skip the audit.jsonl mirror
        head = g.split("**")[0].rstrip(os.sep)
        if os.path.isdir(head):
            t.append((head, "*/.claude/projects/*", "*.jsonl"))
    for r in P.COPILOT_ROOTS:                        # sessions are .json AND .jsonl
        ws = os.path.join(r, "User", "workspaceStorage")
        if os.path.isdir(ws):
            t.append((ws, "*/chatSessions/*", None))
        ew = os.path.join(r, "User", "globalStorage", "emptyWindowChatSessions")
        if os.path.isdir(ew):
            t.append((ew, None, None))
    return t


def _swept_by_cleanup(path, targets):
    """True if the generated commands would delete this file."""
    import fnmatch
    norm = path.replace("\\", "/")
    for d, pathglob, nameglob in targets:
        if not norm.startswith(d.replace("\\", "/").rstrip("/") + "/"):
            continue
        if pathglob and not fnmatch.fnmatch(norm, "*" + pathglob.lstrip("*")):
            continue
        if nameglob and not fnmatch.fnmatch(os.path.basename(norm), nameglob):
            continue
        return True
    return False


def _is_stale(mtime, now, days):
    """Exactly the predicate this platform's cleanup command uses, so the
    "reclaimable" figure always equals what running it would delete.

    POSIX `find -mtime +N` compares the age in WHOLE 24h units and matches only
    when that integer is > N (so a 90.5-day-old file is NOT matched by +90).
    PowerShell's `LastWriteTime -lt (Get-Date).AddDays(-N)` is an exact instant.
    """
    if not mtime:
        return False
    if IS_WINDOWS:
        return mtime < now - days * 86400
    return int((now - mtime) // 86400) > days


def _cleanup_plan(days=90):
    """Delete-old-logs commands for THIS machine: real paths, right shell.

    The dashboard never deletes anything itself — it hands over commands the user
    can read first. Paths come from the parser's own globs, so they are correct on
    macOS, Linux and Windows alike."""
    cmds = []
    for d, pathglob, nameglob in _cleanup_targets():
        if IS_WINDOWS:
            filt = f'-Filter {nameglob} ' if nameglob else ""
            frag = pathglob.strip("*/").split("/")[0] if pathglob else None
            extra = f'$_.FullName -like "*{frag}*" -and ' if frag else ""
            cmds.append(f'Get-ChildItem -LiteralPath "{d}" -Recurse -File {filt}| '
                        f'Where-Object {{ {extra}'
                        f'$_.LastWriteTime -lt (Get-Date).AddDays(-{days}) }} | Remove-Item -Force')
        else:
            pf = f"-path '{pathglob}' " if pathglob else ""
            nf = f"-name '{nameglob}' " if nameglob else ""
            cmds.append(f"find '{d}' {pf}{nf}-type f -mtime +{days} -delete")
    return {"shell": "PowerShell" if IS_WINDOWS else "bash / zsh",
            "days": days, "commands": cmds}


def build_storage():
    with _lock:
        files = list(_state["files"].values())
    per, rows, growth = {}, [], {}
    for agg in files:
        src = agg.get("source", "?")
        slot = per.setdefault(src, {"source": src, "files": 0, "bytes": 0,
                                    "archived_files": 0})
        if agg.get("archived"):
            slot["archived_files"] += 1
            continue                       # pruned from disk: costs no space now
        size = int(agg.get("size") or 0)
        slot["files"] += 1
        slot["bytes"] += size
        last = agg.get("last_ts") or ""
        day = last[:10] or "unknown"
        gk = (day, src)
        growth[gk] = growth.get(gk, 0) + size
        if size:
            rows.append({
                "path": agg.get("path", ""),
                "source": src,
                "project": agg.get("project") or "(unknown)",
                "title": agg.get("title"),
                "bytes": size,
                "mtime": agg.get("mtime") or 0,
                "last": last,
            })
    rows.sort(key=lambda r: -r["bytes"])
    # Reclaimable is computed over EVERY live file, not over the truncated list
    # the UI receives, so the headline number can't quietly undercount.
    now = time.time()
    reclaim = {}
    targets = _cleanup_targets()
    cleanable = [r for r in rows if _swept_by_cleanup(r["path"], targets)]
    for d in (30, 90, 180):
        stale = [r for r in cleanable if _is_stale(r["mtime"], now, d)]
        reclaim[str(d)] = {"files": len(stale), "bytes": sum(r["bytes"] for r in stale)}
    try:
        du = shutil.disk_usage(HOME)
        disk = {"total": du.total, "used": du.used, "free": du.free}
    except Exception:
        disk = {"total": 0, "used": 0, "free": 0}
    try:
        cache_bytes = os.path.getsize(CACHE_PATH)
    except OSError:
        cache_bytes = 0
    return {
        "generated_at": time.time(),
        "sources": sorted(per.values(), key=lambda r: -r["bytes"]),
        "files": rows[:1000],
        "files_total": len(rows),
        "reclaimable": reclaim,
        "cleanup": _cleanup_plan(),
        "platform": ("windows" if IS_WINDOWS else
                     "macos" if sys.platform == "darwin" else "linux"),
        "growth": [{"date": d, "source": s, "bytes": b} for (d, s), b in growth.items()],
        "extras": _extras(),
        "disk": disk,
        "cache_bytes": cache_bytes,
        "home": HOME,
    }


def _zero():
    return {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
            "asst": 0, "user": 0, "req": 0, "tools": 0, "prem": 0.0, "cost": 0.0}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, ctype):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            self._send(404, "not found", "text/plain")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _static(self, route):
        """Serve static/* — the split frontend (css/js). Path-traversal guarded."""
        rel = route[len("/static/"):]
        base = os.path.join(HERE, "static")
        path = os.path.normpath(os.path.join(base, rel))
        if not path.startswith(base + os.sep) or not os.path.isfile(path):
            self._send(404, "not found", "text/plain")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif route == "/chart.js":
            self._file("chart.umd.min.js", "application/javascript")
        elif route.startswith("/static/"):
            self._static(route)
        elif route == "/api/storage":
            try:
                self._send(200, json.dumps(build_storage()))
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        elif route == "/api/refresh":
            refresh(verbose=False)
            self._send(200, json.dumps({"ok": True, "meta": _meta}))
        elif route == "/api/data":
            try:
                payload = build_payload()
                self._send(200, json.dumps(payload))
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, "not found", "text/plain")


def background_refresher(interval):
    while True:
        time.sleep(interval)
        try:
            refresh(verbose=False)
        except Exception as e:
            sys.stderr.write(f"[refresh] {e}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7878)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--interval", type=int, default=20,
                    help="seconds between background incremental refreshes")
    ap.add_argument("--rebuild", action="store_true", help="ignore cache, full reparse")
    args = ap.parse_args()

    if args.rebuild and os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
    load_cache()
    sys.stderr.write("[init] parsing local logs (first run reads everything, "
                     "incl. one large Codex log)...\n")
    refresh(verbose=True)
    sys.stderr.write(f"[init] {_meta['files']} files in {_meta['last_duration']:.1f}s\n")

    threading.Thread(target=background_refresher, args=(args.interval,), daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    sys.stderr.write(f"\n  ✦ AI Usage Dashboard live at  {url}\n")
    sys.stderr.write(f"    refreshing every {args.interval}s · Ctrl-C to stop\n\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nbye\n")


if __name__ == "__main__":
    main()
