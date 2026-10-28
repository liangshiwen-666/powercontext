# PowerContext 2026 大赛 · 生态赛题资产分支

本分支基于上游 **oceanbase/powercontext** 的 `2026-competition-ecosystem`，用于承载
2026 OceanBase 数据库大赛生态类赛题的资产与实现。

## 上游与基线

| 项 | 值 |
| --- | --- |
| 上游仓库 | `https://github.com/oceanbase/powercontext`（GitHub repository id `1093180734`） |
| 基线分支 | `2026-competition-ecosystem` |
| 基线 commit | `d647275c308e09b7b623f322a26f5c4e85734fb0` |
| 本分支 | 在基线之上仅新增各赛题资产，不改动上游产品代码 |

上游祖先关系可复验：

```bash
git merge-base --is-ancestor d647275c308e09b7b623f322a26f5c4e85734fb0 HEAD
```

## 目录结构

| 目录 | 赛题 | 产物 |
| --- | --- | --- |
| `gaode/` | 高德开放平台 · 周边去哪儿 | `SKILL.md`（地点推荐 Skill） |
| `minimax/` | MiniMax × OceanBase · SDD 上下文管理 | 源码、运行说明、验证样例、证据与记录 |
| `dify/` | Dify 生态集成 | 插件与验证材料 |
| `qoder/` | Qoder 生态集成 | 集成材料 |
| `stepfun/` | 阶跃星辰 × seekdb · 端侧 AI 推理与混合检索 | 可运行的 Web 应用（浏览器本地向量化 + seekdb 混合检索）、源码与脚本、一页技术说明、实测数据 |

各目录内的 `README.md` / `SUBMISSION.md` / `docs/` 为该赛题的运行说明、填表信息与验证材料。

## 提测信息

- 仓库：`https://github.com/liangshiwen-666/powercontext-fork`
- 分支：`2026-competition-ecosystem`
- Commit：以提测平台填写时该分支的 commit 为准（`git rev-parse 2026-competition-ecosystem`）

## 说明

- 本仓库是上游公开仓库的 Fork；另有一份同内容的报名仓库
  `liangshiwen-666/powercontext`。
- 两个仓库的 `2026-competition-ecosystem` 分支保持同一 head，判题从任一仓库克隆均可。
