# 评测测试源码

将每段代码复制到对应仓库路径。

## 普通案例

### cachetools #387

路径：`tests/test_issue_387.py`

```python
import unittest.mock
import warnings

from cachetools import Cache
from cachetools import cachedmethod


class Cached:
    cache = Cache(maxsize=1)

    @cachedmethod(lambda self: self.cache)
    def get(self, key):
        return key


def test_cachedmethod_autospec_emits_no_deprecation_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        unittest.mock.create_autospec(Cached, instance=True)

    assert not [
        warning
        for warning in caught
        if issubclass(warning.category, DeprecationWarning)
    ]
```

### itsdangerous #429

路径：`tests/test_itsdangerous/test_issue_429.py`

```python
import pytest

from itsdangerous import SignatureExpired
from itsdangerous import URLSafeSerializer
from itsdangerous import URLSafeTimedSerializer


def test_url_safe_serializer_rejects_max_age() -> None:
    serializer = URLSafeSerializer("SECRET")
    signed = serializer.dumps("value")

    with pytest.raises(
        TypeError,
        match="unexpected keyword argument 'max_age'",
    ):
        serializer.loads(signed, max_age=-1)


def test_url_safe_timed_serializer_still_accepts_max_age() -> None:
    serializer = URLSafeTimedSerializer("SECRET")
    signed = serializer.dumps("value")

    with pytest.raises(SignatureExpired):
        serializer.loads(signed, max_age=-1)
```

### cattrs #761

路径：`tests/test_issue_761.py`

```python
import json
from decimal import Decimal

import attrs
import yaml

from cattrs.preconf.json import make_converter as make_json_converter
from cattrs.preconf.pyyaml import make_converter as make_pyyaml_converter


@attrs.define
class Order:
    amount: Decimal


def test_json_unstructures_decimal_as_string() -> None:
    converter = make_json_converter()
    value = Decimal("1234567890.12345678901234567890")

    serialized = converter.dumps(Order(amount=value))

    assert json.loads(serialized) == {
        "amount": "1234567890.12345678901234567890"
    }


def test_json_structures_decimal_from_string() -> None:
    converter = make_json_converter()

    result = converter.loads(
        '{"amount": "1234567890.12345678901234567890"}',
        Order,
    )

    assert result.amount == Decimal("1234567890.12345678901234567890")
    assert isinstance(result.amount, Decimal)


def test_pyyaml_unstructures_decimal_as_string() -> None:
    converter = make_pyyaml_converter()
    value = Decimal("1234567890.12345678901234567890")

    serialized = converter.dumps(Order(amount=value))

    assert yaml.safe_load(serialized) == {
        "amount": "1234567890.12345678901234567890"
    }


def test_pyyaml_structures_decimal_from_string() -> None:
    converter = make_pyyaml_converter()

    result = converter.loads(
        'amount: "1234567890.12345678901234567890"\n',
        Order,
    )

    assert result.amount == Decimal("1234567890.12345678901234567890")
    assert isinstance(result.amount, Decimal)
```

### attrs #1245

路径：`tests/test_issue_1245.py`

```python
from typing import List

import pytest
from attrs import define
from attrs import field
from attrs.validators import and_
from attrs.validators import deep_iterable
from attrs.validators import instance_of
from attrs.validators import min_len


@define
class Example:
    values: List[str] = field(
        validator=deep_iterable(
            member_validator=[instance_of(str), min_len(1)],
            iterable_validator=and_(instance_of(list), min_len(1)),
        )
    )


def test_inner_validator_error_identifies_list_member() -> None:
    with pytest.raises(ValueError) as exc_info:
        Example(["valid", ""])

    assert "'values[1]'" in str(exc_info.value)
```

### Click #2740

路径：`tests/test_issue_2740.py`

```python
import click
from click.testing import CliRunner


@click.command()
def cli() -> None:
    error = click.ClickException("foo")
    error.add_note("bar")
    raise error


def test_click_exception_displays_exception_notes() -> None:
    result = CliRunner().invoke(cli)

    assert result.exit_code == 1
    assert "Error: foo" in result.output
    assert "bar" in result.output


@click.command()
def cli_without_notes() -> None:
    raise click.ClickException("foo")


def test_click_exception_without_notes_keeps_existing_output() -> None:
    result = CliRunner().invoke(cli_without_notes)

    assert result.exit_code == 1
    assert result.output == "Error: foo\n"
```

### boltons #261

路径：`tests/test_issue_261.py`

```python
from boltons.funcutils import wraps


def target(a: float, b: int = 10) -> float:
    return a * b


def wrapper(a: int, *, b: int = 1) -> int:
    return a * b


def test_wraps_preserves_keyword_only_calling_semantics() -> None:
    wrapped = wraps(target)(wrapper)

    assert wrapped(3) == 30


def test_wraps_accepts_explicit_keyword_value() -> None:
    wrapped = wraps(target)(wrapper)

    assert wrapped(3, b=4) == 12
```

### python-dateutil #1508

路径：`tests/test_issue_1508.py`

```python
import pytest
from dateutil.parser import parse


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-15T12:00:00+0060",
        "2024-01-15T12:00:00-0060",
        "2024-01-15T12:00:00+2400",
        "2024-01-15T12:00:00-2400",
    ],
)
def test_parse_rejects_invalid_timezone_offsets(value: str) -> None:
    with pytest.raises(ValueError):
        parse(value)


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-15T12:00:00+0059",
        "2024-01-15T12:00:00-2359",
    ],
)
def test_parse_keeps_valid_boundary_offsets(value: str) -> None:
    result = parse(value)

    assert result.tzinfo is not None
```

### pytest-rerunfailures #270

路径：`tests/test_issue_270.py`

```python
pytest_plugins = "pytester"


def test_rerun_except_considers_teardown_exception(testdir) -> None:
    testdir.makepyfile(
        """
        import pytest


        class SomeError(Exception):
            pass


        @pytest.fixture
        def resource():
            yield
            raise SomeError("teardown failed")


        def test_failure(resource):
            assert False
        """
    )

    result = testdir.runpytest(
        "--reruns",
        "1",
        "--rerun-except",
        "SomeError",
        "-vv",
    )

    outcomes = result.parseoutcomes()

    assert outcomes.get("rerun", 0) == 0
```

### more-itertools #1204

路径：`tests/test_issue_1204.py`

```python
import more_itertools


def test_duplicates_yields_each_duplicate_once() -> None:
    duplicates = getattr(more_itertools, "duplicates")

    assert list(duplicates([1, 2, 1, 1, 3, 2, 2, 4])) == [1, 2]


def test_duplicates_supports_key() -> None:
    duplicates = getattr(more_itertools, "duplicates")
    values = [5, 11, 24, 35, 23, 42, 11, 56, 19, 18, 27, 27]

    result = list(duplicates(values, key=lambda value: value // 10))

    assert result == [23, 11]


def test_duplicates_is_lazy() -> None:
    duplicates = getattr(more_itertools, "duplicates")
    consumed = []

    def source():
        for value in [1, 2, 1, 3]:
            consumed.append(value)
            yield value

    iterator = duplicates(source())

    assert consumed == []
    assert next(iterator) == 1
    assert consumed == [1, 2, 1]
```

### humanize #214

路径：`tests/test_issue_214.py`

```python
from humanize import natural_list


def test_natural_list_keeps_and_as_default() -> None:
    assert natural_list([1, 2, 3]) == "1, 2 and 3"


def test_natural_list_supports_or_conjunction() -> None:
    assert natural_list([1, 2, 3], conjunction=False) == "1, 2 or 3"


def test_natural_list_or_for_two_items() -> None:
    assert natural_list(
        ["read", "write"],
        conjunction=False,
    ) == "read or write"


def test_natural_list_single_item_is_unchanged() -> None:
    assert natural_list(["only"], conjunction=False) == "only"
```

## 困难案例

### Rich #3299

路径：`tests/test_issue_3299.py`

```python
from __future__ import annotations

from rich.cells import cell_len
from rich.segment import Segment


def test_split_cells_handles_zero_width_text_after_wide_characters() -> None:
    text = "\N{FOX FACE}" * 2 + "\n" * 3
    left, right = Segment._split_cells(Segment(text), 1)

    assert left == Segment(" ")
    assert right == Segment(" " + "\N{FOX FACE}" + "\n" * 3)
    assert cell_len(left.text) == 1
    assert cell_len(right.text) == 3
```

### Click #2614

路径：`tests/test_issue_2614.py`

```python
from __future__ import annotations

from click.core import Command, Option
from click.shell_completion import ShellComplete


def test_shell_completion_does_not_evaluate_callable_default() -> None:
    calls = 0

    def expensive_default() -> str:
        nonlocal calls
        calls += 1
        return "computed"

    cli = Command(
        "cli",
        params=[
            Option(
                ["--value"],
                default=expensive_default,
            )
        ],
    )

    completer = ShellComplete(
        cli,
        {},
        "cli",
        "_CLI_COMPLETE",
    )

    completer.get_completions([], "-")

    assert calls == 0
```

### Werkzeug #3156

路径：`tests/test_issue_3156.py`

```python
from __future__ import annotations

from werkzeug.routing import BaseConverter, Map, Rule


def test_non_matching_rules_do_not_change_relative_priority() -> None:
    rule_1 = Rule("/<dummy:value>", endpoint="rule_1")
    rule_2 = Rule("/<string:value>", endpoint="rule_2")
    url_map = Map([rule_1, rule_2], converters={"dummy": BaseConverter})

    adapter = url_map.bind("example.org", "/")
    assert adapter.match("/foo") == ("rule_1", {"value": "foo"})

    url_map = Map(
        [
            Rule("/<string:value>/no_match", endpoint="no_match"),
            rule_1.empty(),
            rule_2.empty(),
        ],
        converters={"dummy": BaseConverter},
    )
    adapter = url_map.bind("example.org", "/")

    assert adapter.match("/foo") == ("rule_1", {"value": "foo"})


def test_more_specific_rule_wins_across_equal_weight_converters() -> None:
    url_map = Map(
        [
            Rule("/<string:value>/<path:path>", endpoint="less_specific"),
            Rule("/<string:value>/bar", endpoint="more_specific"),
        ]
    )
    adapter = url_map.bind("example.org", "/")
    assert adapter.match("/foo/bar") == (
        "more_specific",
        {"value": "foo"},
    )

    url_map = Map(
        [
            Rule("/<string:value>/<path:path>", endpoint="less_specific"),
            Rule("/<dummy:value>/bar", endpoint="more_specific"),
        ],
        converters={"dummy": BaseConverter},
    )
    adapter = url_map.bind("example.org", "/")

    assert adapter.match("/foo/bar") == (
        "more_specific",
        {"value": "foo"},
    )
```

### Pluggy #681

路径：`testing/test_issue_681.py`

```python
from __future__ import annotations

from io import BytesIO, TextIOWrapper

from pluggy import HookimplMarker, HookspecMarker, PluginManager


hookspec = HookspecMarker("issue681")
hookimpl = HookimplMarker("issue681")


class _HookSpec:
    @hookspec(firstresult=True)
    def process(self, value: str) -> str:
        pass


class _Plugin:
    @hookimpl
    def process(self, value: str) -> str:
        return "\ud800"


def _manager() -> PluginManager:
    manager = PluginManager("issue681")
    manager.add_hookspecs(_HookSpec)
    manager.register(_Plugin())
    return manager


def test_tracing_escapes_surrogate_in_hook_arguments() -> None:
    manager = _manager()
    output = BytesIO()
    writer = TextIOWrapper(output, encoding="ascii")
    manager.trace.root.setwriter(writer.write)
    manager.enable_tracing()

    manager.hook.process(value="\ud800")
    writer.flush()

    assert b"\\ud800" in output.getvalue()


def test_tracing_escapes_surrogate_in_hook_result() -> None:
    manager = _manager()
    output = BytesIO()
    writer = TextIOWrapper(output, encoding="ascii")
    manager.trace.root.setwriter(writer.write)
    manager.enable_tracing()

    manager.hook.process(value="safe")
    writer.flush()

    assert b"\\ud800" in output.getvalue()
```

### Jinja #2069

路径：`tests/test_issue_2069.py`

```python
from __future__ import annotations

from jinja2 import Environment, meta


def test_find_undeclared_variables_ignores_names_set_in_all_if_branches() -> None:
    environment = Environment()
    template = environment.parse(
        """
        {% if control == 'something' %}
            {% set output = 1 %}
        {% elif control == 'something else' %}
            {% set output = 2 %}
        {% else %}
            {% set output = 3 %}
        {% endif %}
        {{ output }}
        """
    )

    assert meta.find_undeclared_variables(template) == {"control"}
```
