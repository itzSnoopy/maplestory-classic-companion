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
| `test_relay.py` | stdlib unittest, in-process server on 127.0.0.1; run `python relay/test_relay.py` (also in `dev/release_check.py`) |
| `Dockerfile` | `python:3.12-slim`, runs `party_relay.py` as `nobody`, port 8787 |
| `docker-compose.yml` | Build from the Dockerfile; env `PARTY_CODES`, `MAX_MEMBERS`, `FEEDBACK_KEY`, `FEEDBACK_CODES` |
| `portainer-stack.yml` | **Generated** by `make_stack.py`: relay code embedded in an env var, no build/upload needed (Portainer > Stacks > Web editor) |
| `make_stack.py` | `python relay/make_stack.py` regenerates `portainer-stack.yml` from `party_relay.py` (run after every relay change, `$` is escaped). `--local` writes the player's deployable copy `dev/local/relay/portainer-stack.local.yml` with `FEEDBACK_KEY` / `FEEDBACK_CODES` filled from `dev/local/relay/feedback_host.json` (made once; gitignored) |
| `README.md` | Run/deploy notes for humans (Docker, plain Python, TLS via a reverse proxy) |

## Protocol (with `partylink.py`)
- `POST /v1/party/<code>` body = JSON of your state; reply `{members: [...], server_t}`. `GET` same path = read only. `GET /health` = `{ok: true}`.
- Code: `[A-Za-z0-9_-]{6,64}`; name `[A-Za-z0-9]{1,16}`; wrong code (not in `PARTY_CODES`) -> 403, bad name -> 400, junk fields dropped.
- Fields the client sends: `name, state, level, job, map, exp_cur, exp_need, exp_pct, exp_hr, eta, hp, mp, channel, v`. Each member comes back as its fields + `name` + `age_s`. The client drops itself from the list and writes the rest to `party.json`.
- Env: `PORT` (8787), `BIND` (0.0.0.0), `PARTY_CODES` (comma allowlist; empty = any), `MAX_MEMBERS` (0 = no limit).
- Plain HTTP unless behind TLS; the code travels in the URL. Data is low-sensitivity (names + game numbers).
- A change to `FIELDS` must stay in step with `own_state()` in `partylink.py`.

## Challenges (friendly kill races; client side `components/challenge/`)
- Memory only (they last minutes). `POST /v1/challenge/<code>` `{name, mode: 1v1|map|all, minutes: 2|5|10|15, target?}` creates (invited = the target / members on the creator's map / everyone online); `POST /v1/challenge/<code>/<id>` `{name, action: accept|decline|cancel|kills, kills?}`; `GET` the same path = the challenge. A view: `{id, type: "kills", mode, minutes, creator, state: open|running|done|declined|cancelled|expired, phase: starting|live|ending|..., created_t, invited, start_t, end_t, players: [{name, score, accepted, ack}], result: {ranking, tie, winner}, server_t}`.
- The first accept starts it: `start_t = relay clock + CH_COUNTDOWN_S (5)`, `end_t = start_t + minutes`. More invitees may accept until `start_t`. One open/running challenge per member, `CH_MAX` (20) per code, an unanswered invitation expires after 60 s, finished ones are forgotten after 5 min (a result is in replies for 2 min).
- Kill events `{s: seq, n: 1..4, t: relay-clock time}`: ride on the normal party post as `ch: {id, kills: [...]}` (reply gets `challenges: [...]` = the ones you are in or invited to, with your acknowledged `ack`) or on the action `kills`. Counted only while `start_t - 1 <= arrival <= end_t + 4 s` and stamped inside `[start_t, end_t]`; capped at `CH_MAX_KPS` (1.5/s) + 4; duplicates by seq. The relay decides the result when `end_t + CH_GRACE_S` passes: ranking, `tie` when the top two are level, `winner` else.
- The party post also takes a `caps` field (`"ch"` = this build does challenges; the panel shows its button only for members that have it).
- Tests: `relay/test_relay.py` (`test_ch_*`: clock, validation, modes, expiry, kills window, rate cap, tie, party-post ride-along). Names only: no player data beyond what the party post already has.

## Skill feedback inbox (separate from the party endpoints)
- Off unless `FEEDBACK_KEY` is set (empty = every `/v1/feedback` path 404; `/health` says `feedback: false`). `/health` also returns `source` (the public repo, AGPL).
- `POST /v1/feedback/<code>`: anyone with the code sends one `classic-skill-review/1` export (JSON, max 64 KB, `FEEDBACK_MAX_BODY`); reply `{ok, id}`. `branch` may be `all`; exports are partial (1..400 review entries, any classes). Strict validation, only the cleaned copy + `received_t` is stored; note cut at 500 chars; entries with nothing answered dropped; `count` = stored entries.
- `GET /v1/feedback/<code>` (header `X-Feedback-Key`): `{items, server_t}` oldest first. `DELETE` same path + key, optional body `{"ids": [...]}`, none = all for that code; `{deleted: n}`. Wrong key / code not in `FEEDBACK_CODES` -> 403; bad data 400; too big/empty 413.
- Env: `FEEDBACK_KEY`, `FEEDBACK_CODES` (comma allowlist), `FEEDBACK_TTL_DAYS` (30), `FEEDBACK_MAX` (300 items total, oldest dropped).
- Limits: shared per-address bucket, plus 1 feedback POST per 30 s per address (`FEEDBACK_GAP_S`, 429).
- **Memory only**, a restart forgets it. Privacy: only the reviewer nickname (optional) and answers are kept; no addresses stored beyond the in-memory rate buckets, no request logs.
- Owner side: `dev/feedback.py` (`fetch`, `merge`, `suggest`; output in gitignored `dev/local/feedback/`).
- AGPLv3: the relay is part of this public repo; run the published code so `/health`'s `source` is true.

## v2: rooms (`relayd/`, steps 1-4 of the v2 plan; deploy files ready, not deployed yet)
One server, many rooms. Run `cd relay && python -m relayd` (env `PORT`, `BIND`, `DATA_DIR` (./data, Docker /data),
`LOBBY`, `ALLOW_ANYONE_CREATE`, `SECURE_TOKENS`, `LEGACY_V1` (1), `MAX_ROOMS` (500), `MAX_ROOMS_PER_ACCOUNT` (5),
`ANON_CREATES_PER_DAY` (3), `MAX_ROOM_MEMBERS` (0), `FEEDBACK_KEY`, `FEEDBACK_CODES`, `FEEDBACK_TTL_DAYS` (30), `FEEDBACK_MAX` (300),
`ADMIN_PASS` (>= 12 chars, plain) or `ADMIN_PASS_HASH` (`scrypt$salt$hash`; make one with `cd relay && python -c "from relayd.auth import hash_secret as h; print(h('...'))"`, escape `$` in compose), `ADMIN_NETS` (CIDRs; default loopback + private ranges), `TRUSTED_PROXIES` (IPs/CIDRs)).
Tests (all in release_check): `python relay/tests/test_v2.py`, `test_v2_challenge.py`, `test_v2_feedback.py`, `test_v2_admin.py`, `test_v2_public.py`, `test_v2_tokens.py` (shared helpers in `tests/_helpers.py`).
| File | Role |
|---|---|
| `relayd/server.py`, `__main__.py`, `config.py` | `make_server(cfg)`, start from env; `LOBBY`/`ALLOW_ANYONE_CREATE`/`SECURE_TOKENS` override the DB `settings` value when set |
| `relayd/legacy.py` | Handler = v2 routes + `party_relay.Handler`: `/v1/*` runs the v1 code untouched (own memory) while `LEGACY_V1` is on; `/health` = `{ok, version, source, lobby, feedback, legacy_v1, create_open, you, via_proxy}`; `/rooms` + `/rooms/*` go to `public.py` |
| `relayd/http.py` | `/v2` router (V2Mixin), 8 KB bodies (64 KB for feedback POST only: `_json_body(limit)`), per-address bucket 4/s burst 10 |
| `relayd/rooms.py` | `Relay`: create, join, me, leave, view, patch, delete, member actions, sync dispatch, module actions, lobby, hourly cleanup (members idle 30 d, rooms 90 d) |
| `relayd/store.py` | SQLite `DATA_DIR/relay.db` (WAL, migrations): settings, accounts, identities, host_keys, rooms, members (token sha256 only), bans, module_state, feedback (migration 2) |
| `relayd/auth.py` | scrypt secrets, tokens, lockouts (5 wrong per address+room -> 10 min; 30/h per room -> joins paused 15 min), providers `anon`, `hostkey`, `admin` (shared account for admin-made rooms, identity admin/admin made on first use; later `local`, `discord`) |
| `relayd/modules/` | `Module` base + registry; `presence.py` = the party data (FIELDS copied from v1: keep both in step with `own_state()`) |
| `relayd/modules/challenge.py` | Kill races per room, memory only; v1 rules and constants ported. Names come from the token. **Module seam:** `Relay` sets `mod.relay`; a module may use `relay.store` and READ another module through a small public method (challenge uses `presence.live_rows(room_id, now) -> {name: fields}` for map/all invitees). `Module.active_rooms()` lets the hourly cleanup tick live state |
| `relayd/feedback.py` | Server-wide skill feedback inbox (not a room module), items in SQLite `feedback(id, code, received_t, item)`; `clean_submission` = v1's validation; pruned by TTL/max on write and read |
| `relayd/netaccess.py` | Real client address: if the TCP peer is in `TRUSTED_PROXIES`, the right-most `X-Forwarded-For` entry not itself a trusted proxy (+ `via_tls` from `X-Forwarded-Proto`), else the peer and forwarded headers are ignored. v2 uses it for every per-address limit. Admin gate `admin_ok`: (not proxied and client in `ADMIN_NETS`) or (proxied and https) |
| `relayd/admin.py`, `relayd/admin_ui/` | `Admin` (sessions in memory: 12 h max / 2 h idle; login lockout 5 wrong -> 15 min per address, > 20 wrong internet logins in 15 min -> internet logins 429 for 5 min (LAN logins never braked); audit; `second_factor_ok` = TOTP seam, no-op) + `AdminMixin` (HTTP, CSRF, headers). Page `index.html`, `admin.js`, `admin.css` (no inline code, no external resources, served from an allowlist) |
| `relayd/public.py`, `relayd/public_ui/` | `PublicMixin`: the public `GET /rooms` page (`index.html`, `rooms.js`, `rooms.css`), explicit allowlist, same strict CSP/headers as admin, **not** behind the admin gate (reachable from the internet), no cookies/CSRF (it only calls the public `/v2` API). Create-room form (host key sent as `X-Host-Key`), the creator's last rooms + owner tokens in localStorage ("Forget" button), lobby list |
| `relayd/clock.py`, `errors.py` | one clock (tests shift `clock.offset`), `ApiError` |
- API: `GET /v2/lobby`; `POST /v2/rooms` `{name, owner, kind: party|hangout, access: open|approval|private|unlisted, secret?, max_members?, host_key?}` -> `{id, token, room}`;
  `POST /v2/rooms/<id>/join` `{name, secret?}` -> `{token, status, role}`; `GET .../me`; `POST .../sync` `{presence: {...}}` -> `{server_t, presence: {members}}` (1 per s per token);
  `POST .../leave`; `GET/PATCH/DELETE /v2/rooms/<id>`; `POST .../members/<name>/approve|deny|kick|ban|unban|promote|demote`; `POST .../<module>/<action>`.
  Wrong host keys: 5 per address -> 10 min lockout for creating (Lockouts key `!hostkey`, per address only; a right key resets it).
  **Tokens:** owner tokens and host keys are friendly `<monster>-<job>-<4 digits>` (`relayd/words.py`, `secrets`, lowercase; input is trimmed and lowercased before hashing, long tokens stay exact); member tokens given on join stay long random. Host keys retry on collision. Setting `secure_tokens` (admin Overview switch / env `SECURE_TOKENS`) makes new owner tokens and host keys long random; existing ones keep working.
  Wrong bearer tokens (a token was sent, no member matched): 10 per address -> 429 for 10 min for that address (Lockouts key `!token`, address only, never per room; any valid token from the address resets the count; no token at all is not counted). `/health`, the lobby and join-by-PIN never go through it (join has its own secret lockout).
  Room size: `party` holds 1..6 (default 6), `hangout` default 0 = no limit; enforced on create and on owner/admin PATCH (400 "a party room holds up to 6 people"); `MAX_ROOM_MEMBERS` still caps on top. Lobby rooms: `{id, name, kind, access, members, online, max_members, activity}`; `activity` from `Module.lobby_activity(room)` (first non-None in the room's module order; challenge: "Duel waiting" / "Duel running").
  Auth `Authorization: Bearer <token>`. Room ids `xxxx-xxxx` (no 0/o/1/l/i).
- Rules as built: any room with a secret asks for it on join; wrong secret 403 (unlisted: 404, same as an unknown id);
  mods approve/deny/kick plain members only; deny lets the name ask again; a name already in the room is 409 (no token
  recovery yet: the owner kicks the old one); `members` in the room view = active only, pending in `requests` (owner/mod).
- New rooms get modules `["presence", "challenge"]` (existing rooms keep theirs; owners PATCH `modules`). Challenge: `POST .../challenge/<action>`
  `create {mode: 1v1|map|all, minutes: 2|5|10|15, target?}`, `accept|decline|cancel {id}`, `kills {id, kills}`, `get {id}` -> view (+ `server_t`; same
  view shape as v1). 1v1 target = an active room member; map = live presence rows on the creator's map; all = live presence rows. Sync part:
  `{"challenge": {"id", "kills": [...]}}` -> reply `"challenge": {"challenges": [views I am in or invited to]}` whenever the module is on.
- Feedback (off unless `FEEDBACK_KEY`; else every `/v2/feedback` path 404): `POST /v2/feedback/<code>` (export, 64 KB, 1 per 30 s per address, in memory) -> `{ok, id}`;
  `GET` / `DELETE` (optional `{ids}`) `/v2/feedback/<code>` with `X-Feedback-Key`. `/health` `feedback` = v1 or v2 key set. Survives restarts; no addresses stored.
- Admin (off unless `ADMIN_PASS`/`ADMIN_PASS_HASH`; then `/admin` page + `/v2/admin/*`, else 404). Cookie `relay_admin` (HttpOnly, SameSite=Strict, Secure over https), every non-GET needs header `X-Relay-Admin: 1` and a matching `Origin` if sent. Endpoints: `POST login|logout`, `GET me|status|rooms|rooms/<id>|hostkeys|accounts|feedback|feedback/<code>|audit?limit=|backup`, `PATCH settings` (409 when env-locked) and `rooms/<id>`, `DELETE rooms/<id>|feedback/<code>`, `POST rooms/<id>/members/<name>/<action>` (owner actions + `make_owner`), `POST rooms` `{name, owner, kind, access, secret?, max_members?}` -> `{id, token, room}` (admin-created room: no host key / anyone-create check, no per-account limit, MAX_ROOMS still applies; owned by the shared account of provider `admin`, audit "room create"), `POST hostkeys` (key shown once) / `hostkeys/<id>/revoke` / `accounts/<id>/disable|enable`. Audit table `admin_audit` (last 2000, no addresses, no secrets). Admin edits reuse `Relay._patch` / `_member_action` (actor role `admin`).
- **TRUSTED_PROXIES:** a proxy container on the same Docker host has a private address, so without `TRUSTED_PROXIES` its requests count as LAN and the admin page would be open to the internet through it. Set `TRUSTED_PROXIES` to the proxy's address/network as soon as one is in front. The proxy must pass `Host`, `X-Forwarded-For`, `X-Forwarded-Proto` (NPM and Caddy do by default).
- **Deploy (step 4):** `Dockerfile.v2` (python:3.12-slim, copies `party_relay.py` + `relayd/`, uid 10001, `/data` volume, healthcheck on `/health`),
  `compose.yml` (Portainer git stack, compose path `relay/compose.yml`; service `relay` read-only rootfs, tmpfs /tmp, cap_drop ALL; port `RELAY_PORT` (8788) -> 8787;
  `ADMIN_NETS` defaults to `192.168.0.0/16,10.0.0.0/8` here, leaving out Docker's 172.16/12 so traffic Docker forwards itself never counts as LAN;
  service `caddy` (`caddy reverse-proxy --from $RELAY_DOMAIN --to relay:8787`, off while `TLS=0` via `deploy.replicas`), fixed address `PROXY_IP` 172.29.87.10 on
  `RELAY_SUBNET` 172.29.87.0/24 = the relay's `TRUSTED_PROXIES`), `.dockerignore`, `DEPLOY.md` (Portainer steps, v1 -> v2 switch, DuckDNS + TLS, settings table).
  No bind mounts (Portainer git stacks don't resolve relative ones). Use plain `ADMIN_PASS` in Portainer (a `$` in a variable gets eaten).
- Not yet: relic module, optional TOTP. v1's `Dockerfile` / `docker-compose.yml` / `portainer-stack.yml` / `make_stack.py` stay until the switch to v2.

## Test it
`PORT=8787 python relay/party_relay.py`, then `curl http://127.0.0.1:8787/health`; post with curl or run two trackers/`partylink.exchange`. Docker: `cd relay && docker compose up -d --build`. After editing the relay run `python relay/make_stack.py`. Tests: `python relay/test_relay.py`.

## Rules that bite here
- Public repo: no real address, IP or party code in any file (`PARTY_CODES` stays empty in git).
- v1 (`party_relay.py`) writes nothing to disk. v2 (`relayd/`) keeps rooms/members/accounts in SQLite under `DATA_DIR` (player's decision 2026-11-01); live data (presence, rate buckets, lockouts) stays in memory and no addresses are stored.
