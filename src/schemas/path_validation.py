from pathlib import PurePosixPath


TEST_TARGET_FORBIDDEN_CHARACTERS = frozenset("|&;<>\r\n\0")


def normalize_repo_relative_paths(
    values: list[str],
) -> list[str]:
    """规范化并校验仓库内的相对文件或目录路径。"""

    normalized_values: list[str] = []
    for value in values:
        normalized = value.replace("\\", "/")
        is_directory = normalized.endswith("/")
        normalized = normalized.rstrip("/")
        path = PurePosixPath(normalized)

        if (
            not normalized
            or normalized == "."
            or normalized.startswith("/")
            or (
                len(normalized) >= 2
                and normalized[0].isalpha()
                and normalized[1] == ":"
            )
            or ".." in path.parts
        ):
            raise ValueError(f"必须是仓库内的相对路径：{value}")

        normalized_path = path.as_posix()
        if is_directory:
            normalized_path += "/"
        normalized_values.append(normalized_path)

    if len(set(normalized_values)) != len(normalized_values):
        raise ValueError("路径列表不能包含重复项。")

    return normalized_values


def normalize_pytest_targets(values: list[str]) -> list[str]:
    """规范化并校验仓库相对 pytest 文件或 node ID。"""

    normalized_values: list[str] = []
    for value in values:
        if any(
            character.isspace()
            or character in TEST_TARGET_FORBIDDEN_CHARACTERS
            for character in value
        ):
            raise ValueError(f"测试目标不能包含空白或 Shell 控制字符：{value}")

        path_value, *selectors = value.split("::")
        normalized_path = path_value.replace("\\", "/")
        path = PurePosixPath(normalized_path)
        if (
            not normalized_path
            or normalized_path == "."
            or normalized_path.startswith("/")
            or normalized_path.startswith("-")
            or (
                len(normalized_path) >= 2
                and normalized_path[0].isalpha()
                and normalized_path[1] == ":"
            )
            or ".." in path.parts
            or path.suffix.lower() != ".py"
            or any(not selector for selector in selectors)
        ):
            raise ValueError(
                "测试目标必须是仓库相对 .py 文件，可带非空 :: 选择器："
                f"{value}"
            )

        normalized_target = path.as_posix()
        if selectors:
            normalized_target += "::" + "::".join(selectors)
        normalized_values.append(normalized_target)

    if len(set(normalized_values)) != len(normalized_values):
        raise ValueError("测试目标不能包含重复项。")

    return normalized_values
