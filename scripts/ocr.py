#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Windows OCR (winsdk) -> JSON lines with boxes, for reading WeChat's sidebar.

WeChat's Qt window exposes no UI-automation children, so a session-list row can
only be located by reading the screenshot. Windows' built-in OCR (winsdk) does
that offline; zh-Hans is used when installed.

Usage: python ocr.py <image.png>
"""
import asyncio
import json
import sys

from winsdk.windows.globalization import Language
from winsdk.windows.graphics.imaging import BitmapDecoder
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.storage import FileAccessMode, StorageFile
from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream


async def _open_decoder(path):
    """Open a PNG for decoding without going through the WinRT file broker.

    2026-10-24：`StorageFile.get_file_from_path_async` 开始一律抛
    `PermissionError [WinError -2147024891] 拒绝访问`（进程令牌不允许使用 WinRT
    文件代理时就是这样，实测连 %TEMP% 下的文件也读不了），于是语音气泡在
    `ocr.py:32` 炸掉、回落成「语音回复_xxxx.wav」文件卡片。自己读字节再用
    `InMemoryRandomAccessStream` 解码完全不经过代理，实测同一张图能正常 OCR。
    读不到文件时才退回旧的火腿代理路径。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        data = b""
    if data:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(data)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        return await BitmapDecoder.create_async(stream)
    handle = await StorageFile.get_file_from_path_async(path)
    stream = await handle.open_async(FileAccessMode.READ)
    return await BitmapDecoder.create_async(stream)


async def image_size(path):
    """(width, height) of an image, using the broker-free decoder above."""
    decoder = await _open_decoder(path)
    return int(decoder.pixel_width), int(decoder.pixel_height)


async def run(path, language="zh-Hans-CN"):
    available = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    engine = None
    try:
        engine = OcrEngine.try_create_from_language(Language(language))
    except Exception:
        engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return {"ok": False, "error": "no OCR engine", "available": available}
    decoder = await _open_decoder(path)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = [{"t": w.text, "x": int(w.bounding_rect.x), "y": int(w.bounding_rect.y),
                  "w": int(w.bounding_rect.width), "h": int(w.bounding_rect.height)}
                 for w in line.words]
        lines.append({"text": line.text,
                      "x": min((w["x"] for w in words), default=0),
                      "y": min((w["y"] for w in words), default=0),
                      "words": words})
    return {"ok": True, "engine": engine.recognizer_language.language_tag,
            "available": available, "lines": lines}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    print(json.dumps(asyncio.run(run(sys.argv[1])), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
