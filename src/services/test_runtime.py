import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config import PROJECT_ROOT


TEST_RUNTIME_ROOT = PROJECT_ROOT.parent / ".issue-solver-runtime"
SAFE_RUN_NAME = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class TestRuntime:
    path: Path
    cleanup_process: subprocess.Popen[bytes]


def _validated_runtime_path(path: str | Path) -> Path:
    root = TEST_RUNTIME_ROOT.resolve()
    target = Path(path).resolve()
    if target == root:
        raise ValueError("测试临时目录不能等于运行时根目录。")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"测试临时目录超出专用根目录：{target}") from exc
    return target


def _remove_runtime_path(path: str | Path) -> None:
    target = _validated_runtime_path(path)
    if target.exists():
        shutil.rmtree(target)
    for parent in (target.parent, TEST_RUNTIME_ROOT.resolve()):
        try:
            parent.rmdir()
        except OSError:
            pass


def _watch_and_cleanup(path: str | Path) -> int:
    try:
        sys.stdin.buffer.read()
        _remove_runtime_path(path)
    except Exception as exc:
        print(f"无法清理测试临时目录：{exc}", file=sys.stderr)
        return 1
    return 0


def start_test_runtime(
    *,
    run_name: str,
    repair_round: int,
    index: int,
) -> TestRuntime:
    safe_run_name = SAFE_RUN_NAME.sub("_", run_name).strip("_") or "run"
    run_root = TEST_RUNTIME_ROOT.resolve() / safe_run_name
    run_root.mkdir(parents=True, exist_ok=True)
    path = Path(
        tempfile.mkdtemp(
            prefix=f"test-r{repair_round:02d}-i{index:02d}-",
            dir=run_root,
        )
    )

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "services.test_runtime",
                "--watch",
                str(path),
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except Exception:
        _remove_runtime_path(path)
        raise
    return TestRuntime(path=path, cleanup_process=process)


def finish_test_runtime(runtime: TestRuntime, timeout: float = 10) -> str | None:
    process = runtime.cleanup_process
    try:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    except OSError:
        try:
            if process.poll() is None:
                process.kill()
                process.wait()
        except OSError:
            pass

    stderr = b""
    if process.stderr is not None:
        try:
            stderr = process.stderr.read()
            process.stderr.close()
        except OSError:
            pass

    if runtime.path.exists():
        try:
            _remove_runtime_path(runtime.path)
        except Exception as exc:
            detail = stderr.decode("utf-8", errors="replace").strip()
            return detail or f"无法清理测试临时目录：{exc}"
    return None


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--watch":
        raise SystemExit(2)
    raise SystemExit(_watch_and_cleanup(sys.argv[2]))
