import hashlib
import shlex
import subprocess
import time
from collections import deque
from pathlib import Path

from schemas.environment_info import EnvironmentInfo
from schemas.test_result import TestResult
from services.artifacts import ensure_run_logs_directory
from services.python_environment import build_environment_variables


MAX_MODEL_OUTPUT_CHARS = 20_000
SHELL_METACHARACTERS = frozenset("|&;<>\r\n")
DIRECT_TEST_EXECUTABLES = frozenset({"pytest", "pytest.exe"})
PYTHON_EXECUTABLES = frozenset(
    {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
)
PYTHON_TEST_MODULES = frozenset({"pytest"})
ENVIRONMENT_ERROR_MARKERS = (
    "modulenotfounderror",
    "no module named",
    "permissionerror",
    "importerror: dll load failed",
)


def build_targeted_test_command(
    repo_path: str | Path,
    test_targets: list[str],
) -> str:
    """校验测试目标位于仓库内，并构造受限的定向 pytest 命令。"""

    if not test_targets:
        raise ValueError("定向测试目标不能为空。")

    repo_root = Path(repo_path).resolve()
    if not repo_root.is_dir():
        raise ValueError(f"测试仓库不存在或不是目录：{repo_root}")

    for target in test_targets:
        path_value = target.split("::", 1)[0]
        resolved_target = (repo_root / path_value).resolve()
        try:
            resolved_target.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(f"测试目标超出目标仓库：{target}") from exc

    return shlex.join(["pytest", "-q", *test_targets])


def parse_test_command(command: str) -> list[str]:
    """将受支持的测试命令解析为不经过 Shell 的参数数组。"""

    normalized = command.strip()
    if not normalized:
        raise ValueError("测试命令不能为空。")
    if any(character in normalized for character in SHELL_METACHARACTERS):
        raise ValueError("测试命令不能包含 Shell 控制字符。")

    try:
        arguments = shlex.split(normalized, posix=True)
    except ValueError as exc:
        raise ValueError(f"测试命令无法解析：{exc}") from exc
    if not arguments:
        raise ValueError("测试命令不能为空。")

    executable = Path(arguments[0].replace("\\", "/")).name.lower()
    if executable in DIRECT_TEST_EXECUTABLES:
        return arguments
    if (
        executable in PYTHON_EXECUTABLES
        and len(arguments) >= 3
        and arguments[1] == "-m"
        and arguments[2] in PYTHON_TEST_MODULES
    ):
        return arguments
    raise ValueError("首版仅允许 pytest 或 python/py -m pytest。")


def resolve_pytest_command(command: str, python_executable: str | Path) -> list[str]:
    """将逻辑 pytest 命令绑定到目标虚拟环境解释器。"""

    arguments = parse_test_command(command)
    executable = Path(arguments[0].replace("\\", "/")).name.lower()
    if executable in DIRECT_TEST_EXECUTABLES:
        pytest_arguments = arguments[1:]
    else:
        pytest_arguments = arguments[3:]
    return [str(Path(python_executable).resolve()), "-m", "pytest", *pytest_arguments]


def _append_utf8(path: Path, message: str) -> None:
    with path.open("ab") as stream:
        stream.write(message.encode("utf-8", errors="replace"))


def _has_environment_error(*paths: Path) -> bool:
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                lowered = line.lower()
                if any(marker in lowered for marker in ENVIRONMENT_ERROR_MARKERS):
                    return True
    return False


def build_output_tail(
    stdout_path: str | Path,
    stderr_path: str | Path,
    tail_lines: int,
) -> str:
    """从完整日志构造总行数和字符数均受限的模型上下文。"""

    if tail_lines < 1:
        raise ValueError("tail_lines 必须大于 0。")

    lines: deque[str] = deque(maxlen=tail_lines)
    for label, path in (
        ("stdout", Path(stdout_path)),
        ("stderr", Path(stderr_path)),
    ):
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                lines.append(f"[{label}] {line.rstrip()}")

    output = "\n".join(lines)
    if len(output) > MAX_MODEL_OUTPUT_CHARS:
        output = output[-MAX_MODEL_OUTPUT_CHARS:]
    return output


def execute_test_command(
    *,
    repo_path: str | Path,
    run_dir: str | Path,
    command: str,
    environment: EnvironmentInfo,
    timeout: float,
    tail_lines: int,
    repair_round: int,
    index: int,
) -> TestResult:
    """执行一条受限测试命令并将完整输出写入运行目录。"""

    if timeout <= 0:
        raise ValueError("timeout 必须大于 0。")
    if tail_lines < 1:
        raise ValueError("tail_lines 必须大于 0。")
    if repair_round < 1 or index < 1:
        raise ValueError("repair_round 和 index 必须大于 0。")

    repo_root = Path(repo_path).resolve()
    if not repo_root.is_dir():
        raise ValueError(f"测试仓库不存在或不是目录：{repo_root}")
    output_dir = ensure_run_logs_directory(Path(run_dir).resolve())
    stdout_path = output_dir / f"test_stdout_r{repair_round:02d}_i{index:02d}.log"
    stderr_path = output_dir / f"test_stderr_r{repair_round:02d}_i{index:02d}.log"

    started = time.monotonic()
    status = "ENVIRONMENT_ERROR"
    exit_code = -1
    resolved_arguments = [command]
    with stdout_path.open("xb") as stdout_stream, stderr_path.open("xb") as stderr_stream:
        try:
            runtime_dir = output_dir / (
                f"test_runtime_r{repair_round:02d}_i{index:02d}"
            )
            cache_dir = runtime_dir / "cache"
            arguments = resolve_pytest_command(
                command,
                environment.python_executable,
            )
            arguments.extend(
                [
                    f"--basetemp={runtime_dir / 'basetemp'}",
                    "-o",
                    f"cache_dir={cache_dir}",
                ]
            )
            resolved_arguments = arguments
            process_environment = build_environment_variables(
                environment,
                runtime_dir,
            )
            process = subprocess.Popen(
                arguments,
                cwd=repo_root,
                stdout=stdout_stream,
                stderr=stderr_stream,
                env=process_environment,
                shell=False,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                status = "TIMEOUT"
                stderr_stream.write(
                    f"\n测试执行超过 {timeout:g} 秒，进程已终止。\n".encode("utf-8")
                )
                stderr_stream.flush()
            else:
                exit_code = process.returncode
                status = "PASSED" if exit_code == 0 else "FAILED"
        except (FileNotFoundError, OSError, ValueError) as exc:
            stderr_stream.write(f"测试环境错误：{exc}\n".encode("utf-8"))
            stderr_stream.flush()

    if status == "FAILED":
        if _has_environment_error(stdout_path, stderr_path):
            status = "ENVIRONMENT_ERROR"
            exit_code = -1

    duration = time.monotonic() - started
    return TestResult(
        command=command,
        resolved_command=resolved_arguments,
        cwd=str(repo_root),
        python_executable=environment.python_executable,
        status=status,
        exit_code=exit_code,
        duration=duration,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        output_tail=build_output_tail(stdout_path, stderr_path, tail_lines),
    )


def append_environment_error(
    result: TestResult,
    message: str,
    tail_lines: int,
) -> TestResult:
    """在完整 stderr 中追加环境错误并返回同步后的结构化结果。"""

    _append_utf8(Path(result.stderr_path), f"\n测试环境错误：{message}\n")
    return result.model_copy(
        update={
            "status": "ENVIRONMENT_ERROR",
            "exit_code": -1,
            "output_tail": build_output_tail(
                result.stdout_path,
                result.stderr_path,
                tail_lines,
            ),
        }
    )


def worktree_fingerprint(repo_path: str | Path, base_commit: str) -> str:
    """返回 base-relative Diff 与非忽略未跟踪路径的稳定摘要。"""

    repo_root = Path(repo_path).resolve()
    commands = (
        ["git", "diff", "--binary", "--no-ext-diff", base_commit, "--", "."],
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )
    digest = hashlib.sha256()
    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 Git。") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("检查测试前后工作区时 Git 超时。") from exc
        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"无法检查测试工作区：{error or '未知 Git 错误'}")
        digest.update(result.stdout)
        digest.update(b"\0")
    return digest.hexdigest()
