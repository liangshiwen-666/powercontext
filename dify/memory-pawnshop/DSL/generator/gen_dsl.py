#!/usr/bin/env python3
"""Generate 《记忆当铺》 Dify Chatflow DSL (app DSL version 0.7.0)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CODE = os.path.join(HERE, "code")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "memory-pawnshop.yml")

PC = os.environ.get("PC_BASE_URL", "http://powercontext:8000")
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "langgenius/deepseek/deepseek")
MODEL_NAME = os.environ.get("MODEL_NAME", "deepseek-flash")
TODAY = os.environ.get("GAME_DATE", "20261027")

with open(os.path.join(ROOT, "data", "guests.json"), encoding="utf-8") as f:
    _RAW = json.load(f)["guests"]

GUESTS = []
for g in _RAW:
    GUESTS.append({
        "id": g["id"],
        "name": g["name"],
        "case_id": g["case_id"],
        "scope_key": g["scope"],
        "opening": g["openning"],
        "hints": [r["player_hints"] for r in g["rounds"]],
        "beats": [r["guest_beat"] for r in g["rounds"]],
        "cards": [{"name": c["name"], "text": c["text"]} for c in g["planted_cards"]],
        "truth": g["truth"]["text"],
        "settlement": g["settlement"],
        "second_visit": g["second_visit"],
        "scoring": g["scoring"],
        "archive_ids": g["archive_ids"],
    })

guests_json = json.dumps(json.dumps(GUESTS, ensure_ascii=False))


def code_text(name):
    with open(os.path.join(CODE, "prelude.py"), encoding="utf-8") as f:
        prelude = f.read().replace("__GUESTS_JSON__", guests_json)
    with open(os.path.join(CODE, name + ".py"), encoding="utf-8") as f:
        body = f.read().replace("__TODAY__", TODAY)
    return prelude + "\n\n" + body


KEYS = [
    "start", "http_scopes", "llm_intent", "code_intent", "if_else",
    "http_recall", "http_archive", "llm_turn", "code_talk", "assigner_talk", "answer_talk",
    "http_remember", "llm_wake", "code_plant", "assigner_plant", "answer_plant",
    "code_settle", "http_ticket", "assigner_settle", "answer_settle",
    "code_advance", "assigner_advance", "answer_advance",
    "code_menu", "assigner_menu", "answer_menu",
]
NID = {k: str(1700000000000 + i * 7) for i, k in enumerate(KEYS)}
TYPE = {}


def node(key, type_, data, x, y):
    TYPE[key] = type_
    return {
        "data": data,
        "height": 90,
        "id": NID[key],
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }


def edge(a, b, handle="source"):
    return {
        "data": {"isInIteration": False, "isInLoop": False,
                 "sourceType": TYPE.get(a, "start"), "targetType": TYPE.get(b, "answer")},
        "id": NID[a] + "-" + handle + "-" + NID[b],
        "source": NID[a],
        "sourceHandle": handle,
        "target": NID[b],
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


NODES = []

# ---------------------------------------------------------------- start
NODES.append(node("start", "start", {"desc": "", "title": "Start", "type": "start", "variables": []}, 40, 380))

# ---------------------------------------------------------------- scope list (HTTP GET)
NODES.append(node("http_scopes", "http-request", {
    "desc": "列出 PowerContext scopes，用于按标题解析 npc/archive scope_id",
    "title": "HTTP·scope列表",
    "type": "http-request",
    "method": "get",
    "url": PC + "/v1/scopes",
    "authorization": {"type": "no-auth"},
    "headers": "",
    "params": "",
    "body": {"type": "none", "data": ""},
    "timeout": {"connect": 10, "read": 30, "write": 30},
    "retry_config": {"retry_enabled": True, "max_retries": 2, "retry_interval": 800},
}, 280, 380))

# ---------------------------------------------------------------- intent LLM
INTENT_PROMPT = (
    "你是《记忆当铺》的意图解析器。只输出一个 JSON 对象，不要解释、不要 Markdown、不要代码块。\n"
    "当前状态（JSON）：{{#conversation.state#}}\n"
    "玩家输入：{{#sys.query#}}\n"
    "把玩家输入解析成动作：\n"
    "- 查/翻档案/核实 → archive\n"
    "- 追问/说话/观察 → talk\n"
    "- 上酒/调酒/塞记忆/抹掉 → plant（card 取 A/B/C，drink 取 回甘/忘忧/苦胆）\n"
    "- 开票/结算/收手 → settle\n"
    "- 开门/下一位/继续 → advance\n"
    "- 重开/新游戏 → reset\n"
    "拿不准一律 talk。\n"
    '只输出：{"action":"talk","query":"用于检索的短语","card":"A","drink":"回甘","note":"一句备注"}'
)
NODES.append(node("llm_intent", "llm", {
    "context": {"enabled": False, "variable_selector": []},
    "desc": "解析玩家动作",
    "memory": {"query_prompt_template": "{{#sys.query#}}", "window": {"enabled": False, "size": 10}},
    "model": {"completion_params": {"temperature": 0.1}, "mode": "chat",
              "name": MODEL_NAME, "provider": MODEL_PROVIDER},
    "prompt_template": [{"role": "system", "text": INTENT_PROMPT}],
    "selected": True,
    "title": "LLM·意图解析",
    "type": "llm",
    "variables": [],
    "vision": {"enabled": False},
}, 520, 380))

# ---------------------------------------------------------------- code intent
NODES.append(node("code_intent", "code", {
    "code": code_text("intent"),
    "code_language": "python3",
    "desc": "规范化动作、解析 scope、准备 HTTP 请求体",
    "outputs": {k: {"children": None, "type": "string"} for k in
                ("state", "action", "query_text", "card", "card_text", "drink",
                 "scope_id", "archive_scope_id", "recall_json", "remember_json", "archive_json")},
    "selected": False,
    "title": "Code·意图规范化",
    "type": "code",
    "variables": [
        {"value_selector": ["conversation", "state"], "variable": "state"},
        {"value_selector": [NID["llm_intent"], "text"], "variable": "intent_json"},
        {"value_selector": [NID["http_scopes"], "body"], "variable": "scopes_body"},
        {"value_selector": ["sys", "query"], "variable": "query"},
    ],
}, 760, 380))

# ---------------------------------------------------------------- if/else
def cond(value):
    return {"comparison_operator": "is", "id": str(uuid_like := "c" + str(abs(hash(value)) % 10 ** 9)),
            "value": value, "varType": "string",
            "variable_selector": [NID["code_intent"], "action"]}


NODES.append(node("if_else", "if-else", {
    "cases": [
        {"case_id": "talk", "conditions": [cond("talk"), cond("archive")],
         "id": "talk", "logical_operator": "or"},
        {"case_id": "plant", "conditions": [cond("plant")], "id": "plant", "logical_operator": "and"},
        {"case_id": "settle", "conditions": [cond("settle")], "id": "settle", "logical_operator": "and"},
        {"case_id": "advance", "conditions": [cond("advance"), cond("endgame"), cond("reset")],
         "id": "advance", "logical_operator": "or"},
    ],
    "desc": "按动作分流",
    "selected": False,
    "title": "IF/ELSE·动作分流",
    "type": "if-else",
}, 1000, 380))

# ---------------------------------------------------------------- talk branch
NODES.append(node("http_recall", "http-request", {
    "desc": "召回客人相信的记忆", "title": "HTTP·记忆召回", "type": "http-request",
    "method": "post",
    "url": PC + "/v1/atomic-memory/search",
    "authorization": {"type": "no-auth"}, "headers": "", "params": "",
    "body": {"type": "json", "data": "{{#code_intent.recall_json#}}"},
    "timeout": {"connect": 10, "read": 30, "write": 30},
    "retry_config": {"enabled": True, "max_retries": 2, "retry_interval": 800,
                     "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 5000}},
}, 1240, 180))

NODES.append(node("http_archive", "http-request", {
    "desc": "检索当铺档案柜（客观真相）", "title": "HTTP·档案检索", "type": "http-request",
    "method": "post",
    "url": PC + "/v1/atomic-memory/search",
    "authorization": {"type": "no-auth"}, "headers": "", "params": "",
    "body": {"type": "json", "data": "{{#code_intent.archive_json#}}"},
    "timeout": {"connect": 10, "read": 30, "write": 30},
    "retry_config": {"enabled": True, "max_retries": 2, "retry_interval": 800,
                     "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 5000}},
}, 1240, 320))

TURN_PROMPT = (
    "你在扮演《记忆当铺》里的一位客人，用第一人称说话。只输出一个 JSON 对象，不要 Markdown。\n"
    "当前状态（含客人名、巡次、心理值）：{{#conversation.state#}}\n"
    "玩家动作：{{#code_intent.action#}}｜玩家原话：{{#sys.query#}}\n"
    "【客人相信的记忆（PowerContext 召回）】{{#http_recall.body#}}\n"
    "【当铺档案柜检索结果（客观事实，客人还没看见）】{{#http_archive.body#}}\n"
    "写作要求：\n"
    "1) line 是客人此刻说的话，≤120 字，保留他的口癖，只暗示、不解释、不剧透真相；\n"
    "2) 如果玩家动作是 archive，客人要顺着“被翻到的档案”出现不安的反应；\n"
    "3) consistency：这句话有没有说进他心里（0 完全没信 / 1 半信半疑 / 2 信了）；\n"
    "4) cited_archive：你的台词里实际提到的档案编号（如 A-03），没有就空数组；\n"
    "5) cited_memory：你引用到的记忆 artifact_id 后 6 位，没有就空数组；\n"
    "6) suspicion_delta：-2 到 3 的整数，他起疑为正。\n"
    '只输出：{"line":"……","consistency":1,"cited_archive":[],"cited_memory":[],"suspicion_delta":0}'
)
NODES.append(node("llm_turn", "llm", {
    "context": {"enabled": False, "variable_selector": []},
    "desc": "生成客人台词并自判",
    "memory": {"query_prompt_template": "{{#sys.query#}}", "window": {"enabled": False, "size": 10}},
    "model": {"completion_params": {"temperature": 0.8}, "mode": "chat",
              "name": MODEL_NAME, "provider": MODEL_PROVIDER},
    "prompt_template": [{"role": "system", "text": TURN_PROMPT}],
    "selected": True, "title": "LLM·客人台词", "type": "llm", "variables": [],
    "vision": {"enabled": False},
}, 1520, 240))

NODES.append(node("code_talk", "code", {
    "code": code_text("talk"),
    "code_language": "python3",
    "desc": "组装台词/档案卡/菜单，结算信任与怀疑",
    "outputs": {k: {"children": None, "type": "string"} for k in ("state", "answer_text", "consistency")},
    "selected": False, "title": "Code·台词与判定", "type": "code",
    "variables": [
        {"value_selector": ["conversation", "state"], "variable": "state"},
        {"value_selector": [NID["http_recall"], "body"], "variable": "recall_body"},
        {"value_selector": [NID["http_archive"], "body"], "variable": "archive_body"},
        {"value_selector": [NID["llm_turn"], "text"], "variable": "llm_text"},
        {"value_selector": [NID["code_intent"], "action"], "variable": "action"},
    ],
}, 1800, 240))

NODES.append(node("assigner_talk", "assigner", {
    "desc": "写回对话变量", "items": [{
        "input_type": "variable", "operation": "over-write",
        "value": [NID["code_talk"], "state"],
        "variable_selector": ["conversation", "state"], "write_mode": "over-write"}],
    "selected": False, "title": "变量赋值·state", "type": "assigner", "version": "2",
}, 2080, 240))

NODES.append(node("answer_talk", "answer", {
    "answer": "{{#code_talk.answer_text#}}", "desc": "", "title": "Answer", "type": "answer", "variables": [],
}, 2360, 240))

# ---------------------------------------------------------------- plant branch
NODES.append(node("http_remember", "http-request", {
    "desc": "把假记忆写进客人 scope", "title": "HTTP·写入记忆", "type": "http-request",
    "method": "post",
    "url": PC + "/v1/memory/remember",
    "authorization": {"type": "no-auth"}, "headers": "", "params": "",
    "body": {"type": "json", "data": "{{#code_intent.remember_json#}}"},
    "timeout": {"connect": 10, "read": 30, "write": 30},
    "retry_config": {"enabled": True, "max_retries": 2, "retry_interval": 800,
                     "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 5000}},
}, 1240, 520))

WAKE_PROMPT = (
    "你在扮演《记忆当铺》的客人。掌柜刚给你调了一杯“记忆酒”，你现在的记忆被替换成了这句：\n"
    "「{{#code_intent.card_text#}}」\n"
    "当前状态：{{#conversation.state#}}\n"
    "请按“你现在相信的记忆”说一句话，并判断掌柜这次塞得有多深。只输出 JSON：\n"
    '{"line":"≤120字第一人称台词","consistency":0,"cited_archive":[],"cited_memory":[],"suspicion_delta":0}\n'
    "注意：不要直接说“这是假记忆”，要像真人一样半信半疑或全然相信；保留口癖。"
)
NODES.append(node("llm_wake", "llm", {
    "context": {"enabled": False, "variable_selector": []},
    "desc": "客人醒来后的台词",
    "memory": {"query_prompt_template": "{{#sys.query#}}", "window": {"enabled": False, "size": 10}},
    "model": {"completion_params": {"temperature": 0.8}, "mode": "chat",
              "name": MODEL_NAME, "provider": MODEL_PROVIDER},
    "prompt_template": [{"role": "system", "text": WAKE_PROMPT}],
    "selected": True, "title": "LLM·醒神", "type": "llm", "variables": [],
    "vision": {"enabled": False},
}, 1520, 520))

NODES.append(node("code_plant", "code", {
    "code": code_text("plant"),
    "code_language": "python3",
    "desc": "解析写入结果（revision/artifact），组装当票预览",
    "outputs": {k: {"children": None, "type": "string"} for k in
                ("state", "answer_text", "consistency", "scope_npc", "scope_id")},
    "selected": False, "title": "Code·写入结果", "type": "code",
    "variables": [
        {"value_selector": ["conversation", "state"], "variable": "state"},
        {"value_selector": [NID["http_remember"], "body"], "variable": "remember_body"},
        {"value_selector": [NID["llm_wake"], "text"], "variable": "llm_text"},
        {"value_selector": [NID["code_intent"], "card"], "variable": "card"},
        {"value_selector": [NID["code_intent"], "drink"], "variable": "drink"},
    ],
}, 1800, 520))

NODES.append(node("assigner_plant", "assigner", {
    "desc": "写回对话变量", "items": [{
        "input_type": "variable", "operation": "over-write",
        "value": [NID["code_plant"], "state"],
        "variable_selector": ["conversation", "state"], "write_mode": "over-write"}],
    "selected": False, "title": "变量赋值·state", "type": "assigner", "version": "2",
}, 2080, 520))

NODES.append(node("answer_plant", "answer", {
    "answer": "{{#code_plant.answer_text#}}", "desc": "", "title": "Answer", "type": "answer", "variables": [],
}, 2360, 520))

# ---------------------------------------------------------------- settle branch
NODES.append(node("code_settle", "code", {
    "code": code_text("settle"),
    "code_language": "python3",
    "desc": "开票：算金额、拼当票、写回 state",
    "outputs": {k: {"children": None, "type": "string"} for k in
                ("state", "answer_text", "remember_json", "scope_id", "ticket_no")},
    "selected": False, "title": "Code·开票", "type": "code",
    "variables": [{"value_selector": ["conversation", "state"], "variable": "state"}],
}, 1240, 760))

NODES.append(node("http_ticket", "http-request", {
    "desc": "把当票写入记忆（跨会话证据）", "title": "HTTP·当票入库", "type": "http-request",
    "method": "post",
    "url": PC + "/v1/memory/remember",
    "authorization": {"type": "no-auth"}, "headers": "", "params": "",
    "body": {"type": "json", "data": "{{#code_settle.remember_json#}}"},
    "timeout": {"connect": 10, "read": 30, "write": 30},
    "retry_config": {"enabled": True, "max_retries": 2, "retry_interval": 800,
                     "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 5000}},
}, 1520, 760))

NODES.append(node("assigner_settle", "assigner", {
    "desc": "写回对话变量", "items": [{
        "input_type": "variable", "operation": "over-write",
        "value": [NID["code_settle"], "state"],
        "variable_selector": ["conversation", "state"], "write_mode": "over-write"}],
    "selected": False, "title": "变量赋值·state", "type": "assigner", "version": "2",
}, 1800, 760))

NODES.append(node("answer_settle", "answer", {
    "answer": "{{#code_settle.answer_text#}}", "desc": "", "title": "Answer", "type": "answer", "variables": [],
}, 2080, 760))

# ---------------------------------------------------------------- advance branch
NODES.append(node("code_advance", "code", {
    "code": code_text("advance"),
    "code_language": "python3",
    "desc": "开场 / 下一位客人 / 结局 / 重开",
    "outputs": {k: {"children": None, "type": "string"} for k in ("state", "answer_text")},
    "selected": False, "title": "Code·推进", "type": "code",
    "variables": [
        {"value_selector": ["conversation", "state"], "variable": "state"},
        {"value_selector": ["sys", "query"], "variable": "query"},
        {"value_selector": [NID["http_scopes"], "body"], "variable": "scopes_body"},
    ],
}, 1240, 980))

NODES.append(node("assigner_advance", "assigner", {
    "desc": "写回对话变量", "items": [{
        "input_type": "variable", "operation": "over-write",
        "value": [NID["code_advance"], "state"],
        "variable_selector": ["conversation", "state"], "write_mode": "over-write"}],
    "selected": False, "title": "变量赋值·state", "type": "assigner", "version": "2",
}, 1520, 980))

NODES.append(node("answer_advance", "answer", {
    "answer": "{{#code_advance.answer_text#}}", "desc": "", "title": "Answer", "type": "answer", "variables": [],
}, 1800, 980))

# ---------------------------------------------------------------- menu branch (off-topic)
NODES.append(node("code_menu", "code", {
    "code": code_text("menu"),
    "code_language": "python3",
    "desc": "防跑题：客人拉回话题并重印菜单",
    "outputs": {k: {"children": None, "type": "string"} for k in ("state", "answer_text")},
    "selected": False, "title": "Code·防跑题", "type": "code",
    "variables": [
        {"value_selector": ["conversation", "state"], "variable": "state"},
        {"value_selector": ["sys", "query"], "variable": "query"},
    ],
}, 1240, 1160))

NODES.append(node("assigner_menu", "assigner", {
    "desc": "写回对话变量", "items": [{
        "input_type": "variable", "operation": "over-write",
        "value": [NID["code_menu"], "state"],
        "variable_selector": ["conversation", "state"], "write_mode": "over-write"}],
    "selected": False, "title": "变量赋值·state", "type": "assigner", "version": "2",
}, 1520, 1160))

NODES.append(node("answer_menu", "answer", {
    "answer": "{{#code_menu.answer_text#}}", "desc": "", "title": "Answer", "type": "answer", "variables": [],
}, 1800, 1160))

# ---------------------------------------------------------------- edges
EDGES = [
    edge("start", "http_scopes"),
    edge("http_scopes", "llm_intent"),
    edge("llm_intent", "code_intent"),
    edge("code_intent", "if_else"),
    edge("if_else", "http_recall", "talk"),
    edge("http_recall", "http_archive"),
    edge("http_archive", "llm_turn"),
    edge("llm_turn", "code_talk"),
    edge("code_talk", "assigner_talk"),
    edge("assigner_talk", "answer_talk"),
    edge("if_else", "http_remember", "plant"),
    edge("http_remember", "llm_wake"),
    edge("llm_wake", "code_plant"),
    edge("code_plant", "assigner_plant"),
    edge("assigner_plant", "answer_plant"),
    edge("if_else", "code_settle", "settle"),
    edge("code_settle", "http_ticket"),
    edge("http_ticket", "assigner_settle"),
    edge("assigner_settle", "answer_settle"),
    edge("if_else", "code_advance", "advance"),
    edge("code_advance", "assigner_advance"),
    edge("assigner_advance", "answer_advance"),
    edge("if_else", "code_menu", "false"),
    edge("code_menu", "assigner_menu"),
    edge("assigner_menu", "answer_menu"),
]

DSL = {
    "app": {
        "description": "Dify × PowerContext：一家只收记忆的当铺。三位客人、28 条档案、跨会话记忆。",
        "icon": "🕯️",
        "icon_background": "#1F1B16",
        "mode": "advanced-chat",
        "name": "记忆当铺 Memory Pawnshop",
        "use_icon_as_answer_icon": False,
    },
    "dependencies": [],
    "kind": "app",
    "version": "0.7.0",
    "workflow": {
        "conversation_variables": [{
            "description": "游戏状态 JSON（客人/巡次/信任/怀疑/当票/revision）",
            "id": "a1b2c3d4-0000-4000-8000-000000000001",
            "name": "state",
            "selector": ["conversation", "state"],
            "value": "",
            "value_type": "string",
        }],
        "environment_variables": [],
        "features": {
            "file_upload": {"enabled": False},
            "opening_statement": "雾河街十三号，灯下记忆当铺。三更开门，四更闭户；只收记忆，不收物件。\n输入「开门」开始今晚的第一位客人。",
            "retriever_resource": {"enabled": False},
            "sensitive_word_avoidance": {"enabled": False},
            "speech_to_text": {"enabled": False},
            "suggested_questions": ["开门", "查查 2003 年卫生院", "上酒 回甘 A"],
            "suggested_questions_after_answer": {"enabled": False},
            "text_to_speech": {"enabled": False},
        },
        "graph": {"edges": EDGES, "nodes": NODES, "viewport": {"x": 0, "y": 0, "zoom": 0.7}},
    },
}

try:
    import yaml
except ImportError:
    sys.stderr.write("PyYAML missing; install with: pip install pyyaml (or set PYTHONPATH)\n")
    raise


def _fix_refs(obj):
    """把字符串里的 {{#friendly.var#}} 替换成 {{#<node_id>.var#}}，使变量引用指向真实节点 id。"""
    if isinstance(obj, str):
        for k, nid in NID.items():
            obj = obj.replace("{{#" + k + ".", "{{#" + nid + ".")
        return obj
    if isinstance(obj, dict):
        return {kk: _fix_refs(vv) for kk, vv in obj.items()}
    if isinstance(obj, list):
        return [_fix_refs(x) for x in obj]
    return obj


DSL = _fix_refs(DSL)

with open(OUT, "w", encoding="utf-8") as f:
    yaml.safe_dump(DSL, f, allow_unicode=True, sort_keys=False, width=1000)
print("wrote", OUT, os.path.getsize(OUT), "bytes,", len(NODES), "nodes,", len(EDGES), "edges")
