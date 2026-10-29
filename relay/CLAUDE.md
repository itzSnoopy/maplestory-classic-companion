# relay/: the party relay server (stdlib Python, Docker)

A tiny HTTP server the player hosts so trackers in one group can see each other's EXP / HP / MP / map.
Not part of the app: it is its own deployable (first candidate for its own repo, Phase 7). The client
side is `components/party/partylink.py`.

**Memory (local):** `.claude/handoff/components/party.md` (relay deploy facts live there, never in git).
**May import:** standard library only. **Never:** anything from the app.

## Files
| File | Role |
|---|---|
| `party_relay.py` | The server. In memory only; members drop 90 s (`TTL_S`) after their last post; field whitelist (`FIELDS`), 4 KB body cap, 500 codes max, rate limit 4 req/s per address (burst 10), reply capped at 2000 members |
| `Dockerfile` | `python:3.12-slim`, runs `party_relay.py` as `nobody`, port 8787 |
| `docker-compose.yml` | Build from the Dockerfile; env `PARTY_CODES`, `MAX_MEMBERS` |
| `portainer-stack.yml` | **Generated** by `make_stack.py`: relay code embedded in an env var, no build/upload needed (Portainer > Stacks > Web editor) |
| `make_stack.py` | `python relay/make_stack.py` regenerates `portainer-stack.yml` from `party_relay.py` (run after every relay change, `$` is escaped) |
| `README.md` | Run/deploy notes for humans (Docker, plain Python, TLS via a reverse proxy) |

## Protocol (with `partylink.py`)
- `POST /v1/party/<code>` body = JSON of your state; reply `{members: [...], server_t}`. `GET` same path = read only. `GET /health` = `{ok: true}`.
- Code: `[A-Za-z0-9_-]{6,64}`; name `[A-Za-z0-9]{1,16}`; wrong code (not in `PARTY_CODES`) -> 403, bad name -> 400, junk fields dropped.
- Fields the client sends: `name, state, level, job, map, exp_cur, exp_need, exp_pct, exp_hr, eta, hp, mp, channel, v`. Each member comes back as its fields + `name` + `age_s`. The client drops itself from the list and writes the rest to `party.json`.
- Env: `PORT` (8787), `BIND` (0.0.0.0), `PARTY_CODES` (comma allowlist; empty = any), `MAX_MEMBERS` (0 = no limit).
- Plain HTTP unless behind TLS; the code travels in the URL. Data is low-sensitivity (names + game numbers).
- A change to `FIELDS` must stay in step with `own_state()` in `partylink.py`.

## Test it
`PORT=8787 python relay/party_relay.py`, then `curl http://127.0.0.1:8787/health`; post with curl or run two trackers/`partylink.exchange`. Docker: `cd relay && docker compose up -d --build`. After editing the relay run `python relay/make_stack.py`.

## Rules that bite here
- Public repo: no real address, IP or party code in any file (`PARTY_CODES` stays empty in git).
- Nothing is written to disk; keep it that way.
