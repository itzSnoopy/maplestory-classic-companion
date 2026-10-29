"""Rebuild portainer-stack.yml from party_relay.py.

The relay code rides in an environment variable, so Portainer needs no upload or image build:
Stacks > Add stack > Web editor, paste the file, Deploy.

  python relay/make_stack.py           # the tracked file, every value empty (safe for git)
  python relay/make_stack.py --local   # your deployable copy with the feedback key + code filled in:
                                       # dev/local/relay/portainer-stack.local.yml (gitignored). The key and
                                       # code are made once and kept in dev/local/relay/feedback_host.json.
"""
import json
import secrets
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

HEAD = """# Portainer: Stacks > Add stack > Web editor, paste this whole file, Deploy.
# Generated from party_relay.py by make_stack.py (run it again after changing the relay).
services:
  party-relay:
    image: python:3.12-slim
    restart: unless-stopped
    user: nobody
    ports:
      - "8787:8787"
    environment:
      PORT: "8787"
      # comma list of allowed party codes; empty = any 6-64 character code
      PARTY_CODES: ""
      # players per code; 0 = no limit (a hangout of any size)
      MAX_MEMBERS: "0"
      # skill feedback inbox: owner key (empty = feedback disabled), optional comma list of codes
      FEEDBACK_KEY: ""
      FEEDBACK_CODES: ""
      RELAY_CODE: |
"""
TAIL = """    command: ["python3", "-u", "-c", "import os; exec(os.environ['RELAY_CODE'])"]
"""


LOCAL = HERE.parent / "dev" / "local" / "relay"


def host_secrets():
    """The feedback key + code for the player's relay, made once (local, gitignored)."""
    f = LOCAL / "feedback_host.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    abc = string.ascii_lowercase + string.digits
    d = {"code": "skills-" + "".join(secrets.choice(abc) for _ in range(8)),    # reviewers type this one
         "key": secrets.token_urlsafe(32)}                                       # only the owner: reads the inbox
    LOCAL.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")
    return d


def main():
    code = (HERE / "party_relay.py").read_text(encoding="utf-8").replace("$", "$$")
    body = "\n".join(("        " + line) if line.strip() else "" for line in code.splitlines())
    if "--local" in sys.argv:
        d = host_secrets()
        head = (HEAD.replace('FEEDBACK_KEY: ""', f'FEEDBACK_KEY: "{d["key"]}"')
                    .replace('FEEDBACK_CODES: ""', f'FEEDBACK_CODES: "{d["code"]}"')
                    .replace("# Portainer:", "# LOCAL COPY with secrets: never commit or share. Portainer:"))
        out = LOCAL / "portainer-stack.local.yml"
        out.write_text(head + body + "\n" + TAIL, encoding="utf-8")
        print(f"{out} written (feedback code + key from {LOCAL / 'feedback_host.json'})")
        return
    (HERE / "portainer-stack.yml").write_text(HEAD + body + "\n" + TAIL, encoding="utf-8")
    print("portainer-stack.yml updated")


if __name__ == "__main__":
    main()
