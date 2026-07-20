from search_demo import search_items


ITEMS = [
    "Alpha Keyboard",
    "Beta Mouse",
    "Gamma Monitor",
]


def test_search_returns_matching_items() -> None:
    assert search_items(ITEMS, "Beta") == ["Beta Mouse"]


def test_search_preserves_input_order() -> None:
    assert search_items(ITEMS, "a") == ITEMS


def test_search_returns_empty_list_without_matches() -> None:
    assert search_items(ITEMS, "Printer") == []


def test_search_ignores_case() -> None:
    assert search_items(ITEMS, "ALPHA") == ["Alpha Keyboard"]
