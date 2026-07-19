# issue-solver 项目说明

## 1. 项目定位

`issue-solver` 是一个面向**本地 Git 仓库**的 Issue 修复编排工具。它接收一段 Issue 文本、GitHub Issue URL 或本地 UTF-8 Issue 文件，将自然语言问题转为结构化需求；随后在受限范围内探索代码、生成修改、审查修改，并在目标仓库已准备好的 Python 虚拟环境中执行真实测试。

它不是“让模型直接改文件然后相信模型说已完成”的脚本，而是一条带状态、边界、验证和审计证据的闭环：

```text
Issue 输入
   │
   ├─ 环境预检（目标仓库、Git、虚拟环境、pytest）
   ├─ 初始化（干净工作区、base commit、全量测试命令）
   ├─ Issue 规范化
   └─ Coordinator 决策
          │
          ├─ 并行 Explore ────────────────┐
          │                               │
          └─ Code → Review → Real Test ───┤
                                           ▼
                                    Coordinator 再决策
                                      ├─ 继续探索
                                      ├─ 返工修改
                                      ├─ 完成并保存最终 Patch
                                      └─ 失败并按策略回滚/保留现场
```

最终成功时，工具不会替用户提交 Git commit，而是在运行目录保存可复核、可重新应用的 `diff.patch` 和元数据。这样把“修改工作区”和“提交业务代码”分开，开发者仍然掌握最终合并权。

## 2. 想解决的问题与非目标

### 解决的问题

传统的 AI 修复流程常见几个风险：模型凭不完整上下文改错位置；一次改动越过需求范围；测试在错误的 Python 环境中运行；失败时没有可追踪证据；或者模型把“我认为正确”误当成“已经验证正确”。本项目将这些风险分别交给不同模块处理。

### 当前非目标

- 不自动创建虚拟环境，不安装依赖；目标项目、pytest 和全部测试依赖由目标仓库开发者提前安装。
- 不调用 tox 或执行其环境矩阵；即使仓库存在 tox 配置，也只使用已准备好的仓库环境直接运行 pytest。
- 不自动提交、推送代码或创建 Pull Request。
- 首版仅完整支持 Python + pytest；不会伪装支持 Node、Java、Go 等项目。
- 不把目标仓库的 `.env` 作为控制器配置来源；模型配置只读取 `issue-solver` 项目根目录的 `.env`。
- 不执行任意 Shell 测试命令；只接受受限的 pytest 命令形式。

这些限制是刻意的。对于能写入真实工作区的 Agent，先缩小能力边界，比一开始追求“什么都自动化”更可靠。

不接入 tox 也是同一原则的直接结果。按照 [tox 官方生命周期](https://tox.wiki/en/latest/user_guide.html)，tox 是环境编排器，正常执行可能创建或重建虚拟环境、安装环境依赖、构建并安装目标项目，再运行配置命令。这些职责与本工具“只消费开发者已准备环境、不改变依赖状态”的边界冲突。即使 tox 提供跳过安装的参数，也仍要求对应 tox 环境已存在且与配置一致，会形成第二套环境发现和校验规则。因此当前实现不把 `tox.ini` 当作测试执行入口：常规 pytest 项目即使保留 tox 配置也可直接运行；依赖 tox 的 `setenv`、工作目录切换、前置命令或多解释器矩阵的项目则明确不在支持范围内。

## 3. 如何使用与运行边界

本项目提供两种入口。

```powershell
# 在 issue-solver 源码目录中运行，必须显式传入目标仓库
python -m cli.commands run --repo <target-repo> --issue <issue-url-or-text>

# 可编辑安装后，在目标仓库或其子目录中运行
issue-solver run --issue <issue-url-or-text>
```

第二种方式会从当前目录向上定位 Git 根目录。控制器仓库自身被明确禁止作为目标仓库，以避免在 `issue-solver` 本目录执行测试、污染本项目环境或让 Agent 修改自身实现。

`--issue` 支持三类输入：

| 输入 | 处理方式 |
| --- | --- |
| 普通文本 | 直接作为原始 Issue 内容 |
| `https://github.com/<owner>/<repo>/issues/<number>` | 调用 GitHub Issue API 获取标题和正文；可选使用控制器 `.env` 中的 `GITHUB_TOKEN` |
| 绝对路径 `.md` / `.txt` | 以 UTF-8 读取本地文件；相对路径会被拒绝 |

默认运行产物写入控制器项目根目录的 `.issue-solver-runs/<目标仓库名>/<run-id>/`。这个目录必须位于目标 Git 仓库外，避免测试缓存、日志和最终 Patch 出现在用户业务仓库的工作区中。`RUN_ROOT` 可由控制器 `.env` 配置，命令行 `--run-root` 可临时覆盖。

## 4. 总体架构

### 4.1 分层与职责边界

```text
cli/          命令行参数、环境预检、终端可视化、交互式回滚确认
graph/        LangGraph 状态定义、路由规则、节点连接
nodes/        每一个工作流步骤的确定性编排和状态更新
agents/       给 LLM 的角色、工具集合、结构化输出策略
prompts/      各角色的系统提示词与上下文组装
schemas/      Pydantic 数据契约与跨字段校验
tools/        只读仓库工具、Git 查看工具、受限 Patch 修改工具
services/     Issue 加载、项目检测、环境发现、测试执行、产物落盘
```

核心设计原则是：**LLM 负责判断和生成结构化意图；程序负责权限、状态转移、文件写入、测试执行和最终准入。**

例如，Coding Agent 可以说“我改了三个文件”，但 `nodes/coding.py` 仍会调用确定性的 `inspect_coding_changes`，确认真实 Diff 非空、真实文件列表与 `CodingResult.changed_files` 完全一致后，才允许进入 Review。模型输出不是系统事实，Git 快照和测试进程结果才是。

### 4.2 为什么采用状态图

工作流由 LangGraph 的 `StateGraph` 组织，而不是一串线性函数调用。原因是 Issue 修复天然存在回路：探索可能不够，要继续探索；Review 可能要求返工；测试可能暴露根因判断错误；达到上限后还要有确定的失败出口。

主路径如下：

```text
START
  → initialize
  → parse_issue
  → coordinator
       ├─ EXPLORE：动态 fan-out 多个 explore 节点，全部回到 coordinator
       ├─ CODE：coding → review → test → coordinator
       ├─ FINISH：finalize → END
       └─ FAILED：finalize / END（依状态处理）
```

`EXPLORE` 使用 LangGraph 的 `Send` 动态分发，Coordinator 每次最多生成 3 个彼此独立的探索目标。这些探索读同一仓库但不写入，因此可以并行；它们各自返回报告后借助状态 reducer 合并。`CODE` 则严格串行，因为多个写入 Agent 并发修改同一个 Git 工作区会产生竞争、覆盖和不可解释的 Diff。

## 5. 核心流程逐步说明

### 5.1 CLI 与环境预检

`cli/commands.py` 先创建独立的运行目录，随后在**调用模型之前**执行环境预检。预检只查找目标仓库根目录的 `.venv`、`venv` 或 `.conda`，并要求：

1. 只能有一个候选环境；
2. 环境位于目标仓库内、不是符号链接，且已经被 Git ignore；
3. 存在正确的解释器和 `pyvenv.cfg` / `conda-meta` 标识；
4. 用该解释器验证 `sys.prefix`，避免 PATH 指向了错误 Python；
5. 用 `<目标 Python> -m pytest --version` 验证 pytest 已安装且可启动；
6. 运行目录可写。

预检失败会写入 `failure_environment.json`，并明确退出：不安装依赖、不调用 LLM、不修改工作区。它把“开发环境没有准备好”的问题从模型推理问题中剥离出来，避免在流程最后才发现 pytest 无法运行。

这意味着开发者必须在启动前完成环境准备。`.venv`、`venv` 或 `.conda` 中不仅要有 pytest，还要有目标项目及测试导入、插件和运行所需的全部依赖；工具不会根据 `requirements.txt`、`pyproject.toml` 或 tox 配置补装任何内容。

### 5.2 初始化仓库

`initialize` 节点确认目标路径是有效 Git 仓库、工作区干净，并记录当前 `HEAD` 为 `base_commit`。这是之后生成 Diff、验证工作区、回滚的唯一基线。

项目检测当前主要识别 Python 项目标记，并发现全量 pytest 回归命令（例如 `pytest -q`）。`tox.ini` 不参与执行器选择：如果同时存在 pytest 配置或 `tests/`，仍然直接运行 pytest；只有 tox 配置、没有可直接识别的 pytest 入口时会明确终止。如果没有可支持的测试入口，工作流不会假设其他测试框架可执行。

### 5.3 Issue 规范化

`parse_issue` 节点先由 `services/issue_loader.py` 读取原始输入，再让模型输出 `IssueSpec`。它将不稳定的自然语言统一成标题、原始描述、期望行为、实际行为和验收条件，后续角色不必反复从原文猜测任务。

### 5.4 Coordinator：唯一的工作流决策者

Coordinator 没有文件工具，也不能执行命令；它只返回 `CoordinatorDecision`。它的作用类似技术负责人：根据 Issue、探索报告、已有 Diff、Review 结论和最近测试结果决定下一步是 `EXPLORE`、`CODE`、`FINISH` 还是 `FAILED`。

程序会再次验证该决定，而不盲信模型：

- 首次决策只能是 `EXPLORE`；
- `EXPLORE` 必须携带 1 到 3 个目标，且不能同时携带 CodingTask；
- `CODE` 必须携带完整 CodingTask；
- `CodingTask.test_targets` 必须给出 1 到 10 个仓库内的 `.py` 测试文件或 pytest node ID；
- 返工时，新任务的 `allowed_scope` 必须覆盖此前累计修改的所有文件；
- 只有 Review 为 `APPROVE` 且最近一轮全部真实测试为 `PASSED` 时，才允许 `FINISH`；
- `MAX_CYCLES` 默认是 5，达到限制会记录失败原因并在有修改时请求回滚。

`cycle` 在真实测试完成后增加，表示已经完成多少轮“修改—验证”闭环；`repair_round = cycle + 1` 用于给当前修复轮次编号。单轮内若需要再次探索或再次编码，则通过 `stage_call` 区分。

### 5.5 Explore：并行只读调查

Explore Agent 只拿到 `list_files`、`read_file`、文本/符号搜索和 `git_log` 等只读工具。它输出 `ExploreReport`，其中包含相关文件、符号、代码证据、潜在根因、建议测试点和未知项。

设计上，Explore 的结果不是“长篇聊天记录”，而是可供 Coordinator 和 Coder 消费的结构化证据。多个报告并行产生，状态中的 `explore_reports` 使用追加 reducer 聚合，避免一个并行分支覆盖另一个分支的结果。

### 5.6 Coding：受限、可重复、可审计的写入

Coding Agent 是唯一可修改目标工作区的角色，但它并没有 Shell 权限。它只能读文件、搜索，以及调用两个绑定上下文的写入工具：

- `apply_patch`：应用 unified diff；
- `inspect_changes`：读取相对 `base_commit` 的累计修改、文件列表和 Diff 摘要。

Coordinator 下发的 `CodingTask.allowed_scope` 会被固化为 `CodingToolContext`。该上下文同时保存仓库根、base commit、运行目录、修复轮次和阶段调用号。创建上下文时会确认 HEAD 未变化；首次写入前还要求工作区干净，返工时才允许已有的累计修改。

`apply_patch` 的核心安全检查包括：

- 仅接受受控 unified diff，不经过 Shell；
- 拒绝仓库外路径、`..` 路径、符号链接、受保护目录、超出 `allowed_scope` 的路径；
- 默认保护 `.git`、虚拟环境、缓存、构建目录、`node_modules` 和运行产物目录；
- 拒绝二进制 Patch、文件权限/类型变更、submodule、被 Git ignore 的新文件；
- 限制单 Patch 最多修改 20 个文件、文本文件最大 1 MiB、Patch 最大 100,000 字符；
- 在应用前执行 `git apply --check`，应用后重新捕获相对基线的实际 Git 快照；
- 若 Patch 已部分应用后发现验证失败，则恢复这一次 Patch 前的文件状态。

一个 CodingTask 可以通过一次 Patch 同时修改多个相关文件，也可以在同一 Coding 阶段多次 `apply_patch` 迭代；但整个 Coding 节点是串行的。这样既允许“代码 + 测试”作为原子任务一起改，也避免了多个 Agent 争抢同一工作区。

Coding 节点会拒绝以下情况：Agent 报告 `success=false`、在 Coding 阶段提前声称有最终 Patch、实际 Diff 为空、或 Agent 声明的文件列表与 Git 检查结果不一致。发生 Coding 失败时，系统记录失败产物，并在安全条件满足时回滚到 `base_commit`。

### 5.7 Review 与真实测试

Review Agent 是只读角色，能读取当前工作区、搜索代码并查看相对基线的 Git Diff。它输出 `ReviewResult`：`APPROVE` 时 `issues` 必须为空；`REQUEST_CHANGES` 时必须给出至少一个具体问题。结构校验或 Agent 失败会保存 `failure_review_rXX.json`，保留工作区，并在 CLI 交互场景询问用户是否回滚。

Review 后仍会执行真实测试，而不是只依赖 Review 结论。Coordinator 不执行命令，而是在 `CodingTask.test_targets` 中给出精确测试文件或 pytest node ID。Test 节点先把这些目标构造成定向命令；定向测试通过后，再执行 Initialize 检测到的全量回归命令。任一测试失败都会停止本轮后续命令并把结果交回 Coordinator。

测试目标只允许仓库相对 `.py` 文件和可选的 `::` 选择器。程序会拒绝绝对路径、路径穿越、Shell 控制字符和解析后逃出仓库的符号链接。目标通过校验后，逻辑命令统一解析为 argv，并绑定为：

```text
<目标虚拟环境的 python> -m pytest <原测试参数>
    --basetemp=<运行目录中的临时目录>
    -o cache_dir=<运行目录中的缓存目录>
```

测试进程的 `cwd` 是目标仓库根目录，但 `TEMP`、`TMP`、`TMPDIR`、pytest basetemp 和 pytest cache 都指向运行目录；同时通过 `VIRTUAL_ENV` 或 `CONDA_PREFIX` 与 PATH 前缀明确使用目标环境。这样既满足“必须 `python -m pytest`”的解释器一致性，也避免系统临时目录或业务仓库中的 pytest 缓存权限/污染问题。

执行器不允许管道、重定向等 Shell 控制字符，只支持 `pytest ...` 或 `python/py -m pytest ...` 形式，并以 `shell=False` 启动；`tox` 和 `python -m tox` 会被拒绝。每条测试会落盘完整 stdout/stderr；给 Coordinator 的仅是受行数和 20,000 字符限制的末尾摘要。测试前后还会计算工作区指纹，若测试本身修改了 Git 工作区，会标记为环境错误并走保护路径。

### 5.8 Finalize：最终 Patch 的准入门

`finalize` 不是简单把当前 Diff 写盘。只有以下两个条件同时满足，才调用 `save_final_patch`：

1. 最近 Review 的 verdict 是 `APPROVE`；
2. 最近一轮所有 TestResult 都是 `PASSED`。

保存前会再次从 `base_commit` 构建累计 Diff，并用临时 Git index 验证 Patch 可应用到该基线。之后原子写入：

- `diff.patch`：最终可应用的文本 Patch；
- `diff.json`：`base_commit`、实际 `changed_files` 和 Patch SHA-256。

若流程需要回滚，工具确认 HEAD 仍为本次 base，再恢复跟踪文件并安全删除本次产生的未跟踪普通文件；回滚结果也会被写入失败记录。失败路径的目标是“可解释且尽量恢复基线”，而不是静默删除证据。

## 6. 重要数据结构

### 6.1 `ResolverState`：整个工作流的单一事实来源

`graph/state.py` 中的 `ResolverState` 是节点之间共享的 TypedDict。它把“当前处于什么阶段、掌握了什么证据、工作区是否有修改、是否应回滚”放进显式状态，而不是隐藏在提示词或全局变量中。

| 分组 | 代表字段 | 作用 |
| --- | --- | --- |
| 运行标识 | `run_id`、`repo_path`、`run_dir`、`issue_input` | 将一次执行与仓库、日志目录、输入绑定 |
| 生命周期 | `phase`、`status`、`next_action` | 描述当前节点与下一步路由 |
| 修复轮次 | `cycle`、`repair_round`、`explore_stage_call`、`coding_stage_call` | 区分闭环轮次、同轮多次探索/编码和产物命名 |
| 基线与环境 | `base_commit`、`project_type`、`test_commands`、`environment` | 固化可重复验证的仓库、全量回归命令和运行环境 |
| 证据与任务 | `issue`、`explore_reports`、`coding_task`、`coding_result` | 把自然语言任务逐步转为可执行契约 |
| 验证 | `review_result`、`latest_test_results`、`test_results` | 保存审查结论和本轮/历史测试结果 |
| 工作区与失败 | `changed_files`、`diff_path`、`rollback_required`、`rollback_reason`、`error` | 控制 Patch 保存、回滚和错误展示 |

其中 `explore_reports`、`explore_errors` 和 `test_results` 使用 LangGraph 的 `add` reducer。并行 Explore 只追加自己的结果，不会覆盖其他分支；这也是能安全使用 fan-out 的关键。

### 6.2 结构化模型契约

| 模型 | 关键字段 | 设计意义 |
| --- | --- | --- |
| `IssueSpec` | `title`、`body`、`expected_behavior`、`actual_behavior`、`acceptance_criteria` | 把输入 Issue 统一为后续角色可复用的需求视图 |
| `ExploreReport` | `relevant_files`、`relevant_symbols`、`findings`、`root_cause`、`test_targets`、`unknowns` | 把探索转为带不确定性声明的代码证据 |
| `CoordinatorDecision` | `next_action`、`current_summary`、`explore_focuses`、`coding_task` | 用互斥校验限制 Coordinator 的路由输出 |
| `CodingTask` | `objective`、`acceptance_criteria`、`root_cause`、`relevant_files`、`allowed_scope`、`test_targets` | 将“改一下”变成带修改边界和定向测试目标的可验证任务 |
| `CodingResult` | `success`、`changed_files`、`summary`、`validation`、`remaining_risks` | 声明 Agent 的操作结果；随后由 Git 结果交叉验证 |
| `ReviewResult` | `verdict`、`issues`、`suggestions`、`remaining_risks` | 强制通过/不通过与问题列表一致 |
| `EnvironmentInfo` | `kind`、`root_path`、`python_executable`、`pytest_version`、`source` | 固化测试实际使用的虚拟环境身份 |
| `TestResult` | `command`、`resolved_command`、`status`、`exit_code`、`stdout_path`、`stderr_path`、`output_tail` | 区分逻辑命令、真实 argv、完整证据和给模型的受限上下文 |

`CodingTask`、`CoordinatorDecision`、`ReviewResult`、`TestResult` 和 `EnvironmentInfo` 使用较严格的 Pydantic 校验：例如路径必须是仓库内相对路径、字段不能为空、额外字段被禁止或通过跨字段规则拒绝矛盾组合。它们是 LLM 输出与确定性程序之间的“类型边界”。

## 7. Agent、Prompt 与上下文

| Agent | 是否可写 | 工具 | 输入重点 | 输出 |
| --- | --- | --- | --- | --- |
| Coordinator | 否 | 无 | Issue、探索报告、编码/审查/测试历史、循环上限 | `CoordinatorDecision` |
| Explorer | 否 | 文件列表、读文件、文本/符号搜索、`git_log` | 一条探索目标、仓库路径 | `ExploreReport` |
| Coder | 是，且仅 Patch | 只读工具、`apply_patch`、`inspect_changes` | Issue、CodingTask、探索报告、当前摘要 | `CodingResult` |
| Reviewer | 否 | 只读工具、`git_diff` | Issue、CodingTask、CodingResult、探索报告、base commit | `ReviewResult` |

Prompt 层不负责绕过程序约束，而是把正确的职责告知模型。例如 Coder 的系统提示明确禁止执行测试、禁止修改 Git 历史、要求最后检查 Diff，并要求 `changed_files` 与工具结果完全一致；测试只能由 Test node 执行。即使模型违背提示，工具和节点仍会二次拦截。

模型层使用 `langchain-deepseek`。`ReasoningChatDeepSeek` 对 DeepSeek 多轮工具调用做了兼容：回填必要的 `reasoning_content`，并避免在 thinking 模式传递不兼容的 `tool_choice`。Coordinator 则使用函数调用形式的结构化输出；有工具的角色通过 `ToolStrategy` 取得 Pydantic 模型输出。

## 8. 日志、审计与可追踪性

每一次运行都拥有一个 ULID 风格 `run_id` 和独立目录。所有 JSON 以 UTF-8 写入，并使用排他创建（`x` 模式）避免悄悄覆盖同名证据；最终 Patch 使用原子写入。

常见产物如下：

| 文件模式 | 内容 |
| --- | --- |
| `environment_result.json` / `failure_environment.json` | 环境预检结果或失败原因；失败文件还记录未调用 LLM、未安装依赖、未修改工作区 |
| `issue.json` | 规范化后的 Issue |
| `explore_r01_s01_i01.json` | 第 1 修复轮、第 1 次探索调用、第 1 个并行任务的报告 |
| `coding_task_r01_s01_i00.json` | 某次 Coding 前的受限任务契约 |
| `coding_result_r01_s01_i02.json` | Coding 阶段第 2 次 Patch 后的结构化结果 |
| `coding_audit_r01_s01.jsonl` | 每次 `apply_patch` 的成功/失败、触碰文件、输入 Patch 哈希和累计 Diff 哈希；一行一个事件 |
| `review_result_r01.json` / `failure_review_r01.json` | 审查结论或结构/Agent 失败记录 |
| `test_result_r01.json` | 本轮全部 TestResult；完整输出另存为 stdout/stderr 日志 |
| `test_stdout_r01_i01.log`、`test_stderr_r01_i01.log` | 第 1 条测试命令（通常为定向测试）的完整进程输出 |
| `rollback_decision_r01.json` | Review 失败后用户是否选择回滚 |
| `diff.patch`、`diff.json` | 仅在 Review 和测试都通过后保存的最终补丁与校验元数据 |
| `finalize_result_r01.json`、`failure_coding_*.json`、`failure_test_r01.json` | 收尾、编码或测试失败的可追踪记录 |

文件名中的坐标含义是：`r` 为修复轮次（repair round），`s` 为同一轮中的阶段调用次数（stage call），`i` 为并行子任务或 Coding 内 Patch 次数（index）。因此，面对一次失败可以精确回答“第几轮、哪次探索/编码、哪次工具调用出了什么问题”。

## 9. 可靠性与安全设计总结

这个项目的可靠性不依赖单一提示词，而是多层约束叠加：

```text
自然语言需求
  → Pydantic 结构化输出
  → Coordinator 路由校验
  → CodingTask 路径范围
  → CodingToolContext 绑定基线与仓库
  → Patch 语法/路径/文本/快照校验
  → Review 只读审查
  → 目标虚拟环境中的真实 pytest
  → Review + Test 双门槛保存最终 Patch
```

几个核心的设计取舍：

- **把“模型判断”和“程序事实”分开。** 模型负责生成计划和解释；Git Diff、文件系统校验、进程退出码和 pytest 日志负责决定事实。
- **以最小权限划分 Agent。** Explore/Review 没有写工具；Coder 没有 Shell、没有 Git 历史操作、没有执行测试的工具。
- **先锁定基线再写入。** `base_commit` 是 Diff、回滚和最终 Patch 验证的共同坐标系。
- **并行只用于只读，写入保持串行。** 并行提升探索效率，串行保证工作区确定性。
- **测试环境是项目的一部分。** 不临时装包，不偷偷用控制器 Python；强制目标环境解释器和 `python -m pytest`。
- **日志是流程产物而不是调试附属物。** 每个关键输入、决策、工具调用和测试输出都能复盘。

## 10. 项目概述与常见问题

### 简要介绍

“我做的是一个面向本地 Git 仓库的 Issue 修复工作流。它不是让一个 Agent 直接改代码，而是用 LangGraph 把流程拆成初始化、Issue 解析、并行探索、受限编码、只读 Review 和真实 pytest 测试。Coordinator 只做结构化路由，Coder 只能在 CodingTask 允许的路径里通过 unified diff 修改文件，程序会用 Git 实际 Diff 反查 Agent 的结果。测试强制使用目标仓库自己的虚拟环境和 `python -m pytest`，缓存都放到运行目录。只有 Review 通过且真实测试全绿才保存最终 Patch；全过程带按轮次编号的 JSON、日志和 Diff 哈希，因此失败也能回放和定位。”

### 常见追问与回答要点

**为什么不用一个万能 Agent？**  
因为万能 Agent 同时拥有理解、写入、执行和验收能力，出错时很难定位责任，也容易越权。这里按能力拆分，写入面最小化，验收交给独立节点和确定性程序。

**为什么 Coding 不能并行？**  
两个 Agent 即使分别改不同文件，也可能依赖同一接口、测试或累计 Diff；并发会产生写入竞争和无法解释的合并结果。一个 Patch 可以覆盖多个相关文件，且 Coder 可在同一阶段多次迭代，足以表达原子修改。

**为什么还需要 Review，测试通过不就够了吗？**  
测试只能覆盖已有或新增的断言，无法保证没有扩大范围、破坏未覆盖路径或违背架构约束。Review 关注 Diff 与 Issue 的一致性；测试验证实际行为，两者是互补门槛。

**如何防止模型乱改？**  
提示词只是第一层；真正的限制来自路径白名单、受保护目录、Patch 格式校验、临时 Git index、实际 Diff 对账、测试和回滚机制。

**如何处理环境不一致？**  
流程一开始验证目标仓库根目录内唯一的 `.venv` / `venv` / `.conda`，验证解释器身份和 pytest；测试时始终用这个解释器执行 `-m pytest`，并把临时文件和缓存重定向到运行目录。

## 11. 当前限制与下一步可演进方向

当前实现刻意选择可靠的最小闭环，后续可在不破坏边界的前提下演进：

1. 增加 Node、Java、Go 等项目的环境发现器和受限测试适配器；每种生态仍应使用其自身解释器/运行时和非 Shell 参数化执行。
2. 允许从仓库配置读取明确声明的测试命令，但仍需要解析为白名单 argv，而不是直接执行任意命令字符串。
3. 引入 Git worktree 或临时克隆，让修复发生在隔离副本中，进一步降低对开发者工作区的要求。
4. 增加 PR 生成前的人工审批层；提交和推送仍应是显式授权动作。
5. 为测试失败加入更细的失败分类、回归测试选择和覆盖率信号，但不让模型绕过真实测试。
6. 扩展针对真实开源 Issue 的评测集，衡量成功率、平均修复轮次、越界修改率和失败可恢复率。

这些演进共同遵循同一原则：**先保证状态、权限、环境和证据链清晰，再扩大自动化能力。**
