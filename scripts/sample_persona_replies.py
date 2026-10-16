# -*- coding: utf-8 -*-
"""用新人设直接问 deepseek-flash 三句，人工看看口吻是否还带「人机感」。"""
import json, os, sqlite3, sys, urllib.request

sys.stdout.reconfigure(encoding="utf-8")

key = os.environ.get("DEEPSEEK_API_KEY", "")
if not key:
    import subprocess
    key = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"],
        capture_output=True, text=True).stdout.strip()
assert key, "no DEEPSEEK_API_KEY"

persona = sqlite3.connect("wechat-ai-stack/astrbot/data/data_v4.db").execute(
    "select system_prompt from personas where persona_id='1'").fetchone()[0]

sysmsg = {
    "role": "system",
    "content": persona + "\n\n每次回复至多三句话，简单问题保持 1~2 句；不要用 emoji 或颜文字；不要输出星号/括号动作描写；不要用 markdown。",
}

for q in ["在吗", "你今天干嘛呢", "你是AI吗，怎么说话这么奇怪"]:
    body = json.dumps({
        "model": "deepseek-flash",
        "messages": [sysmsg, {"role": "user", "content": q}],
        "temperature": 1.3,
        "max_tokens": 200,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=90))
        print("用户：" + q)
        print("用户E：" + d["choices"][0]["message"]["content"].strip())
        print("-" * 40)
    except Exception as e:
        print("ERR", e)
        break
