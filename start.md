# Cairn 启动说明（后台运行）

本页用于记录当前项目的后台启动与重启命令，默认使用 `screen` 管理常驻进程，命令日志写入 `/tmp/cairn-launch.log`。

## 一、后台启动

```bash
cd /Users/apple/code/Cairn
screen -dmS cairn-launch \
  zsh -lc 'cd /Users/apple/code/Cairn && uv run --project cairn cairn launch --config dispatch.yaml > /tmp/cairn-launch.log 2>&1'
```

- `-dmS cairn-launch`：在后台新建名为 `cairn-launch` 的 screen 会话，不占用当前终端。
- `zsh -lc '...'`：使用登录 shell 执行命令，保证环境变量、`uv` 等命令可用。
- `> /tmp/cairn-launch.log 2>&1`：将标准输出和错误都写到日志文件，便于排查问题。

## 二、查看运行状态

```bash
screen -ls | grep cairn-launch
ps -ef | grep -E 'cairn launch|SCREEN -dmS cairn-launch' | grep -v grep
tail -f /tmp/cairn-launch.log
```

- `screen -ls`：确认 `cairn-launch` 会话是否还在。
- `ps -ef ...`：确认 `cairn launch` 进程链路是否存在。
- `tail -f`：实时查看启动日志。

## 三、后台重启（推荐）

```bash
pkill -f "cairn launch --config dispatch.yaml" || true
screen -S cairn-launch -X quit || true
cd /Users/apple/code/Cairn
screen -dmS cairn-launch \
  zsh -lc 'cd /Users/apple/code/Cairn && uv run --project cairn cairn launch --config dispatch.yaml > /tmp/cairn-launch.log 2>&1'
```

- 先杀掉旧的 `cairn launch` 进程（`pkill -f`），避免端口冲突或多实例竞争。
- 再关闭旧 `screen` 会话（`screen -S ... -X quit`）。
- 重新按“后台启动”逻辑拉起新实例。

## 四、完整停止

```bash
pkill -f "cairn launch --config dispatch.yaml" || true
screen -S cairn-launch -X quit || true
```

- 两条命令一起执行可清理进程与会话。
- 如果你只想优雅退出，可只 `pkill` 先停服务，再确认 `ps` 无残留再 `quit` 会话。

## 五、端口与访问地址

- 本地服务启动后默认监听：`http://127.0.0.1:8000`
- 若配置了允许外网访问，会显示 `http://0.0.0.0:8000`，可按你的 `dispatch.yaml` 与防火墙策略访问。
