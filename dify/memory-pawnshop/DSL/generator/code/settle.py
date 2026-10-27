def main(state: str) -> dict:
    st = _load(state)
    g = _guest(st)
    cur = st["current"]
    cited = len(st["cited_archives"])
    citedm = len(st["cited_memories"])
    amount = BASE_AMOUNT[g["id"]] + (st["trust"] - 50) * 2 + 20 * cited + 15 * citedm - 25 * st["suspicion"]
    amount = max(0, int(amount))
    failed = (st["suspicion"] >= 8) or (st["trust"] <= 10)
    if failed:
        amount = 0
    st["cash"] = st["cash"] - amount
    st["spent"] = st["spent"] + amount
    rev = int(cur.get("revision", 0) or 0)
    no = "WH-__TODAY__-" + g["case_id"] + "-" + g["id"].upper() + "-R" + str(rev)
    st["last_amount"] = amount
    st["last_ticket_no"] = no
    st["tickets"].append({"no": no, "guest": g["name"], "amount": amount, "rev": rev})
    st["phase"] = "between"
    parts = ["【开票 · 第" + str(len(st["tickets"])) + " 号】",
             "（客人把当票接过去，纸边有点潮）",
             _ticket_card(st),
             "",
             str(g["settlement"].get("line") or ""),
             "",
             _status(st),
             "",
             "灯灭。雾进来了。下一个推门的是谁？",
             _menu(st)]
    if failed:
        parts.insert(1, "◆ 这张票作废了。他起身时，椅子都没响。")
    mem = ("当票 " + no + "：当物「" + ((cur.get("last_memory") or "空杯")[:30]) +
           "」，存本 R" + str(rev) + "，金额 " + str(amount) + " 元。")
    scope = st["scopes"].get(g["id"], "")
    return {
        "state": _dump(st),
        "answer_text": "\n".join(parts),
        "ticket_memory": mem,
        "remember_json": json.dumps({"scope_id": scope, "kind": "ticket", "text": mem}, ensure_ascii=False),
        "scope_id": scope,
        "ticket_no": no,
    }
