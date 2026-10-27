# Dify 赛道 · OceanBase 2026 Competition Ecosystem

本目录是 **Dify × OceanBase 2026「Just for Fun」** 赛道的参赛作品目录。

## 作品

**《记忆当铺 Memory Pawnshop》** —— 一个"把记忆当进当铺"的文字互动游戏。

玩家扮演雾河街十三号「灯下记忆当铺」的掌柜，接待三位客人（阿沅 / 老周 / 白棠）：翻镇子档案核对真相 → 追问 → 上酒（把一段指定记忆写进客人的记忆库）→ 客人醒来按"他现在相信的记忆"说话 → 开票结算。客人的"相信的记忆"与"客观档案"都存放在 **PowerContext** 中，并由**嵌入式 seekdb** 做持久化与混合检索（全文 + 向量）。

- 作品目录：[`memory-pawnshop/`](memory-pawnshop/)
- 快速开始：[`memory-pawnshop/README.md`](memory-pawnshop/README.md)
- 验证步骤：[`memory-pawnshop/VERIFY.md`](memory-pawnshop/VERIFY.md)
- 提交元数据（填表用）：[`memory-pawnshop/SUBMISSION.md`](memory-pawnshop/SUBMISSION.md)

## 仓库 / 上游 / 基线

| 项 | 值 |
| --- | --- |
| 上游仓库（官方） | <https://github.com/oceanbase/powercontext> |
| 官方基线分支 | `2026-competition-ecosystem` |
| 官方基线 commit | `d647275c308e09b7b623f322a26f5c4e85734fb0`<br>（`chore: initialize 2026 competition ecosystem directories`，2026-10-23） |
| 本团队仓库（fork） | <https://github.com/liangshiwen-666/powercontext-fork> |
| 本作品提交分支 | `2026-competition-ecosystem` |
| 分支与官方基线关系 | `behind_by = 0`，`merge_base = d647275`（直接基于官方基线开发，未落后官方） |
| 本作品目录 | `dify/memory-pawnshop/` |

> 本仓库同时承载其他赛道的作品：`gaode/`、`minimax/`、`qoder/`、`stepfun/`。
> 本目录只负责 **Dify 赛道**，不修改其他赛道目录。

## 目录结构

```
dify/
├── README.md                      # 本文件：Dify 赛道入口 / 上游与提交信息
└── memory-pawnshop/               # 《记忆当铺》作品
    ├── README.md                  # 运行说明（环境、启动、复现步骤、评分点对照）
    ├── VERIFY.md                  # 逐客人验证样例（可照做复现）
    ├── SUBMISSION.md              # 提交元数据（仓库/分支/commit/文件清单）
    ├── requirements.txt
    ├── DSL/
    │   ├── memory-pawnshop.yml    # Dify Chatflow DSL（直接导入）
    │   └── generator/             # DSL 生成器 + 各 Code 节点源码
    ├── data/                      # 初始化数据（档案、客人、种子记忆）
    ├── docker/                    # PowerContext(seekdb) 容器化 + compose 补丁
    ├── scripts/                   # seed / smoke / verify / dify_ops / 证据采集
    ├── docs/                      # 设计、架构、PowerContext API 手册
    └── evidence/                  # 实测证据（Dify 逐轮实录、PC 读写、离线仿真）
```

## 如何确认"确实用到了 Dify + PowerContext + seekdb"

1. **Dify**：`DSL/memory-pawnshop.yml` 可直接导入 Dify 1.18；`evidence/dify_run_full.json` 是经由 Dify Service API 跑出的 14 轮完整对局实录（含三张当票与结局）。
2. **PowerContext**：每张当票上的 `memory_id`、`scope`、`R{revision}` 都是 PowerContext 的真实返回值；`evidence/pc_smoke_seekdb.json` 记录了写入与混合检索（`matched_by:["text","vector"]`）。
3. **seekdb**：PowerContext 以 `POWERCONTEXT_SERVER_DATABASE_KIND=seekdb` 启动，`evidence/pc_ready_*.json` 的 `database=ready` 即 seekdb 就绪。

## 提交信息

- **Branch**：`2026-competition-ecosystem`
- **Commit id**：填写本目录所在分支的最新提交（测评平台提交时以 `git rev-parse HEAD` 为准；完整 SHA 见平台提交记录或聊天记录）
- **作品路径**：`dify/memory-pawnshop/`
