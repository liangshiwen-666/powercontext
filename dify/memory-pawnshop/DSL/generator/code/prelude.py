import json

GUESTS = json.loads(__GUESTS_JSON__)
BASE_AMOUNT = {g["id"]: g["scoring"]["base_amount"] for g in GUESTS}
PULLBACK = {
    "ayuan": ["嗐，您这话我听不懂。您就说，我这件东西能当多少钱？",
              "您别绕了，我这人手笨，嘴也笨。",
              "（低头数纸钱，数到第七张停住）您说的那个，跟我不搭界。"],
    "laozhou": ["你听我说，我算过的——这事儿跟时间没关系，跟人有关。",
                "分秒不差。（擦眼镜）您再说一遍？",
                "咱们还是说十七号那天。别的日子我不认。"],
    "baitang": ["笑一下，掌柜的。您一笑，我就知道您没听懂。",
                "底片不撒谎。您刚才那句话，底片洗不出来。",
                "灯光不行。这话得在亮处说。（把相机放下）"],
}


def _reset():
    return {
        "started": True, "guest_idx": 0, "round": 0, "phase": "opening",
        "trust": None, "suspicion": None, "cash": 3200, "spent": 0,
        "tickets": [], "scopes": {}, "current": {}, "offtopic": 0,
        "cited_archives": [], "cited_memories": [], "turn": 0, "last_amount": 0,
        "last_ticket_no": "",
    }


def _load(raw):
    try:
        st = json.loads(raw) if raw else {}
    except Exception:
        st = {}
    if not st.get("started"):
        st = _reset()
    for k, v in _reset().items():
        st.setdefault(k, v)
    return st


def _dump(st):
    return json.dumps(st, ensure_ascii=False)


def _guest(st):
    idx = st.get("guest_idx", 0)
    if idx >= len(GUESTS):
        idx = len(GUESTS) - 1
    return GUESTS[idx]


def _setup_guest(st, idx):
    st["guest_idx"] = idx
    st["round"] = 0
    st["phase"] = "explore"
    g = GUESTS[idx]
    st["trust"] = g["scoring"]["trust_init"]
    st["suspicion"] = g["scoring"]["suspicion_init"]
    st["current"] = {"id": g["id"], "name": g["name"], "case_id": g["case_id"],
                     "revision": 0, "artifact": "", "last_memory": "",
                     "beat": g["beats"][0], "hint": g["hints"][0]}
    return g


def _strip(h):
    h = str(h or "")
    for p in ("翻档案：", "翻档案:", "追问她：", "追问：", "上茶：", "上酒/调酒：", "观察：", "查："):
        if h.startswith(p):
            h = h[len(p):]
    return h.strip()


def _menu(st):
    g = _guest(st)
    phase = st.get("phase", "explore")
    r = st.get("round", 0)
    if phase == "between":
        return "┌── 当铺菜单 ──\n│ 1  开门：请下一位客人进来\n└──────────────"
    if phase == "settled_ready":
        return ("┌── 当铺菜单 ──\n"
                "│ 1  开票：收手，结算这一局\n"
                "│ 2  再塞一段（说“再调一杯 B”）\n"
                "└──────────────")
    if phase == "endgame":
        return "┌── 当铺菜单 ──\n│ 1  重开一局（输入 重开）\n└──────────────"
    if phase == "closing" or r >= 3:
        return ("┌── 当铺菜单 ──\n"
                "│ 1  上酒：调最后一杯，收尾（说“上酒 回甘 A”）\n"
                "│ 2  空杯开票：什么也不塞，直接结算\n"
                "│ 3  再追问一句\n"
                "└──────────────")
    hints = g["hints"][min(r, len(g["hints"]) - 1)]
    return ("┌── 当铺菜单 ──\n"
            "│ 1  翻档案：" + _strip(hints[0])[:46] + "\n"
            "│ 2  追问：" + _strip(hints[1])[:46] + "\n"
            "│ 3  上酒：调一杯把话收住（说“上酒 回甘 A”）\n"
            "└──────────────")


def _status(st):
    return (f"◆ 信任 {st['trust']}｜怀疑 {st['suspicion']}｜本铺余银 {st['cash']} 元"
            f"｜客人 {st['guest_idx'] + 1}/3")


def _hits(body):
    try:
        d = json.loads(body) if isinstance(body, str) else (body or {})
        if isinstance(d, str):
            d = json.loads(d)
        return d.get("hits", []) or []
    except Exception:
        return []


def _parse_json(text):
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception:
        return {}


def _apply_judge(st, judge):
    try:
        c = max(0, min(2, int(judge.get("consistency", 1))))
    except Exception:
        c = 1
    st["trust"] = max(0, min(100, st["trust"] + (c * 6 - 4)))
    ca = judge.get("cited_archive") or []
    if isinstance(ca, str):
        ca = [ca]
    st["trust"] = max(0, min(100, st["trust"] + min(len(ca), 2) * 3))
    try:
        sd = max(-2, min(3, int(judge.get("suspicion_delta", 0))))
    except Exception:
        sd = 0
    st["suspicion"] = max(0, min(10, st["suspicion"] + sd))
    for a in ca:
        a = str(a)
        if a and a not in st["cited_archives"]:
            st["cited_archives"].append(a)
    cm = judge.get("cited_memory") or []
    if isinstance(cm, str):
        cm = [cm]
    for m in cm:
        m = str(m)[:8]
        if m and m not in st["cited_memories"]:
            st["cited_memories"].append(m)
    text = {0: "他往后靠了靠，看你的眼神变了。",
            1: "他愣了一下，像在分辨雾里的方向。",
            2: "他信了。信得很稳。"}.get(c, "他沉默了一会儿。")
    return c, text


def _archive_parts(body):
    hits = _hits(body)
    if not hits:
        return "【档案柜】空的。雾把这一格吃了。", None
    main = hits[0]["memory"]["text"]
    others = []
    for h in hits[1:3]:
        t = h["memory"]["text"]
        i = t.find("｜")
        others.append(t[2:i].strip() if i > 0 else t[:10])
    extra = ("\n▸ 另有相关：" + "、".join(others)) if others else ""
    return main, extra


def _ticket_card(st):
    g = _guest(st)
    cur = st["current"]
    amount = st.get("last_amount", 0)
    no = st.get("last_ticket_no", "WH-00000000-C00-XXX-R0")
    mem = (cur.get("last_memory") or "空杯（未易一字）")
    cited = "、".join(st["cited_archives"][-3:]) or "无"
    return "\n".join([
        "┌──────── 雾 河 记 忆 当 铺 · 当 票 ────────┐",
        "  票号：" + no,
        "  当客：" + g["name"] + "（案号 " + g["case_id"] + "）",
        "  记忆库：npc/" + g["id"],
        "  ────────────────────────────",
        "  当物：" + mem[:60],
        "  存本：R" + str(cur.get("revision", 0)),
        "  复核：档案 " + cited + " ／ 记忆 " + str(len(st["cited_memories"])) + " 条",
        "  数额：人民币 " + str(amount) + " 元",
        "  信任：" + str(st["trust"]) + " ／ 怀疑：" + str(st["suspicion"]),
        "  掌柜批注：存这里，是怕你忘了。",
        "                                〔铺印〕",
        "└──────────────────────────────────────────┘",
        "  三更开门，四更闭户。二十年为期，逾期不赎，当物归铺。",
    ])


def _ending(st):
    tiers = 0
    for t in st["tickets"]:
        base = BASE_AMOUNT.get({"阿沅": "ayuan", "老周": "laozhou", "白棠": "baitang"}.get(t["guest"], "ayuan"), 500)
        if t["amount"] >= base * 1.4:
            tiers += 3
        elif t["amount"] >= base:
            tiers += 2
        elif t["amount"] >= base * 0.6:
            tiers += 1
    truth = sum(1 for t in st["tickets"] if t["amount"] >= 500)
    score = tiers + truth * 2
    if st["spent"] > 3200:
        name, text = "结局 D·银柜见底", "铺子的灯再也点不着。客人替你说：掌柜的，您这铺子，当得起记忆，当不起钱。"
    elif st["cash"] < 500:
        name, text = "结局 C·空当", "四更天，雾散了。客人走出去，什么都没发生。这大概就是最像真相的一种结局。"
    elif score >= 8:
        name, text = "结局 S·灯", "记忆不是用来骗的。它是用来在雾里认出彼此的。"
    elif score >= 4:
        name, text = "结局 A·雾散", "疼的那段，才是我的。她把两段记忆都摸了一遍，自己挑了更疼的那段。"
    else:
        name, text = "结局 B·长夜", "门帘落下。灯没灭。三分钟后门帘又响——还是她，还是那句“第一次来”。"
    return (f"【{name}】\n{text}\n\n"
            f"当票 {len(st['tickets'])} 张｜档引 {len(st['cited_archives'])} 条｜"
            f"余银 {st['cash']} 元｜评级 {score} 分\n"
            "雾河镇不会缺客人。缺的是敢把票撕掉的人。")
