# -*- coding: utf-8 -*-
"""生图链路用的本地假上游（OpenAI 兼容外壳 + SiliconFlow 响应形态）。

用途：在拿不到真 key 时验证 `jimeng_image_server.py(:8790) → image_companion → 微信`
整条链路是否通畅。只模仿被调用到的字段，不做真实出图。

    python scripts/_mock_jimeng.py --port 8791

被 jimeng_image_server 调用时用它覆写上游地址：
    SILICONFLOW_API_BASE=http://127.0.0.1:8791/v1

响应形态来自 scripts/jimeng_image_server.py:305 `payload.get("images")[].url`
（SiliconFlow 规范，不是 OpenAI 的 data[]/b64_json）。
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 1x1 纯色 PNG，避免依赖 Pillow
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _png(w: int, h: int, rgb=(200, 220, 255)) -> bytes:
    """生成 w*h 纯色 PNG（无第三方依赖）。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认日志
        print(f"[mock] {self.address_string()} {fmt % args}", flush=True)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/img"):
            host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_port}"
            body = _png(512, 512)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/v1/models"):
            self._json(200, {"data": [{"id": "mock-image", "object": "model"}]})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            req = {}
        if self.path.startswith("/v1/images/generations"):
            n = max(1, min(4, int(req.get("n") or 1)))
            host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_port}"
            urls = [f"http://{host}/img/{int(time.time() * 1000)}-{i}.png"
                    for i in range(n)]
            self._json(200, {
                "images": [{"url": u} for u in urls],
                "shared_id": f"mock-{int(time.time())}",
                "timings": {"inference": 0.0},
                "mock": True,
                "echo": {"prompt": str(req.get("prompt"))[:80],
                         "model": req.get("model")},
            })
            return
        self._json(404, {"error": "not found"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[mock] listening http://{args.host}:{args.port}/v1", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
