# 自定义 validator 接口

实现应使用 Node.js ES modules（`.mjs`），并保持同步执行。Stop gate 当前不会 `await` validator handler。

## `speech-format.mjs`

必须导出：

```js
export function getBlocks(body) { /* 返回解析后的角色块数组 */ }
export function createSpeechFormatTool({ text, schema }) {
  return {
    definitions: [],
    handlers: {
      validate_speech_format({ body, mode }) {
        return { passed: true, issues: [] };
      },
    },
  };
}
```

`getBlocks(body)` 的每个元素至少应含 `role`、`label`、`line`、`lines`：`role` 使用 `assistant`、`teacher`、`member` 或 `other`；`label` 是原始角色标记；`line` 是 1 起始的整数；`lines` 是该角色标记之后的行字符串数组。

`createSpeechFormatTool` 必须返回 `handlers.validate_speech_format` 函数。Gate 会分别用 `{ body, mode: 'news' }` 和 `{ body, mode: 'teacher' }` 调用它。结果应为 `{ passed: boolean, issues: Array<{ line?: number, message: string }> }`；可额外返回 `news_count`、`teacher_count`。`issues[].message` 用于给 Claude 提供修正原因，`line` 用于定位问题。

## `whatsapp-voice.mjs`

必须导出：

```js
export function createWhatsAppVoiceTool({ text, schema }) {
  return {
    definitions: [],
    handlers: {
      validate_whatsapp_voice({ body }) {
        return { passed: true, issues: [] };
      },
    },
  };
}
```

结果可返回 `{ passed, checked_messages, member_messages, needs_review, issues }`。每个 issue 至少含 `{ severity, type, message }`，可选 `line`、`role`、`term`。只有 `severity: 'block'` 会进入 Stop gate 阻断；`review` 等其他级别只供 validator 自己或后续流程参考。

## 兼容性与错误处理

工厂参数 `text` 与 `schema` 是轻量工具定义 helper，供模块构造工具定义使用；运行时实际调用的是 `handlers` 中的函数。不要返回 Promise，不要把完整正文、图片 URL 或命中词写入日志，也不要返回原始文章文本作为 issue 字段。

所有导入路径都必须由 ESM 相对路径或 Node 可解析的包路径正确解析。若自定义模块或其依赖抛错，Gate 会写入 `status: "error"` 与 `error_type`，并以退出码 2 阻止 Stop。JSONL 不保留异常原文；用 `WESTCOTT_STOP_GATE_DEBUG=1` 或直接复现命令读取 stderr/stack，避免把用户内容或异常上下文永久写入常规分析日志。
