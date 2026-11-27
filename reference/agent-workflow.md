# 基础技能接入

写稿规则使用用户指定的已安装 `westcott-efficient-workflow` 2.3.2。仓库内 [base-workflow](base-workflow/SKILL.md) 是原样的运行快照，云端通过随仓库分发的 `manifest.json` 校验它。正式规则更新从已安装技能发起，按其中的[维护流程](base-workflow/references/maintenance.md)同步。按当前任务加载实际相关模块。项目 MCP 使用自己的状态记录，基础技能的 Python 记忆文件不自动导入项目状态。

- 写稿前按技能入口选择模块，完整场次按技能运行 `daily_prep.py`，续写核对 Google Docs 当天正文及停点。晚间先读前两晚的采用内容，再按[晚间入口](base-workflow/references/evening.md)执行。
- 含现实财经内容时，依照[助理规则](base-workflow/references/assistant.md)核实原文和时点。资讯批次可用 `validate_news_broadcast`，传入实际日期、场次、播报截点及 `require_source_evidence: true`。实际证据、标记关系与图片许可由当前会话核查。
- 整场或用户指定的审稿交 Haiku 的 `westcott-reviewer`。该 Agent 通过只读的 `review_candidate_draft` 运行同一技能的 `review_draft.py`，再做人工口吻核对。审稿工具返回正文和规则清单哈希，稿件变化后重新核对。执行机需有 Python 3.9+。若默认的 `python` 或 `python3` 不可用，在 MCP 宿主配置 `WESTCOTT_PYTHON` 指定实际可执行文件。
- 复盘按[维护规则](base-workflow/references/maintenance.md)只读当天页签，运行 `review_draft.py`，有草稿时运行 `adoption_diff.py`。任何状态写入或归档均使用本项目[执行契约](instruction-execution.md)。

## 历史检索与候选稿交接

- `search_docs_history` 读执行机私有索引。按 `date` 查日期，按 `query` 查原话或课题，也可以加 `before` 和 `limit: 2` 找较早的两份相关记录。结果附日期、原页签、正文片段、修订和缓存时间。需要续写当天或引用正式内容时，回到原页签核对正文。晚课采用稿按技能使用 `recent_adopted.py` 读取前两晚。
- `get_daily_run_status` 按日期查看新闻候选流程，显示稿件版本、同版 Codex 复核和 Google Docs 保存回执。 `needs_evidence` 时返回具体缺项。 `ready_for_human_review` 表示可打开该版审阅。用户的采用、整课完成和实际发送仍由现有记录处理。
- 自动运行读取 `automation/daily.env`。订阅模式使用执行用户已有的 Claude Code 登录。打开 `WESTCOTT_CODEX_REVIEW` 后，用同一执行用户的 Codex CLI 登录复核，生成 `codex-review.json`。回执绑定稿件和研究包的 SHA-256。资料不符或 Codex 不可用时，保留候选稿。发布仍要求既有的独立事实核验和绑定同版及目标的人工批准。参见 [Google Docs 保存流程](../docs/google-docs-publisher.md)。
- 设置 `WESTCOTT_HISTORY_DOC_ID` 和执行用户的 Google ADC 只读权限后，每次调度先读取历史文档并刷新日期索引。也可运行 `node tools/docs-history.mjs update 授权导出.json .westcott-state/history-index.json` 加载实际授权导出。索引存有历史正文，放在私有状态目录。MCP 和调度器的 `WESTCOTT_STATE_DIR` 指向同一个持久目录，Android 或 iOS 通过既有云端会话查询。目标环境应在电脑离线时实测。
