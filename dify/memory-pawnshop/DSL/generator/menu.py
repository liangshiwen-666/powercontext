def main(state: str, query: str) -> dict:
    st = _load(state)
    g = _guest(st)
    lines = PULLBACK.get(g["id"], ["雾太大了，我听不真。"])
    st["offtopic"] = st.get("offtopic", 0) + 1
    if st["offtopic"] > 1:
        st["suspicion"] = min(10, st["suspicion"] + 1)
    line = lines[st["offtopic"] % len(lines)]
    parts = ["（灯芯爆了一下）",
             g["name"] + "：“" + line + "”",
             "",
             "（当铺只接三样：钱、记忆、这个人。别的，绕回正题）",
             "",
             _menu(st),
             _status(st)]
    return {"state": _dump(st), "answer_text": "\n".join(parts)}
