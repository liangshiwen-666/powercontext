#!/usr/bin/env python3
"""端到端验证：资料入库 → seekdb 混合检索 → PowerContext 记忆写入/召回 → 复习计划 → 证据记录。

用法：
    python scripts/verify.py --base http://127.0.0.1:8080
    python scripts/verify.py --base http://127.0.0.1:8080 --user my-check

脚本只依赖标准库，Windows / WSL / Linux 都能跑。任意一步不符合预期即以非 0 退出。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TIMEOUT = 300
RESULTS: list[tuple[str, bool, str]] = []


def request(method: str, url: str, payload: dict | None = None, form: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(base: str, path: str) -> dict:
    return request("GET", base + path)


def post(base: str, path: str, payload: dict | None = None, form: dict | None = None) -> dict:
    return request("POST", base + path, payload=payload, form=form)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--user", default=f"verify-{time.strftime('%m%d%H%M%S')}")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    user = args.user

    print(f"目标应用: {base}")
    print(f"验证用户: {user}\n")

    # 1. 健康检查
    health = get(base, "/api/health")
    seekdb = health.get("seekdb", {})
    pc = health.get("powercontext", {})
    check("应用可访问", True, f"LLM={'已配置' if health.get('llm') else '未配置（模板降级）'}")
    check("seekdb 可用", seekdb.get("ok") is True, f"{seekdb.get('collection')} / {seekdb.get('dimension')} 维 / {seekdb.get('embedding')}")
    check("PowerContext 可用", pc.get("ok") is True, f"v{pc.get('version')}")

    # 2. 初始资料入库（随项目附带的两份样例笔记）
    seeded = post(base, "/api/seed", form={"user_key": user})
    total_chunks = sum(item.get("chunks", 0) for item in seeded.get("ingested", []))
    check("样例资料入库", seeded.get("files") == 2 and total_chunks >= 8, f"{seeded.get('files')} 个文件 / {total_chunks} 个片段")

    health = get(base, "/api/health")
    check("seekdb 中已有向量", health.get("seekdb", {}).get("chunks", 0) >= 8, f"当前 {health['seekdb'].get('chunks')} 条")

    # 3. 提问：应返回带出处的片段，且两条检索通道都有命中
    question = "页面置换算法里 LRU 和 Clock 有什么区别？"
    ask1 = post(base, "/api/ask", payload={"user_key": user, "question": question})
    retrieval = ask1.get("retrieval", {})
    channels = retrieval.get("channels", {})
    citations = ask1.get("citations", [])
    top = citations[0] if citations else {}
    check("问答返回引用", len(citations) >= 3, f"{len(citations)} 条引用，首条 {top.get('title')} · {top.get('section')}")
    check(
        "seekdb 向量通道有命中",
        len(channels.get("vector", [])) > 0,
        f"{len(channels.get('vector', []))} 条",
    )
    check(
        "seekdb 全文通道有命中",
        len(channels.get("keyword", [])) > 0,
        f"{len(channels.get('keyword', []))} 条，关键词 {retrieval.get('keywords')}",
    )
    check("检索定位到目标段落", top.get("section") == "虚拟内存与页面置换", f"chunk_id={top.get('chunk_id')} score={top.get('score')}")

    # 4. 标记没懂 → PowerContext 原子记忆
    flagged = post(
        base,
        "/api/flag",
        payload={
            "user_key": user,
            "concept": "Clock 算法为什么能近似 LRU",
            "detail": "总把访问位和脏位混在一起",
            "citation": "os-notes · 虚拟内存与页面置换",
        },
    )
    records = flagged.get("records", [])
    artifact = records[0].get("artifact", {}) if records else {}
    artifact_id = artifact.get("artifact_id", "")
    check("薄弱点写入 PowerContext", artifact_id.startswith("art_"), f"{artifact_id}@r{artifact.get('revision')}")

    # 5. 再次提问：历史薄弱点应被召回
    ask2 = post(base, "/api/ask", payload={"user_key": user, "question": "Clock 算法和 LRU 是什么关系？"})
    recalled = [m for m in ask2.get("memories", []) if m.get("artifact_id") == artifact_id]
    check("后续提问召回该记忆", bool(recalled), f"matched_by={recalled[0].get('matched_by') if recalled else None}")

    # 6. 复习计划：必须引用刚才的记忆
    plan = get(base, f"/api/plan?user_key={urllib.parse.quote(user)}")
    weak_refs = [w.get("artifact_id") for w in plan.get("weak_points", [])]
    check("计划引用记忆", artifact_id in weak_refs, f"weak_points={len(weak_refs)} 条")
    check("计划内容包含该薄弱点", "Clock" in plan.get("plan", ""), plan.get("plan", "").splitlines()[0][:40] if plan.get("plan") else "")

    # 7. 证据：检索日志与统计
    evidence = get(base, "/api/evidence?limit=5")
    retrievals = evidence.get("retrievals", [])
    stats = evidence.get("stats", {})
    check("检索日志可追溯", len(retrievals) >= 2, f"最近一条命中 {len(retrievals[0].get('hit_ids', []))} 个片段")
    check("记忆写入计数", stats.get("memories_written", 0) >= 1, f"累计 {stats.get('memories_written')} 次")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n结果: {passed}/{total} 通过")
    if passed != total:
        print("失败项:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name} {detail}")
        return 1

    print("\n证据摘要（可直接贴进提交材料）:")
    summary = {
        "user_key": user,
        "seekdb": {
            "collection": health.get("seekdb", {}).get("collection"),
            "dimension": health.get("seekdb", {}).get("dimension"),
            "chunks": health.get("seekdb", {}).get("chunks"),
            "keywords": retrieval.get("keywords"),
            "vector_hits": len(channels.get("vector", [])),
            "keyword_hits": len(channels.get("keyword", [])),
            "top_chunk": {
                "id": top.get("chunk_id"),
                "title": top.get("title"),
                "section": top.get("section"),
                "score": top.get("score"),
            },
            "latency_ms": retrieval.get("latency_ms"),
        },
        "powercontext": {
            "artifact": f"{artifact_id}@r{artifact.get('revision')}",
            "recalled_by": recalled[0].get("matched_by") if recalled else None,
            "plan_refs": weak_refs,
        },
        "stats": stats,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        print(f"[FAIL] HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"[FAIL] 无法连接应用: {exc.reason}")
        print("请先按 README 启动 PowerContext 与应用。")
        sys.exit(1)
