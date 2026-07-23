from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from schemas.failure import FailureInfo


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
OutputTail = Annotated[
    str,
    StringConstraints(max_length=20_000, strict=True),
]


class TestResult(BaseModel):
    """Test Executor 真实运行某条测试命令后产生的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    command: NonEmptyText = Field(description="程序构造或仓库检测得到的逻辑测试命令")
    resolved_command: list[NonEmptyText] = Field(
        min_length=1,
        description="绑定目标虚拟环境后的实际 argv",
    )
    cwd: NonEmptyText = Field(description="测试进程工作目录")
    python_executable: NonEmptyText = Field(description="目标虚拟环境解释器")
    status: Literal[
        "PASSED",
        "FAILED",
        "ENVIRONMENT_ERROR",
        "TIMEOUT",
        "SAFETY_ERROR",
    ] = Field(
        description="测试结论；只有 PASSED 表示该命令通过，其余状态均阻止 FINISH。"
    )
    exit_code: int = Field(strict=True, description="测试进程退出码")
    duration: float = Field(
        strict=True,
        ge=0,
        allow_inf_nan=False,
        description="执行耗时，单位为秒",
    )
    stdout_path: NonEmptyText = Field(description="完整标准输出日志路径")
    stderr_path: NonEmptyText = Field(description="完整错误输出日志路径")
    output_tail: OutputTail = Field(
        description="受限测试日志尾部；仅失败结果传给 Coordinator"
    )
    failure: FailureInfo | None = Field(
        default=None,
        description="非 PASSED 时必须提供、且与 status 对应的结构化失败事实。",
    )

    @model_validator(mode="after")
    def validate_status_matches_exit_code(self) -> "TestResult":
        if self.status == "PASSED" and self.exit_code != 0:
            raise ValueError("PASSED 的 exit_code 必须为 0。")
        if self.status == "FAILED" and self.exit_code == 0:
            raise ValueError("FAILED 的 exit_code 不能为 0。")
        if self.status in {
            "ENVIRONMENT_ERROR",
            "TIMEOUT",
            "SAFETY_ERROR",
        } and self.exit_code != -1:
            raise ValueError(f"{self.status} 的 exit_code 必须为 -1。")
        expected_failure_types = {
            "FAILED": "SOLUTION",
            "ENVIRONMENT_ERROR": "ENVIRONMENT",
            "TIMEOUT": "LIMIT",
            "SAFETY_ERROR": "SAFETY",
        }
        if self.status == "PASSED" and self.failure is not None:
            raise ValueError("PASSED 不能包含 failure。")
        if self.status != "PASSED":
            if self.failure is None:
                raise ValueError(f"{self.status} 必须包含 failure。")
            expected = expected_failure_types[self.status]
            if self.failure.type != expected:
                raise ValueError(
                    f"{self.status} 的 failure.type 必须为 {expected}。"
                )
        return self
