# Cairn

基于事实图（Fact-Intent Graph）的协作式探索引擎。本 README 只记录本次维护改动与运行方式，原有的项目介绍内容已移除。

## 快速开始

统一入口是根目录的 [`start.py`](./start.py)（纯标准库，零依赖）。

**前提**
- macOS 或 Linux，Python ≥ 3.12，已安装 [`uv`](https://docs.astral.sh/uv/)
- PATH 上至少有一个 worker CLI：`codex` 或 `claude`

**常用命令**

```bash
python3 start.py            # 前台启动 server + dispatcher（Ctrl+C 停止）
python3 start.py up -d      # 后台启动，写 pidfile + 日志到 workspace/
python3 start.py status     # 查看进程与服务健康
python3 start.py logs -f    # 跟随后台日志
python3 start.py restart -d # 后台重启
python3 start.py stop       # 优雅停止后台实例
python3 start.py doctor     # 环境自检（uv / 配置 / worker CLI）
python3 start.py test       # 运行回归测试
python3 start.py --help     # 查看全部命令
```

首次运行会自动从 `dispatch.example.yaml` 生成 `dispatch.yaml`，之后可在 Web UI 或直接编辑该文件配置 worker。服务默认监听 `http://127.0.0.1:8000`。

## 运行时目录

所有运行产物统一落在 `./workspace/`（已 gitignore，可用 `CAIRN_WORKSPACE` 覆盖）：

- `workspace/cairn.db` —— SQLite 数据库
- `workspace/prompts/` —— worker 引用的图快照
- `workspace/pi/` —— pi worker 运行目录
- `workspace/cairn.pid` / `workspace/cairn.log` —— 后台实例的 pid 与日志

## 本次改动

引用（commit `bbdc119` 及本次提交）：

### 1. 统一控制器 `start.py`（本次新增）
单文件入口，负责启动与编排，业务逻辑仍委托给已验证的 `cairn` CLI。通过命令注册表扩展（见文件末尾“扩展方式”），后台实例用 workspace 下的 pidfile 管理，不依赖 screen。参见 [`start.py`](./start.py)。

### 2. 服务端并发正确性 —— [`cairn/src/cairn/server/db.py`](./cairn/src/cairn/server/db.py)
- `get_conn` 改为自动提交 + 手动 `BEGIN IMMEDIATE`，每个请求全程持写锁，使 `heartbeat`/`conclude` 的“先 SELECT 校验、再 UPDATE”认领逻辑原子化，杜绝并发下同一 intent 被多个 worker 重复认领。
- 增加 `PRAGMA busy_timeout=15000`：WAL 写冲突时排队等待，而非直接抛 `database is locked` 导致 500。
- `configure` 改用独立连接建表/迁移，避免与显式事务和 `executescript` 冲突。

### 3. 运行时目录归拢 —— [`cairn/src/cairn/workspace.py`](./cairn/src/cairn/workspace.py)（新增）
DB、prompt 快照、pi worker 目录从 `~/.local/share`、`/tmp` 统一到 `./workspace/`（`CAIRN_WORKSPACE` 可覆盖）。接线见 `server/db.py`、`dispatcher/tasks/common.py`、`dispatcher/workers/adapters/pi.py`。

### 4. Web UI 黑客终端暗色主题 —— [`cairn/src/cairn/server/static/index.html`](./cairn/src/cairn/server/static/index.html)
通过 `tailwind.config` 重映射调色板（slate 阶梯反相、accent 的 50/100 转暗、700/800 转亮）实现整体换肤，避免逐个改工具类；仅对背景/文字复用同一色 token 的冲突做类名分离（`bg-white→bg-panel`、`bg-slate-800/900→bg-btn`）。全局等宽字体走系统栈（离线可用），Cytoscape 图的边标签与节点同步调暗。

## 测试

```bash
python3 start.py test
# 等价于：uv run --project cairn --group dev pytest
```

## License

见 [LICENSE](./LICENSE)（GNU AGPLv3）。
