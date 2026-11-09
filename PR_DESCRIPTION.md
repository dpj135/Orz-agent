# feat: 持久化会话上下文与独立交互日志

关联 #14；复用 #12 的缓存字段组装方向，不扩展输出上限、store 或 metadata。

## 目标与语义

- 新建生成 UUID，或以 --session-id ID 指定未占用 ID；已存在报错，不覆盖、不隐式恢复。--resume ID 仅恢复已有有效检查点；两参数互斥，启动显示身份及操作类型。不新增 AGENT_SESSION_ID。
- messages 是后续模型输入的唯一上下文来源，包含基础指令和结构化交互。恢复不重复注入基础指令，未来压缩只更新 messages。
- history 独立追加用户输入、应用收到的 LLM 响应、工具调用参数、执行结果/错误、轮次结束/中断，用于调试和未来轨迹功能。不参与恢复、不重放工具、不与检查点绑定事件序号。

## 存储

用户级应用数据目录：Windows LOCALAPPDATA/orz-agent；Linux XDG_DATA_HOME/orz-agent（缺省 ~/.local/share/orz-agent）；macOS ~/Library/Application Support/orz-agent。支持 --session-dir 覆盖 sessions 根目录。

每个会话包含 checkpoint.json、history.jsonl、writer.lock。ID 限制为 `[A-Za-z0-9_-]{1,64}`，目录名为 ASCII ID 的 SHA-256 十六进制摘要；缓存键仍为原始 ID，不截断、不变更。禁止任意路径。检查点仅包含 schema_version、session_id、messages、workspace、created_at、updated_at。不保存模型配置快照、鉴权信息或执行现场。检查点上限 32 MiB，超过时明确报错，不裁剪上下文。

新建身份和初始 messages 先安全落盘；完整轮次后原子更新检查点。全生命周期保持单写者锁，异常退出由操作系统释放。保存失败保留上一有效检查点。失败/中断轮次不提交上下文，已有日志保留。恢复使用保存的工作区；不存在时报错，不默默换目录。

## 缓存

cache_key = session_id。同会话多轮/工具循环/重启恢复保持稳定。已知协议适配器默认发送 prompt_cache_key，提供显式关闭选项供不兼容端点使用；未知网关不宣称支持。不映射为 user，不静默降级重试，不自动换端点。每次仍发送所需 messages；缓存不是服务端历史或上下文隔离，也不保证命中/省钱。

## 边界

history 不因压缩删除。不记录鉴权配置、HTTP 鉴权头或完整环境；用户输入和工具输出本身仍可能敏感。恢复是内容续接，使用当前模型/工具/鉴权，不是环境快照；不回滚外部副作用，不重放工具。

不实现压缩、轨迹 UI、完整审计、fork、跨会话记忆、多 Agent、Responses 续接、web search 改造或崩溃现场续跑。不调用真实模型端点；兼容性实测需另行授权。

## 验证计划

离线测试覆盖身份隔离、重复/非法 ID、缺失/损坏/不完整序列恢复、独立日志、原子保存失败、跨进程锁及退出释放、失败轮次不提交、工作区恢复、实际请求体缓存生命周期及关闭选项。运行完整 pytest 和 pre-commit，并提供 CLI 离线验证。

## 验证结果

- Windows / Python 3.12：`python -m pytest -q -rs`：197 passed，7 skipped。
- `python -m pre_commit run --all-files` 全部适用钩子通过；JSON 检查因无匹配文件跳过。
- 真实 CLI 子进程 + loopback HTTP 验证新建/重启恢复、稳定前缀与缓存键、显式禁用、重复 ID、400 拒绝只请求一次并提示关闭方式。全部 synthetic fixtures，无真实 API 调用。
- Windows 跨进程锁、正常/异常退出释放、junction/reparse 拒绝实测通过。7 个跳过为 6 个 symlink 权限不足用例和 1 个 POSIX 权限用例；Linux/macOS 未本机验证，Windows/Linux CI 待 PR 检查。
- 基于 origin/main 开发，不夹带尚未合并的 issue #13 E2E 分支改动。修正 `python -m mini_agent` 未传播 main 返回码的问题，使 CLI 失败正确退出非零。

## 已知限制

- 日志不是崩溃事务，最后一条事件可能断尾；后续追加先分隔断尾，不解析或修复坏行。日志写入失败时停止当前操作，不默默继续执行工具。
- 新建时保存失败可留下已占用但不可恢复的目录，需在无写入者时人工检查，不自动复用。
- 本地路径校验不是无竞争窗口的 OS sandbox；Windows 使用继承 ACL，POSIX 使用私有目录/文件权限。远程文件系统锁和断电级持久性不作保证。
- 端点字段支持未做真实验证；需要真实网关验证时另行授权。
