
# 公众影像授权核对服务

本仓库承载摄影作品声明、人物授权与公开用途的纯后端服务。服务以 SQLite 文件保存业务数据，不连接共享数据库、缓存或第三方网络接口；数据库位置由 `DATABASE_PATH` 配置，监听端口由 `PORT` 配置。

代码按 HTTP 入口、领域规则和持久化职责组织。当前代码提供统一的启动入口、健康检查、首个数据库迁移以及对应的自动化检查，`fixtures/example.json` 保存不含真实主体信息的本地样例。

## 开发命令

- `make migrate`：初始化或升级 SQLite 文件。
- `make test`：运行自动化测试。
- `make run`：启动后端进程。
- `docker compose up --build`：构建并启动容器，宿主机端口可通过 `APP_PORT` 调整。

本地运行和测试不要求固定账号，也不会请求外部业务系统。

## 开发检查

- 安装依赖：`python3 -m pip install -r requirements.txt`
- 运行测试：`python3 -m pytest`
- 编译检查：`python3 -m compileall -q .`
