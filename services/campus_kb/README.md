# Campus KB RAG Service

`feat/campus-kb` — 校园知识库 RAG 服务，负责冯文哲模块的 HTTP 接口，固定监听
`127.0.0.1:8093`。服务独立启动，不依赖 BetterAgent 的 Go Core、NATS 或其他微服务。

## 本地启动

```bash
# 在仓库根目录执行
python -m pip install -r services/campus_kb/requirements.txt

# 方式一：模块方式
python -m services.campus_kb.main

# 方式二：uvicorn 方式
python -m uvicorn services.campus_kb.main:app --host 127.0.0.1 --port 8093
```

## 环境变量（均为可选）

服务默认使用本地确定性向量，因此不配置任何变量也能启动；Qdrant 不可用时会自动
降级到进程内内存索引。

```text
QDRANT_URL=http://127.0.0.1:6333
QDRANT_API_KEY=...
CAMPUS_KB_QDRANT_COLLECTION=campus_kb

# 可选：改用 OpenAI-compatible embedding API
EMBEDDING_BASE_URL=https://your-provider/v1
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=text-embedding-...
EMBEDDING_DIM=1536
```

## 接口

### 健康检查

```bash
curl http://127.0.0.1:8093/health
```

### 文档入库

```bash
curl -X POST http://127.0.0.1:8093/api/kb/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "documents": [
      {
        "content": "图书馆周一至周五开放至22:00，周末20:00关闭。",
        "source": "lib_faq.md",
        "category": "faq",
        "metadata": {}
      }
    ]
  }'
```

### 知识检索

```bash
curl -X POST http://127.0.0.1:8093/api/kb/search \
  -H "Content-Type: application/json" \
  -d '{"query": "图书馆几点关门", "top_k": 5, "category": null}'
```

## 检索增强说明

- 混合检索：BM25 关键词检索 + 稠密向量检索，使用 RRF 融合排序。
- 语义/长度混合分块：按句子边界切分，同时限制 chunk 长度并保留重叠。
- 查询扩展：校园同义词表补全问法。
- 类别路由：`category` 为空时按关键词做类别加权，不丢弃其他类别结果。
- 幂等入库：以 `source + chunk` 生成稳定 point id，重复入库不会产生重复结果。

返回结果中的 `score` 为 0~1 的稠密向量相似度（越高越相关），RRF 仅用于排序；
当查询与知识库明显无关（稠密相似度低于阈值）时返回空结果。
