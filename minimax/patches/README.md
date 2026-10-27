# 补丁（由 MiniMax 生成）

| 文件 | 说明 |
|---|---|
| `0001-orders-filters.patch` | MiniMax 针对验证样例生成的 unified diff（before → after） |
| `0001-orders-filters.meta.json` | 生成元数据：模型、请求记录路径、上下文 sha256、响应 sha256、补丁 sha256、usage、trace id |

对应开发记录在 `records/minimax/`。核对方式：

```bash
python -c "import hashlib,json;d=json.load(open('patches/0001-orders-filters.meta.json'));print(d['patch_sha256']);print(hashlib.sha256(open('patches/0001-orders-filters.patch','rb').read()).hexdigest())"
```

重新生成（需要 API Key）：

```bash
export MINIMAX_API_KEY=sk-...
bash scripts/live-generate.sh
```
