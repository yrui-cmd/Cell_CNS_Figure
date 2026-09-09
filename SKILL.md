---
name: cell-figure
description: Submit research text and optional JPG, PNG, or PDF references to the Xiaomiao journal-figure API, show the live balance, persist and resume asynchronous jobs, and automatically retrieve the verified PNG. Use for Xiaomiao journal figures or Xiaomiao API balance; do not use for path recognition or local figure generation.
---

# cell_figure

这是小描期刊图 API 的生产客户端。它只提交用户原始科研内容、持久化任务、后台轮询并领取 PNG；不在本地生成图片，也不修改路径识别服务。

## 执行入口

脚本均位于本 Skill 的 `scripts/`。正常生成使用：

```text
python -X utf8 scripts/xiaomiao_client.py run --text-file <UTF-8文本文件> [--reference <JPG|PNG|PDF>]...
```

短文本可使用 `--text`，但长文本必须写入临时 UTF-8 文件并用 `--text-file`，避免命令行转义和泄露。用户只问余额时运行：

```text
python -X utf8 scripts/xiaomiao_client.py balance
```

首次使用或任务恢复时确保后台 worker 已启动：

```text
python -X utf8 scripts/xiaomiao_client.py start-worker
```

`run` 会自动启动 worker。若操作系统重启后需要自动恢复，可由代理执行 `install-autostart`；不要要求普通用户配置环境变量、Python 路径、任务 ID 或计划任务。

## 固定流程

1. 原样使用用户的 `brief`。只有用户明确要求优化提示词时才改写。
2. 当前消息所附 JPG、JPEG、PNG、PDF 默认作为参考文件，不再确认。
3. 客户端按固定允许来源发现并验证 API Key：当前进程 `XIAOMIAO_API_KEY`、Windows 安全凭据、真实桌面的 `Cell_skills.txt`、本次明确提供的 Key。不得扫描其他文件、浏览器、磁盘或项目。
4. 提交前实时请求 `/api/balance`。只有可用额度不少于 3 且期刊图权限未被明确禁用时才提交；余额缺失、鉴权失败或服务不可确认时停止，不猜测。
5. 本地预检：brief 为 1–12000 字符；最多 6 个参考文件；单文件不超过 10 MB；合计不超过 30 MB。
6. 以 brief 和参考文件内容哈希去重。活动任务继续原 job_id；已下载的相同任务直接返回现有 PNG，绝不因轮询或下载失败重复 POST。
7. Python worker 每 600 秒查询任务。发现 `completed` 后立即领取，不再等待下一轮；`received`、`processing` 继续原任务；`cancelled`、`expired`、`failed` 按终态记录。
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

提交后显示任务已提交、预留额度和服务端返回的剩余可用额度；不要展示请求头、Key、端点、JSON、哈希、SQLite、重试和轮询细节。后台轮询不持续制造消息。

成功后默认只交付一张可打开的 `final.png` 和再次查询得到的当前余额。失败时只说明真实、可行动的原因。任何地方都不得输出完整 API Key、Authorization、客户科研内容或原始响应体。

## 恢复与安全

- 状态数据库在用户本地应用数据目录的 `journal_jobs.sqlite`；结果位于 `results/<job_id>/final.png`。数据库不保存 brief、完整 Key 或附件内容。
- Key 从桌面文件成功验证后，自动迁移到 Windows DPAPI 加密存储；桌面原文件不删除、不改写。非 Windows 测试环境使用权限收紧的私有文件。
- 401 自动尝试下一允许来源；402 直接报告额度不足；409 视为尚未就绪；500、503 和网络超时按有限退避重试。
- 当前客户端不会自动重提过期任务。用户要求重新提交时，只有确认原任务终止且预留已释放后才允许重新提交一次；不能确认就停止。无效输入、权限或额度错误不自动重提。
- 日志只能包含时间、job_id、状态、HTTP 状态、重试次数、结果路径和额度，禁止任何完整秘密。

## 维护边界

稳定基础设施为 `XiaomiaoClient`、`CredentialManager`、`JournalWorker`、`JobStore`。通过测试后只修缺陷，不因代码风格重构。任何变更不得触碰小描路径识别接口、队列、处理节点或扣费逻辑。
