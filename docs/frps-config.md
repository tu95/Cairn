# FRP 配置

域名：

```text
i.havehave.fun
```

Token：

```text
6f0ec0aec56c4e933ddf09e6b1bb80e5dc2a33fb5b7f8c3a
```

## frps 服务端

`frps.toml`：

```toml
bindAddr = "0.0.0.0"
bindPort = 7000

auth.method = "token"
auth.token = "6f0ec0aec56c4e933ddf09e6b1bb80e5dc2a33fb5b7f8c3a"
```

服务端需要放行：

```text
7000/tcp
10022/tcp
20000-20100/tcp
```

## frpc 客户端

`frpc.toml`：

```toml
serverAddr = "i.havehave.fun"
serverPort = 7000

auth.method = "token"
auth.token = "6f0ec0aec56c4e933ddf09e6b1bb80e5dc2a33fb5b7f8c3a"

[[proxies]]
name = "ssh"
type = "tcp"
localIP = "127.0.0.1"
localPort = 22
remotePort = 10022
```

连接客户端机器 SSH：

```bash
ssh -p 10022 <user>@i.havehave.fun
```

## 端口范围映射

FRP 的端口范围映射用 INI 写法更直接。`frpc.ini` 示例：

```ini
serverAddr = i.havehave.fun
serverPort = 7000

auth.method = token
auth.token = 6f0ec0aec56c4e933ddf09e6b1bb80e5dc2a33fb5b7f8c3a

[ssh]
type = tcp
local_ip = 127.0.0.1
local_port = 22
remote_port = 10022

[range:tcp_ports]
type = tcp
local_ip = 127.0.0.1
local_port = 20000-20100
remote_port = 20000-20100
```

这会把客户端本机：

```text
127.0.0.1:20000-20100
```

映射到公网：

```text
i.havehave.fun:20000-20100
```

如果必须使用 TOML，需要把范围展开成多个 `[[proxies]]`。
