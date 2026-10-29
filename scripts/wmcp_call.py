"""用 stdio JSON-RPC 驱动 Windows-MCP（用户要求：操作电脑走 windows-mcp 工具）。

用法：
    python scripts\\wmcp_call.py list                 # 列出工具
    python scripts\\wmcp_call.py app                  # App 工具：列窗口
    python scripts\\wmcp_call.py call Click '{"loc":[2131,838]}'
    python scripts\\wmcp_call.py shot --out logs/wmcp/probe/wm_shot.png
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
EXE = os.path.join(os.path.expanduser("~"), ".local", "bin", "windows-mcp.exe")


class MCP:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [EXE, "serve", "--transport", "stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.next_id = 0

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self, timeout: float = 60.0) -> dict | None:
        assert self.proc.stdout is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self.next_id += 1
        rid = self.next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            msg = self._read(timeout)
            if msg is None:
                return {"error": "timeout"}
            if msg.get("id") == rid:
                return msg

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str]) -> int:
    mcp = MCP()
    try:
        init = mcp.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "dsh-probe", "version": "0.1"},
        })
        print("initialize:", json.dumps(init.get("result", init), ensure_ascii=False)[:300])
        mcp.notify("notifications/initialized")

        cmd = argv[1] if len(argv) > 1 else "list"
        if cmd == "list":
            res = mcp.request("tools/list")
            tools = res.get("result", {}).get("tools", [])
            print(f"tools: {len(tools)}")
            for tool in tools:
                print(" -", tool.get("name"), "|", (tool.get("description") or "").splitlines()[0][:80])
            return 0

        if cmd == "call":
            name = argv[2]
            args = json.loads(argv[3]) if len(argv) > 3 else {}
            res = mcp.request("tools/call", {"name": name, "arguments": args}, timeout=120)
            print(json.dumps(res, ensure_ascii=False)[:3000])
            return 0

        if cmd == "app":
            res = mcp.request("tools/call", {"name": "App", "arguments": {"mode": "list"}}, timeout=120)
            print(json.dumps(res, ensure_ascii=False)[:4000])
            return 0

        if cmd == "move":
            x, y = int(argv[2]), int(argv[3])
            res = mcp.request("tools/call", {"name": "Move", "arguments": {"loc": [x, y]}}, timeout=120)
            print(json.dumps(res, ensure_ascii=False)[:800])
            cur = mcp.request("tools/call", {"name": "Screenshot", "arguments": {}}, timeout=120)
            for item in (cur.get("result", {}).get("content") or []):
                if item.get("type") == "text":
                    for line in item["text"].splitlines():
                        if "Cursor Position" in line:
                            print("after:", line.strip())
            return 0

        if cmd == "click":
            x, y = int(argv[2]), int(argv[3])
            res = mcp.request("tools/call", {"name": "Click", "arguments": {"loc": [x, y]}}, timeout=120)
            print(json.dumps(res, ensure_ascii=False)[:800])
            return 0

        if cmd == "type":
            text = argv[2]
            res = mcp.request("tools/call", {"name": "Type", "arguments": {"text": text}}, timeout=120)
            print(json.dumps(res, ensure_ascii=False)[:800])
            return 0

        if cmd == "shot":
            out = argv[argv.index("--out") + 1] if "--out" in argv else os.path.join(STACK, "logs", "wmcp", "probe", "wm_shot.png")
            res = mcp.request("tools/call", {"name": "Screenshot", "arguments": {}}, timeout=120)
            print(json.dumps(res, ensure_ascii=False)[:500])
            content = res.get("result", {}).get("content") or []
            for item in content:
                if item.get("type") == "image":
                    import base64
                    data = base64.b64decode(item["data"])
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    with open(out, "wb") as fh:
                        fh.write(data)
                    print("saved", out, len(data), "bytes")
            return 0

        print("unknown command", cmd)
        return 2
    finally:
        mcp.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
