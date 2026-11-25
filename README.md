# birding-chat-digest · 观鸟群聊总结

本项目是一个供 AI Agent 使用的 skill，用于将观鸟群在一段时间内的聊天消息整理为日报，使未阅读群消息的成员能在几分钟内了解期间的鸟讯、活动召集、鸟类知识、观鸟经验及其他主要讨论。日报涵盖所有主要内容，但不逐条复述消息。

其中的规则来自多个观鸟群每日两次的日报实践，并针对实际出现的问题逐条修订，例如：消息过多时被抽样处理、获取消息失败被写成“本期无消息”、群成员的推测被写成确定的鸟讯、使用本人通讯录中的备注名称呼群成员。

默认的“分类简报”风格把内容分成鸟讯、讨论和活动召集三类：鸟讯按“地点 → 鸟种”分条，讨论按论点分条，避免逐人复述聊天。需要更稳定的质量时，可以启用审稿：由另一个 Agent 对照原始消息审稿，写作者确认后再发布（见 `references/review.md`）。

## 前提条件

使用前需要在本机运行 chatlog。本项目不包含 chatlog。

运行环境需要 Python 3.9 或以上版本。Windows 用户还需安装时区数据包：`python -m pip install tzdata`。

skill 附带的脚本 `scripts/fetch_chatlog.py` 从 chatlog 获取指定时间区间内的全部消息，并将发言者名称替换为群昵称或微信昵称，不使用本人通讯录中的备注名。

## 读取超时与副本模式

微信频繁写入数据库时，直接通过 chatlog 读取聊天记录可能超时。此时可以使用副本模式：先复制一份数据库，再从副本读取。副本模式需要 chatlog 源码和 Go 编译环境，仅支持 macOS 和 Linux，详见 `chatlog-snapshot-reader/README.md`。

## 安装

### 由 AI Agent 安装

将以下指令发送给 Agent（例如 Claude Code 或 Codex）。Agent 会按照安装指南完成安装、运行测试、检查 chatlog 是否在运行，并在需要时询问安装位置和群列表：

```text
请阅读 https://raw.githubusercontent.com/theggs/birding-chat-digest/main/AGENT_INSTALL.md ，按照其中的步骤安装 birding-chat-digest skill。
```

### 手动安装

**Claude Code**

```bash
git clone https://github.com/theggs/birding-chat-digest.git
cp -r birding-chat-digest/skills/birding-chat-digest ~/.claude/skills/
```

**Codex 及其他支持 Agent Skills 的工具**：将 `skills/birding-chat-digest` 复制到该工具的 skills 目录，例如 `~/.agents/skills/`。

如需每天定时生成日报，请参考 `examples/scheduled-task-prompt.md` 创建定时任务。

## 使用方法

1. 将 `examples/groups.example.json` 复制为 `groups.json`，并填写需要总结的群名称和群 ID。
2. 向 Agent 发出如下指令：

```text
使用 birding-chat-digest skill 总结 groups.json 中的群聊，统计区间为 2026-11-15 21:00 至 2026-11-16 21:00，总结风格为分类简报。
```

Agent 会先运行消息获取脚本，再逐个群生成总结。

运行测试：`python3 -m unittest discover -s tests`

## 目录结构

```
AGENT_INSTALL.md                # 供 AI Agent 执行的安装指南
chatlog-snapshot-reader/        # 副本模式的读取器源码和编译脚本
skills/birding-chat-digest/
├── SKILL.md                    # 总结流程和通用写作规则
├── scripts/
│   ├── fetch_chatlog.py        # 从 chatlog 获取指定时间区间内的全部消息
│   └── run_snapshot.py         # 副本模式：复制数据库后再获取消息
└── references/
    ├── styles.md               # 总结风格：分类简报（默认）、简报、时间线回顾、详略回顾
    ├── review.md               # 可选的审稿流程和审稿清单
    ├── report-format.md        # 报告结构、数据覆盖情况说明、归档规则
    ├── preview-image.md        # 分享长图的内容筛选和渲染规范
    ├── input-format.md         # 消息获取脚本输出文件的格式
    └── example.md              # 示例报告（内容为虚构）
examples/
├── groups.example.json         # 群列表示例
└── scheduled-task-prompt.md    # 每日定时生成日报的提示词模板
tests/
├── test_fetch_chatlog.py       # 消息获取脚本的测试（使用模拟的 chatlog 服务）
└── test_run_snapshot.py        # 副本模式的测试
```

## 隐私说明

群聊内容属于全体群成员。对外分享报告前，请先征得群成员同意；未获同意时，仅在群内分享。

- 报告中只使用群昵称或微信昵称，不使用本人通讯录中的备注名。
- 请勿将原始聊天记录、脚本输出的消息文件、`groups.json` 或密钥提交到代码仓库，或发布到公开渠道。
- 生成分享长图时，会按规则删除争执和人身攻击类内容；发布前仍建议人工检查。

## 许可证

MIT
