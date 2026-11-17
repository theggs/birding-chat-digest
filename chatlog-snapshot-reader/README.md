# 副本模式：从数据库副本获取聊天记录

## 适用情况

`fetch_chatlog.py` 默认通过正在运行的 chatlog 读取微信当前使用的数据库。微信频繁写入数据库时（例如群消息很多，或微信刚启动、正在同步消息），读取可能超时，导致部分群获取失败。

副本模式先把数据库复制一份，再从副本读取，不再与微信的写入相互影响：

1. 将微信账号目录下 `db_storage` 中的全部 `.db`、`.db-wal` 文件和 `all_keys.json` 复制到临时目录；
2. 用专用读取器 `chatlog-snapshot-reader` 打开副本。该读取器不会连接正在运行的微信，也不会提取密钥；
3. 调用 `fetch_chatlog.py` 从读取器获取消息；
4. 关闭读取器，删除副本。

整个过程只读取原数据库，不做任何修改。

## 运行环境

- macOS（已测试）或 Linux。Windows 不在支持范围内，见文末说明。
- Python 3.9 或以上版本。
- Go，并且能使用 CGO（macOS 需安装 Xcode 命令行工具）。
- chatlog 源码，并且已在 chatlog 中配置好微信账号和数据库密钥。读取器会使用 chatlog 配置文件（默认 `~/.chatlog/chatlog.json`）中的账号、数据目录和密钥。

chatlog 源码版本需提供 `modules.PlatformFactory`、`chatlog.NewApplicationWithFactory` 等接口。版本过旧时，编译会报 `undefined` 错误，此时需要更新 chatlog 源码。

## 编译读取器

```bash
python3 chatlog-snapshot-reader/build_reader.py \
  --chatlog-src <chatlog 源码目录> \
  --out ~/bin/chatlog-snapshot-reader
```

编译脚本通过 Go 的 `-overlay` 参数把 `src/` 下的文件加入编译，不会修改 chatlog 源码目录中的任何文件。编译前会先运行读取器自带的测试，可用 `--skip-tests` 跳过。

`src/` 中的文件：

| 文件 | 作用 |
|---|---|
| `internal/chatlog/modules/snapshot.go` | 副本数据源：不查找微信进程，不提取密钥，只允许打开副本目录内的数据库 |
| `internal/chatlog/modules/snapshot_test.go` | 上述限制的测试 |
| `cmd/snapshot-reader/main.go` | 读取器入口，使用独立的配置目录，收到终止信号后正常关闭 |

这些文件引用了 chatlog 的内部包，按照 Go 的规则只能放在 chatlog 源码中编译，因此无法单独发布为独立程序。

## 使用

把读取器路径设置到环境变量，然后用 `run_snapshot.py` 代替 `fetch_chatlog.py`，`--` 之后的参数与 `fetch_chatlog.py` 相同（不要传 `--server`）：

```bash
export CHATLOG_SNAPSHOT_READER=~/bin/chatlog-snapshot-reader
python3 skills/birding-chat-digest/scripts/run_snapshot.py -- \
  --groups groups.json --start 2026-11-15T21:00 --end 2026-11-16T21:00 --out ./export
```

| 参数 | 说明 |
|---|---|
| `--reader` | 读取器路径，默认取环境变量 `CHATLOG_SNAPSHOT_READER` |
| `--account` | 要复制的微信账号，默认取 chatlog 配置中的 `last_account` |
| `--chatlog-config` | chatlog 配置目录，默认取环境变量 `CHATLOG_DIR`，否则为 `~/.chatlog` |
| `--wait` | 等待读取器就绪的秒数，默认 180 |

退出码：获取完成时与 `fetch_chatlog.py` 相同（0 表示全部成功，1 表示部分群失败）；复制、启动或清理副本失败时为 3。

注意事项：

- 副本占用的磁盘空间与微信数据库大致相同，运行结束后自动删除。
- 复制过程中微信可能仍在写入，副本反映的是复制时刻的数据，复制之后的新消息不会包含在内。
- 正在运行的 chatlog 不受影响，两者可以同时运行。

## 测试

```bash
python3 -m unittest discover -s tests
CHATLOG_SNAPSHOT_READER=~/bin/chatlog-snapshot-reader python3 -m unittest discover -s tests
```

第二条命令会额外运行一项集成测试：用编译好的读取器打开一个临时生成的数据库副本，确认能读到 WAL 中已提交的数据，并确认结束后副本被删除、原文件未被修改。

## Windows

本仓库未在 Windows 上实现或测试副本模式。如需在 Windows 上使用，需要自行调整以下几处：

- `run_snapshot.py` 使用 POSIX 进程组（`start_new_session`、`os.killpg`）启动和终止读取器，Windows 上需改用其他方式终止子进程；
- 脚本开头的平台检查会拒绝在 Windows 上运行；
- 读取器需按 chatlog 的 Windows 编译要求（MinGW-w64）编译；
- chatlog 配置目录和微信数据目录的位置与 macOS 不同，需通过 `--chatlog-config` 指定。
