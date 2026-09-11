# 接口与代码契约

核对日期：2026-09-11。
来源：[官网](https://xiaomiao-ai.com/)、[开发文档](https://xiaomiao-ai.com/docs)、[OpenAPI](https://xiaomiao-ai.com/openapi.json)，文档版本 1.1.0。

固定鉴权查询：GET https://xiaomiao-ai.com/api/balance，Authorization: Bearer。不携带密钥访问任意用户提供的地址，不跟随重定向。正常每轮一次，临时故障最多三次尝试，单请求超时 10 秒。Retry-After 超过 5 秒则本轮停止，不提前重试。

余额要求 HTTP 成功且 ok=true；available_credits 为非负整数，否则未知。services 中 enabled/allowed 布尔值才显示明确权限，缺失不猜；can_submit 只是本次提交条件，不是处理服务在线的保证。

| 功能 | 提交接口 | 参考额度 |
|---|---|---|
| 路径识别，图片转 SVG | POST /api/images | 首次成功领取 1 |
| 期刊图，文字及参考文件转 PNG | POST /api/journal-figure-jobs | 预留 20，首次领取正式消耗 |
| 图片去水印，返回处理图片 | POST /api/watermark-jobs | 预留 1，首次领取正式消耗 |

费用取自核对时官网，可能调整；配置不触发这些 POST 或收费下载。不要把旧版 OpenAPI 个别描述中残留的 3 当作期刊图费用。结果效果、隐藏水印清除能力不作保证。

JSON schema_version=1：status、message、balance（整数或 null）、queried、checked_at（本地本轮时间）、config_path、editor、features。features 包含 id/name/endpoint/documented/reference_cost/permission/can_submit/executed。不包含密钥或原始响应。

退出码：0 成功；2 待填或配置无效；3 鉴权失败；4 权限不足；5 额度不足；6 限流；7 网络错误；8 服务错误；9 协议/重定向错误；10 文件或平台权限错误；11 未预期本地错误。只有 0 表示配置与实时余额验证成功，不表示其他业务已成功运行。

Python API：`setup(...)` 总是返回脱敏状态，`query_balance(key)` 固定查询（原始字典仅供内部，禁止直接输出），`read_key(path)` 和 `child_environment(path)` 的返回值属于秘密，不可记录日志。`transport` 注入仅用于模拟测试，不向 CLI 暴露自定义服务器地址。
