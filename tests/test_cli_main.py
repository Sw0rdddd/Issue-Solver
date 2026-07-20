from unittest.mock import Mock

import pytest

from cli import main as main_module


def test_global_main_uses_global_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    main = Mock(return_value=0)
    monkeypatch.setattr(main_module, "main", main)

    assert main_module.global_main() == 0
    main.assert_called_once_with(global_mode=True)
