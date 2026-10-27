#!/usr/bin/env python3
"""Seed PowerContext for 《记忆当铺》.

- Resolve/create scopes by idempotency keys (archive + 3 NPC scopes)
- Write archive entries (parsed from data/archives.md)
- Write NPC cover-story memories (data/seed_memories.json)
- Save manifest (scope ids, memory refs) to data/pc_manifest.json

Usage:
  python3 pc_seed.py             # seed (idempotent for scopes; memories may duplicate on rerun)
  python3 pc_seed.py --reset     # forget all active memories in the four scopes first, then seed
  PC_BASE=http://127.0.0.1:17429 python3 pc_seed.py
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PC_BASE", "http://127.0.0.1:17429").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(HERE, "..", "data"))


def call(method, path, body=None, headers=None, timeout=90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, dict(r.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, dict(e.headers), json.loads(raw)
        except Exception:
            return e.code, dict(e.headers), {"raw": raw[:500]}


def ensure_scope(title, summary, idempotency_key):
    st, _, body = call("POST", "/v1/scopes", {
        "title": title, "summary": summary, "idempotency_key": idempotency_key,
    })
    if st in (200, 201) and body.get("scope_id"):
        return body["scope_id"], st
    raise SystemExit(f"scope create failed: {st} {body}")


def remember(scope_id, kind, text):
    st, _, body = call("POST", "/v1/memory/remember", {"scope_id": scope_id, "kind": kind, "text": text})
    if st != 200 or not body.get("records"):
        raise SystemExit(f"remember failed: {st} {body}")
    return body["records"][0]


def list_active(scope_id):
    st, _, body = call("POST", "/v1/atomic-memory/list", {"scope_id": scope_id, "limit": 100})
    if st != 200:
        raise SystemExit(f"list failed: {st} {body}")
    return body.get("items", [])


def forget(scope_id, record):
    st, _, body = call("POST", "/v1/atomic-memory/lifecycle", {
        "scope_id": scope_id,
        "target": {"artifact": record["artifact"], "state_version": record["state_version"]},
        "state": "forgotten",
    })
    return st, body


def parse_archives(path):
    text = open(path, encoding="utf-8").read()
    blocks = re.split(r"^### ", text, flags=re.M)[1:]
    out = []
    for b in blocks:
        lines = b.splitlines()
        head = lines[0].strip()
        m = re.match(r"^([ZAFR]-\d+)\s*[｜|]\s*(.+)$", head)
        if not m:
            continue
        aid, title = m.group(1), m.group(2).strip()
        meta = {}
        for ln in lines[1:]:
            mm = re.match(r"^-\s*\*\*(.+?)\*\*\s*[：:]\s*(.*)$", ln.strip())
            if mm:
                meta[mm.group(1)] = mm.group(2).strip()
        body = meta.get("正文") or meta.get("正文：") or ""
        out.append({
            "id": aid,
            "title": title,
            "source": meta.get("出处", ""),
            "tags": meta.get("标签", ""),
            "body": body,
        })
    return out


def main():
    reset = "--reset" in sys.argv
    seed_cfg = json.load(open(os.path.join(DATA, "seed_memories.json"), encoding="utf-8"))
    archives = parse_archives(os.path.join(DATA, "archives.md"))
    print(f"parsed archives: {len(archives)}")

    manifest = {"base": BASE, "scopes": {}, "archives": [], "npc_memories": {}}

    for sc in seed_cfg["scopes"]:
        scope_id, st = ensure_scope(sc["title"], sc["summary"], sc["idempotency_key"])
        print(f"scope {sc['id']:8s} -> {scope_id} (http {st})")
        manifest["scopes"][sc["id"]] = {"scope_id": scope_id, "title": sc["title"],
                                        "summary": sc["summary"], "case_id": sc.get("case_id")}
        if reset:
            items = list_active(scope_id)
            n = 0
            for it in items:
                stc, _ = forget(scope_id, it)
                if stc == 200:
                    n += 1
            print(f"  reset: forgot {n}/{len(items)} memories")

        if sc["id"] == "archive":
            for a in archives:
                text = f"【{a['id']}｜{a['title']}】{a['body']}（出处：{a['source']}）"
                rec = remember(scope_id, "archive", text)
                manifest["archives"].append({"id": a["id"], "artifact": rec["artifact"], "kind": "archive"})
            print(f"  wrote {len(archives)} archive memories")
        else:
            recs = []
            for mem in sc["memories"]:
                rec = remember(scope_id, mem["kind"], mem["text"])
                recs.append({"kind": mem["kind"], "text": mem["text"], "artifact": rec["artifact"]})
            manifest["npc_memories"][sc["id"]] = recs
            print(f"  wrote {len(recs)} npc memories")

    out = os.path.join(DATA, "pc_manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("\nmanifest ->", out)

    # quick verification: hybrid search on archive scope
    arch_scope = manifest["scopes"]["archive"]["scope_id"]
    st, _, res = call("POST", "/v1/atomic-memory/search", {
        "scope_id": arch_scope, "query": "2003年 卫生院 家属 到院", "mode": "hybrid", "limit": 3,
    })
    print("\narchive search check:", st, "hits:", len(res.get("hits", [])))
    for h in res.get("hits", [])[:3]:
        print("  -", h["memory"]["text"][:60], "| matched_by:", h["matched_by"])


if __name__ == "__main__":
    main()
