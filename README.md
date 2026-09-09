# cell_figure

小描期刊图 API 的 Python 客户端与 Codex Skill。接收科研文字及可选 JPG、PNG、PDF 参考文件，查询额度、提交异步任务，并通过 Python 后台程序查询和领取 PNG。

显示名称为 `cell_figure`，Codex 调用标识为 `$cell-figure`。

An MIT-licensed Python client and Codex skill for Xiaomiao scientific figures: submit research text and optional references, check credits, resume asynchronous jobs, and retrieve the final PNG. The remote generation service is separate from this open-source client.

## 目录

- [功能](#功能)
- [安装](#安装)
- [使用](#使用)
- [凭据和费用](#凭据和费用)
- [验证与当前边界](#验证与当前边界)
- [许可证](#许可证)

## 功能

- 接收科研文字和 JPG、PNG、PDF 参考文件，提交前显示实时额度。
- 保存任务 ID，支持后台轮询、继续领取和复用已经下载的同一任务结果。
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

# 提交并启动后台领取程序
python -X utf8 scripts/xiaomiao_client.py run --text-file research.txt --reference reference.png

# 查看已提交任务
python -X utf8 scripts/xiaomiao_client.py status JOB_ID

# 恢复后台处理
python -X utf8 scripts/xiaomiao_client.py start-worker

# Windows 登录后自动启动（可选）
python -X utf8 scripts/xiaomiao_client.py install-autostart
```

研究内容限制为 1–12000 字符；参考文件最多 6 个，单个不超过 10 MB，合计不超过 30 MB。默认每 600 秒查询一次，在查询发现完成时立即下载。电脑需要保持运行和联网。

Windows 默认结果位置为 `%LOCALAPPDATA%\cell_figure\results\<job_id>\final.png`；macOS/Linux 为 `${XDG_DATA_HOME:-~/.local/share}/cell-figure/results/<job_id>/final.png`。

## 凭据和费用

客户端会读取当前进程的 `XIAOMIAO_API_KEY`、本客户端安全凭据，以及用户真实桌面上的 `Cell_skills.txt`。桌面文件支持 `XIAOMIAO_API_KEY=<你的Key>`、`小描=<你的Key>` 或单行 Key。请勿提交该文件到 Git。

你也可以在当前助手对话中提供自己的 Key，让助手通过 `configure-key` 的标准输入完成配置，无需把 Key 写入脚本或命令参数；聊天本身仍由所用平台保存和管理。

Windows 安全存储使用当前用户的 DPAPI 加密。macOS/Linux 使用权限为 `0600` 的本地私有文件，不是加密钥匙串。客户端源码开源；小描远程服务需要独立 API Key 和额度。客户端以每任务 3 额度进行提交前检查，实际预留与扣费以服务端为准。输入文字与参考文件会发送到 `https://xiaomiao-ai.com`。

## 验证与当前边界

```sh
python -B -X utf8 scripts/test_client.py
python -B -X utf8 scripts/test_worker.py -v
```

测试使用临时数据目录、临时桌面和本地模拟服务，覆盖余额、PNG 领取、基本去重、额度不足、参考数量限制、桌面凭据发现和凭据输出检查。后台测试覆盖重复启动时进程仍存活，以及成功状态别名在下载失败后的恢复，不产生线上费用。模拟测试通过不等同于所有故障场景已验证。

当前版本的后台完成结果保存在本地，不会自动向已结束的 Codex 对话发送消息。重启恢复依赖重新启动 worker 或安装登录启动项。多进程并发提交、提交响应丢失时的端到端幂等保证、worker 单实例锁以及所有异常场景的恢复仍需完善；服务器应提供幂等提交支持后再用于严格计费场景。过期任务不会自动重新提交。

请勿将 API Key、客户科研内容、参考文件、任务数据库或生成结果放入公开 issue。

## 许可证

MIT，见 [LICENSE](LICENSE)。许可证适用于本仓库代码，不授予远程服务额度或第三方材料权利。
