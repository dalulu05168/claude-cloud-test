# 晚间课程入口 版本2.3.0

本入口按用户要求用新版晚间规则覆盖2.2.9晚间部分。先读[共同约定](evening/contract.md)，再只读本次相关模块。

| 当前任务 | 读取 |
|---|---|
| 排课、恢复课程位置、理念路线、整课统筹 | [课程总控](evening/control.md) |
| 图表、Logo、DOCX | [课件图表](evening/courseware.md) |
| 讲解、现实案例与理念课 | [先生授课](evening/teacher.md) |
| 模拟成员提问、误解及承接 | [成员互动](evening/interaction.md)及原人物模块 |
| 检查、修订、图文验收 | [质量审校](evening/review.md) |

主课20:00，继承Europe/Bucharest时区。五个职责是本包内部模块，不是五个需要自动调用的外部技能。

## 优先关系
本次用户范围与停点→本晚间入口及共同约定→相关新版模块→原模块中无冲突的身份、口吻、事实边界、记忆与结构检查。平台指令始终优先。白天及周日行情复盘不套用晚间课程路线。

## 本地执行
从实际技能根目录运行：
`python scripts/plan_context.py --task LESSON --evening`
编号对话增加本次所需--ids；只选相关编号，不加载全部档案。
完成晚间稿后：
`python scripts/review_draft.py 稿件.txt --scope evening --date YYYY-MM-DD --json`
片段任务加--fragment。编号稿另按contracts.md运行check_scene.py。

## 图表执行
正式Logo已内置；制作前看[品牌资产信息](../assets/logo-metadata.json)。本包提供[模拟图渲染器](../scripts/render_evening_chart.py)，使用Pillow和用户指定的中文字体，不联网、不生成真实行情。参数说明在脚本帮助及[课件模块](evening/courseware.md)。此工具只生成PNG，不代替DOCX制作与页面验收。

## 进度
有授权时沿用原项目memory/session存储，按[课程进度映射](evening/progress.md)保存；未提供历史时不能声称继续到某一实际课次。合并包测试样例不进入正式项目历史。

## 晚场写法要点（2026-11-25 对照用户采用稿整理）
写晚场前先运行 `python scripts/recent_adopted.py --docjson 文档json --tab 当天日期`，读前两晚已采用的开场、交棒、收尾，照它们的写法。用户采用并改稿后，运行 `python scripts/adoption_diff.py 草稿.txt --docjson 文档json --tab 当天日期` 对比差异，把确认的教训并入对应规则，同时删掉被取代的旧规则。
1. 19:30 开场题用生活场景类比（矿泉水、鸡蛋），不用图表术语。用户没给题目时先问，不自编。
2. 同一条声明（“数字只用于教学、不是推荐价位”“昨晚结论今晚继续有效”）一堂课最多说一次。
3. 收尾照前一晚已采用的格式：只讲图和核心问题、交给顾问Gabriela、一句“明晚见”。不把课件全部作业和下节预告搬进群里。
4. David 一个块只讲一张图或一个话题；回答成员问题和进入下一张图拆成两个块。
成员台词、交棒句的相关要求在 members.md（口头禅、引号、“有成员问”、交棒句不点课题）。
