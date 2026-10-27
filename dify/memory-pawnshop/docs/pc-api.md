# PowerContext HTTP API 手册（Dify HTTP Request 节点可用）

> 目标：给出可以直接复制粘贴调用的 PowerContext HTTP API 参数与响应说明。
> 适用版本：**powercontext 1.4.1**（PyPI）。文档基线：`oceanbase/powercontext` 仓库 `master`，
> 其 `openapi/powercontext.yaml` 的 `info.version` 就是 **`1.4.1`**，与你实际安装的 PyPI 版本一致。
>
> 材料来源（下文每节标注）：
> - 官方文档站 <https://powercontext.oceanbase.io/zh/docs/develop/http-api/>、API 参考 <https://powercontext.oceanbase.io/zh/api/>
> - 仓库文档源码 `docs/zh/docs/**`（`develop/http-api.md`、`develop/api-quickstart.md`、`workflows/atomic-memory.md`、
>   `workflows/artifact*.md` 系列、`workflows/topic-memory.md`、`operate/configuration.md` 等）
> - **契约源文件 `openapi/powercontext.yaml`（116 个 path，454 个 schema）** —— 本文所有端点、方法、字段名以它为准
> - 实现校验：`src/powercontext/server/app.py`、`server/atomic_memory.py`、`server/cli.py`（用于确认 ETag 字面量、
>   错误码映射、CLI 参数等契约未写明的细节）
>
> 约定：**端点路径 / 方法 / 字段名均取自契约原文**；契约或文档没写清的地方标 **【待验证】**，并给出最可能形式。
> 示例中的 `$POWERCONTEXT_TOKEN`、`project:demo`、`ARTIFACT_ID` 都是占位值。

---

## 0. 五分钟跑通：建 Scope → 写一条记忆 → 搜出来

只想验证链路的话，在**装了 powercontext 的那台机器**上开个 Bash/WSL 终端，整段复制执行：

```bash
export POWERCONTEXT_URL=http://127.0.0.1:8000
export POWERCONTEXT_TOKEN=""          # ACCESS_MODE=disabled 时留空即可

# 有 token 时才加 Authorization，避免发出空的 Bearer
AUTH=(); [ -n "$POWERCONTEXT_TOKEN" ] && AUTH=(-H "Authorization: Bearer $POWERCONTEXT_TOKEN")

# 1) 建 scope（幂等：同一个 idempotency_key 重复调用返回同一个 scope）
export SCOPE_ID="$(
  curl -sS -X POST "$POWERCONTEXT_URL/v1/scopes" \
    -H 'Content-Type: application/json' "${AUTH[@]}" \
    -d '{"title":"Dify demo","summary":"Demo scope for Dify integration","idempotency_key":"dify-demo-001"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["scope_id"])'
)"
echo "SCOPE_ID=$SCOPE_ID"

# 2) 写一条记忆
curl -sS -X POST "$POWERCONTEXT_URL/v1/scopes/$SCOPE_ID/artifacts" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d '{"family":"atomic-memory","content":{"schema":"powercontext.atomic-memory.v1","kind":"decision","text":"公开 API 保持异步。"}}'
echo

# 3) 搜出来
curl -sS -X POST "$POWERCONTEXT_URL/v1/atomic-memory/search" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"query\":\"公开 API\",\"mode\":\"text\",\"limit\":5}"
echo
```

说明：
- 第 2 步要求**已经启动 Server**（见 §1）。没配任何模型也能跑通——显式记忆写入和全文检索都不依赖推理服务。
- 第 3 步是最小部署下唯一保证可用的检索模式。`vector`/`hybrid` 需要额外配置 Embedding（见 §4）。
- 这套请求体结构可以直接搬到 Dify 的 HTTP Request 节点，见 §8。

---

## 1. 服务启动与配置

### 1.1 安装

【来源：`docs/zh/docs/get-started/install-and-run.md`】

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
```

- 依赖：Python 3.11+、`uv`、Git。macOS/Linux 支持，**Windows 为 `experimental`**；内嵌 seekDB 后端不支持 Windows。
- 只想连远端 Server 的机器装 `cli` extra 即可；要跑本地 Server 或 `service` 命令必须装 `server` extra。
- 从同一 tag 的源码安装的等价写法：
  ```bash
  uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.4.1"
  ```

### 1.2 启动

【来源：`install-and-run.md`；CLI 参数取自 `src/powercontext/server/cli.py:633-663`】

```bash
powercontext server run
```

不带任何环境变量/环境文件时，Server 会：

- 监听 **`127.0.0.1:8000`**
- 在 `/mcp` 暴露 Streamable HTTP MCP
- 创建默认 Scope
- 在操作系统用户数据目录建持久化 SQLite 库
- **不配置推理服务也能支持显式 Memory 操作**

`Ctrl-C` 正常关闭；再次运行会打开同一个数据库。

CLI 参数（`powercontext server run --help` 的实际选项）：

| 参数 | 说明 |
| --- | --- |
| `--host TEXT` | 监听地址 |
| `--port INT` | 监听端口（1–65535） |
| `--env-file PATH` | 显式指定环境文件 |
| `--no-env-file` | 不发现、不加载环境文件（不能与 `--env-file` 同时用） |
| `--role all\|api\|background` | 只跑 API、只跑后台处理，或全跑 |

配置优先级：**CLI 参数 > 进程环境变量 > 所选环境文件 > 默认值**。当前工作目录存在 `.env` 时 `server run`
会自动加载它（这就是"为什么我改了 `.env` 没生效"的常见原因：进程环境变量会覆盖文件值）。

持久化数据目录用 `POWERCONTEXT_HOME` 指定：

```bash
export POWERCONTEXT_HOME=/srv/powercontext
powercontext server run
```

### 1.3 关键环境变量

【来源：`docs/zh/docs/operate/configuration.md`（Server 配置统一 `POWERCONTEXT_SERVER_` 前缀）】

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | 监听地址 |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | 监听端口（配置向导首次建议 `17429`，**运行时默认仍是 8000**） |
| `POWERCONTEXT_SERVER_ACCESS_MODE` | `disabled` | **唯一正式 Access 开关**：`disabled` 或 `enforced` |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | 未设置 | 静态 Bearer token；未注入 Authentication Provider 时作为兼容认证，映射为内置管理员 |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | 旧静态 Bearer 兼容开关；`true` 会自动映射成 `ACCESS_MODE=enforced`，且必须设 `AUTH_TOKEN` |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `false` | 个人/演示 Dashboard；本地 `ACCESS_MODE=disabled` 时免 token |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | 启用 Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP 路径 |
| `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK` | `false` | 鉴权关闭时显式允许绑定非 loopback 地址 |
| `POWERCONTEXT_SERVER_HTTP_MAX_REQUEST_BODY_BYTES` | `16777216` | 解析前允许的外部请求体字节数（16 MiB） |
| `POWERCONTEXT_SERVER_HTTP_MAX_CONCURRENT_REQUESTS` | `64` | 每进程进行中的外部 HTTP 请求上限 |
| `POWERCONTEXT_SERVER_DATABASE_KIND` | `sqlite` | `sqlite` / `seekdb` / `oceanbase` |
| `POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES` | `8` | `assembly.sections[].limit` 之和上限（§5） |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PREVIEW_TTL_SECONDS` | `300` | 恢复预览有效期（§6） |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PREVIEW_SIGNING_SECRET` | 未设置 | 至少 32 字符；未设置时预览/带 token 恢复返回 `422 invalid_preview`（**直接恢复仍可用**） |
| `POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET` | 本地持久化密钥 | 分页 cursor 签名密钥，至少 32 字节 |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | 在 `/metrics` 暴露 Prometheus 指标 |

### 1.4 `ACCESS_MODE=disabled` 时的行为（重要）

【来源：`operate/configuration.md`、`operate/deploy-server.md`、`develop/http-api.md`】

- `disabled` 表示在**可信本地边界**内跳过授权决策：不带 `Authorization` 也能调用全部 `/v1/**`。
- 可以完全不设 `POWERCONTEXT_SERVER_AUTH_TOKEN`。
- Dashboard、HTTP API、MCP 共用这一套认证设置；`disabled` 时浏览器可直接打开 Dashboard，无需登录。
- **默认只监听 loopback**。鉴权关闭时官方明确要求不要把监听地址改成非 loopback；真需要对外暴露，
  要么启用 `enforced` + Bearer，要么显式设 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`
  （仅在完全受信的网络边界内这么做）。
- 启用 `enforced` 后，`/health/live`、`/health/ready` **始终公开**；API、MCP、`/metrics`、`/openapi.json` 需要 token；
  `/docs` 页面外壳公开，但页面里发出的请求仍需鉴权。

### 1.5 健康检查与能力探测

`GET /health/live` —— **进程活着就返回 200，不需要鉴权**

```bash
curl -sS http://127.0.0.1:8000/health/live
```

```json
{"status": "ok"}
```

> 【来源：契约 `/health/live` 响应 `{status: string}`；字面量 `"ok"` 取自 `src/powercontext/server/app.py:1626-1627`
> `return HealthResponse(status="ok")`，以及 `tests/test_cli_workbuddy.py` 的 stub 响应。】

`GET /health/ready` —— 发业务流量前检查；必需绑定不可用时返回 **503**

```bash
curl -sS http://127.0.0.1:8000/health/ready
```

```json
{
  "status": "ready",
  "checks": {}
}
```

> - `status` 取 `ready` / `degraded` / `not_ready`（`ReadinessStatus`）。
> - **可选推理服务故障时可能返回 200 + `degraded`**，数据库操作仍可用。所以 `curl --fail` 只能判断 HTTP 状态，
>   不能把 `degraded` 当失败；依赖推理时应额外校验 `status == "ready"`。
> - `checks` 是 `{名称: 字符串状态}` 的 map。
> - 【来源：`operate/deploy-server.md`；`ReadinessResponse = {status, checks: dict[str,str]}` 取自 `http/_generated/models.py:4541`。
>   `checks` 具体键名属于实现细节，未在文档中固定，标 **【待验证】**。】

`GET /v1/capabilities` —— 探测这台部署到底开了哪些能力（**写死逻辑前先查它**）

```bash
curl -sS http://127.0.0.1:8000/v1/capabilities \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"
```

关键字段（裁剪示例，字段名与类型来自契约 `Capabilities`）：

```json
{
  "artifact_families": ["atomic-memory", "topic-memory", "experience", "skill", "handoff", "profile", "prompt"],
  "source_types": ["content"],
  "search_modes": ["auto", "fts"],
  "context_versions": ["powercontext.prepared-context.v1"],
  "memory_extraction": false,
  "handoff_generation": false,
  "artifact_dreaming": false,
  "artifact_dreaming_operations": [],
  "experience_generation": false,
  "managed_skill_generation": false,
  "external_skill_registry": false,
  "extraction": {
    "configuration": "unconfigured",
    "background": {"location": "none", "role": null, "state": "stopped", "automatic_processing_enabled": null},
    "observation": {"status": "unverified", "since": "2026-10-27T13:30:00Z", "last_failure": null, "last_success_at": null}
  },
  "prompts": {}
}
```

> - `search_modes` 是**判定能否用向量/混合检索的地方**：没配 Embedding 的机器实测只报 `["auto","fts"]`
>   （见仓库 `evaluation/memory/longmemeval_v2/docs/longmemeval-v2-smoke-delivery.md` 的实测记录），
>   此时 §4 的 `mode: "vector" | "hybrid"` 会失败。
> - `artifact_families` 的**具体成员因部署而异**（上面的列表只是示意），写死之前先打一次接口确认。
> - `context_versions` 含 `powercontext.prepared-context.v1` 时才可调用 §5 的 `prepare`。
> - `artifact_families` 决定 `POST /v1/scopes/{id}/artifacts` 能写哪些 `family`。
> - 其余字段（`extraction.*`、`prompts.*`）为 **裁剪示意**；完整形状以 `/v1/capabilities` 实际返回为准，
>   上面 `extraction`/`prompts` 的具体取值组合标 **【待验证】**。

### 1.6 Dashboard 开关

【来源：`install-and-run.md`、`deploy-server.md`】

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

- 打开 **`http://127.0.0.1:8000/dashboard/home`**（改了端口就用实际端口）。
- Dashboard 和 HTTP API / MCP **共用一个端口**，没有独立 Dashboard 端口。
- `enforced` 时浏览器用 **Server token** 登录（不是模型 API Key）；凭据存在仅限 `/dashboard` 的
  HttpOnly、SameSite=Strict Cookie，最长 8 小时。
- 注入 Authentication Provider / AccessControlService 的部署**必须关闭 Dashboard**，否则启动时报错。
- Dashboard 只适合个人与演示；多成员 RBAC 团队部署应设 `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false`。

其他实用地址：`/docs`（交互式 Scalar API 参考）、`/openapi.json`（本进程实际契约，鉴权后可得）、
`/metrics`（Prometheus，需鉴权）、`/mcp`（MCP）。

### 1.7 常用配置文件写法

```bash
powercontext config init --output .env        # 交互式向导，生成配置
powercontext config show --env-file .env      # 查看（隐藏凭据）
powercontext config validate --env-file .env  # 校验
powercontext server run --env-file .env       # 用这份配置启动
```

启用鉴权的最小写法：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

---

## 2. Scope

Scope 是数据分区边界。**`scope_id` 是不透明 ID，只标识数据，不是访问权限**——切换到别的 `scope_id` 不会帮你打开别人的数据。

### 2.1 创建 Scope

`POST /v1/scopes` → **201**

请求体（`CreateScopeRequest`，`additionalProperties: false`）：

| 字段 | 类型 | 必填 | 限制 |
| --- | --- | --- | --- |
| `title` | string | ✅ | 1–256 字符，非空白 |
| `summary` | string | ✅ | 1–2000 字符，非空白 |
| `idempotency_key` | string | ✅ | 1–256 字符，非空白 |
| `parent_scope_id` | string \| null | ❌ | 1–256 字符 |
| `context_references` | string[] | ❌ | 默认 `[]`，元素 1–256 字符，去重 |
| `external_references` | `{kind, value}[]` | ❌ | 默认 `[]`；`kind` 1–128，`value` 1–2000，去重 |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/scopes" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "title": "Billing assistant",
    "summary": "Billing application context",
    "idempotency_key": "billing-assistant",
    "external_references": [{"kind": "git-repo", "value": "github.com/acme/billing"}]
  }'
```

响应 201（`ScopeDescriptor`）：

```json
{
  "scope_id": "scp_7f3c1a2b9d4e",
  "title": "Billing assistant",
  "summary": "Billing application context",
  "parent_scope_id": null,
  "context_references": [],
  "external_references": [{"kind": "git-repo", "value": "github.com/acme/billing"}],
  "version": 1
}
```

> **关于 `scope_id` 的值**：契约只规定它是 1–256 字符、非空白的不透明字符串，**服务端分配**，文档反复强调
> "不要从目录名、仓库名、分支名或 Agent 会话 ID 推导它"。示例中的 `scp_...` 只是形态示意，**真实前缀/格式【待验证】**；
> 以服务端返回值为准，并把它持久化下来。
>
> **`idempotency_key` 语义**（`docs/zh/docs/develop/api-quickstart.md` + CLI help
> "Stable creation key; repeating it returns the same Scope"）：
> - 同一个 key + 相同参数重复调用 → 返回同一个 Scope（**可安全重试**）。
> - 同一个 key + 不同参数 → **409 `scope_idempotency_conflict`**。
> - 这是"建 scope 步骤可以无脑重跑"的依据。

### 2.2 查询 Scope

```bash
# 按 ID 精确读取
curl -sS "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"

# 读默认 Scope（本机默认绑定目标）
curl -sS "http://127.0.0.1:8000/v1/scopes/default" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"

# 列举（可选 query / query_field / limit(1-100,默认50) / cursor / parent_scope_id / binding_* 等过滤）
curl -sS "http://127.0.0.1:8000/v1/scopes?limit=20" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"
```

`GET /v1/scopes` 响应（`{items, next_cursor}`）：

```json
{
  "items": [
    {
      "scope_id": "scp_7f3c1a2b9d4e",
      "title": "Billing assistant",
      "summary": "Billing application context",
      "parent_scope_id": null,
      "context_references": [],
      "external_references": [],
      "version": 1
    }
  ],
  "next_cursor": null
}
```

`GET /v1/scopes` 的 `query` 过滤说明：按 `query_field` 选中的字段做**数据库原生子串匹配**（非正则、非通配）；
大小写/重音/全半角行为跟随底层数据库 collation，不同后端结果不保证一致。

### 2.3 更新与默认 Scope

`PUT /v1/scopes/{scope_id}` → 200（必须带 `expected_version`，取自上次读到的 `version`）

```bash
curl -sS -X PUT "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "expected_version": 1,
    "title": "Billing assistant v2",
    "summary": "Billing application context (renamed)",
    "external_references": []
  }'
```

`PUT /v1/scopes/default` → 200，请求体 `{"scope_id": "..."}` 切换本机默认 Scope。

### 2.4 Scope 相关错误

| 状态码 | `error.code` | 含义 / 处理 |
| --- | --- | --- |
| 404 | `scope_not_found` | Scope ID 不存在，或没有可用绑定 |
| 409 | `scope_idempotency_conflict` | 同一个 `idempotency_key` 对应了不同参数 |
| 409 | `scope_version_conflict` | `expected_version` 过期；`details` 带 `expected_version`/`current_version` |
| 409 | `scope_binding_target_missing` | 持久绑定指向的 Scope 已丢失（`details.scope_id` 保留原目标）——**运维修数据，不要自动重建** |
| 422 | `invalid_scope_relationship` | 父子关系非法；`details` 带 `relationship`/`issue` |

> 【来源：`openapi/powercontext.yaml` 各响应；`error.code` 取值来自 `src/powercontext/server/app.py:_map_scope_error`
> 与 `_map_base_access_error` 的实际映射，与 `docs/zh/docs/workflows/scopes-and-access.md` 一致。】

权限提示：`enforced` 模式下**创建 Scope 需要 `server.admin`**（`scopes-and-access.md`）。

---

## 3. Memory 写入

记忆以 **`atomic-memory` Artifact** 存储：**正文 revision 不可变，当前生命周期状态单独保存**。
显式创建/修订**不调用生成模型**；只配置了 Embedding 时，发布会额外准备检索向量。

### 3.1 推荐入口：通用 Artifact 创建

`POST /v1/scopes/{scope_id}/artifacts` → **201**

请求体由 `family` 做判别（`discriminator.propertyName = "family"`）：

```json
{
  "family": "atomic-memory",
  "content": {
    "schema": "powercontext.atomic-memory.v1",
    "kind": "decision",
    "text": "公开 API 保持异步。"
  }
}
```

`content` 对象（`AtomicMemoryWriteContent`，`additionalProperties: false`）：

| 字段 | 类型 | 必填 | 限制 |
| --- | --- | --- | --- |
| `schema` | string | ❌ | 唯一合法值 `powercontext.atomic-memory.v1`（默认即此值，可省略） |
| `kind` | string | ✅ | 1–128 字符，非空白。**应用自定义名称**，服务端只校验并原样保存 |
| `text` | string | ✅ | 非空；**新写入先做 Unicode NFC 规范化 + 去首尾空白，结果必须非空且 ≤ 8192 UTF-8 字节** |

```bash
curl -sS -i -X POST "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "family": "atomic-memory",
    "content": {
      "schema": "powercontext.atomic-memory.v1",
      "kind": "decision",
      "text": "公开 API 保持异步。"
    }
  }'
```

响应头（对写记忆非常有用）：

```
Location: /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}
ETag: "revision:1"
```

> ETag 字面量格式取自 `src/powercontext/server/app.py:_artifact_etag` → `f'"revision:{revision}"'`。

响应体 201（`ArtifactCreated`）：

```json
{
  "scope_id": "scp_7f3c1a2b9d4e",
  "family": "atomic-memory",
  "artifact_id": "am_5b8e2f4c71a0",
  "revision": 1,
  "sources": [],
  "artifacts": []
}
```

> - `artifact_id` 由服务端分配，1–128 个可打印 ASCII 字符（`^[\x21-\x7E]+$`）。示例值形态 **【待验证】**，以返回为准。
> - `sources` / `artifacts` 是这次写入的**直接 lineage**。纯创建请求体里没有 lineage 字段，因此实测为 `[]`
>   （服务端另有内部系统 provenance Source，但不通过这两个数组返回）。合并、发布等操作才会填充它们。
> - `revision` 从 1 开始，单调递增。

### 3.2 兼容入口：`/v1/memory/remember`

`POST /v1/memory/remember` → **200**

这是旧 Memory 路由的保留入口，请求层会转换成一次 Atomic 创建。**结果里就已经是真实的 Atomic ArtifactRef**，
不再有旧集合版本 / `entry_version_id` / MemoryCitation。

| 字段 | 类型 | 必填 | 限制 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 字符 |
| `kind` | string | ✅ | 1–128 字符 |
| `text` | string | ✅ | 规范化后 ≤ 8192 UTF-8 字节 |
| `reason` | string \| null | ❌ | ≤ 512 字符（见下方警告） |
| `expected_revision` | integer \| null | ❌ | **必须省略或传 `null`**；传非空值 → 422 `legacy_memory_operation_unsupported` |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/memory/remember" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d "{
    \"scope_id\": \"$SCOPE_ID\",
    \"kind\": \"decision\",
    \"text\": \"Validate refund eligibility before offering a refund action.\",
    \"reason\": \"Confirmed billing policy\"
  }"
```

响应 200（`MemoryMutationResponse`）：

```json
{
  "changed": true,
  "records": [
    {
      "artifact": {
        "family": "atomic-memory",
        "artifact_id": "am_5b8e2f4c71a0",
        "revision": 1
      },
      "kind": "decision",
      "text": "Validate refund eligibility before offering a refund action.",
      "state": "active",
      "state_version": 0,
      "merged_into_id": null
    }
  ]
}
```

> 【来源：响应结构 = 契约 `MemoryMutationResponse{changed, records: AtomicMemoryRecord[]}`，
> `AtomicMemoryRecord{artifact, kind, text, state, state_version, merged_into_id}`；
> `success` 时 `state` 固定 `active`、`merged_into_id` 为 `null`，由 `server/mapping.py:search_response`/
> `atomic_memory.py:record_response` 的实现确认。】
>
> ⚠️ **`reason` 在 1.4.1 里"接受但不落库"**：`server/app.py:remember_memory` 只把 `kind` 和 `text` 组装成
> `AtomicMemoryContent`，`reason` 没有进入内容模型（`AtomicMemoryContent` 只有 `schema`/`kind`/`text`/`creation`）。
> 所以**不要用 `reason` 记录来源**。标 **【待验证】**：后续版本是否会把 `reason` 落到 revision 元数据。

### 3.3 `kind` 用什么值

- 对 `atomic-memory`：`kind` 是**应用自定义**名称，1–128 字符，服务端**只校验、原样保存，绝不猜测或替换**
  （`AtomicMemoryContent.nonblank` 只拒绝全空白）。
- 契约给旧 `memory` family 的条目建议值是 `fact` / `preference` / `decision` / `constraint` / `working_note`。
  这组词作为 `atomic-memory` 的约定俗成取值同样好用，官方示例里出现的也是 `decision`。
- 同一个 `kind` 可以后续用 `filters.kind` / `kind` 过滤（§4）。

### 3.4 tags / metadata：写在哪？

这是最容易踩坑的地方：**创建记忆的请求体里没有 `tags`，也没有 `metadata`**。

| 你想加的东西 | 正确位置 | 说明 |
| --- | --- | --- |
| 标签 / 分类 | `PUT /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/tags` | 独立接口，必须带 `If-Match`；见 §3.6 |
| 任意元数据 | **Atomic Memory 不支持** | `AtomicMemoryWriteContent` 是 `additionalProperties: false`，塞额外字段直接 422 |
| Source 采集时的附加信息 | `POST /v1/sources/content` 的 `metadata` 对象 | 这是 **Source** 的元数据，不是 Memory 的 |

标签规则（`docs/zh/docs/workflows/manage-artifact-tags.md`）：

- 每个对象最多 **32** 个标签；**过滤**时一次接受 **1–16** 个。
- 每个标签 1–64 个 Unicode 码点，**首尾不能有空白**，不能含控制字符/代理字符/未分配字符。
- 匹配键 = NFC 规范化后再 Unicode case folding；显示保留原始写法。规范化后重复（如 `Straße` 与 `STRASSE`）
  会让**整次请求失败**；规范化键不能超过 128 码点。
- 标签是 Scope 内的检索元数据，**不是权限、不会进模型提示词**。

### 3.5 如何记录"来源"

文档给的语义链是：**Source（证据）→ 处理/写入 → Memory（已整理结论）**。实际操作有三种层次：

**(A) 先采集一条 Source，再写 Memory（推荐，可追溯）**

`POST /v1/sources/content` → **202**

| 字段 | 类型 | 必填 | 限制 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 字符 |
| `source_id` | string | ✅ | 1–256 字符，**稳定唯一**（按它幂等） |
| `content` | string | ✅ | ≤ 200000 字符 |
| `metadata` | object | ❌ | 保留键 `kind: "working_note"` 会被拒绝 |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/sources/content" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d "{
    \"scope_id\": \"$SCOPE_ID\",
    \"source_id\": \"billing-validation-2026-08-31\",
    \"content\": \"Refund validation failed for an expired order. Adding boundary tests for eligibility and timezone conversion caught the defect before release.\",
    \"metadata\": {\"origin\": \"application-test-run\"}
  }"
```

响应 202（`CaptureContentSourceResponse`）：

```json
{
  "status": "accepted",
  "source": {"name": "content", "source_id": "billing-validation-2026-08-31"},
  "position": 1
}
```

> 【来源：`docs/zh/docs/develop/api-quickstart.md` 的 Python 示例 + 契约 `CaptureContentSourceResponse`。
> `source.name` 是稳定的 Source 类型名；内置 content Source 的值为 `content`。】
> - 同一个 `source_id` 重复提交完全相同内容 = 幂等；内容不同 → **409 `source_conflict`**。
> - 注意：**采集 Source 不等价于写 Memory**，也不满足"显式记住"的请求；反过来，显式写 Memory 也不要求先有 Source。

**(B) 在合并时给记忆挂显式依据（唯一能写 `source_refs` 的记忆入口）**

`POST /v1/atomic-memory/merges` 的请求体支持 `source_refs` 与 `artifact_refs`，见 §6.4。

**(C) 用标签承载来源标识（最省事的工程做法）**

创建后立刻给这条记忆打一个来源标签：

```bash
curl -sS -X PUT "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/$ARTIFACT_ID/tags" \
  -H 'Content-Type: application/json' \
  -H 'If-Match: "revision:1"' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{"tags": ["src:billing-validation-2026-08-31", "release"]}'
```

然后就能用 `tags` 精确过滤（§4.3）。

> 说明：**(C) 是工程变通，不是文档承诺的 provenance 机制**。契约层面 `atomic-memory` 创建**不支持** source 关联，
> 标 **【待验证】**：后续版本是否会像 `topic-memory`/`handoff` 那样在创建体里开放 `source_refs`。
> 如果你需要严格可审计的来源，走 (A) + (B)。

### 3.6 写入限制与错误

| 现象 | 状态码 | `error.code` | 说明 |
| --- | --- | --- | --- |
| 正文超限 | 422 | `invalid_request`（`details.code: "text-too-long"`） | 规范化后 > 8192 UTF-8 字节 |
| 正文空白 | 422 | `invalid_request` | `text` 去空白后为空，或 `kind` 全空白 |
| 多余字段 | 422 | `invalid_request` | `additionalProperties: false`，`content` 里不许多写字段 |
| `remember` 传了 `expected_revision` | 422 | `legacy_memory_operation_unsupported` | `details` 含 `operation`/`alternatives`/`instruction` |
| 同 `source_id` 不同内容 | 409 | `source_conflict` | Source 幂等冲突 |
| 写入闸门拒绝 | 422 | `memory_write_rejected` | `details` 含 `code`/`reason`（需开 `MEMORY_WRITE_GATE_ENABLED`） |
| 能力缺失 | 422 | `capability_not_supported` | `details.capability` |
| 运行时未就绪 | 503 | `runtime_not_ready` | 依赖未装配 |

---

## 4. Memory 读取与检索

四个入口，别混用：

| 入口 | 路径 | 用途 | 需要模型？ |
| --- | --- | --- | --- |
| **Atomic 列表** | `POST /v1/atomic-memory/list` | 按状态/kind/标签翻页，**不走语义查询** | 否 |
| **Atomic 检索** | `POST /v1/atomic-memory/search` | 专用检索，返回当前状态 + 原分 RRF | `text`/`fts` 模式不需要 |
| **统一检索** | `POST /v1/scopes/{scope_id}/artifacts/atomic-memory/search` | 支持 `fusion` 权重、`min_score`、`include_scores`，返回**不可变 content + lineage** | `vector`/`hybrid` 需要 |
| **兼容入口** | `POST /v1/memory/entries/list`、`POST /v1/memory/search` | 旧参数形状（`tag_filter`、`auto/fts`） | 同左 |

### 4.1 列表（Atomic）

`POST /v1/atomic-memory/list` → 200

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 |
| `states` | string[] | ❌ | `active` / `forgotten` / `merged` / `retired` 任意组合；**不传时只列 `active`** |
| `kind` | string | ❌ | 精确匹配 |
| `tags` | string[] | ❌ | 最多 16 个 |
| `tag_match` | `all` \| `any` | ❌ | 传 `tags` 时默认 `all`；**只传 `tag_match` 不传 `tags` → 422** |
| `limit` | integer | ❌ | 1–100，默认 **50** |
| `cursor` | string \| null | ❌ | 上一页的 `next_cursor`；≤ 4096 字符 |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/atomic-memory/list" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d "{
    \"scope_id\": \"$SCOPE_ID\",
    \"states\": [\"active\", \"forgotten\"],
    \"kind\": \"decision\",
    \"limit\": 50
  }"
```

响应 200（`ListAtomicMemoryResponse`）：

```json
{
  "items": [
    {
      "artifact": {"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 2},
      "kind": "decision",
      "text": "公开 API 保持异步，内部同步适配器另行提供。",
      "state": "active",
      "state_version": 0,
      "merged_into_id": null
    }
  ],
  "next_cursor": null
}
```

> `next_cursor` 是 **nullable**，但字段始终存在（契约 `required`）。翻页时**原样回传**，并保持 Scope / 过滤条件一致。

兼容入口 `POST /v1/memory/entries/list` 的差别：用 `include_inactive: true|false`（默认 `false`）代替 `states`，
响应字段是 `entries` 而不是 `items`：

```json
{
  "entries": [
    {
      "artifact": {"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 2},
      "kind": "decision",
      "text": "公开 API 保持异步，内部同步适配器另行提供。",
      "state": "active",
      "state_version": 0,
      "merged_into_id": null
    }
  ],
  "next_cursor": null
}
```

> `entries[]` 的元素与 `items[]` 完全是同一个 `AtomicMemoryRecord` 结构，只是字段名从 `items` 变成 `entries`
> （契约 `ListMemoryEntriesResponse`）。

### 4.2 检索（Atomic 专用）

`POST /v1/atomic-memory/search` → 200

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 |
| `query` | string | ✅ | 1–8192 字符 |
| `mode` | string | ❌ | **`text`（默认）/ `vector` / `hybrid`**；注意这里**没有 `auto`、没有 `fts`** |
| `kind` | string \| null | ❌ | 精确过滤 |
| `tags` | string[] | ❌ | 1–16 个，每个 1–64 字符 |
| `tag_match` | `all` \| `any` | ❌ | 默认 `all` |
| `limit` | integer | ❌ | 1–100，默认 **20** |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/atomic-memory/search" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d "{
    \"scope_id\": \"$SCOPE_ID\",
    \"query\": \"公开 API\",
    \"mode\": \"text\",
    \"kind\": \"decision\",
    \"tags\": [\"release\"],
    \"tag_match\": \"all\",
    \"limit\": 10
  }"
```

响应 200（`SearchAtomicMemoryResponse`）：

```json
{
  "mode": "text",
  "hits": [
    {
      "memory": {
        "artifact": {"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 2},
        "kind": "decision",
        "text": "公开 API 保持异步，内部同步适配器另行提供。",
        "state": "active",
        "state_version": 0,
        "merged_into_id": null
      },
      "score": 0.01639344262295082,
      "matched_by": ["text"]
    }
  ]
}
```

返回结构逐字段说明：

| 字段 | 含义 |
| --- | --- |
| `mode` | **实际执行**的检索模式，枚举 `text` / `vector` / `hybrid`（**响应里永远不会是 `auto`/`fts`**） |
| `hits[].memory` | 一条 `AtomicMemoryRecord`：`artifact`（精确 `{family, artifact_id, revision}`）、`kind`、`text`、`state`、`state_version`、`merged_into_id` |
| `hits[].score` | **原始 RRF 分**（未归一化）。混合模式下已套用部署配置的 FTS/向量相对权重 |
| `hits[].matched_by` | 命中通道名数组，元素枚举 `text` / `vector`；纯文本检索就是 `["text"]` |

> - 本专用入口**只返回 `active` 记忆**，`memory.state` 恒为 `active`，`merged_into_id` 恒为 `null`
>   （`server/atomic_memory.py:search_atomic_memory` 硬编码）。想看 forgotten/merged 请用 §4.1 列表或 §6 的状态接口。
> - 引用结果时**保留整个 `memory.artifact`**，不要只存 `artifact_id`。
> - 【来源：`docs/zh/docs/workflows/atomic-memory.md` 搜索章节 + 契约 `SearchAtomicMemoryResponse`。
>   `score` 数值为示意，真实值随语料/后端变化。】

兼容入口 `POST /v1/memory/search` 的差别（`SearchMemoryRequest`）：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | — | 必填 |
| `query` | string | — | 必填，≤ 8192 |
| `limit` | integer | **10** | **1–50**（比专用入口小） |
| `mode` | string | **`auto`** | `auto` / `fts` / `vector` / `hybrid`；**`fts` 在实现里被映射成 `text`** |
| `tag_filter` | `{tags, match}` | — | 旧标签结构，`match` 默认 `all` |

响应 `mode` 仍是 `text`/`vector`/`hybrid` 三值。`auto` 的实现语义（按部署能力挑通道）属于运行时行为，
文档未给出确定映射，**具体选择规则【待验证】**。

### 4.3 检索（统一入口，需要 `fusion` 权重时用这个）

`POST /v1/scopes/{scope_id}/artifacts/atomic-memory/search` → 200

> 路径里的 `{family}` 直接写字面量 `atomic-memory`。`memory` 这个 family **已移除，会返回 404**。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | ✅ | 1–8192，非空白 |
| `limit` | integer | ❌ | schema 允许 1–200；**`atomic-memory` family 实际为 1–100**，默认 10 |
| `mode` | string | ❌ | 省略时对 Atomic **恒为文本检索**；显式 `vector`/`hybrid` 要求 Embedding profile，且**不回退** |
| `filters` | object | ❌ | Atomic 支持 `kind`、`tags`、`tag_match` |
| `admission` | object | ❌ | 词法：`lexical_coverage`（默认 0.25，0–1）、`lexical_min_matched_terms`（默认 2，≥1）；语义：`min_semantic_similarity`（默认 0.3，-1–1） |
| `fusion` | object | ❌ | `{"method": "rrf", "params": {"rank_constant": 60, "weights": {"text": 2, "vector": 1}}}` |
| `min_score` | number | ❌ | 0–1，按**归一化**检索分过滤，先于重排和截断 |
| `include_scores` | boolean | ❌ | 默认 `false`；`true` 才返回 `scores` |
| `rerank` | object | ❌ | **请求不开放**（传了会被拒）；部署配置的重排器仍会自动生效 |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/search" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "query": "release rollback",
    "mode": "hybrid",
    "limit": 10,
    "filters": {"kind": "decision", "tags": ["release"], "tag_match": "all"},
    "fusion": {"method": "rrf", "params": {"rank_constant": 60, "weights": {"text": 2}}},
    "include_scores": true
  }'
```

响应 200（`SearchArtifactsResponse`）：

```json
{
  "results": [
    {
      "family": "atomic-memory",
      "artifact_id": "am_5b8e2f4c71a0",
      "revision": 2,
      "content": {
        "schema": "powercontext.atomic-memory.v1",
        "kind": "decision",
        "text": "公开 API 保持异步，内部同步适配器另行提供。"
      },
      "lineage": {"sources": [], "artifacts": []},
      "scores": {
        "retrieval": 0.5,
        "channels": {
          "text": {"raw": -0.000004, "metric": "sqlite_bm25", "higher_is_better": false}
        }
      }
    }
  ]
}
```

> - **和 §4.2 的关键区别**：`results[].content` 是**不可变的完整正文**，`lineage` 是持久化 lineage；
>   当前生命周期状态（`state`/`state_version`）**不在这里**，要另外读 §6.3 的 state 接口。
> - `scores.retrieval` 是**归一化到 `[0,1]` 的 RRF 分**；`scores.channels.<通道>.raw` 是通道原分
>   （`sqlite_bm25` 越小越好、`oceanbase_match` 越大越好、`l2_distance` 越小越好）。
>   没命中的通道不会出现，也不会补造 `0`。
> - 【来源：`docs/zh/docs/workflows/search-artifacts.md`（含 `scores` 示例原文）+ `workflows/search-fusion.md`
>   + 契约 `SearchArtifactsRequest` / `ArtifactSearchScores` / `ArtifactChannelScore`。】

### 4.4 `fusion` 参数与权重规则（RRF）

【来源：`docs/zh/docs/workflows/search-fusion.md`】

**当前唯一支持的融合方法就是 `rrf`（Reciprocal Rank Fusion）。**

```text
raw_rrf(x)   = Σ [ w_c / (K + rank_c(x)) ]      对包含 x 的通道求和
upper        = Σ [ w_c / (K + 1) ]              对所有启用通道求和
retrieval(x) = raw_rrf(x) / upper
```

| 字段 | 类型 | 默认 | 合法范围 |
| --- | --- | --- | --- |
| `fusion.method` | string | 省略 `fusion` 时用 `rrf` | **只支持 `rrf`** |
| `fusion.params.rank_constant` | integer | `60` | 正整数（即公式里的 `K`） |
| `fusion.params.weights` | object | `{}` | 有限非负数；启用通道总权重必须为正 |

| Family | 模式 | 合法权重键 |
| --- | --- | --- |
| Atomic | `text` | `text` |
| Atomic | `vector` | `vector` |
| Atomic | `hybrid` | `text`、`vector` |
| Topic | `text` | `topic_fts`、`detail_fts` |
| Topic | `vector` | `topic_vector`、`detail_vector` |
| Topic | `hybrid` | 上述四个 |

硬规则：

- `K` 只能通过 `fusion.params.rank_constant` 设；**顶层 `K` 或 `rank_constant` 都非法**。
- 数字字符串、布尔值、显式 `null`、非有限数、未知参数键、负权重 **一律拒绝**。
- 省略 `fusion` → 混合检索用部署级权重，单通道检索用权重 `1`。
- **显式提供 `fusion` 会完整取代部署级权重，即使 `params` 为空**。
- 权重可以只覆盖部分通道，未列出的启用通道仍用 `1`；权重 `0` 会把该通道从分子和分母一起去掉。
- 所有启用通道总权重为 `0` → 拒绝。
- 未启用或未知的通道键，**即使权重为 `0` 也会被拒绝**。
- 融合分并列时：按 Artifact ID 的 UTF-8 升序，再按 Revision 降序。

> **可粘贴的最小正确写法**：`"fusion": {"method": "rrf"}` 或 `"fusion": {"method": "rrf", "params": {"weights": {"text": 2}}}`。

### 4.5 检索常见错误

| 状态码 | `error.code` | 场景 |
| --- | --- | --- |
| 404 | `artifact_family_not_found` | 路径 `{family}` 未知或已移除（如 `memory`） |
| 422 | `artifact_search_not_supported` | 该 family 不支持统一检索（`profile`/`handoff`/`prompt`），或检索能力未配置；`details.field` 指出字段 |
| 422 | `invalid_request` | 字段类型/取值非法（显式 `null`、`limit` 非整数、禁用字段等） |
| 503 | `inference_timeout` | 向量化超时（**不能回退时**才报错） |
| 503 | `inference_unavailable` | Embedding provider 报错 |

> 缩小 `limit` 或用空 Scope **都不能绕过参数校验**；模型超时/不可用在不可回退时返回 503；
> 实现与存储故障保留失败状态，**不会伪造成成功的空结果**。

---

## 5. PreparedContext（为一次模型请求组装有界上下文）

`POST /v1/context/prepare` → 200

这是"给 Dify 用"最关键的接口：它返回**一段可以直接塞进 system 消息的 Markdown**，并且是临时只读的。

### 5.1 请求

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 |
| `query` | string | ✅ | 1–8192，非空白 |
| `max_bytes` | integer | ❌ | **512–32768，默认 8000**；限制 `content` 的 UTF-8 字节数 |
| `include_code` | boolean | ❌ | 默认 `false`；纳入当前 Scope 的本地代码证据（准备过程中不建索引） |
| `explain` | boolean | ❌ | 默认 `false`；开启后若有通道丢失，响应才多一个 `reason` |
| `assembly` | object | ❌ | 显式分组 Markdown。**省略 = 保留原有输出与选择行为；传 `null` 是非法值** |
| `bootstrap_receipt_id` | string | ❌ | 1–64 个可打印 ASCII；上次已注入 bootstrap 包的回执 ID |

`assembly` 细节（`ContextAssembly`，`additionalProperties: false`）：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `format` | string | `"markdown"` | 唯一合法值 `markdown` |
| `sections` | array（**最多 4 项**） | `[{"family":"memory","limit":6},{"family":"experience","limit":2}]` | **数组顺序 = 输出顺序 + 字节预算优先级**；`family` 枚举 `memory`/`experience`/`profile`/`topic-memory`；`limit` 1–8 |
| `show` | string[]（最多 2，去重） | `[]` | 可选 `confidence`、`recall_rank`（`confidence` 目前固定显示 `unknown (not assessed)`） |

`sections[].limit` 的硬约束：

- 各类别单独上限：Memory / Profile / Topic Memory 各 **8**，Experience **2**。
- **所有 section 的 `limit` 之和必须 ≤ 服务端 `context_assembly_max_entries`（默认 8）**，超出 → **422**（召回前就拒）。
- `sections: []` = 完成校验后返回空结果、不召回任何候选（但仍然可用）。
- 重复 family、非法 limit、`sort_by`/`min_confidence` 等不支持的字段、`assembly: null` → **422**。

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/context/prepare" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "scope_id": "project:demo",
    "query": "修改 OpenAPI 后，如何修复客户端契约不一致的问题？",
    "max_bytes": 8000,
    "assembly": {
      "format": "markdown",
      "sections": [
        {"family": "experience", "limit": 2},
        {"family": "memory", "limit": 5}
      ],
      "show": ["confidence", "recall_rank"]
    }
  }'
```

### 5.2 响应

命中时（`status: "ready"`）：

```json
{
  "schema": "powercontext.prepared-context.v1",
  "status": "ready",
  "content": "## Memory\n\n...\n",
  "content_bytes": 1832
}
```

空结果（`status: "empty"`，**这是成功**）：

```json
{
  "schema": "powercontext.prepared-context.v1",
  "status": "empty",
  "content": null,
  "content_bytes": 0
}
```

请求里带 `"explain": true` 且确实丢了一条本来会用的通道时，才多一个 `reason`：

```json
{
  "schema": "powercontext.prepared-context.v1",
  "status": "ready",
  "content": "## Memory\n\n...\n",
  "content_bytes": 1832,
  "reason": "inference_timeout"
}
```

> - `reason` 枚举只有两个值：`inference_timeout`（`recall_embedding_timeout_seconds` 预算耗尽）、
>   `inference_unavailable`（provider 报错）。**没丢通道时该键不出现**；没请求 `explain` 的响应永远只有那四个字段
>   —— 这正是 Host 端可以硬校验四字段的原因。
> - 【来源：`docs/zh/docs/workflows/prepare-context-text.md`（原文示例与四字段约定）+ 契约 `PreparedContext`
>   + `server/mapping.py:prepared_context_response`（该键按需添加）】

### 5.3 行为要点

【来源：`docs/zh/docs/workflows/prepare-context-text.md`】

| 配置 | 行为 |
| --- | --- |
| 省略 `assembly` | 保持原有输出和选择行为；**默认还会召回当前 Scope 可用的 Topic Memory** |
| `"assembly": {}` | Markdown，Memory 最多 6 条 → Experience 最多 2 条；**不含 Topic Memory、不含 Profile** |
| `"assembly": {"sections": []}` | 返回空，不召回候选 |
| 只配 `memory` | 只召回 Memory，limit 1–8 |
| 只配 `experience` | 只召回**已批准**的 Experience，limit 1–2 |
| 只配 `profile` | 读所选 Scope 的最新正式画像快照，limit 1–8（每次 prepare **不触发生成**） |
| 只配 `topic-memory` | 当前 Scope 内检索 Topic Memory，limit 1–8 |

其他规则：

- `max_bytes` 限制的是**完整文本的 UTF-8 字节数**；**每条正文上限 2000 bytes**。空间不足时服务端会截短正文或跳过条目，
  但保留完整引用与边界，所以**实际条数可能少于请求条数**。
- 接入端应**原样使用 `content`，不要二次裁剪**；可以在它外面加自己的提示。
- 同类内保留召回顺序（含 Memory reranker 的排序）；前面的候选装不进去时 rank 可能不连续。
- 组装 Topic 时每次 Topic 查询 Embedding **最多等 250ms**，超时回退 FTS；这个选择在同一次 prepare 的扩展轮次内复用。
- PreparedContext 是**临时、只读**数据：不持久化、不注入、不创建任何新 Revision。**返回了 content 不代表 Host 真的注入了**。
- 关闭/放宽组合上限：`POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES=16`（需重启）。

### 5.4 用 jq 安全落盘的官方写法（避免误清空文件）

```bash
set -euo pipefail
tmp_context="$(mktemp "${TMPDIR:-/tmp}/powercontext-context.XXXXXX")"
trap 'rm -f "$tmp_context"' EXIT
curl --fail-with-body -sS http://127.0.0.1:8000/v1/context/prepare \
  -H 'Content-Type: application/json' --data-binary @prepare.json \
  | jq -er 'if .status == "empty" then "" elif .status == "ready" and (.content | type) == "string" then .content else error("unexpected prepare response") end' \
  > "$tmp_context"
mv "$tmp_context" context.md
```

> 好处：HTTP 错误不会被 `jq` 误报成"空结果"，也不会先清空已有文件。

---

## 6. 修订 / 遗忘 / 恢复

Atomic Memory 的核心语义：**正文 revision 不可变**，`PUT` 产生下一条 revision；**生命周期状态独立保存**，
遗忘/恢复**不改变正文 revision**（`state_version` 单独递增）。

### 6.1 产生新 revision

`PUT /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}` → 200

**必须带 `If-Match`**，值为读取时拿到的正文 ETag `"revision:N"`。

```bash
# 先读到当前 ETag
curl -sS -D - -o /dev/null "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/$ARTIFACT_ID" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"
# 记下 ETag: "revision:2"
```

```bash
curl -sS -X PUT "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/$ARTIFACT_ID" \
  -H 'Content-Type: application/json' \
  -H 'If-Match: "revision:2"' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "content": {
      "kind": "decision",
      "text": "公开 API 保持异步，内部同步适配器另行提供。"
    }
  }'
```

响应 200：新的 revision（`revision` 变成 3），响应头 `ETag: "revision:3"`。
响应体形状与 §3.1 的 `ArtifactCreated` 类似，但带 `content`/`content_digest`：

```json
{
  "scope_id": "scp_7f3c1a2b9d4e",
  "family": "atomic-memory",
  "artifact_id": "am_5b8e2f4c71a0",
  "revision": 3,
  "content": {
    "schema": "powercontext.atomic-memory.v1",
    "kind": "decision",
    "text": "公开 API 保持异步，内部同步适配器另行提供。"
  },
  "sources": [],
  "artifacts": [],
  "content_digest": "sha256:9f2a...c41e"
}
```

| 情况 | 状态码 | `error.code` |
| --- | --- | --- |
| 缺少 `If-Match` | **428** | `precondition_required` |
| ETag 过期 | **412** | `revision_conflict`（`details` 带 `provided_etag` / `current_etag`） |
| 正文非法 | 422 | `invalid_request` |

> Atomic Memory 的 Replace **不接受** `restored_from_revision` / `reason`（这两个字段用于 Profile/Prompt/Experience/
> Skill/Handoff/Topic Memory 的回滚）；回滚 Atomic 内容请走 §6.5 的 restoration 流程。
> 普通 Replace 可以修订 `active` 或 `forgotten` 的正文并保留其状态；**`merged`、`retired` 不能直接修订**。
> 恢复旧版本时保留当时的正文，**不重新套用新的规范化规则**。

### 6.2 读不可变历史

```bash
# 版本列表（降序，不含正文）
curl -sS "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/$ARTIFACT_ID/revisions?limit=50" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"

# 读精确历史 revision
curl -sS "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/$ARTIFACT_ID/revisions/1" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"
```

列表响应 200：

```json
{
  "items": [
    {
      "scope_id": "scp_7f3c1a2b9d4e",
      "family": "atomic-memory",
      "artifact_id": "am_5b8e2f4c71a0",
      "revision": 3,
      "sources": [],
      "artifacts": [],
      "content_digest": "sha256:9f2a...c41e",
      "created_at": "2026-10-27T13:41:02Z"
    }
  ],
  "next_cursor": null
}
```

精确 revision 响应 200 额外带 `content`（结构同 §6.1）。`created_at` 对很旧的 revision 可能缺失。

### 6.3 读当前生命周期状态

`GET /v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/state` → 200

```bash
curl -sS -i "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/atomic-memory/$ARTIFACT_ID/state" \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN"
```

```json
{
  "artifact": {"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 3},
  "state": "active",
  "state_version": 0,
  "merged_into_id": null
}
```

> **这个接口的 ETag 格式和正文接口不同**：`"revision:{revision}:state:{state_version}"`
> （取自 `server/atomic_memory.py:get_atomic_memory_state`）。它同时覆盖正文 revision 和状态版本，
> 支持 `If-None-Match` → **304**。**修订正文仍用正文接口的 `"revision:N"` ETag**，别混用。

四种状态（`docs/zh/docs/workflows/atomic-memory.md`）：

| 状态 | 含义 |
| --- | --- |
| `active` | 参与普通搜索和上下文召回 |
| `forgotten` | 正文与历史保留，**可恢复在役** |
| `merged` | 作为合并输入被冻结；`merged_into_id` 指向合并结果 |
| `retired` | 退出当前使用，**不能编辑也不能恢复**；要重新采用正文请新建 Artifact |

### 6.4 遗忘

`POST /v1/atomic-memory/lifecycle` → 200

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 |
| `target.artifact` | object | ✅ | 精确 `{family, artifact_id, revision}`；`family` 必须是 `atomic-memory` |
| `target.state_version` | integer | ✅ | 取自 §6.3 的当前 `state_version` |
| `state` | string | ✅ | **1.4.1 只接受 `forgotten`** |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/atomic-memory/lifecycle" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "scope_id": "project:demo",
    "target": {
      "artifact": {"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 3},
      "state_version": 0
    },
    "state": "forgotten"
  }'
```

响应 200：

```json
{
  "changed": true,
  "records": [
    {
      "artifact": {"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 3},
      "kind": "decision",
      "text": "公开 API 保持异步，内部同步适配器另行提供。",
      "state": "forgotten",
      "state_version": 1,
      "merged_into_id": null
    }
  ]
}
```

> **遗忘不增加正文 revision**（`revision` 仍是 3），只把 `state_version` 从 0 推到 1。
> `revision` 和 `state_version` 必须**原样带回**读到的值，写死示例值会 409。

### 6.5 恢复 / 撤销合并

**没有独立的 "reactivate/unforget" 端点**——恢复统一走 restoration。

先预览影响范围：`POST /v1/atomic-memory/restoration-previews` → 200

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/atomic-memory/restoration-previews" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{"scope_id":"project:demo","operation":"restore","target":{"artifact_id":"am_5b8e2f4c71a0","revision":3}}'
```

```json
{
  "preview_token": "pv_1f8c...",
  "expires_at": "2026-10-27T13:46:02Z",
  "endpoint": {"artifact_id": "am_5b8e2f4c71a0", "revision": 3, "state_version": 1},
  "restore": [{"artifact_id": "am_5b8e2f4c71a0", "source_revision": 3, "creates_revision": true}],
  "retire": [],
  "undo_merge_results": []
}
```

再提交：`POST /v1/atomic-memory/restorations` → 200

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/atomic-memory/restorations" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "scope_id": "project:demo",
    "operation": "restore",
    "target": {"artifact_id": "am_5b8e2f4c71a0", "revision": 3},
    "preview_token": "pv_1f8c..."
  }'
```

```json
{
  "changed": true,
  "restored": [{"family": "atomic-memory", "artifact_id": "am_5b8e2f4c71a0", "revision": 4}],
  "retired": [],
  "undo_merge_results": []
}
```

| 字段 | 说明 |
| --- | --- |
| `target.artifact_id` | 必填 |
| `target.revision` | 可选；指定时目标的新版本使用该历史正文 |
| `operation` | `restore`（默认）或 `undo_merge` |
| `preview_token` | 可选；带 token 才校验预览一致性 |

行为（`docs/zh/docs/workflows/atomic-memory.md`）：

- 恢复 `forgotten` → 重新在役。
- 恢复**已经 active 且未指定 revision** → `changed: false`，**不追加版本**。
- 恢复 `merged` → **撤销冻结该目标的后续合并**。例：A+B→C、C+D→E，恢复 B 会让 A、B、D 在役，C、E 退役。
- 每个实际恢复/退役的记忆都在**原 Artifact 身份下追加新 revision**，版本号 = 当前 revision + 1。
- `undo_merge` 用来撤销"创建结果 C 的合并"，`target: {"artifact_id": "C"}`；此时**不能同时指定 revision**。
- 普通下游 Artifact 和 Source cursor **不随之回滚**。
- 合并与恢复**没有 `idempotency_key`**，不会自动重放旧响应。连接中断时先读各对象状态再决定是否重试。

### 6.6 合并

`POST /v1/atomic-memory/merges` → 200

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/atomic-memory/merges" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "scope_id": "project:demo",
    "inputs": [
      {"artifact": {"family": "atomic-memory", "artifact_id": "am_aaa", "revision": 1}, "state_version": 0},
      {"artifact": {"family": "atomic-memory", "artifact_id": "am_bbb", "revision": 3}, "state_version": 2}
    ],
    "content": {"kind": "decision", "text": "公开 API 异步，内部适配器可同步。"},
    "source_refs": [{"name": "content", "source_id": "billing-validation-2026-08-31"}],
    "artifact_refs": [{"family": "experience", "artifact_id": "exp-1", "revision": 2}]
  }'
```

- `inputs` **至少两个** active 输入的精确引用 + `state_version`。
- 服务端创建结果 C，把 A、B 置为 `merged`。
- `source_refs`（`{name, source_id}`）与 `artifact_refs`（`{family, artifact_id, revision}`）是**额外的显式依据**；
  合并输入本身也会成为精确 Artifact 依据。**这是记忆写入侧唯一能显式挂 source 的地方**（见 §3.5）。

### 6.7 修订/恢复相关错误码

| 状态码 | `error.code` | 处理 |
| --- | --- | --- |
| 428 | `precondition_required` | 补 `If-Match` |
| 412 | `revision_conflict` | 重新读取正文 ETag 再提交 |
| 409 | `invalid_memory_state` | 别直接编辑 `merged`，别恢复 `retired` |
| 409 | `atomic_memory_changed` | 当前内容或状态已变，重读后决定是否重试 |
| 409 | `invalid_memory_relation` | 关系数据不一致，需要排查存储 |
| 409 | `preview_expired` | 重新预览 |
| 409 | `preview_stale` | 重读影响范围并重新预览 |
| 422 | `invalid_preview` | 检查 token / 主体 / Scope / operation / target 及签名配置 |
| 422 | `legacy_memory_operation_unsupported` | 用了旧集合语义（如 `entries/revise`），改走 Atomic 接口 |

> 【来源：`docs/zh/docs/workflows/atomic-memory.md` 的错误表；`error.code` 字面量与
> `server/app.py:_map_domain_error`（`AtomicMemoryError` → 409、`InvalidAtomicMemoryPreviewError` → 422）一致。】

---

## 7. Topic Memory

Topic Memory 是**面向长期主题**的 Artifact family：把一个 Scope 里持续累积的 Source 处理成
`title` / `summary` / 渐进式 `detail`，适合"关于 X，我们这几次聊出了什么结论"这类问题。
**它不替代 Memory / Experience / Skill / Handoff。**

> **是否需要模型**：自动生成需要 Generation 模型 + 正数
> `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` + 有可归纳的 Source 证据。
> **但你可以直接通过通用 Artifact 接口写完整主题，这条路径不调用模型**（下面 7.1）。

### 7.1 直接创建（不经过模型）

`POST /v1/scopes/{scope_id}/artifacts` → 201

```json
{
  "family": "topic-memory",
  "content": {
    "title": "Release 流程与回滚策略",
    "summary": "关于 release 分支、灰度与回滚判定的既有结论。",
    "detail": "（完整正文，可逐步展开）"
  }
}
```

| 字段 | 限制 |
| --- | --- |
| `title` | 1–512 字符，非空白 |
| `summary` | 1–8000 字符，非空白 |
| `detail` | 1–125000 字符，非空白 |

- 内容**不经过语义生成**；主题内容、当前 head、分块和当前部署启用的检索索引**在同一次提交内完成**，
  所以通用读取和专用检索看到的是同一个版本。
- 创建需要 `scope.contribute`；**整体替换（`PUT .../artifacts/topic-memory/{artifact_id}`）需要 `scope.admin`**，
  并且必须带当前 head 的 `If-Match`。
- 通用接口**不提供 Topic Memory 的 delete / retire**。

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "family": "topic-memory",
    "content": {
      "title": "Release 流程与回滚策略",
      "summary": "关于 release 分支、灰度与回滚判定的既有结论。",
      "detail": "1) 先灰度 5%。2) 关键指标异常 10 分钟内回滚。3) 回滚后冻结 24 小时。"
    }
  }'
```

响应 201：

```json
{
  "scope_id": "scp_7f3c1a2b9d4e",
  "family": "topic-memory",
  "artifact_id": "topic-release",
  "revision": 1,
  "sources": [],
  "artifacts": []
}
```

> `artifact_id` 同样是服务端分配；`topic-release` 只是可读形态示意，**真实值以返回为准【待验证】**。

### 7.2 检索主题摘要

`POST /v1/topic-memory/search` → 200

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `scope_id` | string | ✅ | 1–256 |
| `query` | string | ✅ | 1–8192，首尾必须非空白 |
| `limit` | integer | ❌ | **1–20，默认 10** |

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/topic-memory/search" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{"scope_id":"project:demo","query":"release process","limit":5}'
```

响应 200（`SearchTopicMemoryResponse`）：

```json
{
  "mode": "fts",
  "hits": [
    {
      "artifact": {"family": "topic-memory", "artifact_id": "topic-release", "revision": 3},
      "title": "Release 流程与回滚策略",
      "summary": "关于 release 分支、灰度与回滚判定的既有结论。",
      "snippet": "关键指标异常 10 分钟内回滚",
      "score": 87.5,
      "matched_by": ["topic_fts"]
    }
  ]
}
```

> - `mode` 是**部署实际使用的模式**，枚举只有 **`fts` / `hybrid`**，**调用方不能选**。
> - `score` 是**加权 RRF 归一化到 0–100 的旧评分**（不是 `[0,1]`）。
> - `snippet` 可为 `null`；`matched_by` 至少 1 项（Topic 通道名：`topic_fts`/`detail_fts`/`topic_vector`/`detail_vector`）。
> - 【来源：`docs/zh/docs/workflows/topic-memory.md` + 契约 `SearchTopicMemoryResponse`/`SearchTopicMemoryHit`。】

### 7.3 读精确 Revision（拿全文）

`POST /v1/topic-memory/get` → 200

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/topic-memory/get" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{
    "scope_id": "project:demo",
    "artifact": {"family": "topic-memory", "artifact_id": "topic-release", "revision": 3}
  }'
```

```json
{
  "artifact": {"family": "topic-memory", "artifact_id": "topic-release", "revision": 3},
  "title": "Release 流程与回滚策略",
  "summary": "关于 release 分支、灰度与回滚判定的既有结论。",
  "detail": "1) 先灰度 5%。2) 关键指标异常 10 分钟内回滚。3) 回滚后冻结 24 小时。",
  "source_refs": [{"name": "content", "source_id": "release-runbook-2026-09"}]
}
```

> **不要用标题自己拼最新版本**：把搜索结果里的整个 `artifact` 原样传进来。即使 head 后来前进了，
> 精确引用仍指向同一历史 Revision（适合审计/引用/渐进式 disclosure）。

### 7.4 请求后台处理（可选，需要 Generation 模型）

`POST /v1/topic-memory/flush` → 200

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/topic-memory/flush" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{"scope_id":"project:demo"}'
```

```json
{"status": "accepted"}
```

- `accepted` = 请求已持久化；`idle` = Source 游标本来就已经最新。**两者都不等于"已经生成了某个主题"。**
- 需要 `scope.contribute`。没配 Generation 模型时 flush 不会凭空产生主题。

处理状态/阻塞诊断（需要 `scope.read`）：

```bash
curl -sS -X POST "http://127.0.0.1:8000/v1/topic-memory/processing/status" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_TOKEN" \
  -d '{"scope_id":"project:demo"}'
```

```json
{
  "state": "idle",
  "source_cursor": 12,
  "source_head": 12,
  "target_source_through": 12,
  "block": null
}
```

`state` 枚举 `idle` / `pending` / `blocked`。阻塞时 `block` 含 `block_id`、`attempts`、`error_code`、
`last_error_code`、`blocked_at` 等。重试用 `POST /v1/topic-memory/processing/retry`（需 `scope.admin`），
读具体阻塞记录用 `POST /v1/topic-memory/processing/blocks/get`（`block_id` ≤ 36 字符）。

> `block` 的结构在契约里是 `any`（返回 `null` 或对象），具体字段文档在 `topic-memory.md` 的文字描述里，
> 标 **【待验证】**：`block` 的完整字段集以实际返回为准。

### 7.5 Topic Memory 的两种检索入口对比

| | `POST /v1/topic-memory/search`（专用） | `POST /v1/scopes/{id}/artifacts/topic-memory/search`（统一） |
| --- | --- | --- |
| 模式 | 部署决定，只返回 `fts`/`hybrid` | 可选 `text`/`vector`/`hybrid` |
| 评分 | `0–100` 旧评分 | `[0,1]` 归一化 RRF |
| 返回 | 摘要 + snippet | 完整不可变 `content` + `lineage` |
| `fusion` | 不支持 | 支持（`topic_fts`/`topic_vector`/`detail_fts`/`detail_vector`） |
| `limit` | 1–20 | Topic 为 1–20 |
| `filters` | — | **只接受 `{}`**（Topic 不支持 kind/tag 过滤） |

### 7.6 组装进 PreparedContext

```json
{
  "scope_id": "project:demo",
  "query": "release process",
  "assembly": {
    "sections": [
      {"family": "topic-memory", "limit": 2}
    ]
  }
}
```

- 省略 `assembly` 时，Runtime 可以沿用默认的 Topic Memory 召回（若部署和数据可用）。
- `"assembly": {}` **排除** Topic Memory（只选 Memory + Experience）。
- 组装结果包含有界的标题、摘要、可选匹配片段、Scope 和精确修订 citation；完整 detail 要按精确 revision 读。
- 组装时 Topic 查询 Embedding 最多等 250ms，超时回退 FTS。

### 7.7 排查"主题缺失/过期"

| 现象 | 优先检查 |
| --- | --- |
| 没有 Source | Agent Hook / Connector、Server 地址、Token、Scope 绑定 |
| 有 Source 但没有主题 | Generation readiness、Topic 调度是否为**正数**、Worker 错误、证据是否足够有意义 |
| 搜索没结果 | 当前 Scope、查询文本、检索能力、处理是否已完成 |
| mode 是 `fts` 而非 `hybrid` | Embedding 地址、模型、维度、检索 Profile |
| flush 后主题仍旧 | flush 只负责异步接纳；等 Worker 完成再搜 |
| 新会话召回不到 | Scope 是否相同、上下文组装策略、精确 citation、Agent 集成诊断 |

---

## 8. 给 Dify HTTP Request 节点的实操建议

### 8.1 base_url 写法

| 场景 | `base_url` | 备注 |
| --- | --- | --- |
| Dify 与 PowerContext 在**同一台机器的宿主机进程** | `http://127.0.0.1:8000` | 默认部署，最省事 |
| **Dify 跑在 Docker 里**，PC 在宿主机 | `http://host.docker.internal:8000` | Linux 需 `--add-host=host.docker.internal:host-gateway`；**且 Server 必须监听非 loopback** |
| Dify 与 PC 在同一 Docker 网络 | `http://powercontext:8000`（服务名） | 同样要求监听 `0.0.0.0` |
| 反向代理 / HTTPS | `https://pc.example.com` | PC 自身**不提供 TLS**，必须在外部终止 |

关键坑：**Server 默认只监听 `127.0.0.1`**。容器里的 Dify 访问 `127.0.0.1:8000` 打到的是**它自己**。
要让容器访问，二选一：

```bash
# 选项 A（推荐）：启用鉴权 + 监听 0.0.0.0
export POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_TOKEN"
powercontext server run
```

```bash
# 选项 B（仅完全受信的内网）：关鉴权 + 显式允许非 loopback
export POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0
export POWERCONTEXT_SERVER_ACCESS_MODE=disabled
export POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true
powercontext server run
```

> URL 里**不要带尾斜杠**，也不要带 token 参数 —— PC 文档明确要求"不要把 token 放进 URL"。
> 路径全部以 `/v1/...` 开头，拼在 base_url 后面即可。

### 8.2 Header 配置

| Header | 值 | 何时需要 |
| --- | --- | --- |
| `Content-Type` | `application/json` | **每次都要**（PC 全部写接口都是 JSON body） |
| `Authorization` | `Bearer $POWERCONTEXT_TOKEN` | `ACCESS_MODE=enforced` 时必须；`disabled` 时可完全省略 |
| `If-Match` | `"revision:N"`（**带双引号**） | 修订正文、替换标签时必需 |
| `If-None-Match` | `"revision:N"` | 需要 304 短路时可选 |

Dify 里建议把 token 放**环境变量/密钥**，在 Header 里写成：

```
Authorization: Bearer {{env.POWERCONTEXT_TOKEN}}
```

**留空/鉴权关闭时**：直接把这一行删掉，或让 env 为空。不要发送 `Authorization: Bearer `（空 token），
那在 `enforced` 下会失败，在 `disabled` 下也徒增风险。

### 8.3 Body 模板（直接粘）

**建 Scope**（幂等，可重试）

```json
{
  "title": "Dify workflow scope",
  "summary": "Context boundary for the Dify app",
  "idempotency_key": "dify-{{app_id}}-v1"
}
```

**写一条记忆**（推荐通用入口，能拿到 `Location` / `ETag`）

```json
{
  "family": "atomic-memory",
  "content": {
    "schema": "powercontext.atomic-memory.v1",
    "kind": "decision",
    "text": "{{memory_text}}"
  }
}
```
→ `POST /v1/scopes/{{scope_id}}/artifacts`

**写一条记忆**（兼容入口，返回体更扁平）

```json
{
  "scope_id": "{{scope_id}}",
  "kind": "decision",
  "text": "{{memory_text}}"
}
```
→ `POST /v1/memory/remember`

**检索**

```json
{
  "scope_id": "{{scope_id}}",
  "query": "{{sys.query}}",
  "mode": "text",
  "limit": 5
}
```
→ `POST /v1/atomic-memory/search`

**准备上下文**（推荐接在 LLM 节点前面）

```json
{
  "scope_id": "{{scope_id}}",
  "query": "{{sys.query}}",
  "max_bytes": 4000,
  "assembly": {
    "sections": [
      {"family": "memory", "limit": 5},
      {"family": "experience", "limit": 2}
    ]
  }
}
```
→ `POST /v1/context/prepare`

### 8.4 从节点输出里取字段

Dify 的 HTTP Request 节点把响应放进 `body`。常用取值：

| 想取的东西 | Dify 变量表达式 |
| --- | --- |
| 新 scope ID | `{{#<HTTP节点名>.body.scope_id#}}` |
| 新记忆的 Artifact 引用 | `{{#<HTTP节点名>.body.records[0].artifact.artifact_id#}}` |
| 检索命中条数 | `{{#<HTTP节点名>.body.hits | length#}}`（长度用 Code 节点更稳） |
| 命中正文 | `{{#<HTTP节点名>.body.hits[0].memory.text#}}` |
| 准备上下文正文 | `{{#<HTTP节点名>.body.content#}}` |

注意事项：

- **`content` 可能是 `null`**（`status: "empty"`）。接 LLM 前用 IF/条件分支判 `status == "ready"`，
  或在 Code 节点里 `content or ""`，否则会出现 `None` 字样被喂给模型。
- **`hits` 可能是空数组**（这是成功结果，不是错误）。取 `hits[0]` 前必须判长度。
- 数组/对象在变量选择器里展开不可靠时，**先用 Code 节点 `JSON.stringify` 一次**再用。
- `content` 里已经包含标题、历史证据提示、引用和截断标记，**不要再自己裁剪**，最多在它外面加提示。

### 8.5 超时设置

| 节点 | 建议超时 | 说明 |
| --- | --- | --- |
| `GET /health/live` | 3–5 s | 探活 |
| `POST /v1/scopes`、`/v1/memory/remember`、`artifacts` | 10–15 s | 纯 DB 写，正常很快 |
| `POST /v1/atomic-memory/search`（`text`） | 10–20 s | 全文检索 |
| `POST /v1/*/search`（`vector`/`hybrid`） | 30–60 s | 要走 Embedding provider；Dify 默认超时往往不够 |
| `POST /v1/context/prepare` | **30 s** | 内部有 Embedding 预算与 fallback，但可能接近上限 |
| `POST /v1/topic-memory/flush` | 10 s | 只是**异步接纳**，立刻返回，别等生成 |
| `/v1/atomic-memory/restorations` | 15–30 s | 涉及多对象事务 |

> 超时的正确处理：**写接口超时属于"结果未知"，不要直接重试**。先
> `POST /v1/atomic-memory/list` 或用 `GET /v1/scopes/{id}` 确认是否已生效，再决定。
> （这也是 Atomic Memory 合并/恢复没有 `idempotency_key` 的原因。）

### 8.6 请求追踪

**每个响应都带 `X-PowerContext-Request-ID`。排查失败请求时务必把它记下来**（服务端日志按它检索）。
Dify 里可以在 HTTP 节点开启响应头输出，或失败时把整个 `body.error` 透传到日志。

### 8.7 错误码与排查

统一错误信封（**所有非 2xx**）：

```json
{
  "error": {
    "code": "invalid_request",
    "message": "The request violates the API contract.",
    "details": {}
  }
}
```

> 【来源：`docs/zh/docs/develop/http-api.md` 原文示例；`ErrorResponse{error:{code, message, details}}`，
> `details` 可为 `null`。】

| 状态码 | 含义 | 常见 `error.code` | 排查动作 |
| --- | --- | --- | --- |
| **400** | 请求格式问题（查询参数/cursor） | `invalid_cursor`、`invalid_request` | 检查 `limit`/`mode`/cursor 是否被当字符串传；cursor 要原样回传 |
| **401** | 需要有效 Bearer token | `unauthorized` | `ACCESS_MODE=enforced` 却没带/带错 `Authorization` |
| **403** | 已认证但无权 | `forbidden` | Scope/Artifact 权限不足；`enforced` 下创建 Scope 需要 `server.admin` |
| **404** | 目标不存在 | `scope_not_found`、`artifact_not_found`、`artifact_family_not_found` | **Scope ID 写错**最常见；`family` 用了已移除的 `memory` |
| **409** | 与当前不可变状态 / expected version 冲突 | `revision_conflict`（旧式）、`invalid_memory_state`、`atomic_memory_changed`、`scope_idempotency_conflict`、`artifact_already_exists`、`source_conflict` | **重读再写**；不要盲目重试 |
| **410** | 分页 cursor 过期 | `cursor_expired` | 丢掉 cursor 从头翻（有效期 1 小时） |
| **412** | `If-Match`/ETag 过期 | `revision_conflict`、`tag_precondition_failed` | 重读拿新 ETag |
| **413** | 选中内容超输出限制 | `handoff_report_too_large` | 缩小 Scope 选择范围 |
| **422** | body 不符合传输或应用契约 | `invalid_request`、`legacy_memory_operation_unsupported`、`capability_not_supported`、`artifact_search_not_supported`、`invalid_preview`、`memory_write_rejected` | **看 `details`**：多数带 `field`/`code`/`instruction` |
| **428** | 缺 `If-Match` | `precondition_required` | 补 header，**注意值要带双引号** |
| **500** | 服务端错误（不暴露内部细节） | `internal_error` | 记录 `X-PowerContext-Request-ID`，查服务端日志 |
| **503** | 必需 Runtime 绑定/依赖不可用 | `runtime_not_ready`、`inference_timeout`、`inference_unavailable`、`generation_unavailable` | 查 `/health/ready` 与 `/v1/capabilities` |

> 【来源：`docs/zh/docs/develop/http-api.md` 的"常见状态码"表 + `src/powercontext/server/app.py` 的
> `_map_error` / `_map_service_error` / `_map_base_access_error` / `_map_scope_error` / `_map_domain_error`
> / `_map_availability_error` 实际映射。】

**422 是最高频错误，先看这两个地方**：
1. `error.details.field` —— 指出是哪个字段。
2. 字段名拼写/类型：显式 `null`、数字字符串（`"limit": "5"`）、数值字段传布尔、`content` 里多写一个字段
   （`additionalProperties: false`）都会被 422 拒掉。

**Dify 节点调试顺序建议**：

1. 先单独 `GET /health/live` → 排除网络/地址问题。
2. `GET /v1/capabilities`（带 token）→ 确认 `artifact_families`、`search_modes`、`context_versions`。
3. 用 §0 的 curl 在宿主机跑通，再搬到 Dify 节点（**90% 的问题在这一步能区分是网络还是参数**）。
4. 422 时把 `body.error.details` 打到日志节点。

---

## 9. 版本差异与【待验证】清单

### 9.1 版本核对结论

| 项目 | 值 | 依据 |
| --- | --- | --- |
| 你安装的版本 | `1.4.1` | PyPI `powercontext==1.4.1` |
| `master` 的契约版本 | `1.4.1` | `openapi/powercontext.yaml` → `info.version: "1.4.1"` |
| 文档声明版本 | `1.4.1` | `docs/zh/docs/get-started/install-and-run.md`（包 `1.4.1`，Git tag `powercontext-v1.4.1`） |

**结论：契约层面 master 与 1.4.1 对齐，本文端点/字段按 master 与 1.4.1 通用的形态书写。**

需要如实说明的两个限制：

1. 本地检出的仓库是**浅克隆**（`git log` 只有 1 个 commit，无 tag），**无法 diff `powercontext-v1.4.1` tag 与 master**。
   因此"master 上是否有 1.4.1 之后的实现微调"这一点标 **【待验证】**。规避方法：直接读你本机
   `http://127.0.0.1:8000/openapi.json` —— 那是**你这一版实际提供的契约**，比任何文档都准。
2. 官方文档站 `powercontext.oceanbase.io` 与仓库 `docs/zh/` 是同一套源；本文以仓库原文为主。

### 9.2 版本相关的三个易踩坑

**(a) `family=memory` 已废弃，但契约里还留着 schema**

`openapi` 仍声明 `CreateMemoryArtifactRequest`（`family: "memory"`、`content.entries[]`），但运行时：

- 用 `family=memory` 创建/替换/集合回滚 → **422 `legacy_memory_operation_unsupported`**
  （`details` 里有 `alternatives` 指向 Atomic 接口）。
- 统一检索 `/v1/scopes/{id}/artifacts/memory/search` → **404**（`memory` 不再是已注册 family）。
- `/v1/memory/entries/*` 的 `revise` / `retire` / `changes` / `capacity` 同样被拒。

**结论：只用 `atomic-memory`。** 旧 `/v1/memory/remember`、`/v1/memory/search`、`/v1/memory/entries/list`
这些**读/写入口仍可用**，但返回的已经是真实的 Atomic ArtifactRef。

**(b) `GET /v1/scopes/{scope_id}/artifacts/{family}` 的 `tag` 参数**

制品列表支持**重复**的 `tag` query 参数 + `tag_match=all|any`（例如
`?tag=release&tag=customer-a&tag_match=all`）。**没有 `tag` 时不能单独传 `tag_match`**。

**(c) Windows 支持是 `experimental`**

CLI/Server/个人服务在 Windows 上是实验性支持；**内嵌 seekDB 完全不支持 Windows**。文档里的示例用 Bash 语法，
不能直接粘到 PowerShell（`\` 换行、`$()`、`export` 都要改）。

### 9.3 【待验证】清单（本文所有不确定点，集中列出）

| # | 项 | 现状 | 最可能形式 / 建议 |
| --- | --- | --- | --- |
| 1 | master 与 `powercontext-v1.4.1` tag 的实现差异 | 浅克隆无法 diff | 以本机 `/openapi.json` 为准 |
| 2 | `scope_id` 的真实形态/前缀 | 契约只说"不透明、1–256 字符、非空白" | 示例 `scp_...` 仅为形态示意，**永远以返回值为准** |
| 3 | `artifact_id` 的真实形态 | 契约 `^[\x21-\x7E]+$`、1–128 | 示例 `am_...`/`topic-release` 为示意 |
| 4 | `/health/ready` 的 `checks` 键名 | 契约只声明 `dict[str,str]` | 键名属实现细节，别硬编码 |
| 5 | `extraction.*` / `prompts.*` 的完整取值组合 | 只见到裁剪示例与枚举 | 以实际 `/v1/capabilities` 为准 |
| 6 | `/v1/memory/search` 的 `mode: "auto"` 选择规则 | 文档只说"按部署能力" | 生产建议**显式写 `fts` 或 `text`** |
| 7 | `remember` 的 `reason` 是否落库 | 1.4.1 实现中**未进入内容模型** | 视为 no-op；来源请用 §3.5 的 (A)/(B)/(C) |
| 8 | `atomic-memory` 创建能否挂 `source_refs` | 契约 `additionalProperties:false`，**不行** | 目前只有 `merges` 支持；未来版本可能开放 |
| 9 | `POST /v1/topic-memory/processing/status` 的 `block` 字段集 | 契约里是 `any` | 以实际返回为准，先用 `null` 判空 |
| 10 | 恢复预览 `preview_token` 的前缀/长度 | 契约无约束（string） | 示例 `pv_...` 仅为示意 |
| 11 | 各类检索的默认 `limit` 在两入口间的差异 | 已按契约区分（50/20/10/50） | 建议**总是显式传 `limit`**，别依赖默认值 |

> 建议：把上面每一项都在你自己的部署上用一次 curl 实测，然后把结果回填成"已验证"。**文档可以过时，`/openapi.json` 不会。**

---

## 附录 A：本文用到的全部端点速查

| 方法 | 路径 | 用途 | 章节 |
| --- | --- | --- | --- |
| GET | `/health/live` | 进程存活（公开） | §1.5 |
| GET | `/health/ready` | 部署就绪（公开，503 表示未就绪） | §1.5 |
| GET | `/v1/capabilities` | 运行时能力 | §1.5 |
| POST | `/v1/scopes` | 创建 Scope（201） | §2.1 |
| GET | `/v1/scopes` | 列举 Scope | §2.2 |
| GET | `/v1/scopes/{scope_id}` | 读单个 Scope | §2.2 |
| PUT | `/v1/scopes/{scope_id}` | 更新 Scope（`expected_version`） | §2.3 |
| GET | `/v1/scopes/default` | 读默认 Scope | §2.2 |
| PUT | `/v1/scopes/default` | 切换默认 Scope | §2.3 |
| POST | `/v1/scopes/{scope_id}/artifacts` | 创建 Artifact（`family` 判别，201） | §3.1 / §7.1 |
| GET | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | 读当前 head（含 ETag） | §6.1 |
| PUT | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | 整体替换 → 新 revision（`If-Match`） | §6.1 |
| GET | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` | 历史列表 | §6.2 |
| GET | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` | 精确历史 | §6.2 |
| GET | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | 读标签 | §3.5 |
| PUT | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | 替换标签（`If-Match`） | §3.5 |
| POST | `/v1/scopes/{scope_id}/artifact-tags/query` | 跨制品类型按标签查 | §4 |
| POST | `/v1/scopes/{scope_id}/artifacts/{family}/search` | 统一检索（支持 `fusion`） | §4.3 / §7.5 |
| GET | `/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/state` | 生命周期状态 | §6.3 |
| POST | `/v1/memory/remember` | 兼容写入口（200） | §3.2 |
| POST | `/v1/memory/search` | 兼容检索（`auto`/`fts`） | §4.2 |
| POST | `/v1/memory/entries/list` | 兼容列表（`include_inactive`） | §4.1 |
| POST | `/v1/memory/entries/get` | 旧身份 → 当前 Atomic | §6 |
| POST | `/v1/atomic-memory/search` | Atomic 专用检索 | §4.2 |
| POST | `/v1/atomic-memory/list` | Atomic 列表 | §4.1 |
| POST | `/v1/atomic-memory/lifecycle` | 遗忘（只接受 `forgotten`） | §6.4 |
| POST | `/v1/atomic-memory/merges` | 合并（可挂 `source_refs`） | §6.6 |
| POST | `/v1/atomic-memory/restoration-previews` | 恢复预览 | §6.5 |
| POST | `/v1/atomic-memory/restorations` | 恢复 / 撤销合并 | §6.5 |
| POST | `/v1/context/prepare` | 组装 PreparedContext | §5 |
| POST | `/v1/topic-memory/search` | Topic 摘要检索 | §7.2 |
| POST | `/v1/topic-memory/get` | Topic 精确 Revision | §7.3 |
| POST | `/v1/topic-memory/flush` | 请求异步处理 | §7.4 |
| POST | `/v1/topic-memory/processing/status` | 处理状态 | §7.4 |
| POST | `/v1/topic-memory/processing/blocks/get` | 读阻塞记录 | §7.4 |
| POST | `/v1/topic-memory/processing/retry` | 申请重试（`scope.admin`） | §7.4 |
| POST | `/v1/sources/content` | 采集 Source 证据（202） | §3.5 |
| GET | `/v1/scopes/{scope_id}/sources` | 列 Source | §3.5 |
| POST | `/v1/stats` | 使用统计 | — |

## 附录 B：最小可粘贴 curl 集合（Bash）

```bash
export POWERCONTEXT_URL=http://127.0.0.1:8000
export POWERCONTEXT_TOKEN=""     # enforced 时填真 token
AUTH=(); [ -n "$POWERCONTEXT_TOKEN" ] && AUTH=(-H "Authorization: Bearer $POWERCONTEXT_TOKEN")

# 探活
curl -sS "$POWERCONTEXT_URL/health/live"; echo

# 能力
curl -sS "$POWERCONTEXT_URL/v1/capabilities" "${AUTH[@]}"; echo

# 建 scope（幂等）
SCOPE_ID=$(curl -sS -X POST "$POWERCONTEXT_URL/v1/scopes" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d '{"title":"Dify demo","summary":"Dify demo scope","idempotency_key":"dify-demo-001"}' \
  | python -c 'import json,sys;print(json.load(sys.stdin)["scope_id"])')
echo "SCOPE_ID=$SCOPE_ID"

# 写一条记忆（返回体里直接有 artifact）
curl -sS -X POST "$POWERCONTEXT_URL/v1/memory/remember" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"kind\":\"decision\",\"text\":\"公开 API 保持异步。\"}"; echo

# 搜出来
curl -sS -X POST "$POWERCONTEXT_URL/v1/atomic-memory/search" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"query\":\"公开 API\",\"mode\":\"text\",\"limit\":5}"; echo

# 组装上下文
curl -sS -X POST "$POWERCONTEXT_URL/v1/context/prepare" \
  -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d "{\"scope_id\":\"$SCOPE_ID\",\"query\":\"公开 API\",\"max_bytes\":4000}"; echo
```
