# 机器检查契约

脚本只读本地文件，不联网、不写外部应用。格式通过不能替代事实核验或语义审稿。调用路径以技能目录为基准；从其他目录运行时使用脚本绝对路径。

## 最小读取
`python scripts/plan_context.py --task EDIT --ids 14`

任务取 EDIT / CONTINUE / SCENE / ACTIVITY / LESSON / NEWS / QA。仅涉及主持或教师时加 `--roles assistant,teacher`；`--ids` 只填本次角色，不列全员。未知任务/编号返回非零退出码，不能默默使用默认流程。

## 每日稿审稿（2.2.9起）
`python -I scripts/review_draft.py 稿件.txt --date 2026-11-12`，或直接审文档：`--docjson <read_doc保存的文件> --tab 2026-11-12`；加 `--times times.json`（{"标题关键词":"HH:MM"}）检查新闻是否按发布时间、不往回跳。

查成员国内股民黑话、渲染性用词、编造持仓、先生说“答案是”、盘中具体时刻、中国式红绿说法、学习群空表态、各场条数、晚间收尾三句、周二四晚间作业。退出码0=PASS、1=FAIL、2=输入错误；“请确认”项不致失败但要看。交稿前、用户说“审稿”时跑一次；用户级审稿代理 westcott-reviewer 会自动调用它并补人工口吻审查。已发内容只报告不改。

## 结构检查
`python scripts/check_scene.py candidate.txt --contract task.json`

先根据用户请求和有效前文形成契约，再写稿；不能为让稿件通过而改验收条件。实例：
```json
{
  "mode": "scene",
  "expected_count": 3,
  "allowed_ids": ["02", "14"],
  "role_order": ["02", "14", "ASSISTANT"],
  "forbid_close": true,
  "forbidden_literals": ["答案是", "积分"],
  "protected_literals": [],
  "simulation": true
}
```

- `expected_count` 是此次交付条数。补齐场景可改用 `existing_count:21,target_count:35`；两者同时给出时必须一致。已有条数需从有效稿核对，脚本不验证自报数字。
- `role_order` 为每条的编号或 MEMBER / ASSISTANT / TEACHER，长度必须与结果一致。它检查角色顺序，不能证明各条真的完成“出题→讲解”等语义步骤，后者需人工检查。
- `allowed_ids` 默认01—52；scene模式显式指定53—65也会返回INPUT_ERROR，`role_order`同样受限。`mode:edit` 必须提供本次明确编辑的编号；允许用户点名的53—65。其他人的出现会失败。
- `original_text` 仅用于edit，填写指定片段原文，可检查角色顺序与阿拉伯数字文字是否保留。不能自动检查中文数字、所有股票代码或意图；重要代码/词语加入 `protected_literals`，人工再核原意。
- `simulation` 默认true，首行须为 `【教学模拟｜非真实成员发言或交易记录】`。仅无编号人物的真实资讯/教学正文可设false，仍需自行核事实。
- `forbid_close` 是常见闭群措辞的启发式检查，可能误报引用或漏掉同义改写；`forbidden_literals` 可补本次不能出现的答案和文字。两者均不能替代语义检查。
- 错误返回FAIL及退出码1；坏输入/未知契约字段返回INPUT_ERROR及退出码2。调用方只有退出码0且status=PASS才能说“结构检查通过”。脚本输出的not_verified始终保留。
- 不用逐句运行；标签稿完成后检查一次，修改相关错误后复查，最多两轮修复。普通无标签短句不运行本脚本，直接人工核对。

日常只读取当前用到的模块。完整回归仅在技能/脚本修改时运行 `python -m unittest discover -s tests -v`；不要每次写一句话都执行全套测试。

## 2.3.0晚间覆盖检查
晚间稿明确加--scope evening，无开场片段也按晚间检查；片段另加--fragment避免整场配额误报。周二四显式作业与常见变相提交改为FAIL，不再只是提醒；“今晚不布置作业”等否定表达不单凭关键词判错。启发式仍可能漏检或误报，人工复核保留。无日期时周二四规则标待核，不能称该项通过。

## 2.3.1成员口吻检查
review_draft.py现在认“12男：台词”同行写法和“46男 Andrei：”带名字的写法。新增判FAIL：更多国内黑话、中国成语典故、成员称先生“老师”、先生或助理说“学员/同学”、@编号、成员说出后台安排、一场三句以上夸先生。新增提醒：隐蔽的持仓盈亏说法、中国特有生活背景、先生或助理用股民口头说法、成员几乎一人一句。台词长度只在超过60字时提醒，不按固定字数判错；长短和表情是否像真实群聊仍靠人工或审稿代理判断。词表是举例不是穷尽，没被查出不等于口吻合格。
