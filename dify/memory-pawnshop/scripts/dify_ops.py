#!/usr/bin/env python3
"""Dify 控制台自动化：初始化管理员、安装 DeepSeek 插件、配置模型、导入 DSL、发布、建 API Key、跑验证对话。

仅使用 Python 标准库。用于评审/开发者复现时减少手工点击；也可以完全按 README 的手工步骤操作。

用法：
  python3 dify_ops.py all --email admin@example.com --password 'MemoryPawn2026!' \
      --deepseek-key sk-xxx --dsl ../DSL/memory-pawnshop.yml --out ../evidence/dify_run.json
  python3 dify_ops.py chat --token app-xxx --out ../evidence/dify_run.json
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

DIFY = os.environ.get("DIFY_BASE", "http://127.0.0.1:3000")
MARKETPLACE = "https://marketplace.dify.ai"
DEEPSEEK_ID = "langgenius/deepseek:0.0.26@bdf47cb41e9062bba00d26625d2a795c1874011d8ce64bff738c44b5a31787ea"


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None
        self.cookies = {}

    def raw(self, method, url, data=None, headers=None, timeout=120, raw_body=None):
        h = {"User-Agent": "memory-pawnshop-cli/1.0"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            if "csrf_token" in self.cookies:
                h["X-CSRF-Token"] = self.cookies["csrf_token"]
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        body = raw_body
        if data is not None:
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                for sc in r.headers.get_all("Set-Cookie") or []:
                    kv = sc.split(";")[0]
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        self.cookies[k.strip()] = v.strip()
                try:
                    return r.status, json.loads(data.decode())
                except Exception:
                    return r.status, data.decode()[:2000]
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw[:2000]

    def console(self, method, path, data=None, **kw):
        return self.raw(method, self.base + "/console/api" + path, data, **kw)


def json_or_none(v):
    return v if isinstance(v, (dict, list)) else None


def setup_and_login(c, email, password, name="Judge"):
    st, body = c.console("GET", "/setup")
    print("setup status:", st, str(body)[:120])
    finished = isinstance(body, dict) and body.get("step") == "finished"
    if not finished:
        st, body = c.console("POST", "/setup", {"email": email, "name": name, "password": password})
        print("setup:", st, str(body)[:200])
    # 登录接口的前端会做 base64 混淆（FieldEncryption.decrypt_field 只是 base64 解码）
    st, body = c.console("POST", "/login",
                         {"email": email, "password": base64.b64encode(password.encode()).decode(),
                          "remember_me": True})
    print("login:", st, str(body)[:150])
    tok = None
    if isinstance(body, dict):
        tok = (body.get("data") or {}).get("access_token") or body.get("access_token")
    if not tok:
        # 1.18 把 access_token 放在 cookie 里，靠 cookie 会话鉴权即可继续
        tok = c.cookies.get("access_token") or ""
    c.token = tok
    return tok


def install_deepseek(c, pkg_path=None, plugin_id=DEEPSEEK_ID):
    st, body = c.console("GET", "/workspaces/current/model-providers")
    providers = body.get("data") if isinstance(body, dict) else None
    names = [p.get("provider") for p in (providers or [])] if isinstance(providers, list) else []
    print("installed providers:", names)
    if any("deepseek" in (n or "") for n in names):
        print("deepseek provider already installed")
        return names
    if pkg_path and os.path.exists(pkg_path):
        print("installing plugin package:", pkg_path)
        boundary = "----memorypawn" + uuid.uuid4().hex
        with open(pkg_path, "rb") as f:
            content = f.read()
        filename = os.path.basename(pkg_path)
        body_bytes = (
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"pkg\"; filename=\"{filename}\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n").encode() + content +
            f"\r\n--{boundary}--\r\n".encode()
        )
        st, out = c.raw("POST", c.base + "/console/api/workspaces/current/plugin/install/pkg",
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                        raw_body=body_bytes, timeout=600)
        print("plugin install pkg:", st, str(out)[:200])
    else:
        st, out = c.console("POST", "/workspaces/current/plugin/install/marketplace",
                            {"plugin_unique_identifiers": [plugin_id]})
        print("plugin install marketplace:", st, str(out)[:200])
    for _ in range(60):
        time.sleep(5)
        st, body = c.console("GET", "/workspaces/current/model-providers")
        providers = body.get("data") if isinstance(body, dict) else None
        names = [p.get("provider") for p in (providers or [])] if isinstance(providers, list) else []
        if any("deepseek" in (n or "") for n in names):
            print("deepseek provider ready:", [n for n in names if "deepseek" in (n or "")])
            return names
    raise SystemExit("deepseek plugin install timeout")


def set_deepseek_key(c, api_key, providers):
    provider = next((p for p in providers if "deepseek" in (p or "")), None)
    if not provider:
        raise SystemExit("no deepseek provider")
    st, body = c.console("POST", f"/workspaces/current/model-providers/{provider}/credentials",
                         {"credentials": {"api_key": api_key}})
    print("set credential:", st, str(body)[:200])
    # 启用 deepseek-chat 模型（如未自动启用）
    st, body = c.console("POST", f"/workspaces/current/model-providers/{provider}/models/enable",
                         {"model": "deepseek-chat"})
    print("enable model:", st, str(body)[:120])
    return provider


def import_dsl(c, dsl_path):
    with open(dsl_path, encoding="utf-8") as f:
        yaml_content = f.read()
    st, body = c.console("POST", "/apps/imports", {"mode": "yaml-content", "yaml_content": yaml_content})
    print("import:", st, json.dumps(body, ensure_ascii=False)[:800] if isinstance(body, (dict, list)) else str(body)[:800])
    if not isinstance(body, dict):
        raise SystemExit("import failed")
    if body.get("status") == "pending":
        st, body = c.console("POST", f"/apps/imports/{body['id']}/confirm", {})
        print("confirm:", st, json.dumps(body, ensure_ascii=False)[:600])
    if not isinstance(body, dict) or not body.get("app_id"):
        raise SystemExit("import failed: " + json.dumps(body, ensure_ascii=False)[:800])
    return body["app_id"], body


def publish(c, app_id):
    st, body = c.console("POST", f"/apps/{app_id}/workflows/publish", {})
    print("publish:", st, str(body)[:300])


def create_api_key(c, app_id):
    st, body = c.console("POST", f"/apps/{app_id}/api-keys", {"type": "app"})
    print("api-key:", st, str(body)[:200])
    if isinstance(body, dict) and body.get("token"):
        return body["token"]
    # 有些版本返回在 data 里
    return (body.get("data") or {}).get("token") if isinstance(body, dict) else None


def chat(c, token, query, conversation_id="", user="judge"):
    st, body = c.raw("POST", c.base + "/v1/chat-messages",
                     {"inputs": {}, "query": query, "response_mode": "blocking",
                      "conversation_id": conversation_id, "user": user},
                     headers={"Authorization": f"Bearer {token}"}, timeout=300)
    return st, body


def run_demo(dsl_path, out_path, token=None):
    turn_inputs = ["开门", "查 2003 年卫生院", "追问：你那天在袜厂加班到几点", "上酒 回甘 A", "开票", "开门"]
    if token is None:
        raise SystemExit("no app token")
    c = Client(os.environ.get("DIFY_BASE", "http://127.0.0.1:3000"))
    conv = ""
    log = []
    for q in turn_inputs:
        st, body = chat(c, token, q, conv)
        if isinstance(body, dict) and body.get("conversation_id"):
            conv = body["conversation_id"]
        log.append({"query": q, "status": st,
                    "answer": (body.get("answer") if isinstance(body, dict) else body)})
        print("\n>>>", q, "->", str(log[-1]["answer"])[:400])
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"app_token": token, "turns": log}, f, ensure_ascii=False, indent=2)
        print("\nsaved:", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["all", "setup", "import", "publish", "apikey", "chat", "demo"])
    ap.add_argument("--dify", default=DIFY)
    ap.add_argument("--email", default="admin@memory-pawnshop.local")
    ap.add_argument("--password", default="MemoryPawn2026!")
    ap.add_argument("--deepseek-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    ap.add_argument("--dsl", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "DSL", "memory-pawnshop.yml"))
    ap.add_argument("--plugin-pkg", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    c = Client(args.dify)
    if args.cmd == "chat":
        run_demo(args.dsl, args.out, token=args.token)
        return
    if args.cmd == "demo":
        run_demo(args.dsl, args.out, token=args.token)
        return

    setup_and_login(c, args.email, args.password)
    if args.cmd == "setup":
        return
    if args.cmd in ("all", "import"):
        app_id = None
        if args.cmd == "all":
            providers = install_deepseek(c, args.plugin_pkg)
            if args.deepseek_key:
                set_deepseek_key(c, args.deepseek_key, providers)
        app_id, _ = import_dsl(c, args.dsl)
        publish(c, app_id)
        token = create_api_key(c, app_id)
        print("APP_ID:", app_id)
        print("APP_TOKEN:", token)
        if args.cmd == "all" and token:
            run_demo(args.dsl, args.out, token=token)
        return
    if args.cmd == "publish":
        publish(c, args.token)
        return
    if args.cmd == "apikey":
        print(create_api_key(c, args.token))


if __name__ == "__main__":
    main()
