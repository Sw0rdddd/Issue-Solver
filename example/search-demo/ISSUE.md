# 搜索功能应忽略大小写

## 问题

当前商品搜索会区分大小写。例如，目录中存在 `Alpha Keyboard` 时，使用
`alpha` 或 `ALPHA` 搜索无法得到结果。

## 期望行为

- 查询文本和商品名称的大小写不应影响匹配结果。
- 返回结果必须保留商品名称的原始拼写。
- 多个结果的顺序必须与输入目录一致。
- 查询没有匹配项时仍返回空列表。

## 验收标准

- `tests/test_search.py::test_search_ignores_case` 通过。
- 示例仓库的全部 pytest 测试通过。
- 不改变公开函数 `search_items(items, query)` 和 CLI 的调用方式。
