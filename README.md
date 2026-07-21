# issue-solver

`issue-solver` 是一个面向本地 Git 仓库的 Issue 修复工作流：解析 Issue、探索代码、生成修改、Review，并在目标仓库的本地 Python 环境中执行真实测试。

## 前置条件

- Python 3.13+；
- 目标目录是已有提交的 Git 仓库，且工作区干净；
- 开发者已在目标仓库根目录准备好唯一的 `.venv`、`venv` 或 `.conda`，并安装目标项目、pytest 及全部测试依赖；
- 在本项目根目录 `.env` 配置 OpenAI Chat Completions 兼容服务的 `API_KEY`、`BASE_URL` 和 `MODEL_NAME`。

工具不会创建虚拟环境、安装依赖，也不会读取目标仓库的 `.env`。即使目标仓库存在 `tox.ini`，工具也不会调用 tox 或使用其环境矩阵，而是始终在上述已准备好的环境中直接执行 `python -m pytest`。仅能通过 tox 完成环境准备或测试编排的项目暂不支持。

## 准备本项目环境

安装 [uv](https://docs.astral.sh/uv/) 后，在 `issue-solver` 根目录执行：

```powershell
uv sync
Copy-Item .env.example .env
uv run pytest -q
```

`uv sync` 会创建本项目的 `.venv` 并安装锁定依赖。随后在 `.env` 中填写 `API_KEY`、`BASE_URL` 和 `MODEL_NAME`；最后一条命令用于确认本项目环境可用。

## 准备目标仓库环境

以下以 PowerShell 和 `.venv` 为例：

```powershell
cd <target-repo>
python -m venv .venv

# 根据目标仓库的说明安装项目及测试依赖，以下命令二选一
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

# 确认 pytest 可由该环境直接运行
.\.venv\Scripts\python.exe -m pytest --version
```

依赖文件和 extras 名称以目标仓库为准。还需将 `.venv/` 加入目标仓库的 `.gitignore`，并用 `git check-ignore .venv` 确认它已被忽略。仓库根目录只能保留一个受支持的环境目录。

启动前还要确保目标仓库工作区干净：

```powershell
git -C <target-repo> status --short
```

该命令应无输出。工具会把启动时目标仓库的当前 `HEAD` 记录为 `base_commit`，后续变更校验、回滚和最终 Patch 都以此提交为基线。已有修改应由开发者提前提交或自行清理，工具不会覆盖或代为提交这些改动。

## 使用

在本项目内显式指定目标仓库：

```powershell
python -m cli.main run --repo <target-repo> --issue <issue-url-or-text>
```

也可使用本地 Markdown 或文本 Issue，但路径必须是绝对路径：

```powershell
python -m cli.main run --repo <target-repo> --issue "E:\path\to\ISSUE.md"
```

全局命令仅支持可编辑安装：

```powershell
uv tool install --editable <issue-solver-项目路径>
```

可编辑安装会记录项目的绝对路径。移动或重命名 `issue-solver` 项目目录后，需要在新目录重新执行：

```powershell
uv sync --reinstall
uv tool install --force --reinstall --editable <新的 issue-solver 项目路径>
```

安装后，在目标 Git 仓库或其任意子目录执行：

```powershell
issue-solver run --issue <issue-url-or-text>
```

全局命令会自动识别当前目录所属的 Git 仓库。

## 配置与产物

- 配置文件始终是本项目根目录的 `.env`；
- 模型接口使用 OpenAI Chat Completions 格式；程序优先根据 `MODEL_NAME`、其次根据 `BASE_URL` 自动识别供应商；
- `REASONING_HISTORY=auto` 会为 DeepSeek、GLM、Kimi 和 MiMo 回填多轮工具调用所需的 `reasoning_content`，OpenAI、Gemini、Qwen 和未知服务默认不回填；必要时可设为 `true` 或 `false` 强制覆盖，Qwen 设为 `true` 时也会发送 `preserve_thinking=true`；
- 两种命令的运行记录默认都位于 `.issue-solver-runs/<repo>/<run-id>/`；
- 详细 JSON、audit、测试输出和运行时目录统一保存在 `run-id/logs/`；
- `report.md` 与最终 `diff.patch`、`diff.json` 保留在 `run-id/` 根部；模型总结不可用时自动使用程序模板；
- 失败统一使用 `FailureInfo` 输出 `type`、`message`、`suggestion`；CLI、报告、JSON 产物和 Agent 工具反馈使用同一组错误类型；
- 运行失败且存在修改时，交互终端默认询问是否回滚；非交互运行默认保留，安全违规和 Coding 中途失败仍自动回滚；
- `RUN_ROOT` 可在 `.env` 配置，`--run-root` 可临时覆盖；运行日志不得写入目标仓库。

完整的架构、运行逻辑、核心数据结构与设计取舍见[项目说明](docs/project-overview.md)。
