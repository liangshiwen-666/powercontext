#!/usr/bin/env python3
"""Offline simulation of the generated Dify chatflow: executes Code node bodies,
calls the real PowerContext HTTP API, and walks a full 3-guest game."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PC = os.environ.get("PC_BASE", "http://127.0.0.1:17429")
DSL = os.environ.get("DSL_PATH", os.path.join(HERE, "..", "DSL", "memory-pawnshop.yml"))


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
    with open(DSL, encoding="utf-8") as f:
        dsl = yaml.safe_load(f)
    nodes = {n["data"]["title"]: n for n in dsl["workflow"]["graph"]["nodes"]}

    def code_of(title):
        data = nodes[title]["data"]
        src = data["code"]
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
    # 1) opening
    r = ci(state, '{"action":"advance"}', scopes_body, "开门")
    state, action = r["state"], r["action"]
    print("\naction:", action, "| scopes resolved:", len(json.loads(state)["scopes"]))
    r = advance(state, "开门")
    state = r["state"]
    print(r["answer_text"])
    print("=" * 60)

    for gidx in range(3):
        # 2 talks
        for rnd in range(2):
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
        # plant
        r = ci(state, '{"action":"plant","card":"A","drink":"回甘"}', scopes_body, "上酒 回甘 A")
        state = r["state"]
        _, written = http("POST", "/v1/memory/remember", json.loads(r["remember_json"]))
        llm_text = fake_llm("……奇怪，我怎么记得娘说的是花。", consistency=2, delta=-1)
        r2 = plant(state, json.dumps(written, ensure_ascii=False), llm_text, r["card"], r["drink"])
        state = r2["state"]
        print(r2["answer_text"][:700])
        print("-" * 60)
        # settle
        r = ci(state, '{"action":"settle"}', scopes_body, "开票")
        state = r["state"]
        r2 = settle(state)
        state = r2["state"]
        print(r2["answer_text"])
        _, tk = http("POST", "/v1/memory/remember", json.loads(r2["remember_json"]))
        print("[ticket memory written]", tk.get("records", [{}])[0].get("artifact", {}).get("artifact_id"))
        print("-" * 60)
        # advance
        r = advance(state, "开门")
        state = r["state"]
        print(r["answer_text"][:600])
        print("=" * 60)

    # off-topic fallback
    r = ci(state, '{"action":"talk"}', scopes_body, "你们这儿的 Wi-Fi 密码多少")
    state = r["state"]
    r2 = menu(state, "你们这儿的 Wi-Fi 密码多少")
    print("fallback:", r2["answer_text"][:200])

    s = json.loads(state)
    print("\nFINAL: phase=%s cash=%s tickets=%s cited_archives=%s" %
          (s["phase"], s["cash"], len(s["tickets"]), s["cited_archives"]))


if __name__ == "__main__":
    main()
