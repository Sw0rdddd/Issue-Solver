# issue-solver 示例仓库

`search-demo/` 是一个可提交、可复制的完整 Python 项目模板，用于验证
issue-solver 的 Explore、Code、Review 和 Test 流程。

模板故意保留了一个大小写搜索缺陷。初始化后运行测试时，应看到
`4 passed, 1 failed`；失败用例正是需要 issue-solver 修复的目标。

## 1. 初始化独立 Git 仓库

在 issue-solver 项目根目录执行：

```powershell
git -C .\example\search-demo init -b main
uv sync --project .\example\search-demo
git -C .\example\search-demo add .
git -C .\example\search-demo commit -m "初始化搜索示例仓库"
```

`.venv/` 已被示例仓库自己的 `.gitignore` 忽略，因此初始化提交后工作区
应保持干净：

```powershell
git -C .\example\search-demo status --short
git -C .\example\search-demo check-ignore .venv
```

第一条命令应无输出，第二条命令应输出 `.venv`。

## 2. 确认预期失败

```powershell
.\example\search-demo\.venv\Scripts\python.exe -m pytest -q .\example\search-demo\tests
```

预期只有 `tests/test_search.py::test_search_ignores_case` 失败，其余测试
通过。这个失败是示例的初始状态，不是环境安装错误。

## 3. 运行 issue-solver

仍在 issue-solver 项目根目录执行：

```powershell
uv run python -m cli.main run --repo .\example\search-demo --issue (Resolve-Path .\example\search-demo\ISSUE.md).Path
```

运行完成后，在示例仓库中检查修改与测试：

```powershell
git -C .\example\search-demo diff
.\example\search-demo\.venv\Scripts\python.exe -m pytest -q .\example\search-demo\tests
```

## 4. 恢复示例

需要重新测试时，可丢弃 issue-solver 在示例仓库中产生的修改：

```powershell
git -C .\example\search-demo reset --hard HEAD
```

该命令会删除示例仓库中的未提交代码修改，请先确认不需要保留它们。
