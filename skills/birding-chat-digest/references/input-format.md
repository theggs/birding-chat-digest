# 输入格式（fetch_chatlog.py 的产物）

`scripts/fetch_chatlog.py` 在输出目录写入一个 `index.json` 和每群一个 JSON 文件。总结时先读 `index.json`，再逐个读群文件。

## index.json

```json
{
  "date": "2026-11-16",
  "window_start": "2026-11-15T21:00:00+08:00",
  "window_end": "2026-11-16T21:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "groups": [
    {
      "name": "示例观鸟群",
      "file": "12345678.json",
      "fetched": 904,
      "source_coverage": [
        { "date": "2026-11-15", "total_count": 310, "fetched": 310 },
        { "date": "2026-11-16", "total_count": 1303, "fetched": 1303 }
      ],
      "naming_coverage": { "policy": "group_nickname_then_wechat_nickname", "unresolved_senders": 0 },
      "type_counts": { "text": 501, "share": 236, "image": 118, "sticker": 43, "system": 3, "video": 3 }
    },
    { "name": "示例综合群", "error": "FetchError: /api/v1/history failed after 5 attempts (TimeoutError)" }
  ],
  "total_messages": 904,
  "failures": 1
}
```

| 字段 | 用途 |
|---|---|
| `date` | 区间结束所在的日期，作为报告日期 |
| `window_start` / `window_end` | 统计区间，含起点、不含终点 |
| `fetched` | 区间内的消息数（已去重） |
| `source_coverage` | 区间涉及的每个自然日：chatlog 报告的当天总数和实际取到的条数。**两者不一致就是取数不完整**，要在数据覆盖里写明日期和差额 |
| `naming_coverage.unresolved_senders` | 没取到群昵称也没取到微信昵称的发言者数 |
| `type_counts` | 各类消息数，用来写数据覆盖里的图片、表情、系统消息数量 |
| `error` | 该群取数失败的原因；有这个字段的群没有消息文件 |
| `failures` | 失败的群数；大于 0 时报告末尾必须列出 |

## 群消息文件

顶层字段与 `index.json` 中该群的条目相同，另有 `messages` 数组，按时间排序。每条消息是 chatlog `/api/v1/history` 的原样输出，脚本只改了发言者名字。总结时用到的字段：

| 字段 | 说明 |
|---|---|
| `time` / `timestamp` | 发送时间 |
| `sender` | 发言者名字，已按“群昵称 → 微信昵称”替换；取不到时为空字符串 |
| `sender_name_source` | `group_nickname`、`wechat_nickname`、`unavailable`、`system` |
| `type` | `text`、`share`（链接/文章/视频号/小程序）、`image`、`sticker`、`video`、`voice`、`system` 等 |
| `content` | 文本内容；分享类消息是可见的标题和说明 |
| `contents` | 结构化内容，如引用回复（被引用者名字同样已替换）、分享链接的标题和描述 |

chatlog 默认用你通讯录里的备注名作为 `sender`。脚本已改为公开昵称，**不要从 `contents` 里的其他字段或旧报告把备注名补回来**。
