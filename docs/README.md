# Westcott 项目文档

- [使用手册和测试示例](../README.md)
- [Claude Desktop 安装和诊断](../桌面版安装与运行说明.md)
- [开源工具接入和并行 MCP 烟测](开源增强与并发运行说明.md)

## 可选部署

日常总导演使用项目的 Claude Code 本地会话。需要在独立主机运行每日草稿、事实校验或每周反馈时，配置 [每日环境示例](../automation/daily.env.example) 或 [反馈环境示例](../automation/feedback.env.example)。脚本入口为 [每日任务](../automation/run-daily.sh) 和 [每周反馈](../automation/run-weekly-feedback.sh)，配置项以示例文件为准。每日环境默认关闭自动发布，部署 Google Docs 发布须另行配置目标和运行身份。

- [Google Docs 发布配置](google-docs-publisher.md)
- [Google API 参考](google-api-reference-notes.md)
- [多 Agent 协议](multi-agent-protocol.schema.json)和[示例](multi-agent-protocol.examples.json)
- [每周反馈流程](feedback-upgrade-workflow.md)、[反馈候选格式](feedback-proposal.schema.json)和[测试清单](feedback-test-inventory.json)
