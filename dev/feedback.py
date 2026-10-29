"""Dev tool (owner only): fetch skill feedback from the relay and merge it.

    python dev/feedback.py fetch --server URL --code CODE [--key KEY] [--out DIR] [--clear]
    python dev/feedback.py merge [FILES or DIRS ...] [--out FILE.md]
    python dev/feedback.py suggest [FILES or DIRS ...]

fetch    GET the relay's inbox (key from --key or env FEEDBACK_KEY) and write one json per item to
         dev/local/feedback/ (gitignored; reviewer nicknames stay there). --clear then deletes the
         fetched ids on the server.
merge    reads classic-skill-review/1 exports (from fetch, or exported from the standalone review
         page; same format) and prints a per-skill view grouped by each skill's own class (from
         gamedata/skills_classic.json), not by the export's branch. Disagreements with the current
         data (gamedata/skill_kinds_classic.json) come first. Exports are partial by nature: nothing
         is reported as missing.
suggest  prints candidate OVERRIDES changes for dev/make_skill_kinds.py where a reviewer proposed a
         kind/mode different from the current data. Print only: it never edits that file or the json.

Standard library only; nothing here is shipped with the app. With no FILES the default is
dev/local/feedback/.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "dev" / "local" / "feedback"
KINDS_JSON = ROOT / "gamedata" / "skill_kinds_classic.json"
SKILLS_JSON = ROOT / "gamedata" / "skills_classic.json"
FORMAT = "classic-skill-review/1"
JOB_ORDER = {"Beginner": 0, "1st Job": 1, "2nd Job": 2, "3rd Job": 3}


# ------------------------------------------------------------------ fetch
def _req(method, url, key, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Feedback-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"server answered {e.code}: {e.read().decode(errors='replace')[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach the server: {e.reason}")


def _safe(s, default):
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", s or "").strip("-")[:24]
    return s or default


def cmd_fetch(a):
    key = a.key or os.environ.get("FEEDBACK_KEY", "")
    if not key:
        sys.exit("no key: pass --key or set env FEEDBACK_KEY")
    url = f"{a.server.rstrip('/')}/v1/feedback/{a.code}"
    items = _req("GET", url, key).get("items", [])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for it in items:
        day = time.strftime("%Y-%m-%d", time.localtime(it.get("received_t", 0)))
        name = f"{day}_{_safe(it.get('branch'), 'branch')}_{_safe(it.get('reviewer'), 'anonymous')}_{_safe(it.get('id'), 'x')}.json"
        (out / name).write_text(json.dumps(it, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} item(s) written to {out}")
    if a.clear and items:
        r = _req("DELETE", url, key, {"ids": [it["id"] for it in items]})
        print(f"cleared {r.get('deleted')} on the server")


# ------------------------------------------------------------------ data
def load_exports(paths):
    files = []
    for p in (paths or [str(DEFAULT_DIR)]):
        p = Path(p)
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"skip {f.name}: {e}", file=sys.stderr)
            continue
        if isinstance(d, dict) and d.get("format") == FORMAT and isinstance(d.get("reviews"), dict):
            d["_file"] = f.name
            out.append(d)
        else:
            print(f"skip {f.name}: not a {FORMAT} export", file=sys.stderr)
    return out


def current_data():
    kinds = json.loads(KINDS_JSON.read_text(encoding="utf-8")).get("kinds", {})
    skills = json.loads(SKILLS_JSON.read_text(encoding="utf-8")).get("skills", {})
    return kinds, skills


def cur_mode(k):
    if not k:
        return "-"
    if k.get("until") == "death":
        return "until death"
    return "timed" if k.get("duration_s") else "-"


def reviewer_name(e):
    return (e.get("reviewer") or "").strip() or "anonymous"


def collect(exports):
    """-> {skill id: [(reviewer, review dict)]}. A named reviewer's later export wins per skill;
    anonymous exports all count separately."""
    exports = sorted(exports, key=lambda e: (e.get("received_t") or 0, e.get("exported") or ""))
    latest = {}
    n_anon = 0
    for e in exports:
        who = reviewer_name(e)
        if who == "anonymous":
            n_anon += 1
            who = f"anonymous#{n_anon}"
        for sid, r in e["reviews"].items():
            if isinstance(r, dict):
                latest[(sid, who)] = r
    per = defaultdict(list)
    for (sid, who), r in latest.items():
        per[sid].append((who, r))
    return per


def disagrees(sid, reviews, kinds):
    k = kinds.get(sid)
    ck, cm = (k or {}).get("kind"), cur_mode(k)
    for _, r in reviews:
        if r.get("status") == "wrong":
            return True
        if r.get("kind") and r["kind"] != ck:
            return True
        if r.get("mode") and r["mode"] != cm:
            return True
    return False


def tally(reviews):
    st = Counter(r.get("status") for _, r in reviews if r.get("status"))
    parts = [f"{s} {st[s]}" for s in ("ok", "wrong", "unsure") if st[s]]
    for field in ("kind", "mode"):
        c = Counter(r[field] for _, r in reviews if r.get(field))
        if c:
            parts.append(f"{field}: " + ", ".join(f"{v} x{n}" for v, n in c.most_common()))
    return "; ".join(parts) or "no status"


# ------------------------------------------------------------------ merge
def render_merge(exports):
    kinds, skills = current_data()
    per = collect(exports)
    lines = [f"# Skill feedback: {len(exports)} export(s), {len(per)} skill(s) with answers", ""]
    answered = Counter()
    for e in exports:
        answered[reviewer_name(e)] += len(e["reviews"])
    lines.append("Answers per reviewer: " + ", ".join(f"{w} {n}" for w, n in sorted(answered.items())))
    lines.append("")

    def block(sid):
        k = kinds.get(sid) or {}
        s = skills.get(sid) or {}
        name = k.get("name") or s.get("name") or next((r.get("name") for _, r in per[sid] if r.get("name")), "?")
        out = [f"### {sid} {name}",
               f"current: kind {k.get('kind', '?')}, mode {cur_mode(k)}   [{tally(per[sid])}]"]
        for who, r in sorted(per[sid]):
            bits = [r.get("status") or "-"]
            if r.get("kind"):
                bits.append(f"kind {r['kind']}")
            if r.get("mode"):
                bits.append(f"mode {r['mode']}")
            line = f"- {who}: " + ", ".join(bits)
            if r.get("note"):
                line += f" | {r['note']}".replace("\n", " ")
            out.append(line)
        out.append("")
        return out

    def cls(sid):
        s = skills.get(sid) or {}
        return (s.get("class") or "Unknown skills", JOB_ORDER.get(s.get("job"), 9))

    for title, flag in (("Disagreements with the current data", True), ("Everything else", False)):
        ids = [i for i in per if disagrees(i, per[i], kinds) == flag]
        if not ids:
            continue
        lines += [f"## {title} ({len(ids)})", ""]
        groups = defaultdict(list)
        for i in ids:
            groups[cls(i)[0]].append(i)
        for c in sorted(groups, key=lambda c: (min(cls(i)[1] for i in groups[c]), c)):
            lines += [f"#### {c}", ""]
            for i in sorted(groups[c], key=lambda i: (cls(i)[1], i)):
                lines += block(i)
    return "\n".join(lines).rstrip() + "\n"


def cmd_merge(a):
    text = render_merge(load_exports(a.files))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"written to {a.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)


# ------------------------------------------------------------------ suggest
def cmd_suggest(a):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    kinds, skills = current_data()
    per = collect(load_exports(a.files))
    print("# Candidate changes for OVERRIDES in dev/make_skill_kinds.py")
    print("# PRINT ONLY: nothing was edited (not make_skill_kinds.py, not the json). Check each by hand.")
    print("# Shape: _ov(\"id id\", kind[, sub]); UNTIL_DEATH = {ids}; DURATION_FIXED = {id: seconds}")
    n = 0
    for sid in sorted(per):
        k = kinds.get(sid) or {}
        name = k.get("name") or (skills.get(sid) or {}).get("name") or "?"
        kp = defaultdict(list)
        mp = defaultdict(list)
        for who, r in per[sid]:
            if r.get("kind") and r["kind"] != k.get("kind"):
                kp[r["kind"]].append(who)
            if r.get("mode") and r["mode"] != cur_mode(k):
                mp[r["mode"]].append(who)
        for kind, who in kp.items():
            sub = f", {k['sub']!r}" if k.get("sub") and kind == "other" else ""
            print(f'_ov("{sid}", "{kind}"{sub})   # {name}: now {k.get("kind", "?")}; proposed by {", ".join(who)}')
            n += 1
        for mode, who in mp.items():
            if mode == "until death":
                hint = f'UNTIL_DEATH: add "{sid}"'
            elif mode == "timed":
                hint = f'DURATION_FIXED: "{sid}": <seconds> (needs a duration; check the skill text)'
            else:
                hint = "no slot in OVERRIDES for toggle yet; note only"
            print(f"# {hint}   # {name}: now {cur_mode(k)}; proposed by {', '.join(who)}")
            n += 1
    if not n:
        print("# (no differing proposals)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--server", required=True)
    f.add_argument("--code", required=True)
    f.add_argument("--key", default="")
    f.add_argument("--out", default=str(DEFAULT_DIR))
    f.add_argument("--clear", action="store_true")
    f.set_defaults(fn=cmd_fetch)
    m = sub.add_parser("merge")
    m.add_argument("files", nargs="*")
    m.add_argument("--out", default="")
    m.set_defaults(fn=cmd_merge)
    s = sub.add_parser("suggest")
    s.add_argument("files", nargs="*")
    s.set_defaults(fn=cmd_suggest)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
