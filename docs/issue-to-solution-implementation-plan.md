# Issue-to-Solution Agent 详细施工计划

## 1. 实施原则

本计划面向个人独立开发，目标是逐步完成一个可运行、可演示、可解释的 Issue 自动修复 CLI。

实施时遵循以下原则：

1. 先完成最小闭环，再增加恢复、配置和增强能力；
2. 每个阶段只解决一个主要问题；
3. 每个阶段完成后都必须有可验证结果；
4. 不同时开发多个大模块；
5. 不提前实现第二版能力；
6. 普通逻辑优先使用普通函数，不滥用 Agent；
7. 优先复用 LangChain、LangGraph、Pydantic、Git 和 CLI 库已有能力；
8. Coding Agent 始终串行。

推荐总施工顺序：

```text
项目骨架
→ 仓库初始化
→ Issue 输入
→ 只读工具
→ Explore
→ Coordinator
→ Coding
→ Review
→ Test
→ 循环
→ Checkpoint
→ CLI 完善
→ 报告
→ 演示与评测
```

---

# 2. 第一阶段：项目初始化与骨架

## 2.1 目标

建立一个可以启动、可以读取配置、可以创建运行目录的基础项目。

此阶段不接入 LLM，不实现 Agent。

## 2.2 需要完成的内容

### 项目基础

- 初始化 Python 项目；
- 确定最低 Python 版本；
- 配置依赖管理；
- 配置包入口；
- 配置基础代码质量工具；
- 配置环境变量加载；
- 建立基础异常类型。

### 基础目录

先建立最小目录：

```text
issue_solver/
├── cli/
├── graph/
├── nodes/
├── agents/
├── tools/
├── schemas/
├── services/
├── prompts/
└── config.py
```

不要求每个目录立即存在大量文件。

### CLI 最小入口

先支持：

```bash
issue-solver --help
issue-solver run --help
```

`run` 暂时只打印输入参数和仓库路径。

### 配置对象

全局配置至少包含：

```text
model
max_cycles
command_timeout
run_root
log_level
```

### 运行目录服务

负责：

- 生成 run-id；
- 创建运行目录；
- 返回各类产物路径；
- 保存 JSON 和文本文件；
- 避免文件名冲突。

## 2.3 阶段产物

- 可安装或可直接执行的 CLI；
- 基础目录结构；
- 配置读取能力；
- run-id 生成能力；
- 运行目录创建能力；
- 基础日志。

## 2.4 验收标准

- `issue-solver --help` 正常；
- `issue-solver run --issue "test"` 可以启动；
- 每次启动能创建唯一运行目录；
- 配置缺失时有默认值；
- 环境变量异常时给出明确错误；
- 没有 LLM 依赖也能完成本阶段运行。

## 2.5 注意事项

- 不要在此阶段实现 StateGraph；
- 不要接入 GitHub API；
- 不要设计复杂插件系统；
- 不要建立过多抽象基类。

---

# 3. 第二阶段：核心 Schema 与 State

## 3.1 目标

确定整个项目的数据边界，避免后续节点随意传递字典。

## 3.2 需要定义的结构

### IssueSpec

必须包含：

```text
title
body
expected_behavior
actual_behavior
acceptance_criteria
```

### ExploreReport

必须包含：

```text
focus
relevant_files
relevant_symbols
findings
root_cause
test_targets
unknowns
```

### CodingTask

必须包含：

```text
objective
acceptance_criteria
relevant_files
root_cause
allowed_scope
validation
```

### CodingResult

必须包含：

```text
success
changed_files
summary
diff_path
validation
remaining_risks
```

### ReviewResult

必须包含：

```text
verdict
issues
suggestions
remaining_risks
```

### TestResult

保持简单：

```text
command
status
exit_code
duration
stdout_path
stderr_path
```

## 3.3 ResolverState

需要确定以下字段：

```text
run_id
phase
status
cycle

repo_path
base_commit
project_type
test_commands

issue

current_summary
next_action

explore_reports

coding_task
coding_result
changed_files
diff_path

review_result
test_results

error
run_dir
```

## 3.4 状态更新规则

需要提前明确：

- 哪个节点可以修改哪些字段；
- 哪些字段覆盖更新；
- 哪些字段追加更新；
- 哪些字段只在初始化时写入；
- 循环时哪些旧结果需要保留；
- 哪些结果应当清空后重新生成。

建议规则：

```text
issue              初始化后不再修改
base_commit        初始化后不再修改
explore_reports    新一轮探索时整体替换或按轮次记录
coding_task        每次 Coding 前替换
coding_result      每次 Coding 后替换
review_result      每次 Review 后替换
test_results       每轮测试后追加
current_summary    每次 Coordinator 后更新
cycle              每次完整修复循环增加
```

## 3.5 阶段产物

- 核心 Schema；
- ResolverState；
- State 字段说明文档；
- State 初始化工厂；
- State 序列化能力。

## 3.6 验收标准

- 可以构造一个完整初始 State；
- 所有 Schema 能执行校验；
- 缺失必填字段时能报错；
- State 可以保存为 JSON；
- 不在 State 中存储完整源码和大型日志。

## 3.7 注意事项

- 不要为每个小字段创建新的模型；
- 不要过度嵌套；
- 不要将 Agent 消息对象直接作为主要业务状态；
- 不要把完整 Diff 放入 State。

---

# 4. 第三阶段：仓库初始化与项目识别

## 4.1 目标

让工具可以安全进入一个本地项目，并生成可靠的仓库上下文。

## 4.2 Initialize 节点职责

### 仓库检查

- 仓库路径是否存在；
- 是否是目录；
- 是否位于 Git 仓库中；
- 是否能够获取 Git 根目录；
- 是否存在当前 Commit；
- 是否处于 detached HEAD；
- 工作区是否干净。

### Git 基线

记录：

```text
repo_path
base_commit
current_branch
initial_status
```

第一版建议：

- 工作区不干净时直接终止；
- 不自动 stash；
- 不自动 reset；
- 不自动创建分支。

### 项目识别

优先识别 Python 项目：

```text
pyproject.toml
requirements.txt
pytest.ini
tox.ini
setup.cfg
```

识别结果：

```text
project_type
test_commands
ignored_paths
protected_paths
```

### 项目配置

读取可选：

```text
.issue-solver.yaml
```

仅支持：

```text
test_commands
protected_paths
ignored_paths
```

项目配置优先于自动识别。

## 4.3 错误处理

至少覆盖：

- 非 Git 仓库；
- 仓库为空；
- 工作区不干净；
- 配置文件格式错误；
- 无法识别项目类型；
- 无法找到任何测试命令。

无法找到测试命令不一定立即失败，可以继续执行，但 State 中必须明确记录。

## 4.4 阶段产物

- RepositoryService；
- ProjectDetector；
- 项目配置读取器；
- Initialize 节点；
- 仓库状态快照。

## 4.5 验收标准

准备至少三个测试目录：

1. 正常 Python Git 仓库；
2. 非 Git 目录；
3. 有未提交修改的 Git 仓库。

必须分别得到正确结果。

## 4.6 注意事项

- 不要自动安装依赖；
- 不要自动修改项目配置；
- 不要尝试兼容所有构建系统；
- 初始化阶段不调用 LLM。

---

# 5. 第四阶段：Issue 输入与规范化

## 5.1 目标

统一处理用户输入的 Issue。

## 5.2 输入类型

第一优先级：

```text
直接文本
```

第二优先级：

```text
GitHub Issue URL
```

建议先完成直接文本，再接入 GitHub Issue URL。

## 5.3 文本输入流程

- 接收 CLI 参数；
- 保存原始输入；
- 生成标题或保留用户标题；
- 使用结构化输出整理 Issue；
- 无法提取的字段允许为空；
- 保存 `issue.json`。

## 5.4 GitHub Issue URL 流程

需要处理：

- URL 格式验证；
- 提取 owner、repo、issue number；
- 获取标题和正文；
- 获取失败时的明确错误；
- 不默认读取评论和附件；
- 原始内容保存到运行目录。

第一版不需要：

- 自动处理私有仓库；
- 自动读取所有评论；
- 自动解析截图；
- 自动跟踪关联 PR。

## 5.5 Parse Issue 节点输出

生成：

```text
IssueSpec
```

并写入：

```text
state.issue
```

## 5.6 阶段产物

- Issue 输入解析器；
- Issue 规范化节点；
- Issue 原始内容文件；
- `issue.json`。

## 5.7 验收标准

至少测试：

- 简短 Issue 文本；
- 包含复现步骤的文本；
- 信息不完整的文本；
- 无效 URL；
- 正常公开 GitHub Issue URL。

## 5.8 注意事项

- 不要过度推断缺失信息；
- 原始 Issue 必须保留；
- 结构化失败时不能丢失原始文本。

---

# 6. 第五阶段：仓库只读工具

## 6.1 目标

为 Explore 和 Coding 提供稳定、安全、受限的读取能力。

## 6.2 必须实现的工具

### list_files

能力：

- 列出指定目录；
- 支持最大深度；
- 支持忽略路径；
- 限制最大结果数；
- 默认不遍历依赖和缓存目录。

### read_file

能力：

- 读取指定文件；
- 支持行范围；
- 限制最大文件大小；
- 限制最大返回行数；
- 检测二进制文件；
- 返回规范化路径。

### search_text

能力：

- 在仓库内搜索文本；
- 支持路径过滤；
- 限制结果数；
- 返回文件、行号和匹配片段；
- 避免返回超长上下文。

### git_diff

能力：

- 返回当前 Diff 摘要；
- 返回修改文件列表；
- 支持将完整 Diff 写入文件；
- Agent 只接收受限长度内容。

## 6.3 可选工具

### find_symbol

可以基于简单文本或 AST 查找符号。

第一版不需要完整语言服务器。

### git_log

只提供有限历史记录，不允许一次返回大量提交。

## 6.4 通用安全要求

所有文件工具必须：

- 解析为绝对路径；
- 验证路径位于仓库根目录内；
- 禁止 `..` 越界；
- 处理符号链接；
- 限制文件大小；
- 限制返回数据量；
- 使用统一错误格式。

## 6.5 结果格式

工具结果应包含：

```text
success
summary
data
error
truncated
```

不要直接返回不可控的大字符串。

## 6.6 阶段产物

- 文件系统工具；
- 搜索工具；
- Git 只读工具；
- 路径安全检查；
- 工具级单元测试。

## 6.7 验收标准

必须验证：

- 正常读取仓库文件；
- 无法读取仓库外文件；
- 大文件会被截断；
- 依赖目录会被忽略；
- 搜索结果数量受限；
- Git Diff 能正确保存。

## 6.8 注意事项

工具输出大小控制是本阶段重点，不要只关注工具数量。

---

# 7. 第六阶段：Explore Agent

## 7.1 目标

根据 Issue 在仓库中定位相关代码和测试入口。

## 7.2 Agent 输入

- IssueSpec；
- 仓库基本信息；
- 项目类型；
- Explore 目标；
- 只读工具说明；
- 结果格式要求。

## 7.3 Explore 任务生成

第一版支持两种任务：

### 代码定位任务

目标：

- 找到入口；
- 找到相关文件；
- 找到相关符号；
- 追踪调用路径；
- 形成根因假设。

### 测试与影响任务

目标：

- 查找相关测试；
- 查找复现入口；
- 查找调用方；
- 判断可能影响范围；
- 给出建议测试目标。

Issue 简单时只生成一个任务。

## 7.4 Explore Agent 行为约束

- 只能使用只读工具；
- 不修改文件；
- 不运行危险命令；
- 不复制大段源码；
- 每个结论尽量给出文件和符号依据；
- 不确定时写入 `unknowns`；
- 不强行得出确定根因。

## 7.5 Explore 输出

每个任务输出 `ExploreReport`。

需要保存：

```text
state.explore_reports
run_dir/explore.json
```

## 7.6 可选并行

如果使用 LangGraph 并行：

- 最多两个任务；
- 两个任务相互独立；
- 只读；
- 汇总直接在 Explore 节点或 Coordinator 中完成；
- 不单独创建 Aggregator Agent。

## 7.7 阶段产物

- Explorer Agent；
- Explore Prompt；
- Explore 任务输入结构；
- Explore 节点；
- Explore 结果保存。

## 7.8 验收标准

在一个小型 Python 项目中，给定明确 Issue 后，Explore Report 至少包含：

- 1 个相关文件；
- 1 个相关符号或代码位置；
- 1 个根因假设；
- 1 个测试目标或明确说明未找到；
- 未确认问题。

## 7.9 注意事项

- Explore 的价值是减少 Coding 上下文，不是替代 Coding 阅读源码；
- 不要把每次工具调用轨迹全部放入 State；
- 不要为了展示并行强行拆分简单 Issue。

---

# 8. 第七阶段：Coordinator 与首次路由

## 8.1 目标

建立主 Agent 的决策能力。

## 8.2 Coordinator 输入

- IssueSpec；
- current_summary；
- Explore Reports；
- Coding Result；
- Review Result；
- 最新 Test Result；
- 当前 cycle；
- 最大循环限制。

不同阶段只传需要的字段，不需要每次都传全部内容。

## 8.3 Coordinator 输出

直接更新：

```text
next_action
current_summary
coding_task
```

允许动作：

```text
EXPLORE
CODE
FINISH
FAILED
```

## 8.4 初次调用行为

初次调用时：

- 判断 Issue 是否足以探索；
- 生成一个或两个 Explore 目标；
- 设置 `next_action = EXPLORE`。

## 8.5 Explore 后行为

读取 Explore Reports：

- 信息足够时生成 CodingTask；
- 根因不明确时重新 Explore；
- Issue 无法处理时 FAILED。

## 8.6 Review/Test 后行为

- Review 不通过且问题明确：CODE；
- Test 失败且修改方向明确：CODE；
- Test 失败且根因可能错误：EXPLORE；
- Review 通过且测试通过：FINISH；
- 达到上限：FAILED。

## 8.7 current_summary

每次 Coordinator 更新一段简短摘要，包含：

```text
当前根因判断
已完成修改
当前 Review 结论
当前测试状态
下一步原因
```

控制长度，避免随着循环无限增长。

## 8.8 阶段产物

- Coordinator Agent；
- Coordinator Prompt；
- 路由动作定义；
- Coordinator 节点；
- 条件路由函数。

## 8.9 验收标准

通过构造静态 State 测试以下路由：

- 初始状态 → EXPLORE；
- Explore 完成 → CODE；
- Review 不通过 → CODE；
- 测试失败且根因不确定 → EXPLORE；
- Review 和 Test 都通过 → FINISH；
- 超过次数 → FAILED。

## 8.10 注意事项

- Coordinator 不允许调用写文件工具；
- 不要让它生成自由格式长计划；
- 输出必须结构化；
- 路由规则应有确定性保护。

---

# 9. 第八阶段：最小 StateGraph

## 9.1 目标

先将 Initialize、Parse Issue、Coordinator、Explore 串成可运行图。

## 9.2 第一版图结构

```text
START
→ Initialize
→ Parse Issue
→ Coordinator
→ Explore
→ Coordinator
→ END（临时）
```

此阶段暂不加入 Coding、Review、Test。

## 9.3 需要实现

- StateGraph Builder；
- 节点注册；
- 固定边；
- 条件边；
- State 输入输出；
- 错误状态；
- 运行日志；
- 图可视化输出。

## 9.4 验收标准

给定 Issue 后，图可以：

1. 初始化仓库；
2. 解析 Issue；
3. 生成 Explore 任务；
4. 完成 Explore；
5. 生成 CodingTask；
6. 在临时结束节点打印结果。

## 9.5 注意事项

先验证图和状态是否清晰，再继续开发写文件能力。

---

# 10. 第九阶段：代码修改工具

## 10.1 目标

提供安全、单一、可追踪的代码修改能力。

## 10.2 修改方式选择

第一版只选一种主修改方式：

```text
apply_patch
```

或：

```text
write_file
```

建议优先选择适合 Agent 稳定调用、能清晰生成 Diff 的方式。

不要同时实现多套修改协议。

## 10.3 必须能力

- 读取目标文件；
- 修改指定内容；
- 保存文件；
- 检查修改后内容；
- 获取 Git Diff；
- 保存 Patch；
- 返回 changed_files。

## 10.4 写入安全

- 路径必须在仓库内；
- 禁止受保护目录；
- 禁止修改 `.git`；
- 禁止写入运行目录到仓库中；
- 修改前记录文件状态；
- 修改后检查文件是否仍可读取；
- 失败时给出明确错误。

## 10.5 可选受限命令

Coding Agent 可以拥有少量局部验证命令，但必须：

- 经过命令限制；
- 设置超时；
- 默认在仓库根目录运行；
- 不允许交互式命令；
- 不允许网络命令；
- 不允许删除性命令。

## 10.6 阶段产物

- 写文件或 Patch 工具；
- 写入范围验证；
- Git Diff 保存；
- changed_files 收集；
- 修改工具测试。

## 10.7 验收标准

在测试仓库中可以：

- 修改一个文件；
- 修改多个相关文件；
- 获取正确 Diff；
- 阻止修改受保护目录；
- 阻止访问仓库外路径；
- 修改失败时不留下半完成状态。

## 10.8 注意事项

- 不实现并行写入；
- 不实现 worktree；
- 不自动 commit；
- 不自动回滚整个仓库。

---

# 11. 第十阶段：Coding Agent

## 11.1 目标

根据 CodingTask 完成真实代码修改。

## 11.2 输入内容

- IssueSpec；
- CodingTask；
- Explore Reports；
- current_summary；
- 当前 changed_files；
- 当前 Diff 摘要；
- 读取和修改工具说明。

## 11.3 必须行为

1. 读取 CodingTask 中指定文件；
2. 必要时搜索关联代码；
3. 验证根因；
4. 修改代码；
5. 检查 Git Diff；
6. 执行局部验证；
7. 生成 CodingResult。

## 11.4 约束

- 一次只有一个 CodingTask；
- 不允许并行 Coding；
- 不修改无关文件；
- 不做大规模重构；
- 不添加无关依赖；
- 不重写整个文件，除非确有必要；
- 不隐瞒未完成问题；
- 无法完成时 `success = false`。

## 11.5 Coding 节点确定性检查

Agent 结束后，节点必须自行检查：

- Git Diff 是否为空；
- changed_files 是否真实；
- 是否触碰 protected_paths；
- 是否新增大文件或二进制文件；
- diff_path 是否成功生成；
- 当前仓库状态是否可读取。

如果检查失败，不接受 Agent 的成功结论。

## 11.6 阶段产物

- Coder Agent；
- Coding Prompt；
- Coding 节点；
- CodingResult 保存；
- diff.patch。

## 11.7 验收标准

选择一个人工构造的小 Issue，要求系统能够：

- 定位正确文件；
- 修改代码；
- 产生有效 Diff；
- 生成修改摘要；
- 保存 Patch；
- 不修改无关文件。

## 11.8 注意事项

Coding Agent 需要完整的仓库访问能力，但不需要把整个仓库放进模型上下文。

---

# 12. 第十一阶段：Review Agent

## 12.1 目标

对当前修改进行一次结构化质量检查。

## 12.2 输入

- IssueSpec；
- acceptance_criteria；
- Explore Reports；
- CodingTask；
- CodingResult；
- 当前 Diff；
- 修改后的相关文件。

## 12.3 检查维度

- 是否解决 Issue；
- 修改是否对应根因；
- 是否遗漏边界情况；
- 是否破坏接口；
- 是否存在明显回归风险；
- 是否缺少测试；
- 是否修改范围过大。

## 12.4 输出

`ReviewResult`：

```text
verdict
issues
suggestions
remaining_risks
```

`verdict`：

```text
APPROVE
REQUEST_CHANGES
```

## 12.5 节点行为

- 只读；
- 不修改文件；
- 不直接执行测试；
- 结果保存到 `review.json`；
- 结果写入 State。

## 12.6 阶段产物

- Reviewer Agent；
- Review Prompt；
- Review 节点；
- ReviewResult 保存。

## 12.7 验收标准

至少准备两组 Diff：

1. 明显正确；
2. 明显遗漏关键分支。

Reviewer 应给出不同 verdict，并能指出具体问题。

## 12.8 注意事项

- 不拆分多个 Reviewer；
- 不引入复杂严重等级；
- Review 结论不能直接替代测试。

---

# 13. 第十二阶段：Test Executor

## 13.1 目标

真实执行测试，并返回确定性结果。

## 13.2 命令来源

按顺序：

1. `.issue-solver.yaml`；
2. pyproject/pytest 配置；
3. ExploreReport.test_targets；
4. 内置默认 pytest 规则。

## 13.3 命令选择

第一版不要让 LLM 自由生成任意 Shell。

可以由确定性逻辑组合：

```text
目标测试文件
目标测试函数
模块测试
完整 pytest
```

## 13.4 执行要求

- 串行执行；
- 每条命令独立记录；
- 设置超时；
- 记录 exit_code；
- 记录 duration；
- stdout/stderr 写文件；
- 限制输出大小；
- 捕获进程异常；
- 执行后检查工作区是否被测试修改。

## 13.5 状态判断

```text
exit_code == 0
→ PASSED

exit_code != 0
→ FAILED

无法启动依赖或命令不存在
→ ENVIRONMENT_ERROR

超过超时
→ TIMEOUT
```

## 13.6 多条命令策略

建议：

- 先运行最相关测试；
- 相关测试失败时停止后续测试；
- 相关测试通过后再运行模块测试；
- 完整测试可配置是否执行。

## 13.7 阶段产物

- Shell 执行服务；
- 命令限制；
- Test Executor 节点；
- TestResult；
- tests.log。

## 13.8 验收标准

必须测试：

- 正常通过；
- 断言失败；
- 命令不存在；
- 超时；
- 输出过长；
- 多条命令顺序执行。

## 13.9 注意事项

- 测试结果由程序判断；
- LLM 不得宣称未运行的测试通过；
- 第一版默认禁止网络命令。

---

# 14. 第十三阶段：完整修复循环

## 14.1 目标

把 Explore、Coding、Review、Test 连接成完整闭环。

## 14.2 完整图

```text
START
→ Initialize
→ Parse Issue
→ Coordinator
→ Explore
→ Coordinator
→ Coding
→ Review
→ Test
→ Coordinator
→ Explore / Coding / Finalize / Failed
```

## 14.3 路由规则

### Explore 后

```text
信息足够
→ CODE

信息不足且未超限
→ EXPLORE

无法继续
→ FAILED
```

### Coding 后

```text
修改成功且 Diff 有效
→ REVIEW

修改失败
→ COORDINATOR
```

### Review 后

第一版仍然进入 Test，让 Coordinator 同时看到 Review 和 Test 结果。

这样流程固定，减少条件边。

### Test 后

进入 Coordinator。

### Coordinator 最终判断

```text
Review APPROVE + Test PASSED
→ FINISH

Review REQUEST_CHANGES
→ CODE

Test FAILED + 根因明确
→ CODE

Test FAILED + 根因不确定
→ EXPLORE

达到限制
→ FAILED
```

## 14.4 计数器

至少记录：

```text
cycle
explore_rounds
coding_attempts
```

如果不想增加 State 字段，可以将 explore/coding 次数包含在简短运行元数据中，但必须能可靠限制。

## 14.5 验收标准

准备以下场景：

1. 一次修复成功；
2. 第一次 Review 不通过，第二次成功；
3. 第一次测试失败，第二次成功；
4. 根因错误，需要重新 Explore；
5. 达到最大次数后失败。

## 14.6 注意事项

- 不要创建过多条件边；
- 不要让每个节点自由决定跳转；
- 路由集中在 Coordinator 和少量确定性规则中。

---

# 15. 第十四阶段：Checkpoint 与恢复

## 15.1 目标

允许任务中断后继续执行。

## 15.2 Checkpoint 设计

- 使用 SQLite；
- 每个 run 使用唯一 thread-id；
- Checkpoint 与 run-id 关联；
- 保存关键 State；
- 不保存大型日志内容；
- 外部文件仍保存在运行目录。

## 15.3 resume 行为

恢复前检查：

- run-id 是否存在；
- Checkpoint 是否存在；
- 仓库路径是否存在；
- 当前 Commit 是否与预期一致；
- 工作区是否与中断时兼容；
- diff.patch 是否仍与当前状态匹配。

状态不一致时，不自动猜测，直接提示失败原因。

## 15.4 中断场景

需要考虑：

- LLM 调用中断；
- 测试执行中断；
- 用户关闭 CLI；
- 运行目录仍在但 Checkpoint 不完整；
- Checkpoint 存在但仓库已改变。

## 15.5 阶段产物

- SQLite Checkpointer；
- run-id/thread-id 映射；
- resume 命令；
- 恢复前仓库检查；
- 恢复日志。

## 15.6 验收标准

- 在 Explore 后中断，可以恢复；
- 在 Coding 后中断，可以恢复；
- 仓库被修改后恢复，会明确拒绝；
- 不会重复创建新 run。

## 15.7 注意事项

Checkpoint 只保证流程状态，不保证外部仓库不被人为修改。

---

# 16. 第十五阶段：Finalize 与报告

## 16.1 目标

让结果可读、可演示、可复盘。

## 16.2 report.md 内容

建议结构：

```text
任务状态
Issue 摘要
根因分析
修改内容
修改文件
Review 结果
测试结果
剩余风险
Patch 路径
运行信息
```

## 16.3 CLI 最终输出

CLI 只显示简洁摘要：

```text
状态
修改文件数
Review 结论
测试结论
Patch 路径
报告路径
```

完整内容写入 `report.md`。

## 16.4 失败报告

FAILED 也必须生成报告，包含：

- 失败阶段；
- 最后一次根因判断；
- 已经尝试的修改；
- 最后 Review；
- 最后测试；
- 达到的限制；
- 建议人工处理方向。

## 16.5 阶段产物

- Finalize 节点；
- Markdown 报告生成器；
- CLI 结果渲染；
- `report` 命令。

## 16.6 验收标准

成功和失败场景都能生成完整报告。

---

# 17. 第十六阶段：CLI 完善

## 17.1 run

```bash
issue-solver run --issue <issue-url-or-text>
```

参数：

```text
--repo
--model
--max-cycles
--dry-run
```

### dry-run

第一版可定义为：

- 完成 Initialize、Parse Issue、Explore 和 CodingTask；
- 不允许 Coding Agent 写文件；
- 输出计划和相关文件。

不要让 dry-run 有多种模糊语义。

## 17.2 resume

```bash
issue-solver resume <run-id>
```

## 17.3 report

```bash
issue-solver report <run-id>
```

## 17.4 CLI 错误输出

必须区分：

- 用户输入错误；
- 仓库状态错误；
- 配置错误；
- LLM 错误；
- 测试环境错误；
- 系统内部错误。

## 17.5 验收标准

- 三个命令可用；
- 退出码合理；
- 错误信息清楚；
- 不输出内部堆栈给普通用户，调试模式除外。

---

# 18. 第十七阶段：测试计划

## 18.1 单元测试

重点覆盖普通逻辑：

- 路径安全；
- 配置解析；
- 项目识别；
- 运行目录；
- Git 状态检查；
- State 初始化；
- 路由规则；
- 测试状态判断；
- 报告生成。

## 18.2 工具测试

覆盖：

- list_files；
- read_file；
- search_text；
- git_diff；
- apply_patch/write_file；
- 受限命令执行。

## 18.3 节点测试

使用假的 LLM 输出测试：

- Parse Issue；
- Coordinator；
- Explore；
- Coding；
- Review；
- Finalize。

不要让所有自动化测试都依赖真实 LLM。

## 18.4 集成测试

准备固定小仓库，覆盖：

- 简单单文件 bug；
- 多文件相关 bug；
- 测试失败后重试；
- Review 拒绝；
- 达到最大循环；
- 恢复任务。

## 18.5 真实演示测试

选择 3 至 5 个真实或经过整理的 GitHub Issue。

建议类型：

- 条件判断错误；
- 边界值错误；
- 参数校验遗漏；
- 返回值错误；
- 测试缺失。

避免第一批就选择：

- 超大型框架；
- 编译环境复杂项目；
- 依赖外部服务；
- 数据库迁移；
- 前后端联合修改；
- 模糊需求。

---

# 19. 第十八阶段：演示与项目包装

## 19.1 README

需要说明：

- 项目解决什么问题；
- 为什么不做全能 Coding Agent；
- 核心架构图；
- CLI 使用方式；
- 支持范围；
- 演示示例；
- 安全限制；
- 当前不足；
- 后续计划。

## 19.2 演示流程

推荐展示：

```text
输入 Issue
→ 展示 Explore 结果
→ 展示 CodingTask
→ 展示 Diff
→ 展示 Review
→ 展示测试
→ 展示最终报告
```

## 19.3 可展示指标

第一版只记录：

```text
最终状态
总循环次数
Explore 次数
Coding 次数
修改文件数
测试结果
总运行时间
```

## 19.4 简历表述重点

强调：

- 使用 LangGraph 构建可恢复状态工作流；
- 主 Agent 与专用子 Agent 分工；
- 严格串行代码修改；
- 真实 Git Diff 和测试执行；
- 自动 Review/Test 反馈循环；
- CLI 跨仓库调用；
- 上下文压缩和运行产物管理。

---

# 20. 推荐提交顺序

为了方便学习和回顾，每次只完成一小部分。

建议提交粒度：

```text
1. 初始化项目与 CLI
2. 添加核心 Schema 和 State
3. 添加运行目录管理
4. 添加 Git 仓库初始化
5. 添加项目识别
6. 添加 Issue 文本解析
7. 添加只读文件工具
8. 添加搜索与 Git Diff 工具
9. 添加 Explore Agent
10. 添加 Coordinator
11. 构建最小 StateGraph
12. 添加代码修改工具
13. 添加 Coding Agent
14. 添加 Review Agent
15. 添加 Test Executor
16. 完成循环路由
17. 添加 Checkpoint
18. 添加 resume/report
19. 添加集成测试
20. 完善 README 和演示
```

每次提交应能说明：

- 完成了什么；
- 为什么这样设计；
- 如何验证；
- 下一步是什么。

---

# 21. 关键风险与处理

## 21.1 Agent 输出不稳定

处理：

- 结构化输出；
- 字段校验；
- 失败重试；
- 给出明确工具使用规则；
- 减少自由文本。

## 21.2 上下文过长

处理：

- State 只存摘要；
- 工具结果截断；
- Diff 写入文件；
- Coding 按需读取；
- Coordinator 不接收完整轨迹。

## 21.3 Coding 修改范围失控

处理：

- CodingTask.allowed_scope；
- protected_paths；
- changed_files 检查；
- Review；
- Git Diff 检查。

## 21.4 测试环境失败

处理：

- 区分 FAILED 和 ENVIRONMENT_ERROR；
- 保存 stderr；
- 不无限重试；
- 最终报告明确说明。

## 21.5 循环不停止

处理：

- 固定最大 cycle；
- 固定最大 Explore 和 Coding 次数；
- 达到上限后强制 FAILED。

## 21.6 恢复时仓库已变化

处理：

- 检查 base_commit；
- 检查当前 Diff；
- 不自动合并未知修改；
- 明确拒绝恢复。

---

# 22. 第一版完成定义

只有同时满足以下条件，第一版才算完成：

1. CLI 可以启动；
2. 能在本地 Python Git 仓库中运行；
3. 能接收 Issue 文本；
4. 能自动探索仓库；
5. Coordinator 能生成 CodingTask；
6. Coding Agent 能真实修改文件；
7. 能生成 Patch；
8. Review Agent 能检查修改；
9. Test Executor 能真实运行 pytest；
10. 失败后能自动重试；
11. 循环达到上限会停止；
12. 能生成成功和失败报告；
13. 能使用 run-id 恢复任务；
14. 至少完成 3 个可展示 Issue 案例。

---

# 23. 第二版候选能力

第一版稳定后再考虑：

- GitHub Issue API 完整接入；
- 自动创建 PR；
- Docker 沙箱；
- 更多语言；
- 更精确符号索引；
- 多 Reviewer；
- 基线测试；
- SWE-bench 子集；
- 终端实时进度界面；
- 执行轨迹可视化。

这些不应阻塞第一版施工。

---

# 24. 最终施工顺序总结

```text
基础项目
→ State 与 Schema
→ 仓库初始化
→ Issue 解析
→ 只读工具
→ Explore
→ Coordinator
→ 最小 StateGraph
→ 修改工具
→ Coding
→ Review
→ Test
→ 完整循环
→ Checkpoint
→ CLI 完善
→ 报告
→ 测试
→ 演示
```

整个开发过程中始终坚持：

```text
先跑通，再增强
先确定性逻辑，再 Agent
先单仓库、Python、pytest
Coding 永远串行
不提前实现第二版能力
```
