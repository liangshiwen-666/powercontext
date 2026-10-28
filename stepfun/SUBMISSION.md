# 提交元数据（SUBMISSION）

> 本文件把「仓库 / 上游基线 / 分支 / commit / 文件清单」一次性对齐，填表时直接照抄。

## 一、填表值

| 表单字段 | 填写内容 |
| --- | --- |
| 代码仓库地址 | `https://github.com/liangshiwen-666/powercontext-fork.git`（平台已登记，锁定不可改） |
| **Branch** | **`2026-competition-ecosystem`** |
| **Commit id** | 本目录所在分支的最新提交，**40 位完整 SHA**，即 `git rev-parse HEAD` 的输出 |

> 本作品路径：`stepfun/`。仓库是 `oceanbase/powercontext` 的正规 fork，
> `2026-competition-ecosystem` 分支基于官方基线 `d647275` 开发，仅新增赛题资产，不改动上游产品代码。

## 二、上游与基线（可核对）

| 项 | 值 |
| --- | --- |
| 上游仓库 | <https://github.com/oceanbase/powercontext> |
| 官方基线分支 | `2026-competition-ecosystem` |
| 官方基线 commit | `d647275c308e09b7b623f322a26f5c4e85734fb0` |
| 本团队 fork | <https://github.com/liangshiwen-666/powercontext-fork> |
| 提交分支 | `2026-competition-ecosystem` |
| 本作品路径 | `stepfun/` |

祖先关系可复验：

```bash
git merge-base --is-ancestor d647275c308e09b7b623f322a26f5c4e85734fb0 HEAD && echo "based on baseline"
```

## 三、文件清单（对照评分要求）

| 评分要求 | 仓库内文件 | 说明 |
| --- | --- | --- |
| 完整流程（导入→向量化→写库→检索） | `web/src/App.tsx`、`web/src/worker/embed.worker.ts`、`server/src/index.ts` | 拖入/示例语料 → 进度条 → 结果列表与原文 |
| 本地推理 | 同上 + `server/src/db.ts` | 推理在 Web Worker；服务端 collection `embeddingFunction: null`，`/api/health` 返回 `embedding: "browser-local"` |
| 模型量化 | `docs/TECH.md` §1、`scripts/fetch-model.mjs`、`scripts/quantize.py` | fp32/fp16/int8 三档，体积 90.5/45.3/22.9 MiB（3.95×），质量对比表 |
| 后端与降级 | `web/src/worker/embed.worker.ts`、`web/src/App.tsx` | WebGPU(fp16) 优先，失败/缺失自动 WASM(q8)，徽标与横幅提示原因 |
| seekdb 数据层 | `server/src/db.ts`、`docs/TECH.md` §3 | `ik` 全文 + HNSW(512, cosine)；单次 `hybridSearch` 库内 RRF 融合 |
| 状态与刷新 | `server/src/db.ts`、`web/src/App.tsx` | 集合注册表、精确计数、待刷新条数、`refresh_index`、单物理集合 + `kb` 分区 |
| 可复现性 | `README.md`、`docs/REPRO.md`、`docker-compose.yml` | 有/无 Docker 均可启动；自动探测 2881 或回退嵌入式 |
| 一页技术说明 | `docs/TECH.md` | 模型量化、端侧运行、seekdb 使用、状态与优化、实测数据 |
| 实测数据 | `results/` | 三档精度评测 + 四组浏览器实测原始 JSON |

## 四、已验证环境

- Windows 11 x64，Node 24.21，Git 2.53
- seekdb：`quay.io/oceanbase/seekdb` 容器（server 模式，2881）与 seekdb-js 1.4.0 嵌入式，两种模式均跑通完整流程
- 浏览器：Microsoft Edge 156.0.4314.50（`--headless=new`；`--disable-gpu` 用于触发 WASM 降级）
- 模型：`Xenova/bge-small-zh-v1.5`（MIT），512 维，CLS 池化 + L2 归一化

## 五、证据索引

| 文件 | 内容 |
| --- | --- |
| `results/eval-int8.json` / `eval-fp16.json` / `eval-fp32.json` | 50 条查询、40 块的逐条结果与 Recall@5 / nDCG@10 / MRR / 吞吐 |
| `results/browser-webgpu.json` | WebGPU · fp16 全流程（导入、检索、×10 基准、二次加载） |
| `results/browser-wasm.json` | WASM · q8 同上（`--disable-gpu` 触发降级） |
| `results/browser-embedded.json` | 无 Docker（嵌入式 seekdb）同流程 |
| `results/browser-server-auto.json` | 自动探测到 Docker 后的记录 |
| `results/browser-summary.json` | 浏览器两组数据整理版（`docs/TECH.md` 5.2 表格来源） |

## 六、启动与验收

```bash
cd stepfun
npm install
npm run fetch:model     # 模型权重 68.7 MB，不随仓库分发
npm run dev             # 自动选择 seekdb 后端（有 Docker 用 server，无则嵌入式）
```

浏览器打开 <http://localhost:5173>；逐条验收见 `docs/REPRO.md` 第 2 节。
