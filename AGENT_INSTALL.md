# birding-chat-digest 安装指南（供 AI Agent 执行）

你正在为用户安装 `birding-chat-digest` skill。按下面的步骤执行，每一步确认结果后再进行下一步。遇到需要用户决定的事项时停下来询问，不要猜测。

命令以 macOS / Linux 为例；在 Windows 上使用等效的 PowerShell 命令。

## 1. 检查依赖

```bash
git --version
python3 -c "import sys; assert sys.version_info >= (3, 9), sys.version; print(sys.version)"
```

缺少 git 或 Python 3.9 以上版本时，告诉用户需要先安装，然后停止。

在 Windows 上，Python 不带时区数据，还需检查并安装 tzdata：

```powershell
python -c "from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); print('tz ok')"
# 上一条报 ZoneInfoNotFoundError 时执行：
python -m pip install tzdata
```

## 2. 确定安装目录

根据你自己是哪个 Agent 工具，选择 skills 目录：

| Agent 工具 | 用户级目录 | 项目级目录 |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `<项目>/.claude/skills/` |
| Codex 及其他支持 Agent Skills 的工具 | 以该工具文档说明的目录为准，常见为 `~/.agents/skills/` | 以该工具文档为准 |

- 默认安装到用户级目录。用户要求只在某个项目中使用时，安装到项目级目录。
- 无法确定自己所用工具的 skills 目录时，询问用户。
- 目标位置已存在 `birding-chat-digest` 目录时，询问用户是否覆盖更新；未经同意不要覆盖。

## 3. 获取代码并复制 skill

```bash
TMP=$(mktemp -d)
git clone --depth 1 https://github.com/theggs/birding-chat-digest.git "$TMP/birding-chat-digest"
mkdir -p <skills 目录>
cp -R "$TMP/birding-chat-digest/skills/birding-chat-digest" <skills 目录>/
test -f <skills 目录>/birding-chat-digest/SKILL.md && echo installed
```

只复制 `skills/birding-chat-digest` 这个目录，不要把整个仓库放进 skills 目录。

## 4. 运行测试

```bash
cd "$TMP/birding-chat-digest" && python3 -m unittest discover -s tests
```

测试使用模拟服务，不需要 chatlog，也不会读取任何聊天数据。测试失败时把输出原样报告给用户，不要自行修改代码。

## 5. 检查 chatlog

本项目不包含 chatlog，你不负责安装或配置它。只检查它是否在运行：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -m 5 http://127.0.0.1:5030/
```

- 输出任意 HTTP 状态码（如 `200`、`404`）：chatlog 正在运行。
- 输出 `000`：连接失败。告诉用户需要先启动 chatlog；如果用户的 chatlog 使用其他地址，记下该地址，之后运行取数脚本时用 `--server` 指定。

chatlog 未运行不影响 skill 安装，继续下一步。

## 6. 准备群列表

询问用户要把配置和日报放在哪个工作目录（建议 `~/birding-digest/`），然后：

```bash
mkdir -p <工作目录>
cp "$TMP/birding-chat-digest/examples/groups.example.json" <工作目录>/groups.json
```

请用户提供要总结的群名称和群 ID（形如 `12345678901@chatroom`），填入 `groups.json`。用户暂时无法提供时，保留示例内容并提醒用户之后修改。不要编造群 ID。

如果用户需要每天定时生成日报，把 `examples/scheduled-task-prompt.md` 也复制到工作目录，并告诉用户按其中说明创建定时任务。

## 7. 清理并报告

```bash
rm -rf "$TMP"
```

向用户报告：

- skill 安装位置；
- 测试结果；
- chatlog 是否在运行（以及使用的地址）；
- `groups.json` 的位置，以及是否已填入真实的群；
- 下一步可以直接使用的指令，例如：
  `使用 birding-chat-digest skill 总结 <工作目录>/groups.json 中的群聊，统计区间为 <起点> 至 <终点>，总结风格为简报。`

部分 Agent 工具需要重新启动或新开会话后才能识别新安装的 skill，如有需要一并提醒用户。
