---
name: cell-figure
description: Submit research text and optional JPG, PNG, or PDF references to the Xiaomiao journal-figure API, show the live balance, and keep waiting until the verified PNG is retrieved and delivered to the user. Persist and resume the same job across interruptions. Use for Xiaomiao journal figures or Xiaomiao API balance; do not use for path recognition or local figure generation.
---

# cell_figure

这是小描期刊图 API 客户端。它提交用户原始科研内容、持久化任务、持续等待并领取 PNG；不在本地生成图片，也不修改路径识别服务。

## 完成条件：收到并交付图片才结束

**生成任务必须持续执行到返回图片已下载、验证通过，并实际展示或提供可打开的文件给用户，才可正常结束回复。** “已提交”“已排队”“后台正在处理”、任务 ID 或服务器的 `completed` 状态都不是交付完成。

- 默认在当前任务中保持等待，不把工作留给后台就结束，不让用户再发“继续”或自行回来查询。
- 运行下方等待命令后，工具若返回仍在运行的会话，应持续使用相应等待/输出工具跟进该会话。每次工具等待不超过 60 秒；正常生成不设置总等待超时。等待工具的一次超时、让出执行权或暂时无新输出都不代表生成已结束。
- `received`、`queued`、`awaiting_result`、`processing` 和结果暂未就绪继续等待同一个任务。轮询或下载短暂失败时恢复原 job_id，禁止为等待或重试重复提交和扣费。
- 只有用户主动停止、服务明确返回失败/取消/过期，或发生需要用户处理的鉴权、权限等真实阻塞时才能提前结束；说明实际原因并保留任务，不把失败说成完成。

本规则适用于生成与恢复生成；仅查询余额时直接返回余额。

## 执行入口

脚本均位于本 Skill 的 `scripts/`。正常生成使用：

```text
python -X utf8 scripts/xiaomiao_client.py run --wait --text-file <UTF-8文本文件> [--reference <JPG|PNG|PDF>]...
```

短文本可使用 `--text`，但长文本必须写入临时 UTF-8 文件并用 `--text-file`，避免命令行转义和泄露。用户只问余额时运行：

```text
python -X utf8 scripts/xiaomiao_client.py balance
```

已有任务中断后，继续等待原任务并取回结果：

```text
python -X utf8 scripts/xiaomiao_client.py resume <原job_id> --wait
```

`run` 和 `resume` 默认等待图片，`--wait` 明确该意图。`--background`、`submit` 和 `start-worker` 仅供用户明确要求单独后台运行时使用，不能作为普通生成任务的结束步骤。若用户需要开机后恢复，可执行 `install-autostart`；代理负责管理任务 ID 和程序，不要求普通用户配置 Python 路径或计划任务。

## 固定流程

1. 原样使用用户的 `brief`。只有用户明确要求优化提示词时才改写。
2. 当前消息所附 JPG、JPEG、PNG、PDF 默认作为参考文件，不再确认。
3. 客户端按固定允许来源发现并验证 API Key：当前进程 `XIAOMIAO_API_KEY`、Windows 安全凭据、真实桌面的 `Cell_skills.txt`、本次明确提供的 Key。不得扫描其他文件、浏览器、磁盘或项目。
4. 提交前实时请求 `/api/balance`。只有可用额度不少于 3 且期刊图权限未被明确禁用时才提交；余额缺失、鉴权失败或服务不可确认时停止，不猜测。
5. 本地预检：brief 为 1–12000 字符；最多 6 个参考文件；单文件不超过 10 MB；合计不超过 30 MB。
6. 以 brief 和参考文件内容哈希去重。活动任务继续原 job_id；已下载的相同任务直接返回现有 PNG，绝不因轮询或下载失败重复 POST。
7. 等待程序默认每 600 秒查询任务，代理保持任务打开。发现成功状态后立即领取；若结果仍未就绪，继续原任务等待。`cancelled`、`expired`、`failed` 按终态处理。
8. PNG 必须通过签名、完整解码及宽高检查才标记完成。下载临时失败只重试同一 `/result`。
9. PNG 验证成功后再次实时查询余额。服务端余额和扣费字段是唯一真值，不自行计算。

API 默认固定为 `https://xiaomiao-ai.com`，接口契约见 [API 合同](references/api-contract.md)。正常流程禁止浏览器自动化。

## 用户可见内容

正常处理只显示必要状态：

```text
小描额度：<实时 available_credits>
期刊图需要：3
状态：可提交
```

提交状态只作为进度更新，不能作为最终回复。已获得服务端额度字段时可显示预留和剩余额度；没有字段时不编造。等待期间简短说明有意义的状态，不展示请求头、Key、端点、JSON、哈希、SQLite、重试和轮询细节。

成功后在最终回复中展示一张返回的 `final.png`，并提供可打开的本地文件链接和再次查询得到的当前余额。先确认文件实际存在，不能只返回任务 ID、结果目录或“已完成”。若展示工具不可用，至少交付可打开的图片文件链接。失败时只说明真实、可行动的原因。任何地方都不得输出完整 API Key、Authorization、客户科研内容或原始响应体。

## 恢复与安全

- 状态数据库在用户本地应用数据目录的 `journal_jobs.sqlite`；结果位于 `results/<job_id>/final.png`。数据库不保存 brief、完整 Key 或附件内容。
- Key 从桌面文件成功验证后，自动迁移到 Windows DPAPI 加密存储；桌面原文件不删除、不改写。非 Windows 测试环境使用权限收紧的私有文件。
- 401 自动尝试下一允许来源；402 直接报告额度不足；409 视为尚未就绪；500、503 和网络超时按有限退避重试。
- 当前客户端不会自动重提过期任务。用户要求重新提交时，只有确认原任务终止且预留已释放后才允许重新提交一次；不能确认就停止。无效输入、权限或额度错误不自动重提。
- 日志只能包含时间、job_id、状态、HTTP 状态、重试次数、结果路径和额度，禁止任何完整秘密。

## 维护边界

稳定基础设施为 `XiaomiaoClient`、`CredentialManager`、`JournalWorker`、`JobStore`。通过测试后只修缺陷，不因代码风格重构。任何变更不得触碰小描路径识别接口、队列、处理节点或扣费逻辑。
