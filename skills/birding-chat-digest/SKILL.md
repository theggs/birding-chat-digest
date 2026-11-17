---
name: birding-chat-digest
description: Use when summarizing WeChat birdwatching group chats read through a local chatlog service into a digest for members who did not read the messages — 观鸟群聊总结、观鸟群日报、鸟讯汇总、群聊回顾. Also use when a scheduled task asks for a birding group report or a shareable preview image for a time window.
---

# 观鸟群聊总结

## 核心原则

读者是**没看群消息的鸟友**。目标是让他们几分钟内知道这段时间群里有什么鸟讯、活动、知识和经验，以及主要讨论的实质。

- **全文阅读，再取舍。** 不抽样；读完全部消息不等于要复述全部消息。
- **如实。** 区分实报、猜测与许愿；保留分歧、纠正和不确定性；不把未定讨论写成共识。
- **数据缺失要说出来。** 取数失败、条数不一致都写进报告，不能静默变成“0 条”或拿旧数据充数。

## 前提：chatlog

聊天记录来自 chatlog（读取微信 4.x 本地数据库并提供 HTTP API）。本 skill 不包含 chatlog，只通过它读取数据，不修改数据库。有两种取数方式，前提不同：

| 方式 | 脚本 | 前提 |
|---|---|---|
| 直接读取（默认） | `scripts/fetch_chatlog.py` | chatlog 正在运行（默认 `http://127.0.0.1:5030`） |
| 副本模式 | `scripts/run_snapshot.py` | 已编译副本读取器；chatlog 配置中已有账号和数据库密钥。chatlog 不需要在运行 |

用户明确要求副本模式，或直接读取超时时，用副本模式（见下文）。取数失败时直接报错，不要猜测或编造内容。

### 直接读取

用自带脚本取一个区间内全部消息（Python 3.9+，仅标准库）：

```bash
python3 scripts/fetch_chatlog.py --groups groups.json \
  --start 2026-11-15T21:00 --end 2026-11-16T21:00 --tz Asia/Shanghai --out ./export
```

- `groups.json`：`{"groups": [{"name": "示例观鸟群", "chat": "12345678@chatroom"}]}`；也可以用 `--chat ID=群名` 指定单个群。群 ID 在 chatlog 控制台的群聊列表里查。
- 整天用 `--date 2026-11-16` 代替 `--start/--end`。
- 退出码 0 表示全部成功，1 表示有群失败（仍会写出 index.json，失败原因在对应群的 `error` 字段里）。

产物格式见 [references/input-format.md](references/input-format.md)。

### 副本模式

微信频繁写入数据库时，直接读取可能超时，表现为群的 `error` 里出现 `TimeoutError` 或 `failed after 5 attempts`。此时，或用户明确要求时，如果用户已编译副本读取器（设置了环境变量 `CHATLOG_SNAPSHOT_READER`，或用户告知了读取器路径），改用 `scripts/run_snapshot.py` 重新获取，`--` 之后的参数与 `fetch_chatlog.py` 相同，不要传 `--server`：

```bash
python3 scripts/run_snapshot.py -- --groups groups.json \
  --start 2026-11-15T21:00 --end 2026-11-16T21:00 --tz Asia/Shanghai --out ./export
```

它把数据库复制到临时目录、启动只读副本的读取器、获取消息后关闭读取器并删除副本，只读取原数据库。退出码 3 表示副本本身失败（复制、启动或清理），按失败如实报告；此时输出目录中没有本次的 `index.json`，不要读取其中残留的旧文件。用户没有读取器时，告诉用户按 https://github.com/theggs/birding-chat-digest/blob/main/chatlog-snapshot-reader/README.md 编译（该说明不随 skill 安装），不要自行编译或改用其他方式绕过。

## 流程

1. **确定统计区间并取数。** 以 `index.json` 的 `window_start`/`window_end` 为准（含起点、不含终点），统一使用一个时区。只总结区间内的新消息；更早的消息只用来理解上下文，不计入条数，也不重复总结。
2. **核对覆盖。** 有 `error` 的群记为失败，在数据覆盖中写明错误，不写成 0 条。没有 `error` 时，检查 `source_coverage` 里每个源日期的 `total_count` 与 `fetched`：不一致时照常总结，并在数据覆盖中写明源日期和差额。
3. **逐群阅读。** 每个群独立总结，不把不同群混在一起。单个群超过约 6 万 tokens 时，只对这个群按时间切 2–3 段，段内总结后合并。
4. **选风格并撰写。** 三种风格见 [references/styles.md](references/styles.md)，没有指定时用“简报”。
5. **核对事实。** 输出前逐项对照原始消息核对鸟名、地点、时间、发言者和引用关系；不能确认的内容不写成确定表述。
6. **输出。** 结构见 [references/report-format.md](references/report-format.md)。需要分享长图时按 [references/preview-image.md](references/preview-image.md) 生成。

## 通用写作规则（所有风格适用）

| 项目 | 规则 |
|---|---|
| 主题标题 | 四级标题，每群从 1 编号：`#### 1. 主题要点（时间段）`；正文另起一段 |
| 鸟名 | 正文中每次出现都加粗：**遗鸥**、**红头长尾山雀** |
| 发言者 | 用「」括起，保持原样：「山雀」。观点、经验和鸟讯来源要写明是谁，不写“有人”“一位群友” |
| 昵称来源 | 优先群内昵称，其次平台昵称。**不用你自己通讯录里的备注名**——那是你的私人信息，不是对方公开的名字。取不到时标“昵称缺失”，不猜身份 |
| 时间 | 写具体钟点（“07:40 在东滩大堤”），不写“临近截止”之类流程用语 |
| 图片/表情/系统消息 | 只计数，数量放在“数据覆盖”，正文不单列，也不写“未做视觉理解”等处理说明 |
| 分享的文章/视频 | 必须读取卡片的标题和说明（`contents.title`、`contents.desc`），说明里常有鸟种、地点等关键信息；只根据这些可见内容和讨论归纳，不推断没读到的正文 |
| 争执 | 概括实质观点和分歧，不重播攻击、讥讽和升级过程 |
| 口吻 | 直接陈述事实和具体的不确定性；不加“仅为群友说法”“不保证仍在”等泛化免责声明 |

## 取舍

- 鸟讯、活动召集、鸟类知识、观鸟经验是重点，但**其他主要讨论同样要写**；不能因为不属于这四类就忽略。
- 篇幅按讨论的实质内容分配，不按聊天条数机械扩写，也不按“能否立即行动”决定详略。
- 可以省略：重复追问、点赞接话、纯表情互动、无新增信息的附和、上下文不足的孤立片段。
- 不逐条复述消息，不为每位参与者安排一句，不按发言顺序串联问答。

## 常见错误

| 错误 | 正确做法 |
|---|---|
| 消息太多就抽样或只读前半段 | 全量阅读；只有单群超大时按时间切块 |
| 取数失败的群写成“本期无消息” | 在数据覆盖中写明失败和错误 |
| “大家交流了拍摄经验” | 写出具体经验：谁、什么设备或方法、结论或分歧 |
| 群友猜测写成确定鸟讯 | 标明是猜测，或保留辨识上的分歧 |
| 用备注名称呼群友 | 群内昵称 → 平台昵称 → “昵称缺失” |
| 报告里出现原始消息全文、内部路径或密钥 | 报告只放整理后的内容 |

示例报告见 [references/example.md](references/example.md)（内容为虚构）。
