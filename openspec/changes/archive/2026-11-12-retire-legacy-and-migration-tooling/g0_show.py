"""g0_show 的显示部分：从 stdin 读 sqlite3 -json 的结果，打印一份产出的全文。

单独成文件，是因为多行 Python 嵌在单引号里的函数粘贴进终端时容易被截断（出现 `function pipe quote>`）。
"""
import json, sys, textwrap
raw = sys.stdin.read().strip()
if not raw: sys.exit("没有找到这份投影")
r = json.loads(raw)[0]
print(f"角色 {r["agent_role"]} · 范围 {r["scope_id"]} · 时点 {r["as_of"]} · 模型 {r["model_version"]} · 提示词 {r["prompt_version"]}")
for label, key in (("依赖的上游投影/材料", "input_refs"), ("依赖的数据版本", "data_vintage_refs")):
    refs = json.loads(r[key] or "[]")
    print(f"{label}: {len(refs)} 项", "；".join(str(x)[:60] for x in refs[:4]), "…" if len(refs) > 4 else "")
for k, v in json.loads(r["payload"]).items():
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)
    print(f"\n【{k}】"); print(textwrap.fill(text, 110) if isinstance(v, str) else text[:3000])
