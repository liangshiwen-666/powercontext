def main(state: str, recall_body: str, archive_body: str, llm_text: str, action: str) -> dict:
    st = _load(state)
    g = _guest(st)
    judge = _parse_json(llm_text)
    line = str(judge.get("line") or "……").strip()
    if len(line) > 160:
        line = line[:160]
    c, c_text = _apply_judge(st, judge)
    arc_main, arc_extra = _archive_parts(archive_body)
    st["round"] = st.get("round", 0) + 1
    if st["round"] >= 3:
        st["phase"] = "closing"
    parts = []
    if action == "archive":
        parts.append("【档案柜 · 已翻】")
        parts.append(arc_main)
        if arc_extra:
            parts.append(arc_extra)
        parts.append("")
        parts.append(g["name"] + "：“" + line + "”")
    else:
        parts.append("【" + g["name"] + " · 第" + str(st["round"]) + "巡】")
        parts.append(g["name"] + "：“" + line + "”")
    parts.append("◆ " + c_text)
    parts.append("")
    parts.append(_menu(st))
    parts.append(_status(st))
    return {"state": _dump(st), "answer_text": "\n".join(parts), "consistency": str(c)}
