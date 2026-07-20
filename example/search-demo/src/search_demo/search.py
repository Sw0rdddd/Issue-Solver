from collections.abc import Iterable


def search_items(items: Iterable[str], query: str) -> list[str]:
    """返回名称中包含查询文本的商品，并保持输入顺序。"""

    return [item for item in items if query in item]
