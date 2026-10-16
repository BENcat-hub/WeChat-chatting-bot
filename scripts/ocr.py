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
    file = await StorageFile.get_file_from_path_async(path)
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
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
