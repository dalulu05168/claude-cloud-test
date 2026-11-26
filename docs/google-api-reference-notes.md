# Google Docs 与 Drive API 实现依据

本项目的 publisher 采用“生成 DOCX → Drive 上传并转换为 Google Docs → 按 `appProperties` 检索同一 `run_id`”流程。Drive API 上传指南说明了媒体上传及 Workspace MIME 类型转换；文件查询文档说明可用 `appProperties has { key='...' and value='...' }` 查找应用私有元数据。[1] [2]

脚本创建新文档时，目标文件夹通过 Drive `files.create` 元数据中的 `parents` 指定；转换后的文件应返回 Google Docs MIME 类型。publisher 将 run ID 与源 Markdown 的 SHA-256 写入 `appProperties`，使重复调度可识别相同内容。若检索到不同源哈希，当前脚本不会覆盖已有文档，而是报错待人工处理。

当前 `sync_google_doc.py` 不用 Docs `batchUpdate` 直接编辑文档正文，因此不涉及 Docs API 文本索引。如果后续产品需求改为原位更新 Google Doc，应遵循以下规则：Docs API 索引按 UTF-16 code units 计算，不能直接使用 Python `len()` 计算 Emoji 等非 BMP 字符的范围；`batchUpdate` 子请求按顺序执行并作为原子批处理；正文最后一个换行符不能删除；并发更新应读取当前 revision，再使用 `WriteControl.requiredRevisionId` 防止覆盖他人编辑。[3] [4] [5] [6]

OAuth scope 限制 API 可访问范围。示例 publisher 请求 `drive.file`，由部署者在调度主机配置 ADC、Workload Identity 或其他批准的服务端授权，并授予目标文件夹访问权。Claude Desktop 的连接与调度主机的授权分别配置。[7]

## References

[1]: https://developers.google.com/workspace/drive/api/guides/manage-uploads "Drive API 上传和 Google Workspace 格式转换"
[2]: https://developers.google.com/workspace/drive/api/guides/search-files "Drive API 文件查询与 appProperties"
[3]: https://developers.google.com/workspace/docs/api/concepts/structure "Google Docs 文档结构与 UTF-16 索引"
[4]: https://developers.google.com/workspace/docs/api/concepts/rules-behavior "Google Docs 文档编辑规则"
[5]: https://developers.google.com/workspace/docs/api/how-tos/batch "Google Docs batchUpdate 的顺序和原子性"
[6]: https://developers.google.com/workspace/docs/api/how-tos/best-practices "Google Docs revision 控制与并发更新"
[7]: https://developers.google.com/identity/protocols/oauth2 "Google OAuth 2.0 与授权范围"
