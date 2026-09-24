
# 公众影像授权核对服务

本仓库承载摄影作品声明、人物授权与公开用途的纯后端服务。服务以 SQLite 文件保存业务数据，不连接共享数据库、缓存或第三方网络接口；数据库位置由 `DATABASE_PATH` 配置，监听端口由 `PORT` 配置。

代码按 HTTP 入口（`app/api.py`）、领域规则（`app/domain.py`，纯函数）、用例编排（`app/service.py`）和持久化（`app/store.py`）组织。当前实现把原始作品、人物识别声明、监护关系、拍摄场次、授权用途/地域/期限关联为可追溯的影像版本，并支持裁剪、打码、合成与字幕替换的限制继承重算。

## 能力概览

- **版本谱系**：original/crop/mask/composite/subtitle 逐版记录摘要、派生参数与在场人物（含遮蔽状态）。
- **多人最严格门控**：按「版本 × 用途 × 渠道 × 地域 × 时点」逐人判定，任一人未获授权整体无效，期限取最早到期；未成年人授权须监护人签署。
- **时点重放**：门控只采纳 `recorded_at` 不晚于判定时点的事实，迟到授权/撤回不能倒改已发布证据；发布时固化完整证据与摘要。
- **方案审批**：用途方案可修订留痕，批准须由无利益关系审核人作出且门控通过。
- **撤回处置**：撤回只冻结实际依赖该授权的在发渠道，并生成按渠道分类的处置清单。
- **两种视图**：`GET /verify` 对外只返回布尔有效性；`/admin/...` 内部接口可重放影像摘要、授权范围、遮蔽处理与批准链。

字段、枚举与端点约定见 `contracts/entities.json`，领域语义见 `docs/domain.md`，`fixtures/example.json` 为不含真实主体信息的本地样例。

## 主要端点

| 类别 | 方法与路径 |
| --- | --- |
| 录入 | `POST /admin/works` `/sessions` `/persons` `/claims` `/guardianships` `/grants` |
| 版本 | `POST /admin/versions` |
| 方案审批 | `POST /admin/proposals`、`/admin/proposals/{id}/revisions`、`/decisions`、`GET .../chain` |
| 发布 | `POST /admin/publications` |
| 撤回处置 | `POST /admin/grants/{id}/revocations`、`GET /admin/dispositions`、`POST /admin/dispositions/{id}/handle` |
| 核验 | `GET /verify?version_id=&purpose=&channel=&region=&as_of=` |
| 证据 | `GET /admin/publications/{id}/evidence` |

## 开发命令

- `make migrate`：初始化或升级 SQLite 文件（自动应用 `migrations/` 下未执行的迁移）。
- `make test`：运行自动化测试。
- `make run`：启动后端进程。
- `docker compose up --build`：构建并启动容器，宿主机端口可通过 `APP_PORT` 调整。

本地运行和测试不要求固定账号，也不会请求外部业务系统。

## 开发检查

- 安装依赖：`python3 -m pip install -r requirements.txt`
- 运行测试：`python3 -m pytest`（或 `python3 -m unittest discover -s tests`）
- 编译检查：`python3 -m compileall -q .`
