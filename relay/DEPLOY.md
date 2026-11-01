# Deploying relay v2 with Portainer

Relay v2 (`relayd/`) is one container that hosts every room, the admin page and the old `/v1`
paths, so current app builds keep working against it. It keeps rooms, members and accounts in a
small SQLite file on a Docker volume. An optional Caddy container adds HTTPS once you have a domain.

## 1. First deploy (no domain yet)

1. Portainer > **Stacks** > **Add stack** > **Repository**.
   - Name: `classic-relay`
   - Repository URL: `https://github.com/itzSnoopy/maplestory-classic-companion`
   - Repository reference: `refs/heads/main`
   - Compose path: `relay/compose.yml`
2. **Environment variables** (Advanced mode lets you paste them as lines):
   ```
   ADMIN_PASS=<a long pass, at least 12 characters>
   RELAY_PORT=8788
   ```
   Optional, same names as before: `FEEDBACK_KEY`, `FEEDBACK_CODES`, `PARTY_CODES`, `LOBBY=1`,
   `ALLOW_ANYONE_CREATE=1`. Leave `LOBBY` / `ALLOW_ANYONE_CREATE` out to switch them on the admin page instead.
   Use the plain `ADMIN_PASS`, not `ADMIN_PASS_HASH`: a `$` in a Portainer variable gets eaten.
3. **Deploy the stack**. Portainer builds the image from the repo (a minute the first time).
4. Check from a PC on your home network:
   - `http://<box LAN address>:8788/health` shows `{"ok": true, "version": "2.0.0-dev", ...}`
   - `http://<box LAN address>:8788/admin` shows the login. The admin page answers only home-network
     addresses (192.168.x.x / 10.x.x.x) on plain HTTP; from the box itself or the internet it says so and refuses.

The old v1 stack keeps running on 8787 meanwhile. Both can run side by side.

**Updating:** push to GitHub, then the stack's **Pull and redeploy**. Leave "Re-pull image" unticked:
the relay image is built from the repo on every deploy (`pull_policy: build`), not downloaded.
(If an older copy of the stack without that line fails with "pull access denied for classic-relay",
untick "Re-pull image" once; the redeploy then picks up the new compose file.)
The volume `classic-relay_relay-data` keeps the rooms. **Backup:** admin page > Overview > Download backup.

## 2. Switching from v1 to v2

When v2 looks fine:
1. Stop (or remove) the old v1 stack, which frees port 8787.
2. In the v2 stack set `RELAY_PORT=8787` and **Update the stack**.
Apps pointed at `:8787` now talk to v2: the `/v1` paths behave as before, and v2 builds of the app use rooms.
(Forward the same port on the router as before if friends connect from outside.)

## 3. HTTPS with a free DuckDNS name (later)

Needed for the admin page from outside your home, and so app traffic (room PINs, tokens) is encrypted.

1. Go to duckdns.org, sign in, and create a subdomain, e.g. `myrelay` -> `myrelay.duckdns.org`.
   It shows your current public IP; keep it updated with your router's DDNS setting (DuckDNS is often
   built in) or DuckDNS's own update script on the box.
2. On the router, forward TCP **80** and **443** to the Ubuntu box. Caddy needs both: 80 for the
   certificate check, 443 for HTTPS. Nothing else on the box may use 80/443.
3. In the stack's environment variables add:
   ```
   RELAY_DOMAIN=myrelay.duckdns.org
   TLS=1
   ```
   then **Update the stack**. Caddy gets a Let's Encrypt certificate by itself (watch its log for
   "certificate obtained").
4. Check from outside (phone on mobile data): `https://myrelay.duckdns.org/health`, then `/admin`.
   In the app the server becomes `https://myrelay.duckdns.org`.

How it stays safe: the relay trusts forwarded addresses only from Caddy's fixed address
(`PROXY_IP`, default `172.29.87.10` on the stack's own network `172.29.87.0/24`). If that network
clashes with another one on the box, set `RELAY_SUBNET` and `PROXY_IP` to another free range (both together).
Admin logins from the internet have their own lockout and can't lock you out at home.

## Behind your own reverse proxy (e.g. Nginx Proxy Manager)

Already run a reverse proxy with its own certificate? Point it at the relay instead of using Caddy:

1. Proxy host: scheme **http**, forward to `<box LAN address>` port `RELAY_PORT` (8788), websockets off.
   Pick your certificate on the SSL tab.
2. In the stack's environment variables add the address the proxy's requests come from:
   ```
   EXTRA_PROXIES=<proxy address>
   ```
   (Several: comma-separated; a range like `172.18.0.0/16` also works.) Then **Pull and redeploy**.

The relay then reads the real client address from the proxy's `X-Forwarded-For`, and the admin page
opens through the proxy's https address. Only list addresses that really are your proxy: anything
listed may claim to be any client.

Finding the address: for a proxy in Docker on the same box, it's usually its network's gateway
(Portainer > Networks > the proxy's network > Gateway, e.g. `172.18.0.1`) when it forwards to the
box's LAN address. A proxy on another machine: that machine's LAN address.
A request through the proxy to `/health` before setting it shows `"you": "<address>"` if unsure.

Note: the app checks certificates, so a self-made certificate (e.g. for a `.home` name) works in
the browser but not in the app. Point the app at `http://<box LAN address>:8788` on the home network.

## Creating rooms

Two ways, both on the relay itself:

- **Admin page > Rooms tab > Create room.** Fill the form and press Create. You get the room id and the
  *owner token* once; the token is the owner's key to manage the room from the app later, so copy it.
  The admin page ignores the "anyone can create" switch and the host-key limits (only `MAX_ROOMS` applies).
- **The public page `http://<relay address>/rooms`** (same address and port as the relay). Anyone who can
  reach the relay can open it, so friends can make their own rooms. It shows the room id, how friends join,
  and the owner token (once; the page also keeps it in that browser until they press Forget).

Who may create from the public page: if **Anyone can create rooms** is on (Overview > Switches, or
`ALLOW_ANYONE_CREATE=1`), everybody, with a small daily limit per address. If it is off, only people with a
**host key**: make one on the Host keys tab, give it to the friend, and they paste it in the "Host key" field.
Wrong host keys are limited too: 5 wrong tries from one address block creating from it for 10 minutes.
Owner tokens and host keys look like `slime-cleric-4821` (monster, job, 4 digits; not case sensitive) so they are easy to read out. They are short on purpose: wrong bearer tokens are limited per address (10 wrong -> 10 minutes). If someone abuses them, switch on **Long random tokens** (Overview > Switches, or `SECURE_TOKENS=1`): new ones are long random strings, existing ones keep working.
`/health` shows `"create_open": true/false` for the switch. The page is plain HTTP unless you put TLS in front,
so PINs and tokens travel unencrypted until then.

## Settings reference

| Variable | Default | What |
|---|---|---|
| `ADMIN_PASS` | empty (admin off) | admin page pass, 12+ characters |
| `RELAY_PORT` | 8788 | host port for plain HTTP |
| `LOBBY`, `ALLOW_ANYONE_CREATE`, `SECURE_TOKENS` | empty (admin page decides) | `1`/`0` fixes the switch; `SECURE_TOKENS=1` makes new owner tokens and host keys long random ones |
| `LEGACY_V1` | 1 | old `/v1` paths for app builds before v2 |
| `MAX_ROOMS`, `MAX_ROOMS_PER_ACCOUNT`, `MAX_ROOM_MEMBERS` | 500, 5, 0 | limits (0 = none) |
| `FEEDBACK_KEY`, `FEEDBACK_CODES` | empty | skill feedback inbox (empty key = off) |
| `PARTY_CODES`, `MAX_MEMBERS` | empty, 0 | old v1 party codes allowlist / size |
| `ADMIN_NETS` | 192.168.0.0/16,10.0.0.0/8 | who may open the admin page over plain HTTP |
| `TLS`, `RELAY_DOMAIN` | 0, empty | turn Caddy on for that domain |
| `RELAY_SUBNET`, `PROXY_IP` | 172.29.87.0/24, .10 | the stack's network and Caddy's address |

License: AGPLv3. People using your relay can ask for its source; `/health` links to the public repo.
