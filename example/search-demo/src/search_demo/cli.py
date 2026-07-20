import argparse
from collections.abc import Sequence

from search_demo.search import search_items


CATALOG = (
    "Alpha Keyboard",
    "Beta Mouse",
    "Gamma Monitor",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search-demo",
        description="搜索内置商品目录。",
    )
    parser.add_argument("query", help="要搜索的商品名称片段。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matches = search_items(CATALOG, args.query)
    if matches:
        print("\n".join(matches))
    else:
        print("未找到结果")
    return 0
