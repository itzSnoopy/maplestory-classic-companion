# components/feedback: skill review page + Send to the relay feedback inbox

Players who know a class review its skills (kind, buff mode, cost) in a page the helper serves locally and
press Send; the app posts the export to the owner's relay feedback inbox. One partial export, `branch: "all"`:
reviewers browse any class tab, answer only what they know (Send needs >= 1 answered skill). Helper side only.

**Memory (local):** `.claude/handoff/components/feedback.md`.
**May import:** `hub`, `gamedata`, `base`. **Never:** another component, `engine`, `launcher`.

## Files
| File | Role |
|---|---|
| `component.py` | `FeedbackComponent` (`attach` does nothing; `ui(shell)` page, API, menu, Settings); `validate`, `post_feedback(server, code, req)` (gate + POST + relay reply mapping) |
| `review_page.py` | `page(reviewer)`: builds the page at render time from `gamedata/skills_classic.json` + `skill_kinds_classic.json`; `branches(rows)` = class tabs (top class -> job lines; classes not grouped get their own tab) |
| `review.html` | Page template (`__DATA__`, `__CFG__`): class tabs (navigation only), filters, per-skill ok/wrong/unsure + kind/mode/note, nickname (max 24), Send, Export JSON, Copy; answers in ONE localStorage store `classic-skill-review:all`; light/dark; no external fonts |
| `__init__.py` | docstring only |

## Interfaces
- **Listens to / emits / services:** none.
- **Local API:** `POST /api/feedback/send` (registered with `shell.add_api` -> `hub.pages.register_api`; the diary server checks Host + `X-Diary: 1`, body up to 70000 bytes). Body = export `{format: "classic-skill-review/1", branch: "all", reviewer, exported, data_fetched, count, reviews: {skill_id: {name, status, kind?, mode?, note?}}}` (only answered skills). Returns `{ok, id}` or `{error}` with a status. `POST /api/feedback/status` -> `{online, reason}` (`server_status`: GET `<server>/health`, online = ok and `feedback: true`; behind the same gate); the page shows a dot "Review Server ONLINE/OFFLINE" (reason as tooltip), checked on load, every 60 s and after each Send.
- **Relay:** `POST <server>/v1/feedback/<code>` (JSON, 10 s). 200 -> ok+id; 403 wrong code; 404 no inbox; 400/413/429 relay text; connection error -> readable text.
- **Settings:** `feedback` {server, code} (Settings > Internet > Skill feedback). Internet gate `internet.allowed("feedback")` right before connecting. Reviewer nickname is prefilled from `character.name`.
- **Reads:** `paths.asset("gamedata/skills_classic.json" | "gamedata/skill_kinds_classic.json" | "components/feedback/review.html")` (all shipped).
- **Helper side:** page `/skill-review.html`, command `skillreview`, menu "Review skills (send feedback)" (slot `open`).

## Test it
Offline (`MAPLE_COMPANION_OFFLINE=1`): `python dev/feedback_sim.py OUT_DIR` (page -> local API -> in-process relay -> `dev/feedback.py`
fetch/merge/suggest; in release_check). By hand: render `review_page.page()` to a scratch file and `node --check` its script; call
`post_feedback` against a fake local relay (gate monkeypatched); start `diary_server.start_thread(port)` and POST with / without `X-Diary`.
`python dev/check_layers.py --strict`, `python dev/release_check.py`.

## Rules that bite here
- Public repo: no relay address, code or reviewer names in code, docs or logs. Never log the nickname or the code.
- Online use only behind `internet.allowed("feedback")`. Nothing is sent unless the player presses Send.
