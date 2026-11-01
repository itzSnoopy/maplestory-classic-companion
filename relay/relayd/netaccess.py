"""Who is really calling, and may they use the admin page.

If the TCP peer is a trusted proxy, the client is the right-most X-Forwarded-For address that is
not itself a trusted proxy. Otherwise forwarded headers are ignored.
"""
import ipaddress
from collections import namedtuple

DEFAULT_ADMIN_NETS = ("127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
                      "::1/128,fc00::/7,fe80::/10")

# ip: client address string, proxied: peer is a trusted proxy, via_tls: proxy said https
Client = namedtuple("Client", "ip proxied via_tls")


def parse_nets(text):
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            out.append(ipaddress.ip_network(part, strict=False))
    return out


def _addr(text):
    """Text -> ip_address (IPv4-mapped IPv6 becomes IPv4) or None."""
    try:
        a = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a


def _in(addr, nets):
    return any(addr.version == n.version and addr in n for n in nets)


class Access:
    def __init__(self, cfg):
        self.admin_nets = parse_nets(cfg.admin_nets)
        self.proxies = parse_nets(cfg.trusted_proxies)

    def resolve(self, peer, headers):
        p = _addr(peer)
        if p is None:
            return Client(peer, False, False)
        if not self.proxies or not _in(p, self.proxies):
            return Client(str(p), False, False)
        client = p
        xff = ",".join(headers.get_all("X-Forwarded-For") or [])
        for part in reversed(xff.split(",")):
            a = _addr(part)
            if a is None:
                break                       # junk entry: stop, keep the proxy address
            client = a
            if not _in(a, self.proxies):
                break
        tls = (headers.get("X-Forwarded-Proto") or "").strip().lower() == "https"
        return Client(str(client), True, tls)

    def admin_ok(self, c):
        """LAN over plain http, or any client that came through the proxy over https."""
        if c.proxied:
            return c.via_tls
        a = _addr(c.ip)
        return a is not None and _in(a, self.admin_nets)
