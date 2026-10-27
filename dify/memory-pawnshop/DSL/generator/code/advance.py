def main(state: str, query: str, scopes_body: str) -> dict:
    st = _load(state)
    _resolve_scopes(st, scopes_body)
    q = query or ""
    if st.get("phase") == "endgame":
        if any(w in q for w in ("重开", "重新开始", "新游戏", "重玩")):
            st = _reset()
        else:
            return {"state": _dump(st),
                    "answer_text": "（灯还亮着。输入「重开」可以再开一局）\n" + _menu(st)}
    if st.get("phase") == "between":
        idx = st.get("guest_idx", 0) + 1
        if idx >= len(GUESTS):
            st["phase"] = "endgame"
            return {"state": _dump(st), "answer_text": _ending(st)}
        g = _setup_guest(st, idx)
        parts = ["【第" + str(idx + 1) + "位客人 · " + g["name"] + "（案号 " + g["case_id"] + "）】",
                 "（门帘被风掀起半寸，雾贴着门槛进来）",
                 g["name"] + "：“" + g["opening"] + "”",
                 "",
                 _menu(st),
                 _status(st)]
        return {"state": _dump(st), "answer_text": "\n".join(parts)}
    # 开场 / 兜底推进：把当前客人重置为一巡开始并打印开场
    g = _setup_guest(st, st.get("guest_idx", 0))
    st["turn"] = 1
    parts = ["【雾河街十三号 · 灯下记忆当铺】",
             "三更天，雾从西岭下来。当票簿第 240 页空着。",
             "（门帘响了一声）",
             g["name"] + "：“" + g["opening"] + "”",
             "",
             _menu(st),
             _status(st)]
    return {"state": _dump(st), "answer_text": "\n".join(parts)}
