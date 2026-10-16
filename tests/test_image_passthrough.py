"""出站图片链路离线回归测试（不需要微信登录、不需要 WeFlow 在跑）。

覆盖三处真实修过的坑：
1. ``akasha-bridge/ob_protocol.py`` 的图片后缀嗅探 —— 历史上 GIF 被硬编码写成 ``xxx.png``；
2. ``scripts/wxui.py`` 的剪贴板分流 —— 动图必须走文件拖放（CF_HDROP）才能保住动画，
   静态图走位图；历史实现只有 ``Clipboard::SetImage``，动图粘出去只剩第一帧；
3. ``meme_manager`` 语义回退符号 + 默认资源包（防止「模型引用旧 ID → 静默丢弃」回归）。

跑法（用 bridge 的 venv，它带 Pillow）：

    & <stack>\\akasha-bridge\\.venv\\Scripts\\python.exe <stack>\\tests\\test_image_passthrough.py

退出码 0 全部通过，1 有失败项。
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(STACK, "akasha-bridge")
SCRIPTS = os.path.join(STACK, "scripts")
PLUGIN_DATA = os.path.join(STACK, "astrbot", "data", "plugin_data", "meme_manager")
ATRI_PACK = os.path.join(PLUGIN_DATA, "packs", "atri-meme-pack")
SELECTION_RULES = os.path.join(PLUGIN_DATA, "selection_rules.json")
EVENT_HANDLERS = os.path.join(
    STACK, "astrbot", "data", "plugins", "meme_manager", "mixins", "event_handlers.py"
)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    try:
        detail = fn() or ""
        RESULTS.append((name, True, str(detail)))
    except AssertionError as exc:
        RESULTS.append((name, False, str(exc)))
    except Exception as exc:  # noqa: BLE001 - 测试里任何异常都算失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- 1. 后缀嗅探
def test_suffix_sniffing():
    sys.path.insert(0, BRIDGE)
    if "config" not in sys.modules:
        sys.modules["config"] = load_module("config", os.path.join(BRIDGE, "config.py"))
    if "state" not in sys.modules:
        sys.modules["state"] = load_module("state", os.path.join(BRIDGE, "state.py"))
    ob = load_module("ob_protocol_under_test", os.path.join(BRIDGE, "ob_protocol.py"))
    cases = {
        b"GIF89a\x01\x00": ".gif",
        b"GIF87a\x01\x00": ".gif",
        b"\x89PNG\r\n\x1a\n": ".png",
        b"\xff\xd8\xff\xe0": ".jpg",
        b"BM\x00\x00": ".bmp",
        b"RIFF\x00\x00\x00\x00WEBPVP8 ": ".webp",
        b"not-an-image": ".png",  # 兜底
    }
    for data, expected in cases.items():
        got = ob._guess_image_suffix(data)
        assert got == expected, f"{data[:8]!r} 期望 {expected}，实际 {got}"

    # 端到端：base64 → 临时文件必须带正确后缀且内容一致
    raw = b"GIF89a" + b"\x00" * 20
    path = ob._decode_base64_image(base64.b64encode(raw).decode())
    try:
        assert path.endswith(".gif"), f"临时文件后缀错误：{path}"
        with open(path, "rb") as fh:
            assert fh.read() == raw, "临时文件内容与原始字节不一致"
    finally:
        os.unlink(path)
    return f"{len(cases)} 个魔数用例 + base64 落盘"


# ------------------------------------------------------- 2. 剪贴板分流（wxui）
def powershell51_probe(script: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout or "").strip()


def clipboard_state() -> dict:
    out = powershell51_probe(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f=[System.Windows.Forms.Clipboard]::GetFileDropList(); "
        "Write-Output ('files=' + $f.Count); "
        "foreach($i in $f){Write-Output ('path=' + $i)}; "
        "Write-Output ('image=' + [System.Windows.Forms.Clipboard]::ContainsImage())"
    )
    state = {"files": 0, "paths": [], "image": False}
    for line in out.splitlines():
        if line.startswith("files="):
            state["files"] = int(line.split("=", 1)[1] or 0)
        elif line.startswith("path="):
            state["paths"].append(line.split("=", 1)[1])
        elif line.startswith("image="):
            state["image"] = line.split("=", 1)[1].strip() == "True"
    return state


def pick_animated_gif() -> str:
    if not os.path.isdir(ATRI_PACK):
        raise AssertionError(f"找不到 atri 资源包目录：{ATRI_PACK}")
    best = None
    for dirpath, _dirnames, filenames in os.walk(ATRI_PACK):
        for fn in filenames:
            if fn.lower().endswith(".gif"):
                p = os.path.join(dirpath, fn)
                size = os.path.getsize(p)
                if 0 < size < 2_000_000 and (best is None or size < os.path.getsize(best)):
                    best = p
    if best is None:
        raise AssertionError("atri 资源包里没有可用的 GIF")
    return best


def test_clipboard_modes():
    wxui = load_module("wxui_under_test", os.path.join(SCRIPTS, "wxui.py"))
    gif = pick_animated_gif()

    from PIL import Image  # noqa: PLC0415 - 只在需要时依赖 Pillow

    tmp_png = os.path.join(tempfile.gettempdir(), "wxui_static_probe.png")
    Image.new("RGB", (8, 8), (255, 0, 0)).save(tmp_png, "PNG")

    assert wxui.is_animated_image(gif) is True, "动图 GIF 被判定成静态图"
    assert wxui.is_animated_image(tmp_png) is False, "静态 PNG 被判定成动图"

    ok, err = wxui.set_clipboard_image(gif)
    assert ok, f"动图放剪贴板失败：{err}"
    state = clipboard_state()
    assert state["files"] == 1, f"动图应走文件拖放（files=1），实际 files={state['files']}"
    assert os.path.normcase(state["paths"][0]) == os.path.normcase(gif), \
        f"剪贴板里的路径不对：{state['paths']}"

    ok, err = wxui.set_clipboard_image(tmp_png)
    assert ok, f"静态图放剪贴板失败：{err}"
    state = clipboard_state()
    assert state["files"] == 0, f"静态图不该走文件拖放，实际 files={state['files']}"
    assert state["image"] is True, "静态图应作为位图在剪贴板上"
    os.unlink(tmp_png)
    return f"动图={os.path.basename(gif)} → CF_HDROP；静态 PNG → 位图"


# ------------------------------------------------- 3. meme_manager 回退符号与默认包
def test_meme_fallback_wired():
    with open(EVENT_HANDLERS, "r", encoding="utf-8") as fh:
        src = fh.read()
    for symbol in ("_recover_rejected_semantic_ids", "_semantic_fallback_categories"):
        assert symbol in src, f"event_handlers.py 缺少回退实现：{symbol}"
    assert "meme_manager_semantic_query" in src, "回退检索没有复用本轮查询词"
    assert "validate_selected_id" in src, "回退路径绕过了候选校验（安全底线）"

    with open(SELECTION_RULES, "r", encoding="utf-8") as fh:
        rules = json.load(fh)
    default_rule = next(
        (
            r
            for r in rules.get("rules") or []
            if str(r.get("scope") or "") == "default" or str(r.get("id") or "") == "default"
        ),
        None,
    )
    assert default_rule is not None, "selection_rules.json 里没有 default 规则"
    default_pack = str(default_rule.get("pack_id") or "")
    assert default_pack == "atri-meme-pack", \
        f"默认资源包应指向语义化已完成的 atri-meme-pack，实际 {default_pack!r}"
    return f"回退实现 + 默认包 {default_pack}"


# ------------------------------------------- 4. 回读校验必须认「贴纸」类型（localType 47）
def test_verify_image_accepts_sticker_local_type():
    wxui = load_module("wxui_verify_under_test", os.path.join(SCRIPTS, "wxui.py"))
    assert 3 in wxui.IMAGE_LOCAL_TYPES, "图片 localType=3 必须在白名单里"
    assert 47 in wxui.IMAGE_LOCAL_TYPES, \
        "localType=47（微信自定义表情/贴纸）必须在白名单里，否则动图发出去会被判未送达并重复发送"

    fake_rows = [
        {"isSend": 1, "localId": 100, "localType": 1, "mediaType": None, "content": "旧文本"},
        {"isSend": 1, "localId": 101, "localType": 47, "mediaType": None, "content": "[表情]"},
    ]
    original = wxui.api
    wxui.api = lambda *a, **kw: {"messages": fake_rows}
    try:
        hit = wxui.verify_image({"dry_run": False}, "filehelper", __import__("time").time() + 2, 100)
    finally:
        wxui.api = original
    assert hit is not None and hit.get("localId") == 101, \
        "verify_image 没能把 localType=47 识别为图片/表情消息"

    def row(local_id, local_type, content, media=None):
        return {"isSend": 1, "localId": local_id, "localType": local_type,
                "mediaType": media, "content": content}

    name = "0521_EB35D86B71FCB24316C8E1C7D687A3A6.gif"
    appmsg = ('<msg><appmsg appid="wx6618f1cfc6c132f8"><title>' + name +
              '</title><type>6</type><appattach><totallen>110353</totallen></appattach></appmsg></msg>')

    fake_rows = [row(200, 25769803825, appmsg)]
    wxui.api = lambda *a, **kw: {"messages": fake_rows}
    try:
        hit = wxui.verify_image({"dry_run": False}, "filehelper",
                                __import__("time").time() + 2, 199, expect_name=name)
    finally:
        wxui.api = original
    assert hit is not None, "大 GIF 被微信当文件发出（appmsg/totallen）时，回读应判定已送达"

    fake_rows = [row(201, 25769803825, appmsg.replace(name, "别人的文件.pdf"))]
    wxui.api = lambda *a, **kw: {"messages": fake_rows}
    try:
        hit = wxui.verify_image({"dry_run": False}, "filehelper",
                                __import__("time").time() + 2, 199, expect_name=name)
    finally:
        wxui.api = original
    assert hit is None, "文件名不匹配的 appmsg 不该被当成图片送达（否则验证形同虚设）"

    # 大尺寸动图还有一个形态：appmsg <type>8</type>（表情上传），没有 title，只带 emoticonmd5。
    md5 = "96c261bb2a54c7f9cb11842ad2386bf4"
    emoticon = ('<msg><appmsg appid=""><title></title><type>8</type><appattach>'
                '<totallen>1535872</totallen><emoticonmd5>' + md5 + '</emoticonmd5>'
                '<fileext>gif</fileext></appattach></appmsg></msg>')
    fake_rows = [row(202, 34359738417, emoticon)]
    wxui.api = lambda *a, **kw: {"messages": fake_rows}
    try:
        hit = wxui.verify_image({"dry_run": False}, "filehelper",
                                __import__("time").time() + 2, 199,
                                expect_name="0091_摸头.gif", expect_md5=md5)
    finally:
        wxui.api = original
    assert hit is not None, "表情形态（appmsg type 8 + emoticonmd5）应按 md5 判定已送达"

    fake_rows = [row(203, 34359738417, emoticon)]
    wxui.api = lambda *a, **kw: {"messages": fake_rows}
    try:
        hit = wxui.verify_image({"dry_run": False}, "filehelper",
                                __import__("time").time() + 2, 199,
                                expect_name="0091_摸头.gif", expect_md5="0" * 32)
    finally:
        wxui.api = original
    assert hit is None, "md5 不匹配的表情不该算作自己这张已送达"
    return "localType 3/47 + appmsg 表情(8)/文件(6) 均被识别，无关消息不会误判"


def main() -> int:
    check("ob_protocol 图片后缀嗅探（GIF 不再写成 .png）", test_suffix_sniffing)
    check("wxui 剪贴板动图/静态图分流", test_clipboard_modes)
    check("meme_manager 语义回退接线 + 默认资源包", test_meme_fallback_wired)
    check("wxui 回读校验：贴纸(47) / 文件(appmsg) / 图片(3)", test_verify_image_accepts_sticker_local_type)

    width = max(len(name) for name, _ok, _d in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
        failed += 0 if ok else 1
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
