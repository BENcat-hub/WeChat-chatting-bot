"""离线校验 bridge 的图片段落通路（插件 → bridge → 发送器）。

模拟 AstrBot 的 send_private_msg，分别用表情包插件可能使用的两种 file 形式：
  1) 绝对路径
  2) base64://
断言 sender.send_image 收到的是真实存在、可读的图片文件，且联系人映射正确。
另外验证找不到的文件会被拒绝（不会瞎发）。
"""
import asyncio
import base64
import io
import os
import sys

BRIDGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "akasha-bridge"
)
sys.path.insert(0, BRIDGE)
os.chdir(BRIDGE)

import ob_protocol  # noqa: E402
import state  # noqa: E402

CONTACT = "文件传输助手"
UID = 777
MEME_DIR = os.path.join(
    os.path.dirname(BRIDGE),
    "astrbot", "data", "plugin_data", "meme_manager", "packs", "official-001", "memes",
)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(raw)


class FakeSender:
    def __init__(self):
        self.calls = []

    def send_text(self, contact, text):
        self.calls.append(("text", contact, text))
        return True

    def send_image(self, contact, path):
        self.calls.append(("image", contact, path))
        return True


def pick_meme():
    for root, _dirs, names in os.walk(MEME_DIR):
        for name in sorted(names):
            if name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                return os.path.join(root, name)
    raise SystemExit("no meme image found in " + MEME_DIR)


async def run_segment(file_val):
    fake = FakeSender()
    state.sender_instance = fake
    state._ob_id_to_contact[UID] = CONTACT
    state._ob_ws = FakeWS()
    await ob_protocol._handle_ob_api(
        {
            "action": "send_private_msg",
            "params": {
                "user_id": UID,
                "message": [{"type": "image", "data": {"file": file_val}}],
            },
            "echo": {"seq": 1},
        }
    )
    return fake.calls


def check(label, calls, expect_path=None):
    images = [c for c in calls if c[0] == "image"]
    if expect_path is None:
        ok = not images
        detail = "no image sent" if ok else f"unexpected send: {images}"
    else:
        ok = len(images) == 1 and images[0][1] == CONTACT
        if ok:
            sent = images[0][2]
            same = os.path.getsize(sent) == os.path.getsize(expect_path)
            ok = os.path.exists(sent) and same
            detail = f"sent={sent} bytes={os.path.getsize(sent)}"
        else:
            detail = f"calls={images}"
    print(("PASS  " if ok else "FAIL  ") + label + " :: " + detail)
    return ok


async def main():
    meme = pick_meme()
    print("meme:", meme, os.path.getsize(meme), "bytes")

    # bridge 会在发完后删掉 base64 解出的临时文件，这里先抄一份再返回
    captured = {}
    orig_decode = ob_protocol._decode_base64_image

    def spy_decode(b64_data):
        path = orig_decode(b64_data)
        if path:
            keep = os.path.join(os.path.dirname(BRIDGE), "logs", "b64-decode-check.png")
            with open(path, "rb") as src, open(keep, "wb") as dst:
                dst.write(src.read())
            captured["path"] = keep
            captured["size"] = os.path.getsize(path)
        return path

    ob_protocol._decode_base64_image = spy_decode

    results = []
    results.append(check("absolute path", await run_segment(meme), meme))

    with io.open(meme, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    calls = await run_segment("base64://" + b64)
    images = [c for c in calls if c[0] == "image"]
    ok = (
        len(images) == 1
        and images[0][1] == CONTACT
        and images[0][2].lower().endswith(".png")
        and captured.get("size") == os.path.getsize(meme)
        and os.path.getsize(captured["path"]) == os.path.getsize(meme)
    )
    print(
        ("PASS  " if ok else "FAIL  ")
        + f"base64:// ({len(b64)} chars) :: contact={images[0][1] if images else None} "
        + f"decoded={captured.get('size')} expected={os.path.getsize(meme)}"
    )
    results.append(ok)

    results.append(check("missing file is refused", await run_segment(r"C:\no\such\meme.gif")))
    results.append(check("empty file field is refused", await run_segment("")))

    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main()))
