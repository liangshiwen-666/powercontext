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
cp -r demo/app /tmp/after-app
TMP=$(mktemp -d) && cp -r demo "$TMP/demo"
patch -p1 -R -d "$TMP/demo" -i "$PWD/patches/0001-orders-filters.patch" --batch
diff -r "$TMP/demo/app" evidence/snapshots/before/app   # 应无差异
```

关键差异（详见 `patches/0001-orders-filters.patch`）：

- `app/service.py`：新增 `parse_status` / `parse_created_bounds` / `parse_min_amount` 与筛选逻辑，
  只改 `app/`，测试与规格未动；
- `app/routes.py`：`GET /orders` 接收四个筛选参数并校验后传给 service。

快照 sha256（`sha256sum`）：

| 文件 | before | after |
|---|---|---|
| `app/service.py` | `8bd43ebebb15367b48d16a1c1ab14e1d738c172da435ba420fcca21bd855d445` | `fade2c2756657fa015d77ea5a810b014707b754e39ffd6ace9a3e7b1ac9c83a1` |
| `app/routes.py` | `8a4c1eb396be32246e3bc1828920c943e98ff8f1a064c10285adce34d50c5935` | `34cba6d01c9fe3105dbb4c2e532203a3e87b4f09c6ea4542a5fa29bcf19dfc30` |
