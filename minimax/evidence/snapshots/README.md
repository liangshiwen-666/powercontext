# 修改前后代码快照

本目录是验证样例"代码修改有效"的实体快照，可直接与 `patches/0001-orders-filters.patch`、
`evidence/logs/` 的测试日志逐项核对（V1 由 `scripts/` 中的同一纯 Python 补丁引擎生成）。

| 路径 | 内容 | 说明 |
|---|---|---|
| `before/app/` | 修改**前**的业务工程实现代码 | 由当前提交（修改后）**反向应用**补丁得到，逐字节可复现 |
| `after/app/` | 修改**后**（当前提交）的实现代码 | 与仓库中 `demo/app/` 一致 |

生成方式（无需 API Key）：

```bash
TMP=$(mktemp -d) && cp -r demo "$TMP/demo"
python - <<'PY'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from sddctx.patch import reverse_patch
reverse_patch(Path("<TMP>/demo"), Path("patches/0001-orders-filters.patch").read_text(encoding="utf-8"))
PY
diff -r "$TMP/demo/app" evidence/snapshots/before/app   # 应无差异
```

关键差异（详见 `patches/0001-orders-filters.patch`）：`app/service.py` 新增筛选参数解析与过滤逻辑，
`app/routes.py` 接收并校验四个筛选参数；只改 `app/`，测试与规格未动。

快照 sha256（`sha256sum`，与当前补丁对应）：

| 文件 | before | after |
|---|---|---|
| `app/service.py` | `8bd43ebebb15367b48d16a1c1ab14e1d738c172da435ba420fcca21bd855d445` | `9b9dd1df25bb32cf29f6d364a58808081f9e8fdeb3ae6a338eb9451e5bb182c3` |
| `app/routes.py` | `8a4c1eb396be32246e3bc1828920c943e98ff8f1a064c10285adce34d50c5935` | `f7317a885d34c74d203635e015717245448ab6dd0a329daf31d09a43e9cb4fd0` |
