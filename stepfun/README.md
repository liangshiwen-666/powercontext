# StepFun × seekdb 端侧知识库

浏览器本地完成文档向量化，向量与原文写入 [seekdb](https://github.com/oceanbase/seekdb)，检索走 seekdb 的
向量 + 全文混合检索（库内 RRF 融合）。整个过程中**没有任何云端推理调用**。

> 2026 生态大赛 · 阶跃星辰 × seekdb · 题目「端侧 AI 推理性能优化与 seekdb 混合检索」

---

## 核心特性

| 能力 | 实现 |
|---|---|
| 本地推理 | `@huggingface/transformers`（Transformers.js 4.x）在 Web Worker 中加载 ONNX 模型，文档与查询向量都在浏览器计算 |
| 后端选择 | 优先 `WebGPU`（fp16 权重），`navigator.gpu` 缺失或初始化失败时自动降级到 `WASM`（int8 权重），界面顶部常驻徽标显示当前后端与降级原因 |
| 模型量化 | `Xenova/bge-small-zh-v1.5` 的 fp32 / fp16 / int8 三档权重，体积 90.5 MiB → 22.8 MiB（3.97×），质量对比见 [docs/TECH.md](docs/TECH.md) |
| 混合检索 | 一次 `collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings }, rank: { rrf: {} } })`，融合在数据库内完成 |
| 索引刷新 | HNSW 异步维护，写入后调用 `collection.refresh_index()`，界面显示「待刷新条数」 |
| 可观测性 | 模型加载 / 向量化 / 写入+刷新 / 查询编码 / 检索 / 端到端 分阶段计时，可一键导出 JSON |

```
┌──────────────── 浏览器（用户设备）────────────────┐        ┌──── 本地 Node 服务 ────┐
│  React UI                                          │        │                        │
│   ├─ 导入 / 检索 / 计时面板                        │  HTTP  │  Express               │
│   └─ Web Worker                                    │◄──────►│   └─ seekdb-js         │
│        └─ Transformers.js                          │ 仅向量 │        └─ seekdb        │
│             ├─ WebGPU (fp16)  ← 默认               │ + 原文 │           (嵌入式/服务端) │
│             └─ WASM   (q8)    ← 自动降级           │        │  embeddingFunction=null │
└────────────────────────────────────────────────────┘        └────────────────────────┘
```

服务端的 collection 显式设置 `embeddingFunction: null`：它只接收向量与原文，不具备任何编码能力，
这是"推理确实发生在端侧"的结构性保证（而不是口头承诺）。

---

## 快速开始

前置条件：

- Node.js ≥ 20
- Chrome / Edge 最新版（WebGPU 可用时走 GPU，否则自动 WASM）
- seekdb：推荐用 Docker 起一个独立实例（下方命令），也可用嵌入式模式（见「两种部署方式」）

```bash
cd stepfun
npm install
npm run fetch:model              # 下载模型到 web/public/models（int8 + fp16 + 分词器，约 72 MB）
npm run dev                      # 启动服务 + 前端，打开 http://localhost:5173
```

**seekdb 会自动选**：启动时先看环境变量，其次探测 `127.0.0.1:2881`——
连得上就用 Docker 里的独立实例，连不上就自动退回嵌入式（数据落在 `./stepfun-data/seekdb`）。
两种情况都只需要 `npm run dev` 一条命令，界面上会显示当前用的是哪种。

```bash
docker compose up -d   # 可选：有 Docker 就起一个独立 seekdb，重启 dev 后会自动切过去
```

首次进入页面会看到：

1. 顶部徽标显示推理后端（`WebGPU · fp16` / `WASM · q8`）和 seekdb 模式（`server` / `embedded`）；
2. 点「载入示例语料」→ 40 篇文档在本地向量化并写入 seekdb；
3. 输入问题检索，切换「混合检索 / 仅向量 / 仅全文」对比结果；
4. 右侧「耗时」面板查看分阶段延迟，点「导出」保存 JSON。

### 模型下载失败？

```bash
# 默认走 hf-mirror；也可以指定其他镜像或本地已下载目录
MODEL_HOST=https://huggingface.co npm run fetch:model
```

`fetch:model` 只影响模型文件来源，推理始终在浏览器内完成。

---

## seekdb 的两种部署方式

### A. 独立服务（有 Docker 时自动使用）

```bash
docker compose up -d          # 拉取 quay.io/oceanbase/seekdb 并映射 2881
npm run dev                   # 探测到 2881 后自动用 server 模式
```

数据持久化在 `./stepfun-data/docker`。`/api/health` 中 `db.mode` 为 `server`。
若 `docker.io` 拉不动，compose 文件默认使用 `quay.io/oceanbase/seekdb`；
连接参数可用 `SEEKDB_HOST` / `SEEKDB_PORT` / `SEEKDB_USER` / `SEEKDB_PASSWORD` 覆盖。
`npm run dev:docker` 会把 `docker compose up -d` 和 `npm run dev` 串起来。

### B. 嵌入式（没有 Docker 时自动回退）

不设任何环境变量、2881 也没人监听时，自动使用嵌入式——无需额外进程，数据在
`./stepfun-data/seekdb`。首次启动要格式化数据文件，约 5–20 秒。

也可以强制指定：

```bash
npm run dev:embedded                        # = tsx server/src/index.ts --embedded
SEEKDB_MODE=embedded npm run dev            # 等价写法
```

> 嵌入式由 seekdb 原生插件提供，应用已规避其两个已知行为（同进程建第二个集合会阻塞 →
> 改用 `getOrCreateCollection` + 单物理集合；异步 `refresh_index` 会阻塞 → 使用 `syncMode=immediate`），
> 并给写入/检索加了超时保护。
> 若上次进程异常退出，`run/seekdb.pid` 会指向仍存活的子进程，启动会**直接报错而不是卡住**：
> 按提示结束该进程，或 `SEEKDB_FORCE_UNLOCK=1 npm run dev` 让服务强制接管。

---

## 目录结构

```
stepfun/
├─ web/                      前端（Vite + React + TS）
│  ├─ src/worker/embed.worker.ts   推理 Worker：模型加载、WebGPU→WASM 降级、批量编码
│  ├─ src/lib/embedder.ts          Worker RPC 封装
│  ├─ src/lib/chunk.ts             切块（按句边界，带重叠）
│  ├─ src/lib/metrics.ts           Recall@K / nDCG@K / MRR
│  ├─ src/lib/api.ts               服务端接口封装
│  └─ src/App.tsx                  界面与计时面板
├─ server/                   本地服务（Express + seekdb-js）
│  ├─ src/db.ts                    集合 schema、批量写入、刷新、混合检索
│  ├─ src/index.ts                 HTTP 路由
│  └─ src/config.ts                环境变量
├─ scripts/
│  ├─ fetch-model.mjs              下载模型（支持镜像）
│  ├─ seed.mjs                     data/corpus.md → web/public/sample/corpus.json
│  ├─ selftest.ts                  数据层端到端自测（不依赖浏览器）
│  ├─ eval.ts                      检索质量 + 吞吐评测（Recall@5 / nDCG@10 / MRR）
│  └─ quantize.py                  ONNX 动态量化脚本（自行复现 int8）
├─ data/
│  ├─ corpus.md                    示例语料（40 篇中文技术笔记）
│  └─ eval/queries.jsonl           50 条评测查询与目标文档
├─ docs/TECH.md               一页技术说明（模型、量化、检索、性能）
├─ docs/REPRO.md              复现步骤与验收清单
└─ docker-compose.yml         可选：独立 seekdb 服务
```

---

## 脚本

| 命令 | 作用 |
|---|---|
| `npm run dev` | 同时启动 API（8787）与前端（5173） |
| `npm run build` | 构建前端到 `web/dist` |
| `npm start` | 以生产模式启动服务（会托管 `web/dist`） |
| `npm run fetch:model` | 下载 int8 + fp16 权重与分词器 |
| `npm run fetch:model:all` | 额外下载 fp32 基线权重（评测对比用） |
| `npm run seed` | 解析 `data/corpus.md` 生成示例语料 |
| `npm run selftest` | 不启动浏览器，直接验证 seekdb 写入/刷新/混合检索 |
| `npm run eval -- --dtype q8 --label int8` | 通过 API 评测检索质量与吞吐 |
| `npm run browser:check -- --port 9333` | 用无头浏览器跑完整页面（导入 + 检索 + ×10 基准），输出 `results/browser-*.json` |

### 浏览器实测（本机，Edge 156 无头）

| 配置 | 向量化吞吐 | 端到端 P50/P95 | 检索 |
|---|---:|---|---|
| WebGPU · fp16 | 87.5 chunks/s | 25.0 / 32.3 ms | top-1 正确 |
| WASM · q8 | 15.1 chunks/s | 21.4 / 29.4 ms | top-1 一致 |

详见 [docs/TECH.md](docs/TECH.md) 5.2，原始数据在 `results/`。

---

## 验收对照（对应赛题 3.1）

| 检查项 | 在哪看 |
|---|---|
| 完整流程 | 导入 → 进度条 → 写入 → 检索 → 排序结果与原文 |
| 本地推理 | Worker 内 transformers.js；服务端无 embedding 函数；`/api/health` 返回 `embedding: "browser-local"` |
| 模型量化 | 顶部徽标显示 dtype；[docs/TECH.md](docs/TECH.md) 有体积/压缩比/质量表 |
| 后端与降级 | 顶部徽标 + 降级横幅（含原因）；`chrome://flags` 关闭 WebGPU 可复现 |
| seekdb 数据层 | `server/src/db.ts`：`fulltextIndex(ik)` + `hnsw(512, cosine)`，一次 `hybridSearch` 完成融合 |
| 状态与刷新 | 集合列表、文档数、待刷新条数、手动刷新按钮 |
| 可复现性 | 本文档 + [docs/REPRO.md](docs/REPRO.md) |

---

## 常见问题

**模型加载很慢？** 首次需要下载权重（int8 约 22.8 MiB）。`fetch:model` 之后走本地文件，二次加载走浏览器缓存。

**检索不到刚导入的内容？** 看左侧「待刷新条数」；HNSW 异步维护，点「刷新索引」或等本批次自动刷新。

**端口被占用？** `PORT=8878 npm run dev`，前端代理地址在 `web/vite.config.ts` 同步修改。

**能用云端模型吗？** 不能，也不支持配置——题目禁止云端推理，服务端代码里没有任何模型调用路径。
