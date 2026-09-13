# Cell_CNS_Figure

小描期刊图 API 客户端与两阶段科研绘图 Skill：先获得布局图 A，再由用户选择是否生成高级设计图 B。每阶段分别展示费用与实时余额，提交后按阶段间隔（A 每 3 分钟、B 每 10 分钟）自动检查。

**提交并设置定时检查后，当前回复先结束；按阶段间隔（A 每 3 分钟、B 每 10 分钟）醒来查看，有图就下载交付，无新结果则保持安静。** 图片交付才算生成完成，不需要用户再次发送“继续”。成功交付、用户停止、服务明确失败或需要用户处理的问题会停止自动检查。

显示名称为 `Cell_CNS_Figure`，Codex 调用标识为 `$cell-cns-figure`。

An MIT-licensed Python client and Codex skill for Xiaomiao scientific figures: submit research text and optional references, check credits, and schedule checks every 3 minutes for stage A and every 10 minutes for stage B until the final PNG is retrieved and delivered. Each check ends instead of keeping the conversation running. The remote generation service is separate from this open-source client.

## 目录

- [功能](#功能)
- [使用教程](使用教程.md)
- [下载 Word 版使用教程](Cell_CNS_Figure 使用教程 最新版.docx)
- [安装](#安装)
- [使用](#使用)
- [凭据和费用](#凭据和费用)
- [验证与当前边界](#验证与当前边界)
- [许可证](#许可证)

## 功能

- 仅接收文章摘要或核心研究文字；第一阶段生成图 A，第二阶段使用图 A 作为参考，提交前显示实时额度。
- 保存任务 ID，按阶段间隔（A 每 3 分钟、B 每 10 分钟）定时检查并领取图片；中断后恢复同一任务，复用已经下载的结果。
- 验证返回 PNG 的文件签名、完整解码及尺寸，下载后再次查询余额。
- 提供可独立使用的命令行客户端、Codex Skill 和[接口说明](references/api-contract.md)。

## 安装

需要 Python 3.10 或更高版本。可直接把下面这句话发给 Codex：

> 请从 https://github.com/yrui-cmd/Cell_CNS_Figure 安装 cell-cns-figure，并将仓库 dependencies/xiaomiao-api-setup 安装为同级 Skill，安装 requirements.txt 中的依赖。

也可下载仓库 ZIP，解压后将包含 `SKILL.md` 的目录命名为 `cell-cns-figure`，放入用户的 `.codex/skills/`。Windows 默认位置是 `%USERPROFILE%\.codex\skills\cell-cns-figure`；macOS/Linux 默认位置是 `~/.codex/skills/cell-cns-figure`。若已有该目录，先保留自己的版本与改动。

将仓库内 `dependencies/xiaomiao-api-setup` 文件夹复制到同一级 `.codex/skills/xiaomiao-api-setup`；已有配置助手时保留用户修改并先比较版本。该依赖不包含真实密钥。

在 cell-cns-figure 目录运行：

```sh
python -m pip install -r requirements.txt
```

重新加载 Codex 的 Skill 列表后，可以说：“使用 Cell_CNS_Figure，把这段研究内容提交到小描生成期刊图。”

## 使用

```sh
# 查询实时余额
python -X utf8 scripts/configured_client.py balance

# 提交，立即核对服务端实际扣费额度
python -X utf8 scripts/configured_client.py submit --text-file research.txt --reference reference.png

# Skill 在核对费用后使用 Codex 自动任务工具设置按阶段间隔（A 每 3 分钟、B 每 10 分钟）检查
# 每次唤醒查询一次原任务
python -X utf8 scripts/configured_client.py status JOB_ID

# 已完成且费用获授权时领取 PNG
python -X utf8 scripts/configured_client.py fetch JOB_ID

# 独立 CLI 用户明确需要持续等待时（不是 Skill 默认流程）
python -X utf8 scripts/xiaomiao_client.py resume JOB_ID --wait
```

研究内容限制为 1–12000 字符；参考文件最多 6 个，单个不超过 10 MB，合计不超过 30 MB。Skill 默认按阶段间隔（A 每 3 分钟、B 每 10 分钟）通过 Codex 自动任务唤醒一次，发现完成后领取图片。详细流程见 [定时检查](references/scheduled-checks.md)。

Skill 使用“提交 → 核对实际扣费 → 设置按阶段间隔（A 每 3 分钟、B 每 10 分钟）唤醒 → 结束本轮”，后续自动检查同一任务；设置失败会明确说明，不会假称已安排。服务器返回完成但图片暂未就绪时，下一轮再查原任务。独立 CLI 的 `run` 和 `resume` 仍默认持续等待，`--background` 可供明确需要常驻 worker 的用户选择；这些命令本身不会创建 Codex 自动任务。

Windows 默认结果位置为 `%LOCALAPPDATA%\cell_figure\results\<job_id>\final.png`；macOS/Linux 为 `${XDG_DATA_HOME:-~/.local/share}/cell-figure/results/<job_id>/final.png`。

## 凭据和费用

默认通过 xiaomiao-api-setup 每次重新读取桌面隐藏文件 `xiaomiao_api.txt`，格式为 `API_Key=""`；支持聊天输入正式 API Key 或服务端签发的 `jexp_…` 一次体验邀请码后写入文件。邀请码仅在服务端验证有效、可用于期刊图且尚未用完时才能运行一次体验流程；不要将邀请码提交到公开仓库、Issue 或教程示例。固定入口 `configured_client.py` 只读取共享文件，鉴权失败也不回退旧环境变量或历史账户。原独立 CLI 保留兼容行为，不作为 Skill 默认入口。

隐藏文件是明文，不等于加密。Android 使用 Python 宿主可访问的隐藏配置目录；macOS/Android 待实机验证。内容和图片会发送到小描服务。

产品流程：第一阶段布局设计 10 次额度；第二阶段高级设计另计 45 次；两阶段合计 55 次。收到合格研究文字后直接进入第一阶段费用确认。每阶段确认时显示实时余额及“开始设计后不退额度”，拒绝第二阶段则交付图 A 后结束。

计费规则：第一阶段仅提交研究文字，扣除 10 次额度；第二阶段提交研究文字与图 A，扣除 45 次额度。两阶段合计 55 次。提交成功即扣费，开始设计后不退额度，领取结果不重复扣费。

每阶段超过一小时未返回图片提醒一次联系抖音「木纹」主页群，继续查询；超过24小时无有效结果则标记本地超时失败并停止自动检查。超时不代表远端取消或退款。图 A、图 B 分开交付，不覆盖原图。

## 验证与当前边界

两阶段选择、费用授权、阶段记录及超时调度由 Skill 指令与 Codex 自动任务执行；现有 Python 客户端负责接口和下载，不宣称已实现完整的独立两阶段调度引擎。

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
