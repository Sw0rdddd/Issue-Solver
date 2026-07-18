import ulid


def create_run_id() -> str:
    """生成唯一的任务运行 ID。"""

    return f"run_{ulid.new()}"
