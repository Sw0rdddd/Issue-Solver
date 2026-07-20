import io
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli import main as main_module
from cli import run as run_module
from cli.terminal import TerminalReporter
from config import Setting
from schemas.environment_info import EnvironmentInfo


def prepare_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Mock, Mock]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    compiled_graph = Mock()
    graph_builder = Mock()
    graph_builder.compile.return_value = compiled_graph
    model_constructor = Mock(return_value=object())
    environment = EnvironmentInfo(
        kind="VENV",
        root_path=str(repo_root / ".venv"),
        python_executable=str(repo_root / ".venv" / "Scripts" / "python.exe"),
        pytest_version="pytest 9.1.1",
        source=".venv",
    )

    monkeypatch.setattr(run_module, "CONTROLLER_ROOT", tmp_path)
    monkeypatch.setattr(run_module, "find_repo_root", lambda path: repo_root)
    monkeypatch.setattr(run_module, "create_run_id", lambda: "run_test")
    monkeypatch.setattr(
        run_module,
        "_preflight_environment",
        lambda repo, run_dir: environment,
    )
    monkeypatch.setattr(
        run_module,
        "ReasoningChatDeepSeek",
        model_constructor,
    )
    monkeypatch.setattr(
        run_module,
        "build_graph",
        Mock(return_value=graph_builder),
    )

    return repo_root, compiled_graph, model_constructor


def configure_success_stream(
    compiled_graph: Mock,
    captured_state: dict[str, object],
) -> None:
    def stream(state: dict[str, object], *, stream_mode: list[str]):
        captured_state.update(state)
        assert stream_mode == ["updates", "values"]

        yield "values", state
        yield "updates", {
            "initialize": {
                "phase": "PARSE_ISSUE",
                "project_type": "python",
                "test_commands": ["pytest -q"],
            }
        }
        yield "updates", {
            "parse_issue": {
                "phase": "COORDINATE",
                "issue": SimpleNamespace(title="搜索忽略大小写"),
            }
        }
        yield "updates", {
            "coordinator": {
                "phase": "EXPLORE",
                "next_action": "EXPLORE",
                "explore_focuses": ["定位入口", "定位测试"],
                "repair_round": 1,
                "explore_stage_call": 1,
            }
        }
        yield "updates", {
            "explore": {
                "explore_reports": [SimpleNamespace(focus="定位入口")]
            }
        }
        yield "updates", {
            "explore": {
                "explore_reports": [SimpleNamespace(focus="定位测试")]
            }
        }
        yield "updates", {
            "coordinator": {
                "phase": "CODE",
                "next_action": "CODE",
                "coding_task": SimpleNamespace(objective="修复搜索逻辑"),
                "repair_round": 1,
                "coding_stage_call": 1,
            }
        }
        yield "updates", {
            "coding": {
                "phase": "REVIEW",
                "coding_result": SimpleNamespace(summary="搜索已忽略大小写"),
                "changed_files": ["src/search.py", "tests/test_search.py"],
                "coding_iteration": 2,
                "repair_round": 1,
                "coding_stage_call": 1,
            }
        }
        yield "values", {
            **state,
            "phase": "REVIEW",
            "next_action": "CODE",
        }

    compiled_graph.stream.side_effect = stream


def configured_run_dir(controller_root: Path, repo_root: Path) -> Path:
    run_root = run_module._resolve_run_root(
        repo_root,
        Setting().RUN_ROOT,
        controller_root,
    )
    return run_root / repo_root.name / "run_test"


def summary_value(rendered: str, label: str) -> str:
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(label):
            continue
        value = line[len(label) :].lstrip()
        for following in lines[index + 1 :]:
            if not following.startswith(" " * 10):
                break
            value += following[10:]
        return value
    raise AssertionError(f"未找到摘要字段：{label}")


def test_controller_root_matches_repository_root() -> None:
    assert run_module.CONTROLLER_ROOT == Path(__file__).parents[1]


def test_run_rejects_missing_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main_module.main(
        [
            "run",
            "--repo",
            str(tmp_path / "missing"),
            "--issue",
            "修复查询失败",
        ]
    )

    assert exit_code == 1
    assert "仓库路径不存在或不是目录" in capsys.readouterr().err


def test_global_run_requires_editable_installation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(run_module, "CONTROLLER_ROOT", tmp_path)

    exit_code = main_module.main(
        ["run", "--issue", "修复查询失败"],
        global_mode=True,
    )

    assert exit_code == 1
    assert "仅支持可编辑安装" in capsys.readouterr().err


def test_global_run_detects_current_git_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    nested = repo_root / "src" / "package"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    captured_state: dict[str, object] = {}
    configure_success_stream(compiled_graph, captured_state)

    exit_code = main_module.main(
        ["run", "--issue", "修复查询失败"],
        global_mode=True,
    )

    assert exit_code == 0
    assert captured_state["repo_path"] == str(repo_root)
    assert captured_state["run_dir"] == str(
        configured_run_dir(tmp_path, repo_root)
    )


def test_environment_failure_stops_before_model_even_when_quiet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, _, model_constructor = prepare_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        run_module,
        "_preflight_environment",
        Mock(side_effect=RuntimeError("未发现 .venv")),
    )

    exit_code = main_module.main(
        [
            "run",
            "--repo",
            str(repo_root),
            "--issue",
            "修复问题",
            "--quiet",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "环境预检失败" in captured.err
    assert "未调用 LLM" in captured.err
    assert "总 Token" in captured.out
    assert "0" in captured.out
    model_constructor.assert_not_called()
    failure = (
        configured_run_dir(tmp_path, repo_root) / "failure_environment.json"
    )
    assert failure.is_file()


def test_run_root_must_be_outside_target_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="目标 Git 仓库之外"):
        run_module._resolve_run_root(repo_root, repo_root / ".runs")


def test_relative_run_root_resolves_from_controller_root(tmp_path: Path) -> None:
    controller_root = tmp_path / "controller"
    repo_root = tmp_path / "external" / "repo"
    controller_root.mkdir()
    repo_root.mkdir(parents=True)

    result = run_module._resolve_run_root(
        repo_root,
        ".issue-solver-runs",
        controller_root,
    )

    assert result == controller_root / ".issue-solver-runs"


def test_rejects_controller_repository_as_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "controller"
    repo_root.mkdir()
    monkeypatch.setattr(run_module, "CONTROLLER_ROOT", repo_root)
    monkeypatch.setattr(run_module, "find_repo_root", lambda path: repo_root)
    preflight = Mock(side_effect=AssertionError("不应预检控制程序仓库"))
    monkeypatch.setattr(run_module, "_preflight_environment", preflight)

    exit_code = main_module.main(
        ["run", "--repo", str(repo_root), "--issue", "修复问题"]
    )

    assert exit_code == 1
    assert "禁止将 issue-solver" in capsys.readouterr().err
    preflight.assert_not_called()


def test_run_streams_graph_with_initial_state_and_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, model_constructor = prepare_runtime(
        monkeypatch,
        tmp_path,
    )
    captured_state: dict[str, object] = {}
    configure_success_stream(compiled_graph, captured_state)
    usage_callback = SimpleNamespace(
        usage_metadata={
            "test-model": {"total_tokens": 120},
            "review-model": {"total_tokens": 30},
        }
    )
    monkeypatch.setattr(
        main_module,
        "get_usage_metadata_callback",
        lambda: nullcontext(usage_callback),
    )

    exit_code = main_module.main(
        [
            "run",
            "--repo",
            str(repo_root),
            "--issue",
            "修复查询失败",
            "--model",
            "test-model",
            "--max-cycles",
            "5",
        ]
    )

    setting = Setting()
    run_dir = configured_run_dir(tmp_path, repo_root)
    assert exit_code == 0
    assert run_dir.is_dir()
    assert captured_state == {
        "run_id": "run_test",
        "phase": "INITIALIZE",
        "status": "RUNNING",
        "cycle": 0,
        "repo_path": str(repo_root),
        "run_dir": str(run_dir),
        "issue_input": "修复查询失败",
        "max_cycles": 5,
        "test_timeout": setting.TEST_TIMEOUT,
        "test_tail_lines": setting.TEST_TAIL_LINES,
        "environment": EnvironmentInfo(
            kind="VENV",
            root_path=str(repo_root / ".venv"),
            python_executable=str(repo_root / ".venv" / "Scripts" / "python.exe"),
            pytest_version="pytest 9.1.1",
            source=".venv",
        ),
        "explore_reports": [],
        "explore_errors": [],
        "test_results": [],
        "latest_test_results": [],
        "explore_stage_call": 0,
        "coding_stage_call": 0,
    }
    assert model_constructor.call_args.kwargs["model"] == "test-model"
    compiled_graph.invoke.assert_not_called()

    output = capsys.readouterr().out
    assert output.splitlines()[0] == ""
    assert output.splitlines()[1] == "issue-solver · test-model · run_test"
    assert "✓ 环境预检" in output
    assert "✓ 初始化仓库" in output
    assert "搜索忽略大小写" in output
    assert output.count("◆ 修复轮次 r01") == 1
    assert "✓ EXPLORE" in output
    assert "✓ i01/02  定位入口" in output
    assert "✓ i02/02  定位测试" in output
    assert "✓ CODE" in output
    assert "✓ Coding s01 完成" in output
    assert "搜索已忽略大小写" in output
    assert "src/search.py" in output
    assert "tests/test_search.py" in output
    assert "运行摘要" in output
    assert "run_test" in output
    assert "REVIEW" in output
    assert summary_value(output, "运行目录") == str(run_dir)
    assert "150" in output
    assert "最终耗时" in output


def test_quiet_hides_progress_but_keeps_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    configure_success_stream(compiled_graph, {})

    exit_code = main_module.main(
        [
            "run",
            "--repo",
            str(repo_root),
            "--issue",
            "安静运行",
            "--quiet",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "issue-solver ·" not in output
    assert "✓" not in output
    assert "◆" not in output
    assert "→" not in output
    assert "运行摘要" in output
    assert "run_test" in output
    assert "REVIEW" in output
    assert "总 Token" in output
    assert "最终耗时" in output


def test_cli_test_and_run_root_options_override_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    captured_state: dict[str, object] = {}
    configure_success_stream(compiled_graph, captured_state)
    custom_root = tmp_path / "custom-runs"

    exit_code = main_module.main(
        [
            "run",
            "--repo",
            str(repo_root),
            "--issue",
            "配置覆盖",
            "--test-timeout",
            "12.5",
            "--test-tail-lines",
            "24",
            "--run-root",
            str(custom_root),
        ]
    )

    assert exit_code == 0
    assert captured_state["test_timeout"] == 12.5
    assert captured_state["test_tail_lines"] == 24
    assert captured_state["run_dir"] == str(custom_root / "repo" / "run_test")


def test_run_displays_repeated_explore_rounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)

    def stream(state: dict[str, object], *, stream_mode: list[str]):
        yield "updates", {
            "coordinator": {
                "next_action": "EXPLORE",
                "explore_focuses": ["入口", "测试"],
                "repair_round": 1,
                "explore_stage_call": 1,
            }
        }
        yield "updates", {
            "explore": {"explore_reports": [SimpleNamespace(focus="入口")]}
        }
        yield "updates", {
            "explore": {"explore_reports": [SimpleNamespace(focus="测试")]}
        }
        yield "updates", {
            "coordinator": {
                "next_action": "EXPLORE",
                "explore_focuses": ["补充根因"],
                "repair_round": 1,
                "explore_stage_call": 2,
            }
        }
        yield "updates", {
            "explore": {
                "explore_reports": [SimpleNamespace(focus="补充根因")]
            }
        }
        yield "values", {**state, "phase": "CODE", "next_action": "CODE"}

    compiled_graph.stream.side_effect = stream

    exit_code = main_module.main(
        ["run", "--repo", str(repo_root), "--issue", "重复探索"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert output.count("◆ 修复轮次 r01") == 1
    assert "→ Explore s01" in output
    assert "✓ i02/02  测试" in output
    assert "→ Explore s02" in output
    assert "✓ i01/01  补充根因" in output


def test_run_returns_one_when_graph_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)

    def stream(state: dict[str, object], *, stream_mode: list[str]):
        failure = {**state, "status": "FAILED", "error": "无法解析 Issue"}
        yield "updates", {
            "parse_issue": {
                "status": "FAILED",
                "error": "无法解析 Issue",
            }
        }
        yield "values", failure

    compiled_graph.stream.side_effect = stream

    exit_code = main_module.main(
        ["run", "--repo", str(repo_root), "--issue", "失败场景"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "✗ 解析 Issue 失败" in captured.out
    assert "✗ 运行失败" in captured.err
    assert "无法解析 Issue" in captured.err
    assert "Traceback" not in captured.err


def test_run_displays_coding_failure_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)

    def stream(state: dict[str, object], *, stream_mode: list[str]):
        yield "updates", {
            "coordinator": {
                "next_action": "CODE",
                "coding_task": SimpleNamespace(objective="修复搜索"),
                "repair_round": 2,
                "coding_stage_call": 1,
            }
        }
        failure = {
            **state,
            "phase": "CODE",
            "status": "FAILED",
            "error": "Coding 失败：Diff 为空",
        }
        yield "updates", {
            "coding": {
                "phase": "CODE",
                "status": "FAILED",
                "error": "Coding 失败：Diff 为空",
                "repair_round": 2,
                "coding_stage_call": 1,
                "coding_iteration": 3,
            }
        }
        yield "values", failure

    compiled_graph.stream.side_effect = stream

    exit_code = main_module.main(
        ["run", "--repo", str(repo_root), "--issue", "失败场景"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "◆ 修复轮次 r02" in captured.out
    assert "Coding s01/i03 失败" in captured.out
    assert "Coding 失败：Diff 为空" in captured.err


def test_run_returns_one_when_graph_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    compiled_graph.stream.side_effect = RuntimeError("模型不可用")
    usage_callback = SimpleNamespace(
        usage_metadata={"test-model": {"total_tokens": 42}}
    )
    monkeypatch.setattr(
        main_module,
        "get_usage_metadata_callback",
        lambda: nullcontext(usage_callback),
    )

    exit_code = main_module.main(
        ["run", "--repo", str(repo_root), "--issue", "异常场景"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "运行失败" in captured.err
    assert "模型不可用" in captured.err
    assert "Traceback" not in captured.err
    assert "42" in captured.out


def test_review_failure_interactively_rolls_back_and_records_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = {
        "run_dir": str(run_dir),
        "repair_round": 2,
        "rollback_prompt_required": True,
        "rollback_reason": "Review 失败",
        "error": "Review 失败",
        "changed_files": ["app.py"],
    }
    monkeypatch.setattr(run_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(
        run_module,
        "rollback_state_to_base",
        lambda state, reason: {
            "success": True,
            "summary": "rolled back",
            "data": {},
            "error": None,
            "truncated": False,
        },
    )

    run_module._prompt_review_rollback(result, TerminalReporter())

    assert result["changed_files"] == []
    decision = json.loads(
        (run_dir / "rollback_decision_r02.json").read_text(encoding="utf-8")
    )
    assert decision["payload"]["decision"] == "ROLLBACK"
    assert decision["payload"]["rollback_success"] is True
    assert "已回滚" in capsys.readouterr().out


def test_review_failure_noninteractive_keeps_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = {
        "run_dir": str(run_dir),
        "repair_round": 1,
        "rollback_prompt_required": True,
        "error": "Review 失败",
        "changed_files": ["app.py"],
    }
    monkeypatch.setattr(run_module.sys, "stdin", io.StringIO())
    rollback = Mock(side_effect=AssertionError("不应回滚"))
    monkeypatch.setattr(run_module, "rollback_state_to_base", rollback)

    run_module._prompt_review_rollback(result, TerminalReporter())

    assert result["changed_files"] == ["app.py"]
    rollback.assert_not_called()
    decision = json.loads(
        (run_dir / "rollback_decision_r01.json").read_text(encoding="utf-8")
    )
    assert decision["payload"]["decision"] == "KEEP_NON_INTERACTIVE"
