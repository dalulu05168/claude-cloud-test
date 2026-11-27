# Google Docs 自动同步脚本

`automation/sync_google_doc.py` 实现 `run-daily.sh` 需要的 publisher CLI。脚本读取已验证的 Markdown 草稿和 manifest，用 `python-docx` 生成带标题层级、项目符号、编号、粗体/斜体和代码段的 DOCX，再通过 Drive API 上传并转换为原生 Google Docs 文件。Drive API 支持把上传的 DOCX 转换为 Google Workspace 文档；`appProperties` 用于按 `run_id` 查找同一运行的文档。[1] [2]

每个 `run_id` 只允许一个文档。若同一 `run_id` 已存在且保存的源文件 SHA-256 一致，脚本输出幂等成功，不重复创建；若哈希不同或发现多个文档，脚本失败并拒绝覆盖。这样可以避免重试生成重复文档，也避免悄悄覆盖人工改动。要发布修订稿，应由总控创建新的修订运行 ID，并在 manifest 中保留其来源关系。

## 部署依赖与授权

在实际调度主机或 CI runner 安装依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r automation/requirements-docs.txt
chmod 0755 automation/sync_google_doc.py
```

该运行主机需要自己的 Google OAuth/服务账号授权。示例使用 Application Default Credentials 和 `drive.file` scope；可采用 Workload Identity，或由 Secret Manager/服务管理器安全注入 `GOOGLE_APPLICATION_CREDENTIALS` 路径。服务账号必须被明确授权目标 Drive 文件夹；授权材料不要写入仓库、日志或 `daily.env`。Google OAuth scope 决定 API 能访问的资源，应尽量限制到 publisher 所需范围。[3]

Claude Desktop 的 Google Workspace 连接用于本地会话。独立 VM 或 CI 使用单独配置的 Google 身份。

## 配置 `run-daily.sh`

在受管理员控制的 `automation/daily.env` 中配置发布入口和审批目录。

```bash
WESTCOTT_DOCS_PUBLISHER="/srv/westcott/automation/sync_google_doc.py"
WESTCOTT_GOOGLE_DRIVE_FOLDER_ID="从 Drive 文件夹 URL 提取的 ID"
WESTCOTT_DOC_TITLE_PREFIX="Westcott Daily Rhythm"
WESTCOTT_AUTO_PUBLISH=false
WESTCOTT_APPROVAL_DIR="/srv/westcott-approvals"
```

打开发布开关后，运行可以先完成独立事实核查。只有当前稿件通过机器核查，且审批目录中存在匹配的 `<run_id>.json`，才调用发布器。审批目录须在模型运行产物目录之外，仅有人工操作人或已经核验操作者身份的审批服务可写。审批记录由该可信来源在用户确认具体版本后写入。

审批记录需要包含以下字段。哈希对应审核时看到的 `draft.md` 和 `research.json` 原始文件字节。修订正文、证据、规则版本或归档目标后，应重新审批。

| 字段 | 值 |
| --- | --- |
| `action`、`decision` | `publish_google_doc`、`approved` |
| `run_id`、`task_date`、`ruleset_version` | 当前 manifest 对应的字段 |
| `folder_id` | 本次已批准的目标 Drive 文件夹 |
| `draft_sha256`、`research_sha256` | 本次已批准的稿件、证据 SHA-256 |
| `approved_by`、`approved_at` | 人工操作人标识、带时区的 ISO 8601 审批时间 |

发布器可以单独运行并执行同一审批检查。加 `--check-only` 只检查本地文件和审批回执。

```bash
automation/sync_google_doc.py \
  --input .westcott-state/runs/daily-2026-11-23/draft.md \
  --run-id daily-2026-11-23 \
  --date 2026-11-23 \
  --title "Westcott Daily Rhythm 2026-11-23" \
  --folder-id "$WESTCOTT_GOOGLE_DRIVE_FOLDER_ID" \
  --manifest .westcott-state/runs/daily-2026-11-23/manifest.json \
  --approval "$WESTCOTT_APPROVAL_DIR/daily-2026-11-23.json"
```

stdout 只输出一条 JSON，供 `run-daily.sh` 验证并原子写入 `google-docs.json`。诊断写入 stderr。遇到 Google 权限、网络或转换失败时，命令返回非零状态，不会谎报保存成功；调度脚本保留已验证的本地稿件，只重试 Docs 发布阶段。

在测试环境可运行离线假服务测试，不需要 Google 凭据，也不会创建真实文档：

```bash
python -m unittest discover -s automation -p 'test_sync_google_doc.py' -v
```

## Markdown 格式映射

单井号至三级标题映射到 Word/Google Docs 标题样式；`-`/`*` 项映射为项目符号列表；数字列表映射为编号列表；`**粗体**`、`*斜体*` 和围栏代码块会格式化。其他 Markdown（例如表格、引用块、图片和复杂嵌套列表）目前按普通段落处理，正式上线前应在 golden sample 中确认实际导入效果。

## 官方资料

[1]: https://developers.google.com/workspace/drive/api/guides/manage-uploads "Drive API 上传与 Workspace 格式转换"
[2]: https://developers.google.com/workspace/drive/api/guides/search-files "Drive API 按 appProperties 查询文件"
[3]: https://developers.google.com/identity/protocols/oauth2 "Google OAuth 2.0 授权协议"
