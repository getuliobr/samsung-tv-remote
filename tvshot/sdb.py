"""Minimal Tizen SDB client (ADB-compatible wire protocol) — no Tizen Studio needed.

The TV must be in Developer Mode with this computer's IP set as the Host PC IP.
"""
from __future__ import annotations

import socket
import struct
import time

A_CNXN, A_OPEN, A_OKAY, A_CLSE, A_WRTE = (int.from_bytes(c, "little") for c in
                                          (b"CNXN", b"OPEN", b"OKAY", b"CLSE", b"WRTE"))
VERSION = 0x01000000
MAX_PAYLOAD = 4096
SDB_PORT = 26101


class SDB:
    def __init__(self, host: str, port: int = SDB_PORT, timeout: float = 15):
        self.host = host
        self.next_id = 1
        for attempt in range(6):  # the TV sometimes resets back-to-back connections
            try:
                self.sock = socket.create_connection((host, port), timeout=timeout)
                self._send(A_CNXN, VERSION, MAX_PAYLOAD, b"host::\x00")
                cmd, a0, a1, data = self._recv()
                break
            except (ConnectionResetError, ConnectionError):
                time.sleep(1.5)
        else:
            raise ConnectionError("sdb handshake failed")
        if cmd != A_CNXN:
            raise ConnectionError(f"unexpected reply {cmd:#x}")
        self.banner = data.decode(errors="ignore")
        self.max_payload = min(a1 or 4096, MAX_PAYLOAD)

    # ---- packet layer ----
    def _send(self, cmd: int, a0: int, a1: int, data: bytes = b"") -> None:
        hdr = struct.pack("<6I", cmd, a0, a1, len(data), sum(data) & 0xFFFFFFFF, cmd ^ 0xFFFFFFFF)
        self.sock.sendall(hdr + data)

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("sdb connection closed")
            buf += chunk
        return buf

    def _recv(self):
        cmd, a0, a1, length, _crc, _magic = struct.unpack("<6I", self._read_exact(24))
        data = self._read_exact(length) if length else b""
        return cmd, a0, a1, data

    # ---- streams ----
    def _open(self, service: str) -> tuple[int, int]:
        local = self.next_id
        self.next_id += 1
        self._send(A_OPEN, local, 0, service.encode() + b"\x00")
        while True:
            cmd, a0, a1, _ = self._recv()
            if cmd == A_OKAY and a1 == local:
                return local, a0
            if cmd == A_CLSE and a1 == local:
                raise ConnectionError(f"service refused: {service}")

    def shell(self, command: str, timeout: float = 120) -> str:
        """Run `shell:<command>` and return all output (TV only allows `0 <cmd>` forms)."""
        local, remote = self._open(f"shell:{command}")
        out = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            cmd, a0, a1, data = self._recv()
            if a1 != local:
                continue
            if cmd == A_WRTE:
                out += data
                self._send(A_OKAY, local, remote)
            elif cmd == A_CLSE:
                self._send(A_CLSE, local, remote)
                break
        return out.decode(errors="ignore")

    def _write_stream(self, local: int, remote: int, data: bytes) -> None:
        self._send(A_WRTE, local, remote, data)
        while True:  # wait for OKAY (flow control)
            cmd, a0, a1, d = self._recv()
            if a1 != local:
                continue
            if cmd == A_OKAY:
                return
            if cmd == A_WRTE:  # sync status arriving early
                self._pending = getattr(self, "_pending", b"") + d
                self._send(A_OKAY, local, remote)
            if cmd == A_CLSE:
                raise ConnectionError("stream closed during write")

    def push(self, remote_path: str, data: bytes, mode: int = 0o644) -> None:
        local, remote = self._open("sync:")
        self._pending = b""
        spec = f"{remote_path},{mode}".encode()
        self._write_stream(local, remote, b"SEND" + struct.pack("<I", len(spec)) + spec)
        step = self.max_payload - 8
        for i in range(0, len(data), step):
            chunk = data[i:i + step]
            self._write_stream(local, remote, b"DATA" + struct.pack("<I", len(chunk)) + chunk)
        self._write_stream(local, remote, b"DONE" + struct.pack("<I", int(time.time())))
        status = self._pending
        while len(status) < 8:
            cmd, a0, a1, d = self._recv()
            if a1 == local and cmd == A_WRTE:
                status += d
                self._send(A_OKAY, local, remote)
            elif a1 == local and cmd == A_CLSE:
                break
        if not status.startswith(b"OKAY"):
            raise IOError(f"push failed: {status!r}")
        self._send(A_WRTE, local, remote, b"QUIT" + b"\x00" * 4)
        self._send(A_CLSE, local, remote)

    def close(self):
        self.sock.close()


if __name__ == "__main__":
    import sys
    s = SDB(sys.argv[1])
    print("banner:", s.banner)
    print(s.shell(" ".join(sys.argv[2:]) or "0 getduid"))
