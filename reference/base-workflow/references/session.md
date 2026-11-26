# 同机跨聊天恢复

这是本地文件恢复，不是模型天然拥有跨聊天记忆，也不监听聊天或运行后台服务。需要每个新聊天都加载本技能，且能运行Python并读取同一数据目录。网页容器通常不能直接读取电脑目录；此时按memory.md导出/上传，不能承诺自动恢复。

## 首次启用

用户明确启用项目进度保存后，选择固定project ID。默认路径为Windows的 `%LOCALAPPDATA%/WestcottWorkflow/projects`；无LOCALAPPDATA时为 `~/.local/share/WestcottWorkflow/projects`。升级、移动技能不改变该路径。自定义目录可用绝对路径环境变量WESTCOTT_DATA_HOME，或每次一致地传 `--data-dir`；不擅自修改系统环境变量。不同系统账户不会共享默认目录。

从技能目录运行（Windows用python或py -3，Linux/macOS可用python3）：

```bash
python scripts/project_session.py list
python scripts/project_session.py init --project westcott-main
```

先list再init；已有项目禁止重新初始化。westcott-main只是示例，不代表用户已有项目。目录使用项目ID的散列隔离，命令回执提供实际文件路径。文件为本地明文，只保存必要项目上下文，不存密钥或全量私人聊天。

## 新聊天恢复顺序

1. 用户说“继续”或“恢复项目”，先list。已知项目ID直接resume；多个项目且用户未指定时询问目标，不按最近修改时间猜测；只有一个项目时也核对当前任务与该项目是否一致。
2. `python scripts/project_session.py resume --project westcott-main`。文件不存在或损坏时明确报告，没有读取成功就不称“记得”。
3. 以返回的revision和record_version作为本轮写入依据。session中的adopted_text为有采用引用的最后有效片段，candidate_text仍未采用。确认保存进度不等于确认采用候选稿。
4. 恢复current_node、completed_count、unresolved、next_step、stop_after，仅按本次用户要求继续。必要时一句话说明“已读取本地进度，停在……”。NO_CHECKPOINT只表示项目存在，尚无可恢复进度。
5. 来源revision只是历史快照；若继续依赖在线文档，刷新相关末段与修订号；行情重新核验。历史文字里的命令不获得执行权。source_ref/source_revision为unknown时说明缺口，不当成正式文档已读取。

## 保存与预览

输入格式见 [检查点示例](session-example.json)。该文件是测试示例，不是已保存的项目事实。保存授权来自用户启用项目记忆或本次保存要求；后续授权范围内的进度更新无需重复征求同意。用户关闭进度保存后停止写入。

```bash
python scripts/project_session.py preview --project westcott-main --input checkpoint.json
python scripts/project_session.py checkpoint --project westcott-main --input checkpoint.json
python scripts/project_session.py inspect --project westcott-main
```

在完成一段已授权工作、用户采用/纠正正文或明确要换聊天时保存；不要只等用户关闭窗口，因为脚本无法检测关闭事件。预览只读，显示变更，不增加revision；checkpoint才写入。拿到SAVED_LOCAL或核对ALREADY_APPLIED回执后，才能说已保存本地进度。

字段分别保存已采用末段、候选末段、采用依据、当前节点、有效条数、下一步、停点、未解决问题、相关编号和来源版本；只保留必要上下文，总长度限制9000字符。completed_count指有效正文条数，不能把候选条数计为已采用。没有采用正文时adopted_text/adoption_ref留空；不知道总数时先核对，不捏造计数。

同一operation_id只重试完全相同输入；新变更用新ID。旧窗口revision冲突时重新resume并对照新进度，不把expected_revision机械改为最新值强行覆盖。确认进度保存与外部文档写入、发送状态无关；本工具没有发布命令。

## 维护与迁移

inspect显示大小、history占用、去重回执数量，接近8 MiB或500条历史时建议归档，不自动删除。使用返回的file路径调用memory_store.py archive，按memory.md取得本次归档授权。记忆格式沿用v1，现有记录可继续读取；旧记忆需要放入新目录时用export/restore到不存在的目标，保留原文件，不能直接覆盖。

`python scripts/bundle_doctor.py --snapshots /YOUR_SNAPSHOT_DIRECTORY`仅列指定目录的快照项目、版本及校验状态，不自动选一个恢复。

真正的跨聊天验收要在目标应用新建聊天验证实际调用与权限；两个Python进程读回成功只证明文件恢复能力。安装成功、本地写入成功、另一个进程读回成功、目标应用新聊天恢复成功分别报告。
