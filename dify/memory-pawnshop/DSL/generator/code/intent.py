ACTION_MAP = {
    "archive": "archive", "查": "archive", "查档案": "archive", "翻档案": "archive",
    "talk": "talk", "追问": "talk", "说话": "talk",
    "plant": "plant", "上酒": "plant", "调酒": "plant", "塞记忆": "plant",
    "settle": "settle", "开票": "settle", "结算": "settle",
    "advance": "advance", "开门": "advance", "下一位": "advance", "继续": "advance",
    "reset": "reset", "重开": "reset",
}


def _norm_action(raw, query):
    a = str(raw or "").strip().lower()
    q = query or ""
    if a in ACTION_MAP:
        return ACTION_MAP[a]
    for k, v in ACTION_MAP.items():
        if k and k in a:
            return v
    if any(w in q for w in ("重开", "重新开始", "新游戏", "重玩")):
        return "reset"
    if any(w in q for w in ("开票", "结算", "收手", "结账", "打烊")):
        return "settle"
    if any(w in q for w in ("开门", "下一位", "继续", "请进", "迎接")):
        return "advance"
    if any(w in q for w in ("上酒", "调酒", "倒酒", "塞", "抹掉", "抹除")):
        return "plant"
    if any(w in q for w in ("查", "档案", "翻档", "核实", "翻翻")):
        return "archive"
    return "talk"


def _pick_card(intent, query):
    card = str(intent.get("card") or "").strip().upper()
    if card in ("A", "B", "C"):
        return card
    q = (query or "").upper()
    for c in ("A", "B", "C"):
        if ("卡" + c) in q or (" " + c) in q:
            return c
    return "A"


def main(state: str, intent_json: str, scopes_body: str, query: str) -> dict:
    st = _load(state)
    if not st.get("scopes"):
        try:
            data = json.loads(scopes_body)
            for item in (data.get("items") or []):
                title = item.get("title", "")
                for g in GUESTS:
                    if g["name"] in title:
                        st["scopes"][g["id"]] = item.get("scope_id", "")
                if ("档案" in title) or ("archive" in title.lower()):
                    st["scopes"]["archive"] = item.get("scope_id", "")
        except Exception:
            pass
    if not st.get("current"):
        _setup_guest(st, 0)
    intent = _parse_json(intent_json)
    action = _norm_action(intent.get("action"), query)
    phase = st.get("phase")
    if st.get("turn", 0) == 0:
        action = "advance"
    if phase == "endgame" and action != "reset":
        action = "endgame"
    if phase == "between" and action not in ("advance", "reset"):
        action = "advance"
    g = _guest(st)
    cur = st["current"]
    q = str(intent.get("query") or "").strip() or (query or "")
    card = _pick_card(intent, query)
    card_text = g["cards"][{"A": 0, "B": 1, "C": 2}[card]]["text"]
    drink = str(intent.get("drink") or "").strip() or "回甘"
    st["turn"] = st.get("turn", 0) + 1
    st["current"]["beat"] = g["beats"][min(st.get("round", 0), len(g["beats"]) - 1)]
    st["current"]["hint"] = g["hints"][min(st.get("round", 0), len(g["hints"]) - 1)]
    npc_scope = st["scopes"].get(cur.get("id", ""), "")
    arc_scope = st["scopes"].get("archive", "")
    recall_json = json.dumps({"scope_id": npc_scope, "query": q, "mode": "hybrid", "limit": 6},
                             ensure_ascii=False)
    archive_json = json.dumps({"scope_id": arc_scope, "query": q, "mode": "hybrid", "limit": 3},
                              ensure_ascii=False)
    remember_json = json.dumps({"scope_id": npc_scope, "kind": "planted", "text": card_text},
                               ensure_ascii=False)
    return {
        "state": _dump(st),
        "action": action,
        "query_text": q,
        "card": card,
        "card_text": card_text,
        "drink": drink,
        "scope_id": npc_scope,
        "archive_scope_id": arc_scope,
        "recall_json": recall_json,
        "archive_json": archive_json,
        "remember_json": remember_json,
    }
