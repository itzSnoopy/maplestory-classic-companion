# Party relay

A tiny server that lets the trackers in one party see each other's EXP, HP and MP.
Each app posts its own numbers every few seconds under a shared **party code** and gets back
everyone else's. Nothing is stored on disk; members disappear 90 s after their last update.

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

Settings > Internet > **Party link**: server address (e.g. `http://203.0.113.5:8787` or
`https://party.example.com`) and the same party code on both PCs.

The connection is plain HTTP unless you put the relay behind a reverse proxy with TLS
(Caddy: `party.example.com { reverse_proxy localhost:8787 }`). Over plain HTTP the party code and
the numbers above travel unencrypted.
