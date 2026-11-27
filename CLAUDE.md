# ELLERY WESTCOTT · Claude Code

@AGENTS.md

项目级 `.mcp.json` 提供 `westcott-state`，项目级 `.claude/settings.json` 注册校验与状态写入确认 Hook。首次在云端或桌面打开时，先核对实际 MCP 连接和当前分支。调用 `westcott-reviewer` 按项目 Agent 定义执行。云端 Google 文档访问按已授权的连接器读取指定页签；需要从已批准的文本导出定位日期时，用 `tools/read-doc-day.mjs`。

查历史可用 `search_docs_history` 定位日期、关键词和原始页签。续写和采用以原页签最新正文为准。查定时新闻候选的进度用 `get_daily_run_status`，读出版本、证据复核和文档保存状态。运行设置见[历史检索与候选稿交接](reference/agent-workflow.md#历史检索与候选稿交接)。
