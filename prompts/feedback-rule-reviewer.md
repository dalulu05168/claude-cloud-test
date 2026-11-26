# Westcott Rule Candidate Human Review Prompt

可将以下检查单作为 PR 审核模板。审批必须针对确切候选版本和 SHA-256，不接受“看起来可以”或模型代签。

> 我正在审核 Westcott 规则候选 `<candidate_id>`，候选文件 SHA-256 为 `<sha256>`，目标活动版本为 `<active_rules_version>`。请只依据候选 diff、确定性反馈汇总、证据 run_id、测试结果和当前规则文本作判断。
>
> 1. 每条问题是否达到规定的样本数和独立 run 数？证据是否真实存在于汇总 JSON？
> 2. 提案是否只解决被观测到的问题，没有扩大到不相关内容、权限或发布行为？
> 3. 规则文字是否无歧义且可由自动化测试判定？有没有新增应通过与应拦截样本？
> 4. golden set、负例、边界测试和现有 Stop gate/Validator 测试是否全部通过？有没有已知内容被错误拦截或错误放行？
> 5. 风险、适用范围、指标和回滚到的版本是否明确？
>
> 请最终只选择一项并记录理由：`APPROVE <candidate_id> <sha256>`、`REQUEST_CHANGES <candidate_id> <具体问题>` 或 `REJECT <candidate_id> <理由>`。只有被保护分支上的已授权人工批准才算批准。批准候选不是直接修改活动规则；发布仍需通过受保护部署流程。
