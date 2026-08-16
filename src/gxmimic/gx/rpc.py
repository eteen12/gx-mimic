"""Guitarix JSON-RPC 2.0 client: newline-delimited framing over a plain TCP
socket. Verified facts (guitarix-control.md): `params` MUST be a positional
array (named params -> server error -32000). RPC `set` is a silent no-op for
topology parameters on this guitarix build (0.46.0) -- see gx/chain writing
notes in preset.py / api.py for the dual write-path decision (D3); this
module only implements the wire protocol, not the write-path policy.
"""
from __future__ import annotations

import itertools
import json
import socket


class RpcError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class RpcClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7000, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._rfile = None
        self._wfile = None
        self._ids = itertools.count(1)

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        self._rfile = self._sock.makefile("rb")
        self._wfile = self._sock.makefile("wb")

    def close(self) -> None:
        for f in (self._rfile, self._wfile):
            try:
                if f:
                    f.close()
            except OSError:
                pass
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._rfile = None
        self._wfile = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def call(self, method: str, params: list | None = None):
        if not self.connected:
            raise ConnectionError("RpcClient.call() before connect()")
        req = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or []}
        line = (json.dumps(req) + "\n").encode("utf-8")
        self._wfile.write(line)
        self._wfile.flush()
        raw = self._rfile.readline()
        if not raw:
            raise ConnectionError("guitarix RPC connection closed by peer")
        resp = json.loads(raw.decode("utf-8"))
        if resp.get("error") is not None:
            e = resp["error"]
            raise RpcError(e.get("code"), e.get("message"), e.get("data"))
        return resp.get("result")

    # -- convenience wrappers over commonly used methods --------------------
    def getversion(self):
        return self.call("getversion")

    def getstate(self):
        return self.call("getstate")

    def banks(self):
        return self.call("banks")

    def setpreset(self, bank: str, preset: str):
        return self.call("setpreset", [bank, preset])

    def get(self, param_id: str):
        return self.call("get", [param_id])

    def get_parameter(self, param_id: str):
        return self.call("get_parameter", [param_id])

    def get_rack_unit_order(self, rack: int):
        return self.call("get_rack_unit_order", [rack])

    def parameterlist(self):
        return self.call("parameterlist")

    def set(self, param_id: str, value):
        """RPC `set` -- known to silently no-op for topology parameters on
        this guitarix build. Callers that need topology changes MUST use the
        file write path (gx/bank.py + relaunch) instead; see D3."""
        return self.call("set", [param_id, value])

    def shutdown(self):
        try:
            return self.call("shutdown")
        except (RpcError, ConnectionError, OSError):
            return None
