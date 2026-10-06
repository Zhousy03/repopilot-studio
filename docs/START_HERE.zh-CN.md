# RepoPilot Studio 使用指南

RepoPilot Studio 把源码浏览、代码问答、修改审查和检查放在一个本地工作台里。导入仓库后，可以直接查找和编辑源码；连接模型后，可以提问、审查代码和生成修改提案。

## 安装和启动

需要 Python 3.11 或更新版本。前端已经包含在项目中，无需安装 Node.js。

下载并解压源码 ZIP，或通过 Git 获取项目：

```bash
git clone https://github.com/Zhousy03/repopilot-studio.git
```

在解压或克隆后的 `repopilot-studio` 目录中运行下面的命令。

Windows PowerShell：

```powershell
cd "你的路径/repopilot-studio"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ai]"
.\.venv\Scripts\python.exe -m uvicorn repopilot.api:app --host 127.0.0.1 --port 8000
```

macOS / Linux：

```bash
cd /你的路径/repopilot-studio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[ai]"
python -m uvicorn repopilot.api:app --host 127.0.0.1 --port 8000
```

打开 **http://127.0.0.1:8000**，保持终端运行。以后可用 `start.bat` 或 `./start.sh` 启动，停止时按 Ctrl+C。

只安装 `pip install -e .` 也可以使用本地检索、编辑提案、语法检查和兼容 HTTP 模型接口。`ai` 扩展提供 LangChain 适配器和原生 LangGraph 检查点。安装开发和浏览器测试依赖时使用 `.[ai,dev,ui-test]`。

## 导入代码

- **GitHub 仓库**：输入 `https://github.com/OWNER/REPOSITORY`，分支或标签单独填写。私有仓库需要先在设置中保存 GitHub Token。
- **本地文件夹**：选择项目目录，将源码上传为工作副本。
- **ZIP 压缩包**：上传源码 ZIP，单一外层目录会被去除。

支持 UTF-8 文本文件，自动过滤密钥文件、二进制、依赖目录、Git 元数据及忽略文件。默认最多 3,000 个文件、单文件 1 MB、ZIP 32 MB、源码总量 64 MB。大型项目可以先导入相关子目录。

导入的工作副本与原目录分开保存。应用提案后，需要导出源码、Patch 或创建草稿 PR，把结果用于原项目。

## 连接模型

打开“模型与运行设置”，填入服务商提供的 Base URL、模型名称和 API Key。支持 OpenAI 兼容的聊天接口，例如 Base URL 以 `/v1` 结尾的服务。

点击“测试连接”会使用**当前填写的内容**发送一条固定问候，不会上传仓库源码，也不会保存设置。Key 留空时复用已保存的 Key。连接成功后，选择“AI 代码助手”、允许发送相关源码，然后点击“保存设置”。

AI 任务发送问题、选中的源码片段和参考资料给配置的服务。Key 保存在本机数据目录的明文文件中，由系统文件权限保护；输入框不会回显已保存的 Key。可以在设置中清除所有保存的凭据。

连接方式默认自动选择。有兼容问题时，可以尝试“兼容 HTTP 接口”。“原生工具调用”需要模型服务支持；“独立分析与复核”会增加请求量。远端 HTTP 请求使用运行环境中的代理和证书配置，本机 HTTP 模型直接连接。

语义检索另外需要 Embedding 服务。填写向量模型后，在工作区点击“语义索引”。没有向量索引时使用关键词检索；改变向量服务或模型后应重建索引。

## 问答和审查

本地模式输入函数名、路径或英文关键词，可以查看匹配源码。AI 模式可以选择：

- **代码问答**：解释实现和调用关系，例如“登录请求在哪里校验 Token？”
- **代码审查**：指定文件和关注点，例如“审查 api.py 的参数校验和错误处理”。
- **提出修改**：描述预期行为和范围，例如“修复 calc.py 的 add 函数，只修改运算符”。

回答中的 `[E1]` 等编号可以打开对应源码。引用检查只确认编号存在，具体结论仍需要对照代码。任务进度可以展开，运行详情和报告也可导出。

“参考链接”接受 GitHub Issue 和支持的文档站点，每行一个、最多四个。设置旁会显示支持的站点。任务有轮数、调用次数和时间限制，可在高级设置中调整。

“新建任务”开始新的会话。同一会话的连续问题会使用同版本下最近几次完成的问答。停止按钮取消当前任务；关闭页面只断开观看连接，任务仍会继续。历史记录中可以重新打开结果。

手机上使用顶部文件夹按钮打开文件列表和工作区切换。点击“查看引用”可以打开源码侧栏，点击遮罩或按 Escape 收起。

## 修改和检查

打开文件后点击“编辑文件”，修改完整内容并提交。也可以在“修改提案”页新建文件，或提出删除某个文件。

提案会显示差异和语法检查结果。核对差异后勾选确认，再点击“批准并应用”，保存为新版本。若基线版本已经改变，需要重新生成提案。已应用的提案可以生成撤销提案、导出 Patch 或检查该版本。

“语法检查”解析 Python、JSON 和 TOML，无需 Docker。它不会执行项目代码，也不检查业务行为。

运行 pytest、unittest 或 Node.js 测试需要 Docker 和预先准备的测试镜像。先构建 Python 镜像：

```bash
docker build -t repopilot-sandbox:1 -f sandbox/Dockerfile .
```

在设置中启用 Docker 测试，选择正确镜像。每次运行需确认执行所选版本的代码。测试容器无网络，项目依赖应预先安装在可信镜像中。Node.js 和依赖准备详见 [TESTING.md](TESTING.md)。

## 导出与 GitHub

左侧“导出源码”下载当前版本，“导出 Patch”下载对应提案的补丁。回答也可以导出 Markdown 报告。

GitHub 工作区中，应用提案后可以创建草稿 PR。确认目标仓库、基线、分支和差异后提交；Token 需要目标仓库的 Contents 和 Pull requests 写权限。远端基线发生变化时会拒绝发布，需要重新导入并审查。发布可能触发仓库 CI。

## 数据和发布

运行数据默认保存在 `.repopilot/`。使用环境变量 `STUDIO_DATA_DIR` 可以改变位置。数据库、设置、虚拟环境和缓存都不属于发布源码。

修改源码后，在开发环境完成检查并生成发布包：

```bash
python -m pytest -q
python scripts/check_ui.py
python scripts/verify_release.py --update --archive ../repopilot-studio-source.zip
```

发布脚本过滤本机产物、检查常见凭据格式，并生成文件哈希清单。测试在临时目录里创建文件，保留 `tests/` 可以让其他人验证项目。

本项目面向单人本地使用，保持监听 `127.0.0.1`。容器测试执行仓库代码，对不可信仓库应使用可丢弃的专用环境。进一步的运行边界见 [SECURITY.md](../SECURITY.md)，本次验证范围见 [VALIDATION.md](VALIDATION.md)。
