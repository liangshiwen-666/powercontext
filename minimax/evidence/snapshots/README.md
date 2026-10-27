# 修改前后代码快照

本目录是验证样例"代码修改有效"的实体快照，可直接与 `patches/0001-orders-filters.patch`、
`evidence/logs/` 的测试日志逐项核对。

| 路径 | 内容 | 说明 |
|---|---|---|
| `before/app/` | 修改**前**的业务工程实现代码 | 由当前提交（修改后）反向应用 `patches/0001-orders-filters.patch` 得到，逐字节可复现 |
| `after/app/` | 修改**后**（当前提交）的实现代码 | 与仓库中 `demo/app/` 一致 |

生成方式（无需 API Key）：

```bash
# before = after 反向应用补丁
TMP=$(mktemp -d) && cp -r demo "$TMP/demo"
patch -p1 -R -d "$TMP/demo" -i "$PWD/patches/0001-orders-filters.patch" --batch
diff -r "$TMP/demo/app" evidence/snapshots/before/app   # 应无差异
```

关键差异（详见 `patches/0001-orders-filters.patch`）：

- `app/service.py`：新增筛选参数解析与过滤逻辑（状态 / 时间范围 / 金额下限 / 组合），
  只改 `app/`，测试与规格未动；
- `app/routes.py`：`GET /orders` 接收四个筛选参数，校验后传给 service。

快照 sha256（`sha256sum`，与当前补丁对应）：

| 文件 | before | after |
|---|---|---|
| `app/service.py` | `8bd43ebebb15367b48d16a1c1ab14e1d738c172da435ba420fcca21bd855d445` | `b4d1fac6345ac11e5b383d58019e51f4c1f7c25ffa5c10e501ec8c219b010408` |
| `app/routes.py` | `8a4c1eb396be32246e3bc1828920c943e98ff8f1a064c10285adce34d50c5935` | `495d9c7077403f075e0c59ef9adfd2f9707a7385cd683128da872099818abd3d` |
