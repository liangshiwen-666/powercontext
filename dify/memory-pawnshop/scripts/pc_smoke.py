"""End-to-end smoke test for PowerContext (game usage): scope -> memory -> search -> prepare -> revise -> forget.

Stdlib only. Writes evidence JSON to dify-game/evidence/pc_smoke.json
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PC_BASE", "http://127.0.0.1:17429")
EVIDENCE = {
    "base": BASE,
    "steps": [],
}


def call(method, path, body=None, headers=None):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8")
            hd = dict(r.headers)
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return r.status, hd, parsed
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, dict(e.headers), parsed


def step(label, method, path, body=None, headers=None, show=700):
    st, hd, b = call(method, path, body, headers)
    rec = {"label": label, "request": {"method": method, "path": path, "body": body, "headers": headers},
           "status": st, "response": b, "resp_headers": {k: v for k, v in hd.items() if k.lower() in ("etag", "if-match", "location", "content-type")}}
    EVIDENCE["steps"].append(rec)
    print(f"\n== {label} -> {st}")
    print(json.dumps(b, ensure_ascii=False)[:show] if not isinstance(b, str) else b[:show])
    return st, hd, b


# ---------------------------------------------------------------- 1. scope
st, _, scope = step("create_scope", "POST", "/v1/scopes", {
    "title": "当铺客人·阿鸢",
    "summary": "雾河镇绣坊娘子阿鸢的记忆 scope（游戏 NPC 记忆边界）",
    "idempotency_key": "game-npc-ayuan-v1",
})
if st not in (200, 201):
    print("FATAL: scope create failed")
    sys.exit(1)
SID = scope["scope_id"]
print("scope_id =", SID)

# ---------------------------------------------------------------- 2. write memories
memories = [
    ("truth", "三月初七夜，西街绣坊失了火，我是唯一活着出来的人。"),
    ("truth", "那晚我其实先去了河边，回来时火已经起来了。"),
    ("doubt", "我总觉得自己忘了什么，火光照得我眼睛疼。"),
]
refs = []
for kind, text in memories:
    st, _, m = step(f"remember_{kind}", "POST", "/v1/memory/remember", {"scope_id": SID, "kind": kind, "text": text})
    if st == 200 and m.get("records"):
        refs.append(m["records"][0])
print("memories written:", len(refs))

# ---------------------------------------------------------------- 3. hybrid search
st, _, res = step("search_hybrid", "POST", "/v1/atomic-memory/search", {
    "scope_id": SID, "query": "河边 渔火 那晚 火", "mode": "hybrid", "limit": 5,
})
hits = res.get("hits", []) if isinstance(res, dict) else []
print("hit count:", len(hits), "| matched_by:", [h["matched_by"] for h in hits])

# ---------------------------------------------------------------- 4. kind filter
step("search_kind_truth", "POST", "/v1/atomic-memory/search", {
    "scope_id": SID, "query": "那晚", "kind": "truth", "mode": "hybrid", "limit": 5,
}, show=400)

# ---------------------------------------------------------------- 5. prepare context
st, _, ctx = step("prepare_context", "POST", "/v1/context/prepare", {
    "scope_id": SID, "query": "你还记得三月初七那晚吗？", "max_bytes": 1200,
}, show=1200)
print("prepared status:", ctx.get("status") if isinstance(ctx, dict) else "?", "bytes:", ctx.get("content_bytes") if isinstance(ctx, dict) else "?")

# ---------------------------------------------------------------- 6. list entries
step("list_entries", "POST", "/v1/atomic-memory/list", {"scope_id": SID, "limit": 10}, show=500)

# ---------------------------------------------------------------- 7. revise one memory (revision mutation)
target = refs[1]  # the "river" memory
aid = target["artifact"]["artifact_id"]
st, hd, head = step("get_head", "GET", f"/v1/scopes/{SID}/artifacts/atomic-memory/{aid}", show=500)
etag = hd.get("ETag") or hd.get("etag")
print("ETag:", etag, "| digest:", head.get("content_digest") if isinstance(head, dict) else None)
if etag is None:
    etag = f'"{head["revision"]}"' if isinstance(head, dict) else None
st2, _, rep = step("replace_revision", "PUT", f"/v1/scopes/{SID}/artifacts/atomic-memory/{aid}",
                   {"content": {"kind": "truth", "text": "那晚我在河边，看见的不是渔火，是绣坊方向的红光。"}},
                   headers={"If-Match": etag}, show=500)
step("list_revisions", "GET", f"/v1/scopes/{SID}/artifacts/atomic-memory/{aid}/revisions", show=500)

# ---------------------------------------------------------------- 8. forget one memory
victim = refs[2]
st, _, forgot = step("forget_one", "POST", "/v1/atomic-memory/lifecycle", {
    "scope_id": SID,
    "target": {"artifact": victim["artifact"], "state_version": victim["state_version"]},
    "state": "forgotten",
}, show=500)
st, _, res2 = step("search_after_forget", "POST", "/v1/atomic-memory/search", {
    "scope_id": SID, "query": "我忘了什么", "mode": "hybrid", "limit": 5,
}, show=400)
print("hits after forget:", len(res2.get("hits", [])) if isinstance(res2, dict) else "?",
      "| states:", [h["memory"]["state"] for h in res2.get("hits", [])] if isinstance(res2, dict) else "?")
step("list_forgotten", "POST", "/v1/atomic-memory/list", {"scope_id": SID, "states": ["forgotten"], "limit": 10}, show=400)

# ---------------------------------------------------------------- 9. cross-session recall on a fresh query
st, _, res3 = step("cross_session_recall", "POST", "/v1/context/prepare", {
    "scope_id": SID, "query": "掌柜的，我上次跟你说过什么？", "max_bytes": 1500,
}, show=1200)

# ---------------------------------------------------------------- 10. scope isolation
st, _, scope2 = step("create_scope_2", "POST", "/v1/scopes", {
    "title": "当铺客人·柳先生", "summary": "第二个客人的独立记忆边界", "idempotency_key": "game-npc-liu-v1",
})
sid2 = scope2["scope_id"]
st, _, res4 = step("isolation_search", "POST", "/v1/atomic-memory/search", {
    "scope_id": sid2, "query": "河边 渔火 火", "mode": "hybrid", "limit": 5,
}, show=400)
print("other scope hits:", len(res4.get("hits", [])) if isinstance(res4, dict) else "?")

EVIDENCE["scope_id"] = SID
EVIDENCE["scope_id_2"] = sid2

out = os.path.join(os.path.dirname(__file__), "..", "evidence", "pc_smoke.json")
out = os.path.abspath(out)
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(EVIDENCE, f, ensure_ascii=False, indent=2)
print("\n=== evidence saved:", out)
