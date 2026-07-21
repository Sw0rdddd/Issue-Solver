import json
import os
import subprocess
from pathlib import Path

from schemas.environment_info import EnvironmentInfo
from services.artifacts import ensure_run_logs_directory


ENVIRONMENT_NAMES = (".venv", "venv", ".conda")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_git_ignored(repo_root: Path, relative_name: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative_name],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 1:
        raise RuntimeError(
            f"目标虚拟环境 {relative_name} 未被 Git ignore；"
            "请先将它加入目标仓库的 .gitignore。"
        )
    if result.returncode != 0:
        raise RuntimeError("无法确认目标虚拟环境是否被 Git ignore。")


def _environment_paths(root: Path, kind: str) -> list[Path]:
    if os.name != "nt":
        return [root / "bin"]
    if kind == "CONDA":
        return [
            root,
            root / "Scripts",
            root / "Library" / "bin",
            root / "Library" / "usr" / "bin",
            root / "Library" / "mingw-w64" / "bin",
        ]
    return [root / "Scripts"]


def build_environment_variables(
    environment: EnvironmentInfo,
    runtime_dir: str | Path,
) -> dict[str, str]:
    """为单个目标测试进程构造隔离的环境变量。"""

    root = Path(environment.root_path)
    runtime = Path(runtime_dir)
    temporary = runtime / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)

    values = os.environ.copy()
    values.pop("PYTHONHOME", None)
    existing_path = values.get("PATH", "")
    path_entries = [str(path) for path in _environment_paths(root, environment.kind)]
    if existing_path:
        path_entries.append(existing_path)
    values["PATH"] = os.pathsep.join(path_entries)
    values["TEMP"] = str(temporary)
    values["TMP"] = str(temporary)
    values["TMPDIR"] = str(temporary)
    values["PYTHONUTF8"] = "1"
    values["PYTHONIOENCODING"] = "utf-8"

    if environment.kind == "CONDA":
        values.pop("VIRTUAL_ENV", None)
        values["CONDA_PREFIX"] = str(root)
    else:
        values.pop("CONDA_PREFIX", None)
        values["VIRTUAL_ENV"] = str(root)
    return values


def _python_path(root: Path, kind: str) -> Path:
    if os.name != "nt":
        return root / "bin/python"
    if kind == "CONDA":
        return root / "python.exe"
    return root / "Scripts/python.exe"


def discover_python_environment(
    repo_path: str | Path,
    run_dir: str | Path,
) -> EnvironmentInfo:
    """只发现并验证目标仓库根目录内已准备好的 Python 环境。"""

    repo_root = Path(repo_path).resolve()
    candidates = [repo_root / name for name in ENVIRONMENT_NAMES if (repo_root / name).exists()]
    if not candidates:
        raise RuntimeError(
            "目标仓库根目录未发现 .venv、venv 或 .conda；"
            "请开发者先创建虚拟环境并安装项目及 pytest 依赖后重试。"
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise RuntimeError(f"目标仓库存在多个虚拟环境候选：{names}；请只保留一个。")

    candidate = candidates[0]
    if not candidate.is_dir():
        raise RuntimeError(f"目标虚拟环境不是目录：{candidate}")
    resolved = candidate.resolve(strict=True)
    if candidate.is_symlink() or resolved != candidate.absolute():
        raise RuntimeError("目标虚拟环境不能是符号链接或目录联接。")
    if not _is_within(resolved, repo_root):
        raise RuntimeError("目标虚拟环境解析到了目标仓库之外。")
    _require_git_ignored(repo_root, candidate.name)

    kind = "CONDA" if candidate.name == ".conda" else "VENV"
    marker = resolved / ("conda-meta" if kind == "CONDA" else "pyvenv.cfg")
    if not marker.exists():
        raise RuntimeError(f"目标虚拟环境缺少标识文件：{marker}")
    python = _python_path(resolved, kind)
    if not python.is_file():
        raise RuntimeError(f"目标虚拟环境缺少 Python 解释器：{python}")

    provisional = EnvironmentInfo(
        kind=kind,
        root_path=str(resolved),
        python_executable=str(python),
        pytest_version="pending",
        source=candidate.name,
    )
    runtime = ensure_run_logs_directory(run_dir) / "environment_runtime"
    variables = build_environment_variables(provisional, runtime)
    identity = subprocess.run(
        [
            str(python),
            "-c",
            "import json,sys; print(json.dumps({'executable':sys.executable,'prefix':sys.prefix}))",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        env=variables,
        check=False,
    )
    if identity.returncode != 0:
        detail = identity.stderr.strip() or identity.stdout.strip() or "未知错误"
        raise RuntimeError(f"目标虚拟环境 Python 无法启动：{detail}")
    try:
        identity_payload = json.loads(identity.stdout.strip())
        actual_prefix = Path(identity_payload["prefix"]).resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法验证目标虚拟环境 Python 身份。") from exc
    if actual_prefix != resolved:
        raise RuntimeError(
            f"目标解释器 sys.prefix 不匹配：期望 {resolved}，实际 {actual_prefix}。"
        )

    pytest_check = subprocess.run(
        [str(python), "-m", "pytest", "--version"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        env=variables,
        check=False,
    )
    if pytest_check.returncode != 0:
        raise RuntimeError(
            "目标虚拟环境未安装可用的 pytest；"
            "请开发者在该环境中安装目标仓库依赖后重试。"
        )
    version = pytest_check.stdout.strip() or pytest_check.stderr.strip()
    if not version:
        raise RuntimeError("pytest --version 未返回版本信息。")

    probe = runtime / "write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(f"运行目录不可写：{runtime}：{exc}") from exc

    return provisional.model_copy(update={"pytest_version": version})
