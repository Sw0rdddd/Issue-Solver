# search-demo

一个用于演示 issue-solver 的小型商品搜索项目。项目同时提供 Python API
和 `search-demo` 命令行入口。

## 环境准备

```powershell
uv sync
```

项目要求 Python 3.13 或更高版本。开发依赖和精确版本记录在
`pyproject.toml` 与 `uv.lock` 中。

## Python API

```python
from search_demo import search_items

matches = search_items(
    ["Alpha Keyboard", "Beta Mouse"],
    "Alpha",
)
```

`search_items` 返回一个新列表，并保持输入商品名称的拼写和顺序。

## 命令行

```powershell
uv run search-demo Alpha
```

命令会搜索内置商品目录，每行输出一个匹配项；没有匹配项时输出
`未找到结果`。

## 测试

```powershell
uv run pytest -q
```

初始版本故意保留一个大小写匹配缺陷，因此预期结果为 `4 passed, 1 failed`。
修复要求见 `ISSUE.md`。
