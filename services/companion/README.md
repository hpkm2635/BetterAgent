# 陪伴工具服务（Companion Tools Service）

任务3 / Feature Branch: `feat/companion-tools` / 负责人：张劭哲

固定端口：**8096**

## 功能

- 统计写入回调：技术总监每轮 `action_completed` 后写入陪伴统计数据
- 日程提醒 CRUD：新增 / 查询 / 删除，APScheduler 到期自动触发
- SQL Agent：自然语言查询陪伴数据（规则模板实现 NL2SQL）
- 规则推荐：学习话题 + 快到期的日程提醒（纯规则，无 LLM）

## 目录结构

```
services/companion/
  main.py            # FastAPI 入口（端口 8096）
  schedule_service.py # APScheduler 日程管理
  sql_agent.py        # NL2SQL + SQLite 陪伴数据查询
  recommendation.py   # 规则推荐逻辑
  database.py         # SQLite 建表与连接
  companion.db        # SQLite 数据库（已被 .gitignore 忽略）
  requirements.txt
  README.md
```

## 启动

```powershell
# 在项目根目录，激活虚拟环境后
.\.venv\Scripts\activate
pip install -r services\companion\requirements.txt
python -m services.companion.main
```

服务启动后监听 `http://127.0.0.1:8096`，`companion.db` 会自动建表。

## 接口清单

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/companion/stat` | 统计写入（技术总监调用） |
| POST | `/api/schedule/add` | 新增日程 |
| GET | `/api/schedule/list?chat_id=` | 查询日程 |
| DELETE | `/api/schedule/{schedule_id}` | 删除日程 |
| POST | `/api/companion/query` | NL2SQL 陪伴数据查询 |
| GET | `/api/companion/recommendations?chat_id=` | 规则推荐 |

## 契约测试

```powershell
pytest tests/test_api_contract.py -v -k TestCompanionTools
```

## 说明

- 提醒触发由 APScheduler 内部定时，到期时把 ActionDecision 发布到 NATS 的
  `agent.action.{channel}.{chat_id}`，由 Go Core 的 WebGateway / GotdAdapter
  推送到 Web 页面或 Telegram（不再依赖不存在的 8097 内部端点）。
- 服务已接入 `runner.py` 自动启动（端口 8096 就绪探测）；也可手动
  `python -m services.companion.main` 单独启动。
- `companion.db` 已被 `.gitignore` 的 `*.db` 规则覆盖，不会被提交。
