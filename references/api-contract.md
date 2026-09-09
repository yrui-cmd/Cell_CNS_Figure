# 小描期刊图 API 合同

只在接口适配或排障时读取。默认配置不可在普通任务中改写。

## 固定接口

- Base URL：`https://xiaomiao-ai.com`
- 余额：`GET /api/balance`
- 提交：`POST /api/journal-figure-jobs`
- 状态：`GET /api/journal-figure-jobs/{job_id}`
- 结果：`GET /api/journal-figure-jobs/{job_id}/result`
- 取消：`DELETE /api/journal-figure-jobs/{job_id}`
- 鉴权：`Authorization: Bearer <API_KEY>`

提交使用 `multipart/form-data`：`brief` 为 UTF-8 科研内容；每个参考文件使用一个重复的 `references` 字段。

## 解析兼容

客户端兼容以下服务端字段别名，但不改变服务端数据：

- 任务 ID：`job_id`、`task_id`、`id`
- 状态：`status`、`state`
- 可用额度：`available_credits`、`credits_left`、`remaining_credits`、`balance`
- 已用额度：`credits_used`、`credits_spent`、`charged_credits`、`consumed`
- 结果地址：`result_url`、`result_file_url`、`download_url`、`result.url`

期刊图权限优先读取 `services` 中的 `journal_figure`、`journal-figure`、`journal` 或 `journal_figure_jobs`。明确 `false`、`disabled` 或 `unavailable` 时禁止提交；旧服务未返回 `services` 时不虚构权限状态，提交接口仍是最终权限判定者。

结果接口可返回 `image/png`、包含结果 URL 的 JSON，或 `image_base64`。外部结果 URL 不携带小描 Bearer；最终只接受可完整解码的 PNG。

## 终态

- 成功：`completed`、`complete`、`succeeded`、`success`
- 失败：`failed`、`error`、`cancelled`、`canceled`、`expired`

`received`、`queued`、`awaiting_result`、`processing` 均为进行中。HTTP 409 表示结果尚未就绪，不应创建新任务。
