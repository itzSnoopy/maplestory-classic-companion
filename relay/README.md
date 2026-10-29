# Party relay

A tiny server that lets the trackers in one group see each other's EXP, HP, MP and maps.
Each app posts its own numbers every few seconds under a shared **party code** and gets back
everyone else's. A code is a group: your in-game party, or a hangout of friends on any maps, with
no player limit (`MAX_MEMBERS` sets one if you want). Nothing is stored on disk; members disappear 90 s after their last update.

What it receives per player: character name, level, job, map, channel, EXP (current / needed / %),
EXP per hour, time to level, HP and MP (current / max). No screenshots, no account data.

## Run it (Docker)

```bash
cd relay
docker compose up -d --build
```

Set `PARTY_CODES` in `docker-compose.yml` to your party code (e.g. `PARTY_CODES: "maple-duo-7f3k"`)
so nobody else can use the server. Check it: `curl http://<server>:8787/health`.

## Run it (no Docker)

```bash
PORT=8787 PARTY_CODES=maple-duo-7f3k python3 party_relay.py
```

## In the app

Settings > Internet > **Party & friends link**: server address (e.g. `http://203.0.113.5:8787` or
`https://party.example.com`) and the same party code on both PCs.

The connection is plain HTTP unless you put the relay behind a reverse proxy with TLS
(Caddy: `party.example.com { reverse_proxy localhost:8787 }`). Over plain HTTP the party code and
the numbers above travel unencrypted.

## Skill feedback inbox (optional)

Friends can send you a review of the app's skill data. It is off until you set `FEEDBACK_KEY`
(your private owner key; leave it empty and the feedback endpoints answer 404). `FEEDBACK_CODES`
optionally limits which feedback codes are accepted.

- `POST /v1/feedback/<code>`: anyone with the code sends one review export (JSON, max 64 KB, one send
  per 30 s per address).
- `GET /v1/feedback/<code>` with header `X-Feedback-Key: <key>`: the stored items, oldest first.
- `DELETE /v1/feedback/<code>` with the key: delete everything for the code, or `{"ids": [...]}`.
- Kept 30 days (`FEEDBACK_TTL_DAYS`), at most 300 items (`FEEDBACK_MAX`, the oldest goes first).
- **Memory only**: nothing is written to disk and a restart forgets it. Fetch it with
  `python dev/feedback.py fetch` (see the script's header).

Privacy: only the optional reviewer nickname and the answers are kept. No IP addresses are stored
(beyond short-lived in-memory rate-limit counters) and requests are not logged.

License: the relay is AGPLv3, like the rest of this public repository. People using your relay over
the network can ask for its source: run the published code, and `/health` links to it.
