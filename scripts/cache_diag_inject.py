"""伪造一条微信私聊入站消息，走完整的 AstrBot + private_companion 管线。

用途：缓存命中诊断——对比两次真实主链请求落到 provider 层的 system 快照。
安全性：
- 用自己的 OneBot 客户端连接 AstrBot 的反向 WS，self_id 与真实 bridge 不同，
  因此不会顶掉 bridge 的连接；回复只会作为 action 发回本连接，不会真的发到微信。
- user_id 用假联系人，不污染真实用户的会话历史。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1] / "akasha-bridge"
sys.path.insert(0, str(BRIDGE))

from state import stable_int  # noqa: E402  (bridge 里的稳定 id 工具)
from ob_protocol import make_message_event  # noqa: E402

WS_URL = "ws://127.0.0.1:11229/ws"
FAKE_SELF = stable_int("self", "cache-diag-client")
FAKE_USER = stable_int("user", "cache-diag-contact")


async def main() -> None:
    import websockets

    headers = {
        "X-Self-ID": str(FAKE_SELF),
        "X-Client-Role": "Event",
        "User-Agent": "cache-diag/1.0",
    }
    async with websockets.connect(
        WS_URL, additional_headers=headers, max_size=8 << 20
    ) as ws:
        print(f"[ok] connected self_id={FAKE_SELF} user_id={FAKE_USER}", flush=True)

        async def reader() -> None:
            while True:
                try:
                    raw = await ws.recv()
                except Exception:
                    return
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                act = data.get("action") or data.get("post_type")
                if data.get("action"):
                    # 回复会以 action 形式发过来；直接回 echo，避免 AstrBot 等超时
                    print(f"[action] {data.get('action')} {json.dumps(data.get('params'), ensure_ascii=False)[:160]}", flush=True)
                    if "echo" in data:
                        await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": data["echo"]}))
                else:
                    print(f"[event] {act}", flush=True)

        rtask = asyncio.create_task(reader())

        for idx, text in enumerate(["今天天气不错啊", "我下午想去公园走走", "你猜我现在在哪", "算了不猜了"], start=1):
            ev = make_message_event(
                "private",
                FAKE_USER,
                [{"type": "text", "data": {"text": text}}],
                nickname="诊断联系人",
                message_id=int(time.time() * 1000) % 2147483647,
            )
            await ws.send(json.dumps(ev, ensure_ascii=False))
            print(f"[sent] #{idx} {text}", flush=True)
            await asyncio.sleep(18)

        await asyncio.sleep(10)
        rtask.cancel()
        try:
            await ws.close()
        except Exception:
            pass
        print("[done]", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
