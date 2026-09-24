
# 公众影像授权核对服务

本仓库承载摄影作品声明、人物授权与公开用途的纯后端服务。它把**原始作品、人物识别声明、监护关系、拍摄场次、授权用途/地域/期限**关联成可追溯影像版本，回答“某个裁剪、打码、合成或字幕版本，对线下展览 / 社交媒体 / 海外巡展在指定时间点是否仍可公开”。

服务以 SQLite 文件保存业务数据，不连接共享数据库、缓存或第三方网络接口；数据库位置由 `DATABASE_PATH` 配置，监听端口由 `PORT` 配置。

代码按 HTTP 入口（`app/routes.py`）、领域规则（`app/versions.py`、`app/evaluation.py`）、命令服务（`app/service.py`）、追溯查询（`app/queries.py`）与持久化（`app/db.py`、`migrations/`）组织。

## 能力一览

- **可追溯版本血缘**：原始版本派生裁剪 / 打码 / 合成 / 字幕，每级继承并重新计算人物可见状态；打码后的人物不再需要授权。
- **时点核验 + 最严格合并**：多人合影中任一可识别人物在该时点缺少覆盖用途与地域的有效授权，整版即无效；未成年人授权须来自监护人。
- **独立审批**：编辑提交用途方案后，只能由非提交人、非利益相关方的审核人确认；批准时再次核验。
- **不可变发布证据**：发布即固化影像摘要、授权范围、遮蔽处理与批准链；迟到授权、撤回都不倒改历史证据。
- **撤回处置**：撤回只冻结实际受影响的发布渠道，并生成下线处置清单；已遮蔽该人物的版本不受影响。
- **两种视角**：对外核验只回 `valid`；内部可重现任一发布时间采用的证据并比对一致性。

## HTTP 接口（前缀 `/v1`）

| 方法与路径 | 说明 |
| --- | --- |
| `POST /registry/{sessions,works,persons,recognitions,grants}` | 登记场次、作品、人物（含监护）、识别声明、授权 |
| `POST /versions` | 派生版本并重算人物状态（`original/crop/mask/composite/subtitle`） |
| `GET /versions/<ref>` | 版本血缘、摘要与逐人可见/遮蔽状态 |
| `POST /plans` / `POST /plans/decide` | 编辑提交用途方案 / 无利益关系审核人裁决 |
| `POST /publications` | 核验通过后生成不可变发布证据 |
| `POST /withdrawals` | 登记撤回，冻结受影响渠道并生成处置清单 |
| `GET /dispositions?withdrawal_ref=` | 查询处置清单 |
| `POST /verify` | **对外**：仅返回该版本对指定用途是否有效 |
| `POST /internal/evaluate` | 内部：逐人裁决明细与失败原因 |
| `GET /internal/publications/<ref>` | 内部：重放发布时点的摘要/授权范围/遮蔽/批准链 |
| `GET /health` | 健康检查 |

### 对外核验示例

```bash
curl -sX POST localhost:8080/v1/verify -H 'Content-Type: application/json' -d '{
  "version_ref": "V-MASK", "purpose": "public_display",
  "channel": "overseas_tour", "territory": "US",
  "at": "2026-03-01T10:00:00+08:00"
}'
# {"valid": true, ...}   # 只回有效性，不暴露人物或授权细节
```

字段契约见 `contracts/entities.json`，领域规则见 `docs/domain.md`；端到端示例见 `tests/test_authorization.py`，本地样例数据见 `fixtures/example.json`。

## 开发命令

- `make migrate`：初始化或升级 SQLite 文件（按文件名顺序应用迁移）。
- `make test`：运行自动化测试。
- `make run`：启动后端进程（启动时自动迁移）。
- `docker compose up --build`：构建并启动容器，宿主机端口可通过 `APP_PORT` 调整。

本地运行和测试不要求固定账号，也不会请求外部业务系统。

## 开发检查

- 安装依赖：`python3 -m pip install -r requirements.txt`
- 运行测试：`python3 -m pytest`
- 编译检查：`python3 -m compileall -q .`
