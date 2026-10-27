#!/usr/bin/env python3
"""离线仿真：直接执行剧本 DSL 里的 Code 节点，并调用真实 PowerContext HTTP API，
走完三位客人的完整对局（开场 → 追问 ×2 → 上酒 → 开票 → 下一位 → 结局）。

用途：在没有 Dify（或想快速回归）时验证「Code 节点逻辑 + PowerContext 读写」这一层。
需要：Python 3.10+、PyYAML、可访问的 PowerContext（默认 http://127.0.0.1:17429）。

运行：
    PC_BASE=http://127.0.0.1:17429 python3 scripts/test_offline.py
"""
import json
import os
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PC = os.environ.get("PC_BASE", "http://127.0.0.1:17429")


def _find_dsl():
    """依次尝试：环境变量 DSL_PATH → scripts/../DSL/memory-pawnshop.yml → 同目录 memory-pawnshop.yml。"""
    candidates = [
        os.environ.get("DSL_PATH", ""),
        os.path.join(HERE, "..", "DSL", "memory-pawnshop.yml"),
        os.path.join(HERE, "memory-pawnshop.yml"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    raise SystemExit("找不到 DSL：请设置 DSL_PATH 或把 memory-pawnshop.yml 放到 ../DSL/")


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PC + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def main():
    with open(_find_dsl(), encoding="utf-8") as f:
        dsl = yaml.safe_load(f)
    nodes = {n["data"]["title"]: n for n in dsl["workflow"]["graph"]["nodes"]}

    def code_of(title):
        src = nodes[title]["data"]["code"]
        ns = {}
        exec(compile(src, title, "exec"), ns)
        return ns["main"]

    def fake_llm(line, consistency=1, cited_archive=None, cited_memory=None, delta=0):
        return json.dumps({"line": line, "consistency": consistency,
                           "cited_archive": cited_archive or [],
                           "cited_memory": cited_memory or [], "suspicion_delta": delta},
                          ensure_ascii=False)

    st, scopes = http("GET", "/v1/scopes")
    scopes_body = json.dumps(scopes, ensure_ascii=False)
    print("scopes:", [(i.get("title"), i.get("scope_id")) for i in scopes.get("items", [])])

    ci = code_of("Code·意图规范化")
    talk = code_of("Code·台词与判定")
    plant = code_of("Code·写入结果")
    settle = code_of("Code·开票")
    advance = code_of("Code·推进")
    menu = code_of("Code·防跑题")

    state = ""
    # 1) 开场
    r = ci(state, '{"action":"advance"}', scopes_body, "开门")
    state, action = r["state"], r["action"]
    print("\naction:", action, "| scopes resolved:", len(json.loads(state)["scopes"]))
    r = advance(state, "开门", scopes_body)
    state = r["state"]
    print(r["answer_text"])
    print("=" * 60)

    for _gidx in range(3):
        # 2) 两次追问（真实召回 + 真实档案检索）
        for _rnd in range(2):
            r = ci(state, '{"action":"talk"}', scopes_body, "你那天到底看见了什么？")
            state = r["state"]
            stage = json.loads(state)
            recite = {"scope_id": stage["scopes"][stage["current"]["id"]],
                      "query": "那天 那晚 火 河", "mode": "hybrid", "limit": 6}
            _, recall = http("POST", "/v1/atomic-memory/search", recite)
            arch = json.loads(r["archive_json"])
            _, archives = http("POST", "/v1/atomic-memory/search", arch)
            print(f"[recall hits={len(recall.get('hits', []))} archive hits={len(archives.get('hits', []))}]")
            llm_text = fake_llm("嗐，您问这个做什么……我不骗人，真的。",
                                consistency=1, cited_archive=["A-03"], delta=1)
            r2 = talk(state, json.dumps(recall, ensure_ascii=False),
                      json.dumps(archives, ensure_ascii=False), llm_text, r["action"])
            state = r2["state"]
            print(r2["answer_text"][:600])
            print("-" * 60)
        # 3) 上酒：真实写入记忆
        r = ci(state, '{"action":"plant","card":"A","drink":"回甘"}', scopes_body, "上酒 回甘 A")
        state = r["state"]
        _, written = http("POST", "/v1/memory/remember", json.loads(r["remember_json"]))
        llm_text = fake_llm("……奇怪，我怎么记得娘说的是花。", consistency=2, delta=-1)
        r2 = plant(state, json.dumps(written, ensure_ascii=False), llm_text, r["card"], r["drink"])
        state = r2["state"]
        print(r2["answer_text"][:700])
        print("-" * 60)
        # 4) 开票：出当票 + 写当票记忆
        r = ci(state, '{"action":"settle"}', scopes_body, "开票")
        state = r["state"]
        r2 = settle(state)
        state = r2["state"]
        print(r2["answer_text"])
        _, tk = http("POST", "/v1/memory/remember", json.loads(r2["remember_json"]))
        print("[ticket memory written]", tk.get("records", [{}])[0].get("artifact", {}).get("artifact_id"))
        print("-" * 60)
        # 5) 下一位 / 结局
        r = advance(state, "开门", scopes_body)
        state = r["state"]
        print(r["answer_text"][:600])
        print("=" * 60)

    # 6) 防跑题兜底
    r = ci(state, '{"action":"talk"}', scopes_body, "你们这儿的 Wi-Fi 密码多少")
    state = r["state"]
    r2 = menu(state, "你们这儿的 Wi-Fi 密码多少")
    print("fallback:", r2["answer_text"][:200])

    s = json.loads(state)
    print("\nFINAL: phase=%s cash=%s tickets=%s cited_archives=%s" %
          (s["phase"], s["cash"], len(s["tickets"]), s["cited_archives"]))


if __name__ == "__main__":
    main()
