# cell_figure

小描期刊图 API 的 Python 客户端与 Codex Skill。接收科研文字及可选 JPG、PNG、PDF 参考文件，查询额度、提交异步任务，每 20 分钟自动唤醒检查并领取 PNG。

**提交并设置定时检查后，当前回复先结束；每 20 分钟醒来查看，有图就下载交付，无新结果则保持安静。** 图片交付才算生成完成，不需要用户再次发送“继续”。成功交付、用户停止、服务明确失败或需要用户处理的问题会停止自动检查。

显示名称为 `cell_figure`，Codex 调用标识为 `$cell-figure`。

An MIT-licensed Python client and Codex skill for Xiaomiao scientific figures: submit research text and optional references, check credits, and schedule a check every 20 minutes until the final PNG is retrieved and delivered. Each check ends instead of keeping the conversation running. The remote generation service is separate from this open-source client.

## 目录

- [功能](#功能)
- [安装](#安装)
- [使用](#使用)
- [凭据和费用](#凭据和费用)
- [验证与当前边界](#验证与当前边界)
- [许可证](#许可证)

## 功能

- 接收科研文字和 JPG、PNG、PDF 参考文件，提交前显示实时额度。
- 保存任务 ID，每 20 分钟定时检查并领取图片；中断后恢复同一任务，复用已经下载的结果。
- 验证返回 PNG 的文件签名、完整解码及尺寸，下载后再次查询余额。
- 提供可独立使用的命令行客户端、Codex Skill 和[接口说明](references/api-contract.md)。

## 安装

需要 Python 3.10 或更高版本。可直接把下面这句话发给 Codex：

> 请从 https://github.com/yrui-cmd/cell_figure 安装 cell-figure skill，并安装 requirements.txt 中的依赖。

也可下载仓库 ZIP，解压后将包含 `SKILL.md` 的目录命名为 `cell-figure`，放入用户的 `.codex/skills/`。Windows 默认位置是 `%USERPROFILE%\.codex\skills\cell-figure`；macOS/Linux 默认位置是 `~/.codex/skills/cell-figure`。若已有该目录，先保留自己的版本与改动。

在该目录运行：

```sh
python -m pip install -r requirements.txt
```

重新加载 Codex 的 Skill 列表后，可以说：“使用 cell_figure，把这段研究内容提交到小描生成期刊图。”

## 使用

```sh
# 查询实时余额
python -X utf8 scripts/xiaomiao_client.py balance

# 提交，立即核对服务端实际预留额度
python -X utf8 scripts/xiaomiao_client.py submit --text-file research.txt --reference reference.png

# Skill 在核对费用后使用 Codex 自动任务工具设置每 20 分钟检查
# 每次唤醒查询一次原任务
python -X utf8 scripts/xiaomiao_client.py status JOB_ID

# 已完成且费用获授权时领取 PNG
python -X utf8 scripts/xiaomiao_client.py fetch JOB_ID

# 独立 CLI 用户明确需要持续等待时（不是 Skill 默认流程）
python -X utf8 scripts/xiaomiao_client.py resume JOB_ID --wait
```

研究内容限制为 1–12000 字符；参考文件最多 6 个，单个不超过 10 MB，合计不超过 30 MB。Skill 默认每 20 分钟通过 Codex 自动任务唤醒一次，发现完成后领取图片。详细流程见 [定时检查](references/scheduled-checks.md)。

Skill 使用“提交 → 核对预留费用 → 设置每 20 分钟唤醒 → 结束本轮”，后续自动检查同一任务；设置失败会明确说明，不会假称已安排。服务器返回完成但图片暂未就绪时，下一轮再查原任务。独立 CLI 的 `run` 和 `resume` 仍默认持续等待，`--background` 可供明确需要常驻 worker 的用户选择；这些命令本身不会创建 Codex 自动任务。

Windows 默认结果位置为 `%LOCALAPPDATA%\cell_figure\results\<job_id>\final.png`；macOS/Linux 为 `${XDG_DATA_HOME:-~/.local/share}/cell-figure/results/<job_id>/final.png`。

## 凭据和费用

客户端会读取当前进程的 `XIAOMIAO_API_KEY`、本客户端安全凭据，以及用户真实桌面上的 `Cell_skills.txt`。桌面文件支持 `XIAOMIAO_API_KEY=<你的Key>`、`小描=<你的Key>` 或单行 Key。请勿提交该文件到 Git。

你也可以在当前助手对话中提供自己的 Key，让助手通过 `configure-key` 的标准输入完成配置，无需把 Key 写入脚本或命令参数；聊天本身仍由所用平台保存和管理。

Windows 安全存储使用当前用户的 DPAPI 加密。macOS/Linux 使用权限为 `0600` 的本地私有文件，不是加密钥匙串。客户端源码开源；小描远程服务需要独立 API Key 和额度。输入文字与参考文件会发送到 `https://xiaomiao-ai.com`。

**旧版“本次需要 3 额度”的提示不准确。** 3 只是客户端历史最低余额检查门槛，不能视为报价。2026-09-10 实际观察到单次任务预留 **20 额度**；该数字是费用参考，不是固定价格或最终扣费承诺。

每次先查实时余额，提交后立即显示服务端实际预留和返回的可用余额。缺失金额不补造，预留与最终扣费分别说明。若实际预留与已告知金额不一致、金额未知或超出授权范围，先暂停本地自动领取并询问用户，确认后继续原任务。暂停本地程序不等于取消远端生成，也不能保证服务端不会继续结算；不会擅自取消或重新提交。

## 验证与当前边界

```sh
python -B -X utf8 scripts/test_client.py
python -B -X utf8 scripts/test_worker.py -v
python -B -X utf8 scripts/test_wait_completion.py -v
python -B -X utf8 scripts/test_billing_display.py -v
```

测试使用临时数据目录、临时桌面和本地模拟服务，覆盖余额、PNG 领取、基本去重、额度不足、参考数量限制、桌面凭据发现和凭据输出检查。等待测试覆盖默认持续等待、多轮处理中、结果未就绪、短暂服务错误、恢复时不重复提交以及真实失败退出。后台测试覆盖重复启动时进程仍存活，以及成功状态别名在下载失败后的恢复，不产生线上费用。模拟测试通过不等同于所有故障场景已验证。

定时检查依赖 Codex 自动任务能力；本地脚本任务需要电脑开机、联网且 Codex 应用在后台运行，不必一直停留在当前对话。关闭应用或关机时不能保证按时检查，详见 [官方自动任务说明](https://learn.chatgpt.com/docs/automations?surface=app)。每次唤醒复用原任务，交付或终止后暂停计划；缺少调度工具时会报告限制。多进程并发提交、提交响应丢失时的端到端幂等保证、worker 单实例锁以及所有异常场景的恢复仍需完善；服务器应提供幂等提交支持后再用于严格计费场景。过期任务不会自动重新提交。

请勿将 API Key、客户科研内容、参考文件、任务数据库或生成结果放入公开 issue。

## 许可证

MIT，见 [LICENSE](LICENSE)。许可证适用于本仓库代码，不授予远程服务额度或第三方材料权利。
