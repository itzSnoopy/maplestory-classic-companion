"""Minimal Discord Rich Presence client (local IPC pipe, no extra packages).

Talks only to the Discord desktop app on this PC through \\\\.\\pipe\\discord-ipc-N.
Sends text (level, map, ...) and nothing else: no screenshots, no game data files.
Only used when helper_settings.json → discord.enabled is true.
"""
import json
import os
import struct
import threading
import time
import uuid

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2
MIN_SEND_GAP_S = 15          # Discord allows ~5 updates / 20 s; stay well under
RETRY_S = 30


class IPC:
    def __init__(self, client_id):
        self.client_id = client_id
        self.pipe = None

    def connect(self):
        last = None
        for i in range(10):
            try:
                self.pipe = open(rf"\\.\pipe\discord-ipc-{i}", "r+b", buffering=0)
                break
            except OSError as e:
                last = e
        if self.pipe is None:
            raise ConnectionError(f"Discord not running ({last})")
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": str(self.client_id)})
        op, data = self._recv()
        if data.get("evt") != "READY":
            self.close()
            raise ConnectionError(f"handshake refused: {data.get('message') or data}")

    def _send(self, op, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.pipe.write(struct.pack("<II", op, len(raw)) + raw)

    def _recv(self):
        head = self.pipe.read(8)
        if len(head) < 8:
            raise ConnectionError("pipe closed")
        op, n = struct.unpack("<II", head)
        return op, json.loads(self.pipe.read(n).decode("utf-8"))

    def set_activity(self, activity):
        self._send(OP_FRAME, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                              "args": {"pid": os.getpid(), "activity": activity}})
        op, data = self._recv()
        if data.get("evt") == "ERROR":
            raise RuntimeError(data.get("data", {}).get("message", "SET_ACTIVITY failed"))

    def close(self):
        if self.pipe:
            try:
                self._send(OP_CLOSE, {})
            except Exception:
                pass
            try:
                self.pipe.close()
            except Exception:
                pass
        self.pipe = None


class Presence:
    """Background worker. The UI calls update(client_id, activity) as often as it likes;
    the worker sends only changes, rate-limited, and reconnects if Discord restarts."""

    def __init__(self):
        self.lock = threading.Lock()
        self.want = None            # (client_id, activity) or None = presence off
        self.status = "off"
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()

    def update(self, client_id, activity):
        with self.lock:
            self.want = (client_id, activity) if client_id else None

    def disable(self):
        with self.lock:
            self.want = None

    def stop(self):
        self._stop = True

    def _run(self):
        ipc, sent, last_send, last_try = None, object(), 0.0, 0.0
        while not self._stop:
            time.sleep(1)
            with self.lock:
                want = self.want
            try:
                if want is None:
                    if ipc:
                        ipc.close()           # closing the pipe clears the presence
                        ipc, sent = None, object()
                    self.status = "off"
                    continue
                cid, act = want
                if ipc and ipc.client_id != cid:
                    ipc.close()
                    ipc, sent = None, object()
                if ipc is None:
                    if time.time() - last_try < RETRY_S:
                        continue
                    last_try = time.time()
                    ipc = IPC(cid)
                    ipc.connect()
                    self.status = "connected"
                if act != sent and time.time() - last_send >= MIN_SEND_GAP_S:
                    ipc.set_activity(act)
                    sent, last_send = act, time.time()
                    self.status = "connected"
            except Exception as e:
                self.status = f"error: {e}"
                if ipc:
                    ipc.close()
                ipc, sent = None, object()
        if ipc:
            ipc.close()
