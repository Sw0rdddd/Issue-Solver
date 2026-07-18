# issue-solver

`issue-solver` 是一个面向本地 Git 仓库的 Issue 修复工作流：解析 Issue、探索代码、生成修改、Review，并在目标仓库的本地 Python 环境中执行真实测试。

## 前置条件

- Python 3.13+；
- 目标目录是已有提交的 Git 仓库，且工作区干净；
- 目标仓库根目录存在已准备好的 `.venv`、`venv` 或 `.conda`，并已安装 pytest；
- 在本项目根目录 `.env` 配置 `API_KEY`、`BASE_URL` 和 `MODEL_NAME`。

工具不会创建虚拟环境、安装依赖，也不会读取目标仓库的 `.env`。

## 使用

在本项目内显式指定目标仓库：

```powershell
python -m cli.commands run --repo <target-repo> --issue <issue-url-or-text>
```

也可使用本地 Markdown 或文本 Issue，但路径必须是绝对路径：

```powershell
python -m cli.commands run --repo <target-repo> --issue "E:\path\to\ISSUE.md"
```

全局命令仅支持可编辑安装：

```powershell
uv tool install --editable <issue-solver-项目路径>
```

安装后，在目标 Git 仓库或其任意子目录执行：

```powershell
issue-solver run --issue <issue-url-or-text>
```

全局命令会自动识别当前目录所属的 Git 仓库。

## 配置与产物

- 配置文件始终是本项目根目录的 `.env`；
- 本地模块命令的运行日志默认位于 `.issue-solver-runs/<repo>/<run-id>/`；
- 全局命令的运行日志默认位于 `~/.issue-solver/runs/<repo>/<run-id>/`；
- `RUN_ROOT`、`GLOBAL_RUN_ROOT` 可在 `.env` 配置，`--run-root` 可临时覆盖；运行日志不得写入目标仓库。

详细设计见 [架构文档](docs/issue-to-solution-architecture.md) 和 [实施计划](docs/issue-to-solution-implementation-plan.md)。
