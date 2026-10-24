"""Rebuild portainer-stack.yml from party_relay.py.

The relay code rides in an environment variable, so Portainer needs no upload or image build:
Stacks > Add stack > Web editor, paste the file, Deploy.

  python relay/make_stack.py
"""
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
      RELAY_CODE: |
"""
TAIL = """    command: ["python3", "-u", "-c", "import os; exec(os.environ['RELAY_CODE'])"]
"""


def main():
    code = (HERE / "party_relay.py").read_text(encoding="utf-8").replace("$", "$$")
    body = "\n".join(("        " + line) if line.strip() else "" for line in code.splitlines())
    (HERE / "portainer-stack.yml").write_text(HEAD + body + "\n" + TAIL, encoding="utf-8")
    print("portainer-stack.yml updated")


if __name__ == "__main__":
    main()
