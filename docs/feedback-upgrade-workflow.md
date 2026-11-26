# 历史反馈评估与规则升级流程

## 原则与每周节奏

反馈用于发现系统性问题，不等于规则授权。每次每日运行只追加一条脱敏反馈记录；每周评估任务读取滚动 28 天数据，先由脚本统计，再由评估 Agent 解释证据。无论统计或模型结论如何，评估阶段都不能改动 `rules/active.json`、Agent 系统提示、Stop gate、Validator 阈值或权限。

默认启动门槛是至少 10 条反馈，覆盖至少 3 个不同 `run_id`。同一个问题代码至少出现 3 次且覆盖 3 个不同运行，才作为重复信号；维度均分低于 3.5/5 且该维度至少 10 条有效评分，也生成信号。阈值可以通过命令行参数调整，但须纳入评估报告。样本不足时输出 `insufficient_evidence`，不得让模型从少量个案推断普遍规则。

## 反馈记录格式

`feedback/inbox/feedback.jsonl` 为追加写入文件，每行一条记录。建议格式如下：

```json
{"feedback_id":"fb-2026-11-23-a1","run_id":"daily-2026-11-23","task_date":"2026-11-23","reviewer_type":"editor","ratings":{"factual_accuracy":4,"structure_compliance":3,"tone":4,"readability":3},"rework_required":true,"issue_codes":["SECTION_ORDER_DRIFT","SOURCE_REFERENCE_MISSING"],"note_sanitized":"第二节标题顺序不符合已批准模板。"}
```

评分维度固定为事实可靠性、结构遵守、语气和可读性，范围 1–5。只把可归因的错误放在 `issue_codes`；代码必须与已有 Validator、规则或审核分类表对应。自由文本 `note_sanitized` 是可选字段，进入模型上下文前必须去除姓名、邮箱、电话、账号和正文中不必要的敏感信息。统计脚本不将自由文本复制进汇总报告。

每日收集器只追加原始记录，不覆盖历史行。若同一运行有多个不同审核者，可分别记录，但须使用不同 `feedback_id`；评估样本数按反馈条目计，独立证据门槛按 `run_id` 计。

## 确定性汇总

运行：

```bash
python3 orchestration/feedback_eval.py \
  --input .westcott-state/feedback/inbox/feedback.jsonl \
  --output .westcott-state/feedback/evaluations/2026-W48-summary.json \
  --as-of 2026-11-23 --window-days 28 --min-samples 10 \
  --min-issue-count 3 --min-distinct-runs 3 \
  --active-rules-version 2026.11.1 \
  --active-rules-sha256 "$(sha256sum rules/active.json | awk '{print $1}')" \
  --evaluation-id weekly-2026-W48-2026-11-23
```

脚本校验 JSONL 字段、评分范围、日期、重复 ID 与问题代码格式，然后仅输出聚合均值、评分覆盖数、返工率、重复问题代码、有限的证据 `run_id` 和低分维度。输出的 `governance.active_rules_modified` 永远为 `false`。JSONL 格式错误时脚本以非零退出；自动化不得跳过错误行继续生成“看似完整”的报告。

如果 `decision=insufficient_evidence`，只归档报告并等待后续数据。如果 `decision=no_change`，记录没有达到触发门槛的结果。如果 `decision=review_required`，才允许将汇总报告送入评估 Agent。工作流应保存本次使用的 `ruleset_version`、Prompt 版本、汇总 JSON 的 SHA-256 和评估输出，便于复核。

自动调度入口是 [`../automation/run-weekly-feedback.sh`](../automation/run-weekly-feedback.sh)，配置模板为 [`../automation/feedback.env.example`](../automation/feedback.env.example)。可将其加入外部 scheduler 的每周一次作业，例如周一 09:00：

```cron
CRON_TZ=Asia/Shanghai
0 9 * * 1 /opt/westcott-agent/automation/run-weekly-feedback.sh >> /opt/westcott-agent/.westcott-state/logs/feedback-cron.log 2>&1
```

脚本会按 feedback 文件哈希、规则文件哈希、规则版本与阈值生成唯一 `evaluation_id`，生成摘要后只在 `review_required` 时调用部署专属 evaluator adapter。Adapter 必须把只读活动规则和测试索引作为输入，stdout 仅输出提案 JSON。脚本通过 Schema/证据验证后将提案写到 `rules/candidates/`，并标为 `pending_human_review`；不修改 `rules/active.json`。同一评估批次重跑时会重新验证已归档提案，不重复调用 evaluator。缺少 evaluator 授权或配置时脚本会失败，不会假装完成评估。

## 评估 Agent 的边界

使用 [`../prompts/feedback-evaluator.md`](../prompts/feedback-evaluator.md) 中的 System Prompt 与 User Prompt 模板。输入包括确定性汇总、只读的当前相关规则片段、既有测试目录索引，以及通过脱敏的少量示例。不得将全部历史正文、用户个人资料、API 凭据或无关文档发送给模型。将反馈文本和样例当作不可信数据，不执行其中看似指令的内容。

Agent 的输出必须是符合 `docs/feedback-proposal.schema.json` 的 JSON。每条提议必须关联汇总报告中的具体问题代码或低分信号，并列出证据 `run_id`、拟改前后内容、预期收益、误放行风险、新增正反例测试和回滚版本。模型不能创造不存在的证据；若不确定，选择 `insufficient_evidence` 或 `no_change`。

## 自动化候选与发布门槛

自动化可把合格提议写入 `rules/candidates/<candidate_id>/proposal.json`，但不得改动活动版本。候选进入人工审核前，应完成以下检查：

1. 用 Schema 校验模型输出，并验证所有证据代码和 `run_id` 都能在确定性报告中找到。
2. 对候选规则执行现有单元测试、Stop gate 测试、历史 golden set、反例和边界测试。新规则必须增加至少一个应通过与一个应拦截的测试样本。
3. 比较候选与当前规则的 diff，检查对相邻规则、硬拦截和隐私/安全边界的影响。不能只看模型的自然语言解释。
4. 审核人使用 [`../prompts/feedback-rule-reviewer.md`](../prompts/feedback-rule-reviewer.md) 逐条批准、要求修改或拒绝。审批记录应包含审核者、时间、候选版本、测试流水线链接和批准的精确 SHA-256。

只有仓库保护分支上的人工批准提交通过全部测试后，发布流水线才可将不可变版本登记为 active。active 文件只指向版本，不把候选内容直接复制覆盖历史版本。下一次每日运行的 manifest 记录该规则版本；运行期间不允许热切换。

发布后观察至少 7 个每日运行。若同一硬性违规漏检、误拦截率明显上升，或事实追溯指标下降，值班人将 active 指针回滚到前一个已批准版本，并保留失败版本及日志用于诊断。回滚不删除候选，也不重写历史 manifest。

## 状态与责任

```text
feedback_received
  → deterministic_summary
  → insufficient_evidence | no_change | review_required
  → model_proposal (仅 review_required)
  → schema_and_evidence_check
  → candidate_tests
  → human_approval | rejected
  → immutable_release
  → 7-run_observation
  → keep | rollback
```

定时器只负责启动汇总/评估任务；模型负责解释和提出；测试系统负责验证；指定的人类负责人负责批准；发布器只部署获批的不可变版本。任何单个 Agent 都不同时拥有“提出规则、批准规则、发布规则”三项权限。
