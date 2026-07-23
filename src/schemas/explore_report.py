import re

from pydantic import BaseModel, Field, field_validator, model_validator

from schemas.path_validation import (
    normalize_pytest_targets,
    normalize_repo_relative_paths,
)


SOURCE_LOCATION_PATTERN = re.compile(
    r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+:\d+(?:-\d+)?"
)


class ExploreReport(BaseModel):
    """Explore Agent 对仓库进行调查后生成的报告。"""

    focus: str = Field(
        description="本次唯一的调查目标；报告内容必须围绕该目标展开。",
    )

    relevant_files: list[str] = Field(
        default_factory=list,
        description="有工具证据支持、与 Issue 直接相关的仓库相对文件路径。",
    )

    relevant_symbols: list[str] = Field(
        default_factory=list,
        description="在已读取代码中确认、与 Issue 直接相关的类、函数、方法或变量。",
    )

    findings: list[str] = Field(
        default_factory=list,
        description="基于真实代码证据的关键发现；每条必须包含仓库相对 path:line。",
    )

    root_cause: str = Field(
        default="",
        description="引用仓库相对 path:line 的当前根因结论；证据不足时留空，不得猜测。",
    )

    test_targets: list[str] = Field(
        default_factory=list,
        description="经工具和 read_file 验证的既有仓库相对 .py 测试文件或 pytest node ID。",
    )

    unknowns: list[str] = Field(
        default_factory=list,
        description="当前无法由工具证据确认、需要后续调查的问题或候选测试目标。",
    )

    @field_validator("relevant_files")
    @classmethod
    def validate_relevant_files(cls, values: list[str]) -> list[str]:
        return normalize_repo_relative_paths(values)

    @field_validator("findings")
    @classmethod
    def validate_findings_have_source_locations(
        cls,
        values: list[str],
    ) -> list[str]:
        for value in values:
            if not SOURCE_LOCATION_PATTERN.search(value):
                raise ValueError(
                    "每条 findings 必须包含仓库相对 path:line 证据。"
                )
        return values

    @field_validator("root_cause")
    @classmethod
    def validate_root_cause_has_source_location(cls, value: str) -> str:
        if value and not SOURCE_LOCATION_PATTERN.search(value):
            raise ValueError(
                "非空 root_cause 必须包含仓库相对 path:line 证据。"
            )
        return value

    @field_validator("test_targets")
    @classmethod
    def validate_test_targets(cls, values: list[str]) -> list[str]:
        return normalize_pytest_targets(values)

    @model_validator(mode="after")
    def validate_report_has_evidence_or_unknowns(self) -> "ExploreReport":
        if not any(
            (
                self.relevant_files,
                self.relevant_symbols,
                self.findings,
                self.root_cause,
                self.test_targets,
                self.unknowns,
            )
        ):
            raise ValueError(
                "ExploreReport 必须包含已证实证据或待确认的 unknowns。"
            )
        return self
