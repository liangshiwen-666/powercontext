# 复现步骤与验收清单

> 目标：在一台干净的机器上，按下面的顺序在 10 分钟内完成「导入 → 本地向量化 → 写入 seekdb → 混合检索」。

## 0. 环境

| 项 | 版本 |
|---|---|
| Node.js | ≥ 20（开发与实测使用 24.21） |
| 浏览器 | Chrome / Edge 最新版（WebGPU 可用时走 GPU） |
| seekdb | `quay.io/oceanbase/seekdb:latest`（Docker）或嵌入式（seekdb-js 1.4.0） |
| 模型 | `Xenova/bge-small-zh-v1.5`（int8 / fp16 / fp32 三档） |

## 1. 安装与启动

```bash
cd stepfun
npm install
npm run fetch:model            # ~72 MB，落到 web/public/models
npm run dev                    # 自动选 seekdb 后端 + 前端
```

浏览器打开 <http://localhost:5173>。seekdb 后端由启动时自动判定：

| 环境 | 结果 |
|---|---|
| 本机 2881 有 seekdb（`docker compose up -d`） | `server` 模式，数据在 `./stepfun-data/docker` |
| 没有 Docker / 2881 没人监听 | 自动回退 `embedded`，数据在 `./stepfun-data/seekdb` |

想强制指定：`npm run dev:embedded` 或 `npm run dev:server-only`（或 `SEEKDB_MODE=...`）。
页头徽标与 `/api/health` 都会显示当前模式以及是"自动探测"还是"显式配置"。

## 2. 功能验收（对应赛题 3.1）

| # | 操作 | 预期 |
|---|---|---|
| 1 | 打开页面 | 顶部徽标出现 `WebGPU · fp16` 或 `WASM · q8`，以及 `seekdb server …` 或 `seekdb embedded` |
| 2 | 点「载入示例语料」 | 进度条依次显示 本地向量化 → 写入 seekdb → 刷新索引；左侧集合 `kb` 文档数变为 40 |
| 3 | 输入「seekdb 怎么做混合检索」→ 检索 | 返回 10 条结果，第一条为「混合检索与 RRF 融合」；结果行显示 `seekdb hybridSearch（向量 + 全文，库内 RRF 融合）` |
| 4 | 切到「仅全文」再检索 | 仍能命中含关键词的文档，说明全文索引生效 |
| 5 | 再导入一个 .txt 文件 | 文档数增加；新内容立刻可被检索（`syncMode=immediate`） |
| 6 | 点「新建」集合 → 检索 | 新集合为空（无结果）；切回 `kb` 后结果恢复，证明分区隔离 |
| 7 | 点「刷新索引」 | 右侧出现「手动刷新索引」耗时；待刷新条数归零 |
| 8 | 点「导出」 | 下载 JSON，含 UA、后端、dtype、分阶段耗时 |

## 3. 降级路径验证

1. Chrome 打开 `chrome://flags/#enable-unsafe-webgpu` → 设为 Disabled → 重启浏览器；
2. 刷新页面：徽标应为 `WASM · q8`，并出现「已自动降级到 WASM」横幅与原因；
3. 重复第 2–3 步，导入与检索仍应正常，结果基本一致。

## 4. 命令行自测（不依赖浏览器）

```bash
# 有 Docker（server 模式）：完整数据层自测
npm run selftest

# 无 Docker 时：只验证写入路径（伪向量，不加载模型）
npm run selftest -- --no-model

# 检索质量与吞吐（写入临时集合，跑完自动删除）
npm run eval -- --dtype q8   --label int8
npm run eval -- --dtype fp16 --label fp16
npm run eval -- --dtype fp32 --label fp32
```

> `selftest` 在**嵌入式**模式下与 seekdb 原生引擎同进程，加载模型后检索阶段可能阻塞——
> 这是测试脚本的局限，应用本身（服务端从不加载模型）不受影响。无 Docker 的权威验证用
> `npm run browser:check`：它走真实 HTTP + 浏览器流程，本仓库的 `results/browser-embedded.json`
> 就是这样产出的。

结果写入 `results/eval-<label>.json`，控制台打印 Recall@5 / nDCG@10 / MRR / chunks/s。

## 5. 浏览器性能数据采集（技术说明 5.2）

同一台机器、同一浏览器版本、同一语料，分别采集 `WebGPU · fp16` 与 `WASM · q8` 两组：

1. 硬刷新页面（Ctrl+Shift+R），记录「模型加载（首次）」耗时；
2. 再刷新一次，记录二次加载耗时（走浏览器缓存）；
3. 载入示例语料，记录「文档向量化」与「写入 + 索引刷新」耗时；
4. 输入同一问题，点 **「基准 ×10」**：页面会显示 P50 / P95（首次预热自动丢弃）；
5. 点「导出」保存 JSON（其中 `bench` 字段含 10 次原始样本），按 TECH.md 5.2 的表格填写。

### 自动化（可选）

若想用无头浏览器重复上面的采集：

```bash
# 1) 先手动开一个带调试端口的浏览器（WASM 组加 --disable-gpu）
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new --remote-debugging-port=9333 \
  --user-data-dir=%TEMP%\edge-wgpu about:blank

# 2) 驱动页面：导入示例语料 → 检索 → 基准 ×10 → 二次加载
npm run browser:check -- --port 9333 --out results/browser-webgpu.json
```

输出包含 badge（后端）、各阶段耗时、top-3 结果与 10 次原始样本。

> 计时口径：端到端 = 按下回车到结果渲染完成；包含查询编码 + HTTP 往返 + seekdb 检索 + 前端渲染。

## 6. 提交

```bash
git checkout -b 2026-competition-ecosystem   # 或复用已有同名分支
git add stepfun
git commit -m "feat(stepfun): browser-local embedding + seekdb hybrid retrieval mini knowledge base"
git push origin 2026-competition-ecosystem
```

在测评平台填写该分支最新 commit hash。
