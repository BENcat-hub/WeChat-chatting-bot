# -*- coding: utf-8 -*-
"""把「微信口语标点 + 短句单独发」写进 private_companion 插件配置。

幂等；同时改 basic_config 分组键与扁平键（插件两边都会读）。
"""
import json
import pathlib

CFG = (
    pathlib.Path(__file__).resolve().parents[1]
    / "astrbot"
    / "data"
    / "config"
    / "astrbot_plugin_private_companion_config.json"
)

STYLE_SUFFIX = (
    "微信聊天习惯：句尾不加句号，句子之间用空格断句、不要写「，」；"
    "「行吧」「好的」「嗯」这类短应答单独发一条，不要和后面的句子连在一起。"
)
STYLE_MARK = "句尾不加句号"

SEG_PROMPT = (
    "你可以自行决定把本轮回复拆成多条消息发送，这是微信聊天的常态："
    "像「行吧」「好的」「嗯」「哈哈」「真的假的」这类短应答，或者需要停顿喘口气的短句，"
    "都单独占一条，按你说出口的顺序发。长文、教程、代码 等连续性较高的表述尽量少用分段。\n"
    "使用方法：每个消息边界都必须使用下面的完整标记： {{split_marker}}，"
    "并让它单独占一行，不能添加引号，也不能把它放进代码块。\n"
    "示例：行吧\n{{split_marker}}\n那我先去忙了\n{{split_marker}}\n晚点聊\n"
    "只有上述方法可以实现发送多条消息，换行和连续换行都不会作为多条消息发送。（仅在确有必要时使用）。"
)


def patch(container, prefix, changed):
    if not isinstance(container, dict):
        return
    style = container.get("reply_style_prompt")
    if isinstance(style, str) and STYLE_MARK not in style:
        container["reply_style_prompt"] = style.rstrip() + STYLE_SUFFIX
        changed.append(prefix + "reply_style_prompt")
    if "enable_wechat_punctuation_style" not in container:
        container["enable_wechat_punctuation_style"] = True
        changed.append(prefix + "enable_wechat_punctuation_style")
    seg = container.get("llm_controlled_segmenting_prompt")
    if isinstance(seg, str) and "短应答" not in seg:
        container["llm_controlled_segmenting_prompt"] = SEG_PROMPT
        changed.append(prefix + "llm_controlled_segmenting_prompt")


def main():
    data = json.loads(CFG.read_text(encoding="utf-8-sig"))
    changed = []
    patch(data, "", changed)
    patch(data.get("basic_config"), "basic_config.", changed)
    CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("hits:", len(changed))
    for item in changed:
        print(" -", item)


if __name__ == "__main__":
    main()
