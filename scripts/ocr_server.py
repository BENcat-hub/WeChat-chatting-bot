#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""常驻 RapidOCR（PP-OCRv4）服务：给 wxui.py 读微信截图用。

为什么要单独一个进程：
  * 微信 4.x 的窗口里没有 UIA 子控件，会话名/聊天标题只能靠 OCR 读；
  * Windows 自带 OCR（winsdk）对 12px 的「用户F」在侧栏、聊天头部**任何缩放都读不出**
    （固定读成 `丕忄尸`/直接漏行），这是引擎的字表上限，不是参数问题；
  * RapidOCR 在 1x 原图上就能读出「用户F」（score 0.9+），「示例群聊D」也不再把「试」读成「讠羊」。

为什么要常驻：onnxruntime + 模型加载约 2.5 s，wxui.py 每次发送只起一个短命进程；
把引擎放在独立进程里用命名管道复用，每次发送只付 ~0.4 s/帧的推理时间，而且
akasha-bridge 的 venv 不用装 onnxruntime/opencv（依赖留在 .venv-ocr）。

协议（Windows 命名管道，一行一个 JSON）：
  请求  {"id": 1, "path": "C:/.../x.png"}
  响应  {"id": 1, "ok": true, "engine": "rapidocr", "ms": 412,
         "lines": [{"text": "用户F", "x": 126, "y": 15, "w": 54, "h": 26,
                    "words": [{"t": "用户F", "x": 126, "y": 15, "w": 54, "h": 26}]}]}
  失败  {"id": 1, "ok": false, "error": "..."}

命令行：
  .venv-ocr\\Scripts\\python.exe scripts\\ocr_server.py --serve            # 常驻（wxui 会自动拉起）
  .venv-ocr\\Scripts\\python.exe scripts\\ocr_server.py --file x.png       # 单文件调试
"""
import argparse
import json
import os
import sys
import threading
import time
import traceback

AUTHKEY = b"wxui-ocr-v1"
DEFAULT_PIPE = r"\\.\pipe\wxui-ocr"
ROOT = os.path.dirname(os.path.abspath(__file__))

_ENGINE = None
_ENGINE_ERR = ""


def log(msg):
    line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(os.path.join(ROOT, "..", "logs", "ocr-server.log"), "a",
                  encoding="utf-8-sig") as fh:
            fh.write(line)
    except Exception:
        pass


def engine():
    """惰性加载 RapidOCR（首次 ~2.5 s），失败时把原因记下来返回 None。"""
    global _ENGINE, _ENGINE_ERR
    if _ENGINE is not None or _ENGINE_ERR:
        return _ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
        t = time.time()
        _ENGINE = RapidOCR()
        log("engine ready in %.1fs" % (time.time() - t))
    except Exception as exc:  # noqa: BLE001
        _ENGINE_ERR = "%s: %s" % (type(exc).__name__, exc)
        log("engine load failed: %s\n%s" % (_ENGINE_ERR, traceback.format_exc()))
        _ENGINE = None
    return _ENGINE


def _warmup():
    """跑一张小图，把 onnx 的首次分配算掉，别让第一条真实请求付这份时间。"""
    try:
        from PIL import Image
        p = os.path.join(ROOT, "..", "logs", "ocr-warmup.png")
        img = Image.new("RGB", (200, 60), (255, 255, 255))
        img.save(p)
        eng = engine()
        if eng is not None:
            eng(p)
            log("warmup ok")
        try:
            os.remove(p)
        except OSError:
            pass
    except Exception:  # noqa: BLE001
        log("warmup failed: %s" % traceback.format_exc())


def _lines_from(result):
    lines = []
    for item in result or []:
        try:
            box, text, score = item[0], item[1], float(item[2])
        except Exception:  # noqa: BLE001
            continue
        text = (text or "").strip()
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x, y = int(min(xs)), int(min(ys))
        w, h = int(max(xs)) - x, int(max(ys)) - y
        lines.append({"text": text, "x": x, "y": y, "w": w, "h": h,
                      "score": round(score, 4),
                      "words": [{"t": text, "x": x, "y": y, "w": w, "h": h}]})
    return lines


def ocr_file(path):
    eng = engine()
    if eng is None:
        return {"ok": False, "error": "rapidocr unavailable: %s" % (_ENGINE_ERR or "not loaded")}
    if not os.path.exists(path):
        return {"ok": False, "error": "no such file: %s" % path}
    t = time.time()
    try:
        result, _elapse = eng(path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "engine": "rapidocr", "ms": int((time.time() - t) * 1000),
            "lines": _lines_from(result)}


def _serve(pipe_name, idle_s):
    import queue
    from multiprocessing.connection import Listener
    engine()  # 先把模型加载好，客户端第一次连上就能用
    _warmup()
    listener = Listener(pipe_name, family="AF_PIPE", authkey=AUTHKEY)
    log("listening on %s (idle exit %ss)" % (pipe_name, idle_s))
    # accept 没有超时参数；放到线程里、用队列唤醒主循环。
    # 2026-10-29 实测：早先版本主循环 time.sleep(0.5) 轮询，导致**每条连接都要多等
    # 0~0.5s**（ocr 只要 0.3s，往返却 0.8s）。队列 get(timeout) 一到就醒。
    inbox = queue.Queue()

    def _accept_loop():
        while True:
            try:
                inbox.put(listener.accept())
            except Exception:  # noqa: BLE001
                return

    threading.Thread(target=_accept_loop, daemon=True).start()
    last = time.time()
    while True:
        try:
            conn = inbox.get(timeout=0.5)
        except queue.Empty:
            if idle_s and time.time() - last > idle_s:
                log("idle for %ss, exiting" % idle_s)
                try:
                    listener.close()
                except Exception:  # noqa: BLE001
                    pass
                return 0
            continue
        last = time.time()
        try:
            while True:
                try:
                    req = conn.recv()
                except (EOFError, OSError):
                    break
                if not isinstance(req, dict):
                    continue
                rid = req.get("id")
                path = req.get("path") or ""
                res = ocr_file(path)
                res["id"] = rid
                try:
                    conn.send(res)
                except (EOFError, OSError):
                    break
                last = time.time()
        except Exception as exc:  # noqa: BLE001
            log("serve error: %s\n%s" % (exc, traceback.format_exc()))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="常驻服务（命名管道）")
    ap.add_argument("--file", help="单文件调试：OCR 一张图并打印 JSON")
    ap.add_argument("--pipe", default=os.environ.get("WXUI_OCR_PIPE") or DEFAULT_PIPE)
    ap.add_argument("--idle", type=int, default=int(os.environ.get("WXUI_OCR_IDLE") or 1800),
                    help="空闲多少秒退出（0 = 不退出）")
    args = ap.parse_args()
    if args.file:
        print(json.dumps(ocr_file(os.path.abspath(args.file)), ensure_ascii=False))
        return 0
    if args.serve:
        return _serve(args.pipe, args.idle)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
