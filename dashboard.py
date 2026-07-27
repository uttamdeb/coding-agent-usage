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
import os, sys, json, time, threading, argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import parser as P

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, ".usage_cache.json")
CACHE_VERSION = 13

# ---------------------------------------------------------------------------
# In-memory store of per-file aggregates, refreshed on a background interval.
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {"files": {}, "version": CACHE_VERSION}
_meta = {"last_refresh": 0.0, "last_duration": 0.0, "files": 0, "building": False}


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
        files[p]["archived"] = True
    if verbose and gone:
        sys.stderr.write(f"\n[ledger] retaining {len(gone)} pruned-from-disk session(s)\n")
    if verbose:
        sys.stderr.write("\n")
    _meta.update(last_refresh=time.time(), last_duration=time.time() - t0,
                 files=len(files), building=False)
    save_cache()


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
    records = {}      # (date, source, model) -> aggregates
    tools = {}        # (source, name) -> count
    projects = {}     # (project, source) -> {tokens, msgs, sessions, cost}
    hourly = {}       # (hour, source) -> {tokens, msgs}
    dow = {}          # (dow, source) -> {tokens, msgs}
    sessions = []
    model_meta = {}   # model -> vendor

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
                rk = (date, source, "(user)")
                slot = records.setdefault(rk, _zero())
                slot["user"] += r.get("user", 0)
                continue
            rk = (date, source, model)
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
        for name, c in agg.get("tools", {}).items():
            tools[(source, name)] = tools.get((source, name), 0) + c
        for h, v in agg.get("hourly", {}).items():
            slot = hourly.setdefault((int(h), source), {"tokens": 0, "msgs": 0})
            slot["tokens"] += v["tokens"]; slot["msgs"] += v["msgs"]
        for d, v in agg.get("dow", {}).items():
            slot = dow.setdefault((int(d), source), {"tokens": 0, "msgs": 0})
            slot["tokens"] += v["tokens"]; slot["msgs"] += v["msgs"]
        # project rollup
        pk = (project, source)
        pr = projects.setdefault(pk, {"tokens": 0, "msgs": 0, "sessions": 0, "cost": 0.0})
        pr["tokens"] += file_tokens; pr["msgs"] += file_msgs
        pr["sessions"] += 1; pr["cost"] += file_cost
        # sessions
        for s in agg.get("sessions", []):
            s2 = dict(s)
            s2["cost"] = _cost(source, s["model"], s["in"], s["out"], s["cr"],
                               s.get("cc5", 0), s.get("cc1", 0), s.get("cc", 0),
                               (s.get("end") or s.get("start") or "")[:10])
            sessions.append(s2)

    rec_list = []
    for (date, source, model), v in records.items():
        if model == "(user)" and not any(v[f] for f in ("in", "out", "asst")):
            # user-only marker rows still useful for "messages" — keep user count
            pass
        rec_list.append({"date": date, "source": source, "model": model, **v})
    rec_list.sort(key=lambda x: (x["date"], x["source"]))

    tool_list = [{"source": s, "name": n, "count": c} for (s, n), c in tools.items()]
    tool_list.sort(key=lambda x: -x["count"])

    proj_list = [{"project": p, "source": s, **v} for (p, s), v in projects.items()]
    proj_list.sort(key=lambda x: -x["tokens"])

    hour_list = [{"hour": h, "source": s, **v} for (h, s), v in hourly.items()]
    dow_list = [{"dow": d, "source": s, **v} for (d, s), v in dow.items()]

    sessions = [s for s in sessions if (s.get("asst") or s.get("req") or s.get("in"))]
    sessions.sort(key=lambda s: (s.get("end") or ""), reverse=True)

    return {
        "generated_at": time.time(),
        "meta": dict(_meta),
        "records": rec_list,
        "tools": tool_list,
        "projects": proj_list,
        "hourly": hour_list,
        "dow": dow_list,
        "sessions": sessions[:500],
        "model_vendor": model_meta,
        "pricing": {k: list(v) for k, v in P.PRICING.items()},
        "pricing_note": ("Anthropic costs use current list pricing (Fable 5 $10/$50, Opus 4.x "
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
                         "Cursor logs message counts but records tokens on only a few messages and "
                         "no per-message model, dated by session — treat Cursor as message activity, "
                         "not exact tokens. Gemini is not shown: its local logs persist no usable "
                         "prompt/token/model data."),
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

    def do_GET(self):
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif route == "/chart.js":
            self._file("chart.umd.min.js", "application/javascript")
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
