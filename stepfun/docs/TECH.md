# 一页技术说明

> 项目：StepFun × seekdb 端侧知识库 · 2026 生态大赛
> 代码：`stepfun/`（Vite + React 前端 / Node + seekdb-js 服务端）

## 1. 模型与量化

**模型**：`Xenova/bge-small-zh-v1.5`（Hugging Face 上的 Transformers.js 转换版，基座 `BAAI/bge-small-zh-v1.5`），
512 维、最大 512 token、CLS 池化 + L2 归一化，中文检索场景通用。许可证 MIT，权重可公开获取。
本地加载路径 `web/public/models/Xenova/bge-small-zh-v1.5/`，由 `npm run fetch:model` 拉取（默认 hf-mirror，可切换 huggingface.co）。

**量化方案**：三档权重并存，同一模型、同一分词器，仅精度不同。

| 精度 | 文件 | 体积（实测字节） | 压缩比 | 用途 |
|---|---|---|---:|---:|---|
| FP32 基线 | `onnx/model.onnx` | 94,851,877 B（90.5 MiB） | 1.00× | 质量基线 / 对照 |
| FP16 | `onnx/model_fp16.onnx` | 47,497,427 B（45.3 MiB） | **2.00×** | WebGPU 默认 |
| INT8 | `onnx/model_quantized.onnx` | 24,010,842 B（22.9 MiB） | **3.95×** | WASM 默认 |

口径说明：上表只统计参与推理的权重文件。加上分词器（`tokenizer.json` 439 KB、`vocab.txt` 110 KB）与配置，
INT8 首次下载约 **24.6 MiB**（`npm run fetch:model` 实测 68.7 MB 含 fp16），FP32 约 **91.4 MiB**。

**量化方式**：ONNX Runtime 动态量化（`quantize_dynamic`，权重 QInt8、激活运行时按张量动态定标，无需校准集）。
仓库内 `scripts/quantize.py` 给出从 FP32 复现 INT8 的完整命令；默认直接使用官方已发布的同源 INT8 导出，
两者体积一致（23.9 MB 量级），差异仅在算子选择。

**检索质量**：40 篇中文技术文档（40 个块）、50 条带标注查询，文档级相关性，
经 seekdb 混合检索（RRF）实测：

| 配置 | Recall@5 | nDCG@10 | MRR |
|---|---:|---:|---:|
| FP32 | 0.9796 | 0.9118 | 0.8878 |
| FP16 | 0.9796 | 0.9118 | 0.8878 |
| INT8 | 0.9796 | **0.9193** | **0.8980** |

结论：INT8 相比 FP32 **召回无变化、nDCG@10 +0.0075、MRR +0.0102**，差异在 50 条查询的噪声范围内，
未观察到质量下降。注意该语料偏"易分"，FP32 与 INT8 的排序几乎一致；若需要更敏感的质量差异，
应扩大语料到数百篇并加入同主题干扰项（脚本已支持替换 `data/corpus.md` 与 `data/eval/queries.jsonl`）。

## 2. 端侧运行

- **运行时**：`@huggingface/transformers` 4.3.1（ONNX Runtime Web 后端），在 **Web Worker** 中加载模型并推理，
  主线程只负责 UI，避免长任务卡顿。
- **WebGPU 选择**：Worker 启动时检查 `navigator.gpu`，存在则以 `device: "webgpu", dtype: "fp16"` 创建推理会话；
  会话创建异常时捕获并记录原因。
- **WASM 降级**：WebGPU 不可用或初始化失败 → 以 `device: "wasm", dtype: "q8"` 重载；界面顶部徽标显示
  `WASM · q8`，并在横幅中给出具体原因（如"navigator.gpu 不存在"），核心流程不受影响。
- **验证方法**：Chrome 打开 `chrome://flags/#enable-unsafe-webgpu` 关闭 WebGPU（或使用无 GPU 的机器）后刷新，
  徽标应从 `WebGPU · fp16` 变为 `WASM · q8` 并出现降级提示；两次检索结果应基本一致。
- **无云端调用**：服务端 collection 的 `embeddingFunction` 显式为 `null`，`/api/health` 返回
  `embedding: "browser-local"`；浏览器侧无任何外部推理请求。

## 3. seekdb 使用

**连接**：`seekdb` npm 包 1.4.0，**启动时自动选后端**——`SEEKDB_MODE` 显式指定优先，否则探测
`127.0.0.1:2881`：可连则用独立服务（`docker compose up -d`），不可连则回退嵌入式
（`./stepfun-data/seekdb`，无需守护进程）。`/api/health` 返回 `db.mode` 与 `db.detectedBy`
（`env` / `probe`），界面徽标同步显示。

**集合结构**（`server/src/db.ts`）：seekdb 中只有 **一个物理集合** `kb_docs`，
逻辑集合（如 `kb`、`kb_2`）通过 metadata 字段 `kb` 分区，检索时用 `where` 过滤——
这样"切换知识库"在数据库侧同样成立，且规避了嵌入式 seekdb 在同一进程内创建第二个集合会阻塞的问题。

```ts
new Schema({
  fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: 512, distance: "cosine", M: 16, ef_construction: 200, ef_search: 64,
            syncMode: "immediate" },
    embeddingFunction: null,
  }),
})
```

字段：`id`（`doc-<hash>-<chunk>`）、`document`（块文本，首行标题）、
`metadata`（`kb` / `title` / `docId` / `chunk`）、`embedding`（512 维）。

**混合检索**：单次调用完成两路召回与融合，不存在"客户端分别取两路再拼合"：

```ts
await collection.hybridSearch({
  query: { whereDocument: { $contains: queryText }, where: partition, nResults: topK * 2 }, // ik 全文
  knn:   { queryEmbeddings: [queryVector], where: partition, nResults: topK * 2 },          // HNSW 向量
  rank:  { rrf: {} },                                                                       // 库内 RRF
  nResults: topK,
  include: ["documents", "metadatas", "distances"],
});
```

**写入与索引刷新**：`syncMode: "immediate"` 保证写入即可检索；批量导入按 64–128 条/批写入，
导入结束调用一次 `refresh_index()`（15 s 超时保护，超时不阻断导入）。界面显示"待刷新条数"，
可手动刷新。实测（server 模式，40 块）：写入 257–340 ms、刷新 332–521 ms。

**状态管理**：逻辑集合清单与各自条数记录在数据库目录旁的 `collections.json`，
不依赖 SDK 的 `listCollections()`（该接口要求注册嵌入函数，与"服务端不嵌入"冲突）。
切换集合会清空当前结果；`count` 由写入路径维护，`drop` 通过 `delete({ where: { kb } })` 删除该分区。

## 4. 性能与优化

| 优化点 | 优化前 | 优化后 | 观测方式 |
|---|---|---|---|
| 批处理 | batch=1 | 16（Worker 内）/ 64–128（写库） | chunks/s |
| 模型加载 | 每次重新下载 | `fetch:model` 本地文件 + 浏览器 Cache | 首次 vs 二次加载耗时 |
| 权重体积 | FP32 90.5 MiB | INT8 22.9 MiB（3.95×） | 下载字节数 |
| 索引维护 | 异步 + 每批 refresh | `syncMode=immediate` + 结束刷一次 | 写入+刷新总耗时 |
| 检索 | 分两次查再合并 | 一次 `hybridSearch`（RRF） | 检索延迟 |
| 降级 | 直接失败 | WebGPU→WASM 自动回退 | 界面徽标 |

**计时口径**：模型下载/加载（首次、预热后分开）、文档向量化、写入+索引刷新、查询向量化、
seekdb 检索、端到端（点击检索 → 结果可展示）。界面右侧面板实时记录，可导出 JSON（含 UA、后端、dtype、各阶段耗时）。

## 5. 实测数据

### 5.1 Node（CPU）基线 — 已有

环境：Windows 11 x64，Node 24.21，40 篇文档 / 40 块 / 50 条查询，批大小 16，seekdb 1.4.0。

| 配置 | 模型体积 | 向量化吞吐 | seekdb 检索 | 检索质量 |
|---|---|---:|---:|---|
| FP32 | 90.5 MiB | 90.5 chunks/s | 30–200 ms | R@5 0.9796 / nDCG 0.9118 |
| FP16 | 45.3 MiB | 62.7 chunks/s | 同上 | R@5 0.9796 / nDCG 0.9118 |
| INT8 | 22.9 MiB | 74.6 chunks/s | 同上 | R@5 0.9796 / nDCG 0.9193 |

> 吞吐为单次测量，CPU 上随机器负载波动约 ±30%（同一脚本多次运行观测到 63–122 chunks/s）；
> 检索延迟含首次冷启动（约 200 ms）与预热后的 30–70 ms。质量指标在同语料同查询集下可复现。

> 该表用于验证"同一语料、同一查询集下量化前后的质量与吞吐"，由 `npm run eval` 产出，不包含浏览器开销。

### 5.2 浏览器实测（Edge 156 无头模式，WebGPU 与 WASM 各一组）

环境：Windows 11 x64，Microsoft Edge 156.0.4314.50（`--headless=new`），seekdb 1.4.0 server 模式，
40 篇 / 40 块语料，同一句查询连跑 10 次（首次预热丢弃），由页面内「基准 ×10」自动统计。
原始数据：`results/browser-webgpu.json`、`results/browser-wasm.json`、`results/browser-summary.json`。

| 配置 | 模型体积 | 首次加载 | 预热后加载 | 向量化吞吐 | 写入+刷新 | seekdb 检索 | 端到端 P50 / P95 |
|---|---|---:|---:|---:|---:|---:|---|
| WebGPU · fp16 | 45.3 MiB | 7.17 s（编译+解析） | 0.83 s | **87.5 chunks/s** | 566 ms | 30.3 ms | **25.0 / 32.3 ms** |
| WASM · q8 | 22.9 MiB | — | 0.53 s | 15.1 chunks/s | 462 ms | 19.9 ms | 21.4 / 29.4 ms |

结论：

- **批量向量化 WebGPU 比 WASM 快 5.8×**（87.5 vs 15.1 chunks/s），这是端侧推理的主要收益点；
- 单条查询编码只有几毫秒，端到端主要由 HTTP + seekdb 检索决定，因此两组 E2E 接近
  （25.0 ms vs 21.4 ms），WASM 略快来自 WebGPU 单次调用的固定开销；
- 两种后端的检索结果一致（top-1 均为「文档 ID 与重复导入」，与评测集结论吻合），
  说明降级路径**功能完整、质量不变**；
- 降级由 `--disable-gpu` 触发，页面徽标显示 `WASM · q8`，横幅给出原因。

> 说明：无头 Edge 的 WebGPU 适配器是软件/虚拟实现，真实独显上的绝对延迟会不同；
> 该表用于证明"两条路径都能跑通且相对关系成立"，具体机器的绝对值请用同一页面复测。
> 同一台机器多次运行观测到吞吐 64–90 chunks/s、E2E P50 25–35 ms 的波动，属正常噪声。

### 5.3 无 Docker（嵌入式 seekdb）同一流程

把 seekdb 容器停掉后重跑同一浏览器流程（`results/browser-embedded.json`），用于确认"没有 Docker 也能测"：

| 配置 | 写入+刷新 | seekdb 检索 | 端到端 P50 / P95 | top-3 |
|---|---:|---:|---|---|
| WebGPU · fp16 + 嵌入式 | 1599 ms | 75 ms | 43.0 / 45.3 ms | 与 server 模式一致 |

嵌入式比独立服务慢（写入 1.6 s vs 0.57 s，检索 75 ms vs 30 ms），但功能与检索结果完全一致，
符合"单进程、无守护进程"的定位；两种模式的选型由启动时自动探测决定。

## 6. 已知限制

- 演示语料只有 40 篇，评测集 50 条查询，质量差异的统计效力有限（见 1 节说明）。
- 嵌入式 seekdb（seekdb-js 1.4.0）有三处平台相关行为，均已处理：同进程 `createCollection`
  后再 `getCollection` 会阻塞（改用 `getOrCreateCollection` + 单物理集合）、异步 `refresh_index`
  会阻塞（`syncMode=immediate`，刷新为空操作）、异常退出残留的 `run/seekdb.pid` 会让新实例同步阻塞
  （启动时检测：进程已死则清理，仍存活则直接报错并提示 `SEEKDB_FORCE_UNLOCK=1` 接管）。
  两种模式都跑通了完整浏览器流程（见 5.2 与 `results/browser-embedded.json`）。
- 删除行会在 HNSW 里留下墓碑，删除量大时召回会下降；`drop`/`clear` 之后会自动触发一次刷新，
  数据量很大时建议重建物理集合（`SEEKDB_COLLECTION` 换名）。
- 图片/多模态未实现（题目允许作为扩展）。
