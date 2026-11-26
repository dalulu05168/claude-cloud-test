---
name: westcott-stop-gate
description: 编写 Westcott 每日中文财经资讯与对话内容，并配置、运行或排查其 Codex Stop gate。适用于新闻格式/配图、David 发言、WhatsApp 群聊口吻、结构化日志和错误分析。
---

# Westcott Daily Briefing and Stop Gate

本 Skill 兼顾内容生产指导与确定性校验：先按项目格式起草每日内容，再用已注册的 Codex Stop hook 检查；也可用于安装、测试、日志分析和排错。不要把 Skill 本身误认为 Hook 已自动启用。

## 日常内容工作流

收到每日资讯或群聊内容请求时：

1. 确定用户要求的日期、时区、市场范围和内容段数。若缺少会影响事实准确性的来源或市场范围，先询问或明确说明当前覆盖范围。只使用用户提供或能够核实的来源；不得编造新闻、引语、配图或市场影响。
2. 每条新闻使用 `===== 资讯助理 =====` 标记，按三段组织：第一段为“国旗 国家财经新闻｜具体事件标题”；第二段写可核实事实；第三段写有条件、不过度确定的市场影响或观察。新闻正文后另起一行 `配图：https://...`，使用 HTTP(S) 直链，不使用 Markdown 图片语法。国旗、国家、标题和配图应对应同一条已核实事件。
3. David 发言使用 `===== David先生 =====` 标记，正文非空，段落按本场教学环节。WhatsApp 成员发言采用 `数字+性别+冒号` 角色格式，使用简洁自然中文，避开项目已列出的套话、股票黑话和收益口号。
4. 保留来源或核查记录供用户复核；不要把来源链接误当作配图直链。Gate 只检查配图行/URL 形状，不会访问图片或证明图片与新闻相符。
5. Stop gate 返回阻断后，保留交付范围并按具体原因修正，再检查一次。复检仍失败时会显示停止原因，以便排查。

该 Skill 按请求生成内容，不会自行在每天固定时间启动。若“每日节奏”指自动定时推送，而非每日内容结构，需要另行配置调度器。

## 安装并启用 Stop hook

Codex Skill 是一个带 `SKILL.md` 的目录。将本包解压出的 `westcott-stop-gate/` 放到个人路径 `~/.Codex/skills/`（所有项目可用），或项目路径 `.Codex/skills/`（仅该项目可用）。若要同时启用本项目的 Stop gate，建议项目级安装；个人级安装只让 Skill 在各项目可调用，Hook 还需在用户设置中另行注册正确的脚本绝对路径。重新打开会话后可输入 `/westcott-stop-gate`，也可通过 `description` 让 Codex 自动判断何时使用。[项目技能路径与调用方式](https://code.claude.com/docs/en/skills)

完整 Westcott 项目已经在 `.Codex/settings.json` 注册 Stop hook，由 `tools/stop-gate.mjs` 转入本 Skill。单独把 Skill 安装到另一项目的 `.Codex/skills/` 时，再合并 [Hook 模板](templates/stop-hook-settings.json) 并保留原有 Hooks。模板使用 `command` 和 `args` 直接启动 Node。个人级安装需要将模板里的脚本位置改为实际绝对路径。[Hook 命令配置](https://code.claude.com/docs/en/hooks#command-hook-fields)

Gate 优先加载 `WESTCOTT_VALIDATORS_DIR` 指定的目录，再尝试项目 `tools/`，最后尝试本 Skill 的 `scripts/vendor/`。见 [自定义校验接口](references/custom-validator-contract.md) 和 [Stop 排查](references/troubleshooting.md)。

## 测试与日志分析

从仓库根目录运行：

```powershell
node --test .Codex/skills/westcott-stop-gate/scripts/stop-gate.test.mjs .Codex/skills/westcott-stop-gate/scripts/analyze-stop-gate.test.mjs
node .Codex/skills/westcott-stop-gate/scripts/analyze-stop-gate.mjs --since 2026-11-01 --top 5
node .Codex/skills/westcott-stop-gate/scripts/analyze-stop-gate.mjs .westcott-state/logs/stop-gate.jsonl --json
```

分析器统计通过/拦截/错误/跳过状态、拦截率、问题类型频率、UTC 每日趋势、重复被拦截 prompt 数和损坏 JSONL 行号。报告不会输出 session ID、prompt ID 或原始日志行。

常规 JSONL 只记录 `error_type`，不写异常原文、stack、正文、配图 URL 或敏感词。需要定位自定义 validator 异常时，设置 `WESTCOTT_STOP_GATE_DEBUG=1` 并按排错参考将 stderr 留在本机受限临时文件中。
