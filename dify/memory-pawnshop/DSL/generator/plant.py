def main(state: str, remember_body: str, llm_text: str, card: str, drink: str) -> dict:
    st = _load(state)
    g = _guest(st)
    cur = st["current"]
    aid, rev, mtext = "", 0, ""
    try:
        body = json.loads(remember_body) if isinstance(remember_body, str) else (remember_body or {})
        if isinstance(body, str):
            body = json.loads(body)
        recs = body.get("records") or []
        if recs:
            aid = recs[0]["artifact"]["artifact_id"]
            rev = int(recs[0]["artifact"]["revision"])
    except Exception:
        pass
    judge = _parse_json(llm_text)
    line = str(judge.get("line") or "……").strip()
    c, c_text = _apply_judge(st, judge)
    if aid:
        cur["artifact"] = aid
        cur["revision"] = rev
        cur["last_memory"] = g["cards"][{"A": 0, "B": 1, "C": 2}[card]]["text"] if card in ("A", "B", "C") else ""
    st["round"] = st.get("round", 0) + 1
    st["phase"] = "settled_ready"
    parts = []
    parts.append("【上酒 · " + (drink or "回甘") + "】")
    parts.append("（杯底压着一张当票的样张，墨还没干）")
    if aid:
        parts.append("▸ 已写入记忆库：scope = npc/" + g["id"] + "，memory_id = " + aid[-8:])
        parts.append("▸ 存本 revision = R" + str(rev))
        parts.append("▸ 当前记忆摘要：" + (cur.get("last_memory") or "")[:40])
    else:
        parts.append("▸ 柜台的灯灭了一下，那段记忆没落进去。可以再调一杯。")
    parts.append("")
    parts.append("【醒神 · 第" + str(st["round"]) + "巡】")
    parts.append(g["name"] + "：“" + line + "”")
    parts.append("◆ " + c_text)
    parts.append("")
    parts.append(_menu(st))
    parts.append(_status(st))
    return {"state": _dump(st), "answer_text": "\n".join(parts), "consistency": str(c),
            "scope_npc": "npc/" + g["id"], "scope_id": cur.get("id", "")}
