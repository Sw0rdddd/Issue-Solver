# Issue-to-Solution Agent 架构方案

## 1. 项目定位

这是一个面向个人学习、实习求职和项目展示的 Issue 自动修复命令行工具。

用户在本地 Git 仓库中输入 GitHub Issue URL 或 Issue 文本，工具通过 LangChain 和 LangGraph 自动完成：

```text
理解 Issue
→ 探索仓库
→ 定位问题
→ 生成并应用修改
→ Review
→ 运行真实测试
→ 根据结果继续修复或结束
```

最终产出：

- 修改后的本地工作区
- Git Diff 或 Patch 文件
- Issue 根因与修改说明
- Review 结果
- 测试结果
- 完整运行记录

本项目不是生产级通用 Coding Agent，也不追求支持所有语言、所有仓库和所有构建环境。第一版优先保证：

1. 工作流完整；
2. 状态传递清晰；
3. 代码可读；
4. 过程可追踪；
5. 能真实修改仓库并运行测试；
6. 可以通过 CLI 在不同项目中调用。

---

# 2. 设计边界

## 2.1 保留的核心能力

- 手写 `StateGraph` 控制流程；
- Coordinator 负责决策和总结；
- Explore Agent 负责只读探索；
- Coding Agent 严格串行；
- Review Agent 负责只读检查；
- Test Executor 负责真实测试；
- Review/Test 失败后允许回到 Explore 或 Code；
- Checkpoint 支持恢复；
- 运行目录保存 Patch、日志和报告。

## 2.2 第一版明确不实现

- 并行 Coding Agent；
- Git worktree；
- 多个专业 Reviewer；
- 独立 Explore Aggregator Agent；
- 独立 Test Planner Agent；
- 复杂任务 DAG；
- 长期记忆；
- 向量数据库；
- 自动创建 Pull Request；
- 远程沙箱集群；
- 完善的多语言适配；
- Web 前端；
- 复杂预算系统；
- 细粒度失败分类体系。

---

# 3. 总体架构

```text
CLI
 │
 ▼
LangGraph StateGraph
 │
 ├── Initialize
 ├── Parse Issue
 ├── Coordinator
 ├── Explore
 ├── Coding
 ├── Review
 ├── Test
 └── Finalize
```

职责划分：

```text
StateGraph      控制节点执行、状态更新、循环和结束
Coordinator     判断下一步，并生成 Explore 或 Coding 任务
Explore Agent   搜索仓库，定位相关代码、根因和测试入口
Coding Agent    串行读取和修改代码
Review Agent    检查当前 Diff 是否合理
Test Executor   真实执行测试命令
CLI             提供跨仓库调用入口
```

---

# 4. StateGraph 流程图

```text
┌──────────────────┐
│      START       │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Initialize       │
│ 初始化仓库环境    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Parse Issue      │
│ 解析 Issue       │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────┐
│ Coordinator              │
│ 判断下一步需要执行什么     │
└────────────┬─────────────┘
             │
       ┌─────┼──────────────────┐
       │     │                  │
       ▼     ▼                  ▼
   EXPLORE  CODE         FINISH / FAILED
       │     │
       ▼     ▼
┌──────────┐ ┌──────────────────┐
│ Explore  │ │ Coding Agent     │
│ Agent(s) │ │ 串行修改代码      │
└────┬─────┘ └────────┬─────────┘
     │                │
     │                ▼
     │       ┌──────────────────┐
     │       │ Review Agent     │
     │       │ 检查当前 Diff     │
     │       └────────┬─────────┘
     │                │
     │                ▼
     │       ┌──────────────────┐
     │       │ Test Executor    │
     │       │ 串行真实运行测试  │
     │       └────────┬─────────┘
     │                │
     └────────────────┴─────────┐
                                ▼
                     ┌────────────────────┐
                     │ Coordinator        │
                     │ 判断重试或结束      │
                     └─────────┬──────────┘
                               │
                 ┌─────────────┼──────────────┐
                 │             │              │
                 ▼             ▼              ▼
              EXPLORE        CODE           FINISH
                 │             │              │
                 └──────循环───┘              ▼
                                      ┌──────────────┐
                                      │ Finalize     │
                                      │ 输出最终报告  │
                                      └──────┬───────┘
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │     END      │
                                      └──────────────┘
```

核心闭环：

```text
Issue
→ Explore
→ Coordinator
→ Code
→ Review
→ Test
→ Coordinator
→ Finish / Retry
```

---

# 5. 节点职责

## 5.1 Initialize

普通 Python 节点。

负责：

- 确认仓库路径；
- 确认是 Git 仓库；
- 获取当前分支和基础 Commit；
- 检查工作区是否干净；
- 创建本次运行目录；
- 识别基本项目类型；
- 读取项目配置；
- 获取测试命令。

默认要求干净工作区，避免覆盖用户已有修改。

---

## 5.2 Parse Issue

将 Issue URL 或文本转换为统一结构：

- 标题；
- 原始描述；
- 期望行为；
- 实际行为；
- 验收条件。

Issue 信息不完整时，允许字段为空，不强行补全。

---

## 5.3 Coordinator

Coordinator 是主 Agent。

负责：

- 生成 Explore 目标；
- 判断探索信息是否足够；
- 生成 Coding Task；
- 根据 Review 和 Test 结果决定下一步；
- 更新当前工作摘要；
- 控制循环次数；
- 判断完成或失败。

允许动作：

```text
EXPLORE
CODE
FINISH
FAILED
```

Coding 成功后默认依次进入 Review 和 Test，不额外增加 REVIEW、TEST 路由。

Coordinator 不拥有文件修改工具和任意 Shell 工具。

---

## 5.4 Explore Agent

只读探索仓库。

负责：

- 查找相关文件；
- 查找相关符号；
- 追踪调用路径；
- 定位根因线索；
- 查找已有测试；
- 查找复现方式；
- 标记未确认问题。

第一版最多两个 Explore 任务：

```text
任务一：定位相关代码和调用路径
任务二：查找测试、复现方式和影响范围
```

如果 Issue 简单，只执行一个任务。

Explore 返回摘要和定位，不复制大量源码。

---

## 5.5 Coding Agent

严格串行。

输入：

- Issue；
- 验收条件；
- Coding Task；
- Explore Reports；
- 当前工作摘要；
- 当前 Git Diff。

负责：

- 重新读取目标文件；
- 验证根因判断；
- 搜索必要关联代码；
- 修改一个或多个相关文件；
- 检查 Git Diff；
- 执行少量局部验证；
- 返回修改摘要和风险。

约束：

- 同时只运行一个 Coding Agent；
- 不使用 worktree；
- 不进行无关重构；
- 不修改受保护目录；
- 修改范围围绕当前 Issue。

Coding 节点结束时直接进行确定性检查：

- 是否产生 Diff；
- 是否修改禁止目录；
- 是否生成异常文件；
- 修改范围是否合理；
- Diff 是否可读取。

---

## 5.6 Review Agent

只读检查修改。

输入：

- Issue；
- 验收条件；
- Explore 结论；
- Coding Task；
- 当前 Diff；
- 修改后的相关文件；
- Coding Result。

检查：

- 修改是否对应根因；
- 是否解决 Issue；
- 是否有明显逻辑错误；
- 是否可能引入回归；
- 是否缺少必要测试。

输出：

```text
APPROVE
REQUEST_CHANGES
```

以及问题、建议和剩余风险。

第一版只保留一个综合 Reviewer。

---

## 5.7 Test Executor

普通 Python 节点，不是 Agent。

测试命令来源优先级：

1. 项目配置文件；
2. 项目现有配置；
3. Explore 发现的命令；
4. 工具内置规则。

推荐顺序：

```text
相关测试
→ 模块测试
→ 可选完整测试
```

记录：

- 命令；
- 退出码；
- 运行时间；
- stdout；
- stderr；
- 是否超时。

状态：

```text
PASSED
FAILED
ENVIRONMENT_ERROR
TIMEOUT
```

测试是否成功以退出码和测试框架结果为准。

---

## 5.8 Finalize

生成最终报告，包括：

- Issue 摘要；
- 根因；
- 修改文件；
- 修改内容；
- Review 结论；
- 执行的测试；
- 测试结果；
- 剩余风险；
- Patch 路径；
- 运行目录。

---

# 6. State 设计

## 6.1 原则

State 保持扁平，只保存：

- 当前流程需要的结构化数据；
- 简短摘要；
- 文件路径引用。

不直接保存：

- 完整源码；
- 大型 Diff；
- 完整测试日志；
- 全部消息历史；
- 重复中间分析。

## 6.2 推荐结构

```text
ResolverState
├── run_id
├── phase
├── status
├── cycle
│
├── repo_path
├── base_commit
├── project_type
├── test_commands
│
├── issue
│
├── current_summary
├── next_action
│
├── explore_reports
│
├── coding_task
├── coding_result
├── changed_files
├── diff_path
│
├── review_result
├── test_results
│
├── error
└── run_dir
```

---

# 7. 必要数据结构

第一版只保留五个主要结构化模型。

## 7.1 IssueSpec

```text
IssueSpec
├── title
├── body
├── expected_behavior
├── actual_behavior
└── acceptance_criteria
```

## 7.2 ExploreReport

```text
ExploreReport
├── focus
├── relevant_files
├── relevant_symbols
├── findings
├── root_cause
├── test_targets
└── unknowns
```

## 7.3 CodingTask

```text
CodingTask
├── objective
├── acceptance_criteria
├── relevant_files
├── root_cause
├── allowed_scope
└── validation
```

## 7.4 CodingResult

```text
CodingResult
├── success
├── changed_files
├── summary
├── diff_path
├── validation
└── remaining_risks
```

## 7.5 ReviewResult

```text
ReviewResult
├── verdict
├── issues
├── suggestions
└── remaining_risks
```

测试结果使用简单结构：

```text
TestResult
├── command
├── status
├── exit_code
├── duration
├── stdout_path
└── stderr_path
```

---

# 8. CLI 设计

第一版只实现三个命令。

## 8.1 run

```bash
issue-solver run --issue <issue-url-or-text>
```

可选参数：

```text
--repo
--model
--max-cycles
--dry-run
```

## 8.2 resume

```bash
issue-solver resume <run-id>
```

## 8.3 report

```bash
issue-solver report <run-id>
```

---

# 9. 跨项目支持

跨项目指同一套 CLI 可以在不同本地 Git 仓库中运行。

第一版重点支持 Python 项目。

优先识别：

```text
pyproject.toml
requirements.txt
pytest.ini
tox.ini
```

可选项目配置：

```text
.issue-solver.yaml
```

仅保留：

```text
test_commands
protected_paths
ignored_paths
```

---

# 10. 运行目录

```text
.issue-solver-runs/
└── <run-id>/
    ├── issue.json
    ├── explore.json
    ├── diff.patch
    ├── review.json
    ├── tests.log
    ├── state.json
    └── report.md
```

LangGraph Checkpoint 使用 SQLite 保存，不替代这些可读文件。

---

# 11. 循环控制

推荐限制：

```text
最大完整循环次数：3
最大 Explore 轮数：2
最大 Coding 次数：3
单条测试命令超时：可配置
```

决策规则：

```text
Review 通过且测试通过
→ FINISH

Review 不通过
→ CODE

测试失败且根因明确
→ CODE

测试失败且根因可能错误
→ EXPLORE

达到循环上限
→ FAILED

环境无法继续
→ FAILED
```

---

# 12. 项目目录结构

```text
issue_solver/
├── cli/
│   └── commands.py
├── graph/
│   ├── builder.py
│   ├── state.py
│   └── routing.py
├── nodes/
│   ├── initialize.py
│   ├── parse_issue.py
│   ├── coordinator.py
│   ├── explore.py
│   ├── coding.py
│   ├── review.py
│   ├── test.py
│   └── finalize.py
├── agents/
│   ├── coordinator.py
│   ├── explorer.py
│   ├── coder.py
│   └── reviewer.py
├── tools/
│   ├── filesystem.py
│   ├── search.py
│   ├── git.py
│   └── shell.py
├── schemas/
│   ├── issue.py
│   ├── explore.py
│   ├── coding.py
│   └── review.py
├── services/
│   ├── repository.py
│   ├── run_store.py
│   └── project_detector.py
├── prompts/
│   ├── coordinator.py
│   ├── explorer.py
│   ├── coder.py
│   └── reviewer.py
└── config.py
```

目录用于明确职责，不要求一开始全部拆分完成。

---

# 13. 必须实现和注意的点

## Git 安全

- 默认要求干净工作区；
- 保存基础 Commit；
- 所有修改可通过 Git Diff 查看；
- 不自动提交；
- 不自动推送；
- 不自动创建 PR。

## 路径安全

- 文件工具只能访问仓库目录；
- 禁止路径越界；
- 跳过依赖和缓存目录；
- 限制读取文件大小。

## 命令安全

- 测试命令受限；
- 设置超时；
- 禁止危险 Shell；
- 第一版默认禁止网络访问。

## 上下文控制

- Explore 只返回摘要和定位；
- Coding 按需重新读取源码；
- Diff 和日志保存为文件；
- Coordinator 不接收完整消息历史。

## 串行修改

- Coding Agent 永远串行；
- 一次只有一个 Coding Task；
- Review 和 Test 基于同一个工作区。

## 确定性测试

- 测试结果以退出码为准；
- LLM 只分析结果；
- 不允许虚假宣称测试通过。

## 循环终止

- 最大次数明确；
- 达到上限必须结束；
- 最终报告说明失败原因。

---

# 14. 第一版验收标准

1. 可以在本地 Git 仓库中运行 CLI；
2. 可以输入 Issue 文本；
3. 可以自动探索相关代码；
4. Coordinator 能生成 Coding Task；
5. Coding Agent 能修改真实文件；
6. 能生成 Git Diff；
7. Review Agent 能检查修改；
8. Test Executor 能真实运行 pytest；
9. 测试失败后能自动重试；
10. 达到循环上限能停止；
11. 能生成最终 Markdown 报告；
12. 能通过 run-id 恢复任务。

---

# 15. 总结

这是一个受控的 Issue 修复工作流，不是全能 Coding Agent。

```text
CLI
→ StateGraph
→ Coordinator
→ Explore
→ Coding
→ Review
→ Test
→ Coordinator
→ Finish / Retry
```

关键取舍：

```text
Explore 可以少量并行
Coding 永远串行
Review 只读
Test 真实执行
State 保持精简
大型内容保存到文件
最多三轮修复
优先支持 Python 项目
```

对于个人项目，完整、稳定、可解释的闭环，比更多 Agent、更多并行和更多抽象更有价值。
