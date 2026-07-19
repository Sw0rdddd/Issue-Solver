import subprocess
import sys
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli import commands
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

    monkeypatch.setattr(commands, "CONTROLLER_ROOT", tmp_path)
    monkeypatch.setattr(commands, "find_repo_root", lambda path: repo_root)
    monkeypatch.setattr(commands, "create_run_id", lambda: "run_test")
    monkeypatch.setattr(
        commands,
        "_preflight_environment",
        lambda repo, run_dir: environment,
    )
    monkeypatch.setattr(
        commands,
        "ReasoningChatDeepSeek",
        model_constructor,
    )
    monkeypatch.setattr(
        commands,
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


def test_module_help_is_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cli.commands", "--help"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "run" in result.stdout


def test_run_help_does_not_create_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_constructor = Mock(side_effect=AssertionError("不应创建模型"))
    monkeypatch.setattr(
        commands,
        "ReasoningChatDeepSeek",
        model_constructor,
    )

    with pytest.raises(SystemExit) as exc_info:
        commands.main(["run", "--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--repo" in output
    assert "--quiet" in output
    assert "--test-timeout" in output
    assert "--test-tail-lines" in output
    assert "--run-root" in output
    assert ".md/.txt 绝对路径" in output
    model_constructor.assert_not_called()


def test_global_run_help_allows_omitting_repo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_constructor = Mock(side_effect=AssertionError("不应创建模型"))
    monkeypatch.setattr(commands, "ReasoningChatDeepSeek", model_constructor)

    with pytest.raises(SystemExit) as exc_info:
        commands.main(["run", "--help"], global_mode=True)

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "当前目录所在仓库" in output
    model_constructor.assert_not_called()


def test_global_main_uses_global_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    main = Mock(return_value=0)
    monkeypatch.setattr(commands, "main", main)

    assert commands.global_main() == 0
    main.assert_called_once_with(global_mode=True)


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--issue", "修复查询失败"],
        ["run", "--repo", "."],
        [
            "run",
            "--repo",
            ".",
            "--issue",
            "修复查询失败",
            "--max-cycles",
            "0",
        ],
    ],
)
def test_run_rejects_invalid_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        commands.main(argv)

    assert exc_info.value.code == 2


def test_run_rejects_missing_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = commands.main(
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
    monkeypatch.setattr(commands, "CONTROLLER_ROOT", tmp_path)

    exit_code = commands.main(
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

    exit_code = commands.main(
        ["run", "--issue", "修复查询失败"],
        global_mode=True,
    )

    assert exit_code == 0
    assert captured_state["repo_path"] == str(repo_root)
    assert captured_state["run_dir"] == str(
        tmp_path / ".issue-solver-runs" / "repo" / "run_test"
    )


def test_environment_failure_stops_before_model_even_when_quiet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, _, model_constructor = prepare_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        commands,
        "_preflight_environment",
        Mock(side_effect=RuntimeError("未发现 .venv")),
    )

    exit_code = commands.main(
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
    model_constructor.assert_not_called()
    failure = (
        tmp_path
        / ".issue-solver-runs"
        / "repo"
        / "run_test"
        / "failure_environment.json"
    )
    assert failure.is_file()


def test_run_root_must_be_outside_target_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="目标 Git 仓库之外"):
        commands._resolve_run_root(repo_root, repo_root / ".runs")


def test_relative_run_root_resolves_from_controller_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller_root = tmp_path / "controller"
    repo_root = tmp_path / "external" / "repo"
    controller_root.mkdir()
    repo_root.mkdir(parents=True)
    monkeypatch.setattr(commands, "CONTROLLER_ROOT", controller_root)

    result = commands._resolve_run_root(repo_root, ".issue-solver-runs")

    assert result == controller_root / ".issue-solver-runs"


def test_rejects_controller_repository_as_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root = tmp_path / "controller"
    repo_root.mkdir()
    monkeypatch.setattr(commands, "CONTROLLER_ROOT", repo_root)
    monkeypatch.setattr(commands, "find_repo_root", lambda path: repo_root)
    preflight = Mock(side_effect=AssertionError("不应预检控制程序仓库"))
    monkeypatch.setattr(commands, "_preflight_environment", preflight)

    exit_code = commands.main(
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

    exit_code = commands.main(
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

    run_dir = tmp_path / ".issue-solver-runs" / "repo" / "run_test"
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
        "test_timeout": 300.0,
        "test_tail_lines": 100,
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
    assert "[开始] 初始化仓库" in output
    assert "[完成] 初始化仓库：python，全量测试命令 pytest -q" in output
    assert f"[环境] VENV：{repo_root / '.venv' / 'Scripts' / 'python.exe'}" in output
    assert "[完成] Issue：搜索忽略大小写" in output
    assert "[决策 r01/s01] 并行探索，共 2 个任务" in output
    assert "  [1] 定位入口" in output
    assert "[探索 r01/s01/i01 1/2] 完成：定位入口" in output
    assert "[探索 r01/s01/i02 2/2] 完成：定位测试" in output
    assert "[开始] Coordinator 汇总探索结果" in output
    assert "[决策 r01/s01] CODE：修复搜索逻辑" in output
    assert "[完成 r01/s01/i02] Coding：搜索已忽略大小写" in output
    assert "修改文件：src/search.py, tests/test_search.py" in output
    assert "运行 ID：run_test" in output
    assert "当前阶段：REVIEW" in output
    assert "下一动作：CODE" not in output
    assert f"运行目录：{run_dir}" in output
    assert "最终耗时：" in output


def test_quiet_hides_progress_but_keeps_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    configure_success_stream(compiled_graph, {})

    exit_code = commands.main(
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
    assert "[开始]" not in output
    assert "[完成]" not in output
    assert "[决策]" not in output
    assert "[探索" not in output
    assert "运行 ID：run_test" in output
    assert "当前阶段：REVIEW" in output
    assert "最终耗时：" in output


def test_cli_test_and_run_root_options_override_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    captured_state: dict[str, object] = {}
    configure_success_stream(compiled_graph, captured_state)
    custom_root = tmp_path / "custom-runs"

    exit_code = commands.main(
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

    exit_code = commands.main(
        ["run", "--repo", str(repo_root), "--issue", "重复探索"]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[决策 r01/s01] 并行探索，共 2 个任务" in output
    assert "[探索 r01/s01/i02 2/2] 完成：测试" in output
    assert "[决策 r01/s02] 并行探索，共 1 个任务" in output
    assert "[探索 r01/s02/i01 1/1] 完成：补充根因" in output


def test_run_returns_one_when_graph_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)

    def stream(state: dict[str, object], *, stream_mode: list[str]):
        failure = {
            **state,
            "status": "FAILED",
            "error": "无法解析 Issue",
        }
        yield "updates", {
            "parse_issue": {
                "status": "FAILED",
                "error": "无法解析 Issue",
            }
        }
        yield "values", failure

    compiled_graph.stream.side_effect = stream

    exit_code = commands.main(
        ["run", "--repo", str(repo_root), "--issue", "失败场景"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[失败] 解析 Issue：无法解析 Issue" in captured.out
    assert "运行失败：无法解析 Issue" in captured.err
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

    exit_code = commands.main(
        ["run", "--repo", str(repo_root), "--issue", "失败场景"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "[失败 r02/s01/i03] Coding：Coding 失败：Diff 为空" in captured.out
    assert "运行失败：Coding 失败：Diff 为空" in captured.err


def test_run_returns_one_when_graph_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo_root, compiled_graph, _ = prepare_runtime(monkeypatch, tmp_path)
    compiled_graph.stream.side_effect = RuntimeError("模型不可用")

    exit_code = commands.main(
        ["run", "--repo", str(repo_root), "--issue", "异常场景"]
    )

    error = capsys.readouterr().err
    assert exit_code == 1
    assert "运行失败：模型不可用" in error
    assert "Traceback" not in error


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
    monkeypatch.setattr(commands.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(
        commands,
        "rollback_state_to_base",
        lambda state, reason: {
            "success": True,
            "summary": "rolled back",
            "data": {},
            "error": None,
            "truncated": False,
        },
    )

    commands._prompt_review_rollback(result)

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
    monkeypatch.setattr(commands.sys, "stdin", io.StringIO())
    rollback = Mock(side_effect=AssertionError("不应回滚"))
    monkeypatch.setattr(commands, "rollback_state_to_base", rollback)

    commands._prompt_review_rollback(result)

    assert result["changed_files"] == ["app.py"]
    rollback.assert_not_called()
    decision = json.loads(
        (run_dir / "rollback_decision_r01.json").read_text(encoding="utf-8")
    )
    assert decision["payload"]["decision"] == "KEEP_NON_INTERACTIVE"
