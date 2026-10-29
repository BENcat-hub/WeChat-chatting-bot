#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Minimal Windows-MCP stdio client (JSON-RPC over newline-delimited stdio).

Why: this DSH session exposes no windows-mcp MCP tools, so we drive the
installed `windows-mcp.exe` ourselves and use its Snapshot/Click/Type/Shortcut
tools for reliable WeChat window control (much better than blind SendKeys).

Usage:
  python wmcp.py list
  python wmcp.py call Snapshot '{"use_ui_tree": true}'
  python wmcp.py call Screenshot '{}'
  python wmcp.py call Type '{"text":"hello","press_enter":true}'
  python wmcp.py call Click '{"loc":[100,200]}'
Output: JSON on stdout (tool results pretty-printed).
"""
import base64
import json
import os
import subprocess
import sys
import time

EXE = r"C:\Users\<YOU>\.local\bin\windows-mcp.exe"
PROTOCOL = "2025-06-18"
# 2026-10-29：wxui.py 现在由 bridge 用 CREATE_NO_WINDOW 启动（没有控制台），它再拉起
# windows-mcp.exe 时如果不带这个标志，Windows 会为这个 console 子系统进程新建控制台窗口
# ——用户看到的就是「点开微信闪一个黑框」。这里保持一致，纯 stdio 通信不需要控制台。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class Client:
    def __init__(self, debug=False):
        self.debug = debug
        self.proc = subprocess.Popen(
            [EXE, "serve", "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
        self.next_id = 0
        init = self.rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "dsh-lead", "version": "0.1"},
            },
        )
        self.server_info = init
        self.notify("notifications/initialized", {})

    def _send(self, obj):
        line = json.dumps(obj, ensure_ascii=False)
        if self.debug:
            sys.stderr.write(">> " + line + "\n")
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def rpc(self, method, params=None):
        self.next_id += 1
        rid = self.next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                err = ""
                try:
                    err = self.proc.stderr.read() or ""
                except Exception:
                    pass
                raise SystemExit("windows-mcp server closed stdout. stderr tail:\n" + err[-2000:])
            line = line.strip()
            if not line:
                continue
            if self.debug:
                sys.stderr.write("<< " + line[:500] + "\n")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise SystemExit("MCP error: " + json.dumps(msg["error"], ensure_ascii=False))
                return msg.get("result")

    def list_tools(self):
        return self.rpc("tools/list", {}).get("tools", [])

    def call(self, name, args):
        return self.rpc("tools/call", {"name": name, "arguments": args})

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    c = Client(debug="--debug" in sys.argv)
    try:
        if cmd == "list":
            tools = c.list_tools()
            for t in tools:
                print(t.get("name"), "|", (t.get("description") or "").splitlines()[0][:120])
            print("\n# server:", json.dumps(c.server_info, ensure_ascii=False)[:300])
            return 0
        if cmd == "schema":
            tools = c.list_tools()
            for t in tools:
                if len(sys.argv) < 3 or t.get("name") == sys.argv[2]:
                    print(json.dumps(t, ensure_ascii=False, indent=2))
            return 0
        if cmd == "call":
            name = sys.argv[2]
            args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            res = c.call(name, args)
            texts, images = [], []
            for item in (res or {}).get("content") or []:
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "wmcp")
                    os.makedirs(outdir, exist_ok=True)
                    path = os.path.abspath(os.path.join(outdir, "%s-%d-%d.png" % (name, time.time(), len(images))))
                    with open(path, "wb") as fh:
                        fh.write(base64.b64decode(item.get("data") or ""))
                    images.append(path)
            if texts:
                print("\n".join(texts))
            for p in images:
                print("IMAGE SAVED: %s (%d B)" % (p, os.path.getsize(p)))
            if not texts and not images:
                print(json.dumps(res, ensure_ascii=False)[:4000])
            return 0
    finally:
        c.close()
    print("unknown command: " + cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
