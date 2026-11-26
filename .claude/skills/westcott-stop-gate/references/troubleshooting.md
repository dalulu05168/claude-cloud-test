# Stop hook 诊断

首次格式问题会返回 `decision: "block"`，让 Claude 修改并复检一次。复检仍失败时返回 `continue: false` 和 `stopReason`，让会话停止并显示原因。规则拦截和脚本故障可从日志区分。

## 先看 JSONL 状态

```powershell
Get-Content -LiteralPath .westcott-state/logs/stop-gate.jsonl -Tail 8 |
    ForEach-Object { $_ | ConvertFrom-Json } |
    Select-Object timestamp, status, error_type, issue_counts, prompt_id
```

- `status: "blocked"` 且 `issue_counts` 非空：validator 正常运行并识别出规则问题。查看每条记录的 `issues[].type` 与 `line`，判断是输出一直未修正，还是规则误拦。
- `status: "error"`：脚本或 validator 异常。`error_type` 通常是 `invalid_hook_json`、`validator_modules_missing` 或 `stop_gate_runtime_error`。
- 没有新记录：命令可能没有启动、超时，或 JSONL 写入本身失败。使用 Claude Code 的调试日志检查 hook 命令、退出状态和 stderr。

可按具体 prompt 筛选记录；session/prompt ID 属于元数据，调试完成后不要把含 ID 的日志公开：

```bash
jq -c 'select(.prompt_id == "目标prompt-id") | {timestamp, status, error_type, issues}' \
  .westcott-state/logs/stop-gate.jsonl
```

## 捕获实际 validator 异常

常规 JSONL 为隐私保护只保存异常类别 `error_type`，不保存异常消息、stack、正文或匹配词。脚本会把异常消息写入 stderr；设置 `WESTCOTT_STOP_GATE_DEBUG=1` 会额外输出堆栈与每个 validator 搜索目录的导入错误。

在项目根目录可用一个最小 Hook 输入复现。下面的 Bash 命令把诊断输出留在本机临时文件。

```bash
printf '%s' '{"session_id":"diag","prompt_id":"validator-diag","last_assistant_message":"diagnostic"}' \
  | env WESTCOTT_VALIDATORS_DIR="tools/custom-validators" \
        WESTCOTT_STOP_GATE_LOG="/tmp/stop-gate-diagnostic.jsonl" \
        WESTCOTT_STOP_GATE_DEBUG=1 \
        node tools/stop-gate.mjs \
        >/tmp/stop-gate-stdout.txt 2>/tmp/stop-gate-stderr.txt
status=$?
printf 'exit=%s\n' "$status"
cat /tmp/stop-gate-stderr.txt
cat /tmp/stop-gate-stdout.txt
cat /tmp/stop-gate-diagnostic.jsonl
```

脚本首次遇到运行异常会返回 exit 2 并记下 `status: "error"`。常规校验问题以 JSON 回应，日志状态为 `blocked`。若主动修订后仍为异常或未通过，脚本返回可见的停止理由。

在项目终端运行 `node tools/verify-hooks.mjs` 验证实际声明的命令。需要 Claude Code 运行日志时，可在 CLI 用 `claude --debug` 复现并通过 `/hooks` 查看配置来源。Hook 命令本身找不到、到时被终止或没有取得校验结果时，先修复命令环境。Claude Code 对 Stop 不应用 `onFailure`，所以此时重点检查 `node` 可执行文件、脚本路径和运行日志。项目的 PreToolUse 使用 `onFailure: "block"`，由 Claude Code 2.1.295+ 执行。[Hook 启动与超时行为](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails)

调试 stderr 和堆栈可能包含本机路径或自定义 validator 的错误上下文。只在隔离的复现会话打开 debug，诊断完后关闭该环境变量，并勿上传未审查的临时日志。
