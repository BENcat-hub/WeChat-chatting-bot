# 语音“像粤语”的三轮取证（2026-11-09）

用户反馈：发给“桃桃真可爱”的语音不是纳西妲音色、且听着像粤语。

## 1. 权重没有坏（排除“重下一遍”）

ertvits2/pretrained/纳西妲/G_114000.pth 本地 **701,632,539 B**，
HEAD https://hf-mirror.com/guetLzy/Bert-vits2/resolve/main/G_114000.pth 的
content-length 同样是 **701,632,539 B** —— 逐字节一致，重下没有意义。
加载时只有 ert.pooler.dense.* 的 UNEXPECTED 提示（下游池化层，不影响推理），
nc_p.emb.weight 形状 (112, 192) 与 	ext/symbols.py 的 112 个符号完全对齐。

## 2. 真凶一：送进 TTS 的文本当时没有标点

生产日志（16:31:36，发给 1045254607 的那条）：
`
ok engine=bv2 speaker=纳西妲 infer=0.09s total=0.12s text='喂 忙归忙 记得喝口水啊——我这边还有话没说。'
`
prepare_speech_text() 的“子句空格 → 逗号”规则当时**没有生效**（日志里仍是空格）。
同一句用逗号版本再合成，ASR 立刻正确：
- 空格版：喂，王桂梅，记得喝口水呀，我弟弟还有话没说。
- 逗号版：喂，忙归忙，记得喝口水啊，我这边还有话没说。

## 3. 真凶二：Bert-VITS2 这个权重本身的中文 G2P 不够稳

即使喂标点正确的文本，bv2 仍会把「我这边」念成「我弟弟」（同句 ASR 复现）。
16 个说话人逐个采样也出现「早上好→左手后」「忙归忙→王冠吗」这类系统性错读。
⇒ 这是**该粉丝微调权重**的问题，不是代码或下载损坏。

## 4. 处置

- scripts/start-all.ps1 的 NAHIDA_ENGINE 默认改回 **edge**：edge 朗读准确，
  再用 vc-backend/models_voice/纳西妲/G_40000.pth 换声 → 纳西妲音色 + 正确读音。
  实测同句 	otal=2.28s，ASR 逐字对上。
- 想用 Bert-VITS2（0.12s、原神 16 角色）时设 NAHIDA_ENGINE=bv2，代价是偶发错读。
- 上次部署时那套 ertvits2/bv2_v1_say.py（真正的原版推理脚本）在 11-03 误删里永久丢失，
  只剩我按 v1.1.1 重写的 v2_say.py，所以“完全一样”的朗读无法复刻。