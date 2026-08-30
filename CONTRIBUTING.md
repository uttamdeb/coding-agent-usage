# Contributing

Thanks for looking. This is a small, deliberately dependency-free project, and the
constraints below are the reason it stays that way — please read them before opening
a PR.

## Get it running

```bash
git clone https://github.com/uttamdeb/coding-agent-usage.git
cd coding-agent-usage
python3 dashboard.py          # http://127.0.0.1:7878
```

Python 3.8+, macOS / Linux / Windows. No `pip install`, no build step, no config.
First run parses your logs (~30–60s if you have large Codex logs) and caches the
result; later refreshes are incremental.

Useful flags: `--port 9000`, `--rebuild` (ignore cache, full re-parse),
`--interval 20` (background refresh seconds).

**[AGENTS.md](AGENTS.md) is the architecture guide.** It is written for coding agents
but it is the fastest orientation for humans too — read it before changing anything
structural.

## Set your git email first

GitHub links a commit to an account by matching the **author email** to a verified
address on that account. If yours is wrong, your contribution will not appear in the
repository's contributor graph and cannot be fixed later without rewriting history.

```bash
git config user.email "the-address-verified-on-your-github-account"
git log -1 --format='%an <%ae>'     # check before you push
```

This has already cost one contributor their attribution here. Please check.

## The hard rules

These are not style preferences. A PR that breaks one will be asked to change.

1. **Standard library only, and offline.** No third-party runtime dependencies, ever.
   If you need a JS library, vendor it — the page must work with no network.
2. **Never commit `.usage_cache.json`** (or `server.log`). They contain the author's
   own prompts, project names and costs. A fresh clone must start empty.
3. **Never hardcode a path.** Everything derives at runtime from `HOME` /
   `%APPDATA%` / `%LOCALAPPDATA%` / `$XDG_*`. Someone on another OS must see their
   own data with zero configuration. Split path components with `_leaf()`, not
   `os.path.basename` — logs written on Windows get read on macOS and vice versa.
4. **Costs are estimates at API list prices.** Verify any price you add against the
   vendor's own documentation. Do not guess, and do not let the UI imply these are
   amounts actually billed — subscription users pay nothing per token.
5. **Don't swallow exceptions.** `except Exception: pass` around a parser makes a
   crash indistinguishable from "the user doesn't have this tool". Log to stderr.
6. **Attribute by tool, not by model.** A Claude model used inside Copilot counts
   under Copilot.

## Things that bite

- **Chart colours are a validated palette, and the `ORDER` array in
  `static/core.js` is the safety mechanism.** Adjacent pairs are checked for
  colour-vision-deficiency separation (ΔE ≥ 8) and normal-vision separation
  (ΔE ≥ 15) in *both* light and dark. Reordering tools or changing a hue means
  re-validating the whole sequence. Identity must never be colour-alone — keep the
  legend and tooltips.
- **Bump `CACHE_VERSION` in `dashboard.py`** whenever an aggregate's shape changes
  (a new field, a new key format). Otherwise users load a stale cache into new code.
- **Every dimension carries a date.** Aggregates are keyed `records["date\tmodel"]`,
  `tools["date\tname"]`, `hourly["date\thour"]` so the UI can filter by range. A new
  aggregate that isn't date-keyed can't be filtered.
- **A line chart needs two points to draw a segment.** Build line/area datasets with
  `pointRadius: soloPoint(data)` or a single-day range renders as empty axes.
- **Check the function signature you're calling.** Python won't catch a wrong keyword
  argument until that line runs, and if it's inside a broad `except` you'll never see
  it.

## Testing before you open a PR

There is no test suite; verification is empirical. At minimum:

```bash
# 1. It still parses, and the numbers didn't move for reasons you can't explain
python3 dashboard.py --rebuild

# 2. No render failures — uncaught and caught errors both land on data-js-error
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --virtual-time-budget=9000 --dump-dom "http://127.0.0.1:7878/#cost" | grep data-js-error
```

Then click through all seven tabs, and **check `?range=today` as well as a multi-day
range** — single-day ranges have their own failure mode. `?theme=dark|light|auto` and
`?range=30d` preset the UI, which makes headless screenshots easy.

If you touch parsing, say in the PR which numbers you compared before and after, and
on what data.

## Adding a new tool source

1. In `parser.py`: add its path(s), emit entries from `discover()`, write
   `parse_<tool>()`, route it in `update_file()`.
2. In `static/core.js`: add it to `SRC` and `ORDER`.
3. In `static/app.css`: add a `--t-<source>` colour token (see the palette rules).
4. Bump `CACHE_VERSION`.
5. Update the source table in `README.md` and `AGENTS.md`.

Say in the PR **which version of the tool** you tested against and where it stores
its data — these formats change, and a parser written against one version often
breaks silently on another.

## Adding or fixing a model price

1. `PRICING["<Display Name>"] = (input, output, cache_write_5m, cache_write_1h, cache_read)`
   — USD per 1M tokens. OpenAI rows use `0, 0` for the cache-write tiers and put the
   cached rate in the last slot.
2. Make sure the relevant normalizer maps the raw id to that display name.
3. Pricing applies at request time, so no re-parse is needed after a `PRICING` edit.
   A normalizer change needs `--rebuild` and a `CACHE_VERSION` bump.

**Link the vendor's pricing page in the PR.** A wrong price silently misreports
someone's spend.

## Pull requests

Keep them focused — one concern per PR. Explain what you tested and on what data.
Small PRs with evidence get merged; large ones without get questions.

Security problems go through [SECURITY.md](SECURITY.md), not a public issue.

By contributing you agree your work is licensed under the [MIT License](LICENSE).
