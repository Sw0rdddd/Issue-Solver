# issue-solver

`issue-solver` 是一个面向本地 Git 仓库的 Issue 修复工作流：解析 Issue、探索代码、生成修改、Review，并在目标仓库的本地 Python 环境中执行真实测试。

## 前置条件

- Python 3.13+；
- 目标目录是已有提交的 Git 仓库，且工作区干净；
- 开发者已在目标仓库根目录准备好唯一的 `.venv`、`venv` 或 `.conda`，并安装目标项目、pytest 及全部测试依赖；
- 在本项目根目录 `.env` 配置 `API_KEY`、`BASE_URL` 和 `MODEL_NAME`。

工具不会创建虚拟环境、安装依赖，也不会读取目标仓库的 `.env`。即使目标仓库存在 `tox.ini`，工具也不会调用 tox 或使用其环境矩阵，而是始终在上述已准备好的环境中直接执行 `python -m pytest`。仅能通过 tox 完成环境准备或测试编排的项目暂不支持。

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
- 两种命令的运行日志默认都位于 `.issue-solver-runs/<repo>/<run-id>/`；
- `RUN_ROOT` 可在 `.env` 配置，`--run-root` 可临时覆盖；运行日志不得写入目标仓库。

完整的架构、运行逻辑、核心数据结构与设计取舍见[项目说明](docs/project-overview.md)。
