# 秋招助手 · 本机版

Windows 本地秋招邮件待办工具：网易 IMAP 增量读取、CodeBuddy CN 判断、SQLite 存储，提供日历、邮件明细、独立步骤及手动日程。无需腾讯文档或前端构建工具。

> 当前是公开发布准备版。安装限制和发布前事项见 [公开发布检查](docs/PUBLIC_RELEASE_CHECK.md)。

## 全新安装

需要 Windows 10/11、Python 3.10 或更新版本，以及已安装并登录的 CodeBuddy CN CLI。后端只使用 Python 标准库；Node.js 仅用于运行前端测试。CLI 账户的模型权限和费用由服务商决定，本工具不购买权限。

1. 将源码放入独立文件夹，在该文件夹打开 PowerShell。
2. 创建环境并复制配置示例：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json private-config.json
```

`requirements.txt` 当前仅含说明：运行与测试均使用标准库，无第三方 Python 包。不要从通用环境导出全部已安装包作为本项目依赖。也可用独立 Conda 环境代替 `.venv`：

```powershell
conda create -n autumn-recruit python=3.13.12 pip
conda run -n autumn-recruit python -m pip install -r requirements.txt
```

Conda 环境建好后，将该环境的 `pythonw.exe` 路径写入本机 `private/python-path.txt`。已有定时任务的解释器需要另行迁移；只修改启动配置不会修改任务。

3. 编辑 `private-config.json`，将 `codebuddy_cli` 改成自己电脑上实际的可执行文件路径。JSON 路径使用 `/` 或双反斜杠。首选模型默认 `deepseek-v4.1-flash`，失败后允许 `auto` 一次，轻度思考。配置中不要填写邮箱授权码或其他令牌。
4. 在网易邮箱网页开启 IMAP，生成客户端授权码，然后执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/setup_local.py --config private-config.json
```

按提示输入邮箱与授权码，授权码不回显，不放入命令行参数或 Git。支持 `163.com`、`126.com`、`yeah.net`。初始化创建空数据库、当前 Windows 用户绑定的 DPAPI 凭证及本机配置；不会连接邮箱、调用模型或安装定时任务。已有运行目录时拒绝覆盖。中途失败留下文件时，先在全新安装目录检查原因，不要删除已有使用中的数据。

5. 启动页面：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m app.server
```

打开 <http://127.0.0.1:18765/>。需要时可加 `--no-open`，避免自动打开浏览器；启动页面不会自动扫描。端口被同一工具占用时会复用现有服务，因此请勿同时运行多个副本。

首次点击“扫描邮件”读取最近 30 天，之后按本机游标增量读取并留有回查窗口，以邮件标识去重；已读邮件仍会处理。只将需要本人操作的邮件写入台账。扫描会把候选邮件正文和相关旧事项交给 CodeBuddy 服务判断，模型输入、输出等审计也保留在本机，使用前应理解这些邮件数据会交给该服务处理。页面中脱敏显示不代表提交给模型的数据已全部脱敏。

## 使用

- 日历默认隐藏已完成；开始、截止分别显示，按北京时间排序。
- “月历 / 列表”手动切换，记住当前浏览器的选择；缩放或改变窗口宽度不会切换模式。窄屏月历可横向滚动。
- 月历按周连续排列，上下滚动自动加载前后日期并更新月份标题；“今天”回到当前周。刷新状态或勾选后保留滚动位置。
- “新建日程”支持标题、开始/截止时间、备注；点击手动日程可编辑、删除或勾选完成。删除可立即撤销。手动日程与邮件节点一起展示，单独存储，不写入邮件明细。
- 点击日历事项直接打开本机邮件详情，可以勾选步骤和修改备注。
- 勾选邮件完成全部步骤；取消邮件完成恢复全部步骤。单独完成一步只影响该步，全部完成后邮件自动完成。多个页面即时更新。
- 邮件、步骤按接收时间倒序。测评、笔试、AI 面试分别分类和着色。
- 邮件明细支持上一页/下一页，每页可选 10、20、50、100 条，记住当前浏览器的条数选择；搜索或修改筛选后回到第一页。
- 日历的“列表”按完整日期汇总全部日程，按时间先后上下滚动，不受月历所在月份限制；“今天”定位当前或下一个有日程的日期。
- 原邮件正文以脱敏纯文本查看，不执行其中指令、不打开考试链接。
- 已过滤邮件和模型输入、输出、实际模型、纠错、存储结果可在页面查看。

## 运行与数据

设置页提供“更新授权码”和“切换邮箱”，保存前只验证 IMAP 登录，不扫描邮件或调用模型。更新授权码保持当前邮箱和台账；新邮箱建立独立空台账，下次扫描首次回查最近 30 天。已有邮箱可从列表选回，授权码留空则沿用已保存凭证，原记录、完成状态、备注及手动日程随台账恢复。验证失败保持原邮箱，扫描或保存占锁时拒绝切换。

旧台账仍保留在 `private/runtime/`；新增邮箱的数据库、DPAPI 凭证和模型审计分别存储在 `private/runtime/accounts/<邮箱摘要>/`，`active-account.json` 保存当前选择。手动与自动扫描共用此选择，不改变定时频率、重试规则或触发时间；模型与定时参数为项目共用。邮箱地址可在本机设置页查看，授权码不回显、不记录到运行日志。切换邮箱不是删除邮箱，备份时应保留整个运行目录。

- 页面关闭后空闲 2 分钟自动退出，扫描期间不退出。下次重新启动即可。
- 左侧底部提供“退出助手”和“重启服务”：空闲时立即执行，重启后页面自动刷新。扫描、保存进行中会提示稍后重试；本页有未保存改动时须先保存或取消编辑。退出页面服务不会停用独立的定时检查任务。
- 首次更新到支持这两个按钮的版本时，旧服务仍需关闭所有助手页面、等待自动退出后再打开一次；此后可直接使用按钮。
- 数据库 `private/runtime/ledger.sqlite3` 保存邮件、步骤完成、备注和手动日程。模型配置为 `local-gate.json`，加密凭证为 `credentials.dpapi`，审计在 `local-gate/audits/`，日志为 `local-gate.log`。
- DPAPI 仅保护当前用户绑定的加密内容，并非整个数据库和审计目录全盘加密。备注、提取后的待办及审计可能含个人信息，请保护整个 `private/`；凭证不能直接搬到另一 Windows 用户下使用。
- 邮箱只读，不加星、不改已读、不发送邮件或代为确认。无通知、声音或弹窗。格式不合规最多一次自动纠错，失败保留候选重试。
- `private/`、数据库、凭证、邮件及日志不进入 Git；提交前仍需检查历史。页面服务只绑定本机回环地址，不要映射到公网。
- 备份应在扫描与页面服务停止后复制完整 `private/`，避免遗漏 SQLite WAL；不要以 Git 代替数据备份。

## 定时扫描的当前限制

全新安装默认不创建计划任务，可以手动扫描。`tools/scheduled_task.ps1` 的 Install 模式仍依赖旧 Windows 任务作为迁移模板，**不是通用安装器，新用户不要执行 Install 模式**。已有部署的设置页可修改检查间隔、登录延迟和失败重试并回读验证。手动扫描不重置固定计划，关闭页面不影响独立的后台计划任务。

双击 `打开本机秋招助手.vbs` 可无控制台启动页面。Python 解释器依次从以下位置选择：

1. `private/python-path.txt`：一行 `pythonw.exe` 路径，相对路径以项目目录为基准。可复制根目录 `python-path.example.txt`，不需要 JSON 或引号。
2. 环境变量 `AUTUMN_PYTHON`。
3. 项目 `.venv/Scripts/pythonw.exe`。
4. 上次已确认的启动解释器缓存 `private/python-launch-cache.txt`；路径失效时自动重新查找。
5. 属于当前项目、入口匹配的现有自动任务解释器，以兼容旧部署。
6. PATH 中的 `pythonw.exe`，或 `python.exe` 同目录的 `pythonw.exe`；不使用 Windows Store 占位入口。

双击入口会直接使用有效的显式配置、环境变量、项目虚拟环境或启动缓存，跳过 PowerShell。首次无可用路径时才通过原解析流程查找，并生成本机缓存；显式配置无效仍会停止并记录错误。缓存不会修改现有自动任务。

也接受 `python.exe` 路径，但同目录必须存在 `pythonw.exe`。显式配置无效会停止，不会悄悄换环境。启动失败记入 `private/launcher.log`，不弹窗。已有自动任务维护时优先沿用任务当前解释器，保留触发时间、重试和运行入口；修改页面启动配置不会自动迁移已注册任务的 Python。Status 模式只读任务，不依赖 Python 路径解析。

## 开发与检查

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
node tests/test_pagination.js
node tests/test_settings.js
.\.venv\Scripts\python.exe -X utf8 tools/audit_public_history.py
```

测试使用临时数据，不读取真实邮箱、不调用模型。结构：`app/` 为页面与服务，`backend/mail/` 为邮件与步骤，`backend/models/` 为 CLI 校验和审计，`backend/storage/` 为存储，`backend/jobs/` 为扫描任务。没有第三方 Python/前端运行依赖。

## 许可证

本项目采用 [MIT License](LICENSE)，版权署名为 `Copyright (c) 2026 yiwan`。使用、修改或再分发时，请保留版权和许可声明。
