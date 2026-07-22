# issue-solver 项目说明

## 文档导航

- [1. 项目定位](#1-项目定位)
- [2. 想解决的问题与非目标](#2-想解决的问题与非目标)
- [3. 如何使用与运行边界](#3-如何使用与运行边界)
- [4. 总体架构](#4-总体架构)
- [5. 核心流程逐步说明](#5-核心流程逐步说明)
- [6. 重要数据结构](#6-重要数据结构)
- [7. Agent、Prompt 与上下文](#7-agentprompt-与上下文)
- [8. 日志、审计与可追踪性](#8-日志审计与可追踪性)
- [9. 可靠性与安全设计总结](#9-可靠性与安全设计总结)
- [10. 项目概述与常见问题](#10-项目概述与常见问题)
- [11. 当前限制与演进方向](#11-当前限制与下一步可演进方向)
- [12. 配置与 CLI 完整参考](#12-配置与-cli-完整参考)
- [13. 路由、轮次、预算与重试](#13-路由轮次预算与重试)
- [14. 安全不变量与失败恢复](#14-安全不变量与失败恢复)
- [15. 开发、测试与扩展指南](#15-开发测试与扩展指南)
- [16. 常见故障排查](#16-常见故障排查)
- [17. 真实 Issue 测评](#17-真实-issue-测评)

## 1. 项目定位

`issue-solver` 是一个面向**本地 Git 仓库**的 Issue 修复编排工具。当前版本运行于 Windows 开发环境，仅支持 Python + pytest，推荐用于中小型仓库。它接收一段 Issue 文本、GitHub Issue URL 或本地 UTF-8 Issue 文件，将自然语言问题转为结构化需求；随后在受限范围内探索代码、生成修改、审查修改，并在目标仓库已准备好的 Python 虚拟环境中执行真实测试。

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
                         Review APPROVE 且测试全绿 → Finalize
                                           │
                         其他结果 → Coordinator 再决策
                                      ├─ 继续探索
                                      ├─ 返工修改
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
- 当前版本仅完整支持 Python + pytest；不会伪装支持 Node、Java、Go 等项目。
- 不把目标仓库的 `.env` 作为控制器配置来源；模型配置只读取 `issue-solver` 项目根目录的 `.env`。
- 不执行任意 Shell 测试命令；只接受受限的 pytest 命令形式。

这些限制是刻意的。对于能写入真实工作区的 Agent，先缩小能力边界，比一开始追求“什么都自动化”更可靠。

不接入 tox 也是同一原则的直接结果。按照 [tox 官方生命周期](https://tox.wiki/en/latest/user_guide.html)，tox 是环境编排器，正常执行可能创建或重建虚拟环境、安装环境依赖、构建并安装目标项目，再运行配置命令。这些职责与本工具“只消费开发者已准备环境、不改变依赖状态”的边界冲突。即使 tox 提供跳过安装的参数，也仍要求对应 tox 环境已存在且与配置一致，会形成第二套环境发现和校验规则。因此当前实现只把 `tox.ini` 中独立的 `[pytest]` 段作为 pytest 配置识别信号，不把 tox 当作测试执行入口；依赖 tox 的 `setenv`、工作目录切换、前置命令或多解释器矩阵的项目明确不在支持范围内。

## 3. 如何使用与运行边界

本项目提供两种入口。

```powershell
# 在 issue-solver 源码目录中运行，必须显式传入目标仓库
python -m cli.main run --repo <target-repo> --issue <issue-url-or-text>

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

完整的端到端路径见[完整流程图](workflow.md)。

### 4.1 分层与职责边界

```text
src/
├── cli/       命令行参数、环境预检、终端可视化、交互式回滚确认
├── graph/     LangGraph 状态定义、路由规则、节点连接
├── nodes/     每一个工作流步骤的确定性编排和状态更新
├── agents/    给 LLM 的角色、工具集合、结构化输出策略
├── prompts/   各角色的系统提示词与上下文组装
├── schemas/   Pydantic 数据契约与跨字段校验
├── tools/     只读仓库工具、Git 查看工具、受限 Patch 修改工具
└── services/  Issue 加载、项目检测、环境发现、测试执行、产物落盘
```

核心设计原则是：**LLM 负责判断和生成结构化意图；程序负责权限、状态转移、文件写入、测试执行和最终准入。**

例如，Coding Agent 可以说“我改了三个文件”，但 `src/nodes/coding.py` 仍会调用确定性的 `inspect_coding_changes`，确认真实 Diff 非空、真实文件列表与 `CodingResult.changed_files` 完全一致后，才允许进入 Review。模型输出不是系统事实，Git 快照和测试进程结果才是。

### 4.2 为什么采用状态图

工作流由 LangGraph 的 `StateGraph` 组织，而不是一串线性函数调用。原因是 Issue 修复天然存在回路：探索可能不够，要继续探索；Review 可能要求返工；测试可能暴露根因判断错误；达到上限后还要有确定的失败出口。

主路径如下：

```text
START
  → initialize
  → parse_issue
  → coordinator
       ├─ EXPLORE：动态 fan-out 多个 explore 节点，全部回到 coordinator
       ├─ CODE：coding → review → test →（双门槛通过时 finalize，否则 coordinator）
       ├─ FINISH：finalize → END
       └─ FAILED：finalize / END（依状态处理）
  → CLI Report 收尾：生成 report.md
```

`EXPLORE` 使用 LangGraph 的 `Send` 动态分发，Coordinator 每次最多生成 3 个彼此独立的探索目标。这些探索读同一仓库但不写入，因此可以并行；它们各自返回报告后借助状态 reducer 合并。`CODE` 则严格串行，因为多个写入 Agent 并发修改同一个 Git 工作区会产生竞争、覆盖和不可解释的 Diff。

## 5. 核心流程逐步说明

### 5.1 CLI 与环境预检

`src/cli/run.py` 先创建独立的运行目录，随后在**调用模型之前**执行环境预检。预检只查找目标仓库根目录的 `.venv`、`venv` 或 `.conda`，并要求：

1. 只能有一个候选环境；
2. 环境位于目标仓库内、不是符号链接，且已经被 Git ignore；
3. 存在正确的解释器和 `pyvenv.cfg` / `conda-meta` 标识；
4. 用该解释器验证 `sys.prefix`，避免 PATH 指向了错误 Python；
5. 用 `<目标 Python> -m pytest --version` 验证 pytest 已安装且可启动；
6. 运行目录可写。

预检失败会写入 `logs/failure_environment.json`，并明确退出：不安装依赖、不调用 LLM、不修改工作区。它把“开发环境没有准备好”的问题从模型推理问题中剥离出来，避免在流程最后才发现 pytest 无法运行。

这意味着开发者必须在启动前完成环境准备。`.venv`、`venv` 或 `.conda` 中不仅要有 pytest，还要有目标项目及测试导入、插件和运行所需的全部依赖；工具不会根据 `requirements.txt`、`pyproject.toml` 或 tox 配置补装任何内容。

### 5.2 初始化仓库

`initialize` 节点确认目标路径是有效 Git 仓库、工作区干净，并记录当前 `HEAD` 为 `base_commit`。这是之后生成 Diff、验证工作区、回滚的唯一基线。

项目检测当前主要识别 Python 项目标记，并发现全量 pytest 回归命令（例如 `pytest -q`）。`pytest.ini`、`pyproject.toml` 的 pytest 配置、`tox.ini` 的 `[pytest]` 段或根目录 `tests/` 都可作为直接运行 pytest 的识别信号；只有 tox 配置、没有可识别 pytest 入口时才明确终止。系统始终使用已准备环境执行 pytest，不会调用 tox，也不会假设其他测试框架可执行。

### 5.3 Issue 规范化

`parse_issue` 节点先由 `src/services/issue_loader.py` 读取原始输入，再让模型输出 `IssueSpec`。它将不稳定的自然语言统一成标题、原始描述、期望行为、实际行为和验收条件，后续角色不必反复从原文猜测任务。验收条件遵循原文优先和最小推导：原文已经明确时保留原意，没有显式清单但期望可以直接推出时生成最少的可验证条件。首次结果缺少验收条件时，节点只针对验收条件受控重试一次，并保留首次解析的其他字段；期望方向仍不明确或存在互斥预期时才终止并要求补充输入。

### 5.4 Coordinator：唯一的工作流决策者

Coordinator 没有文件工具，也不能执行命令；它只返回 `CoordinatorDecision`。它的作用类似技术负责人：根据 Issue、探索报告、已有 Diff、Review 结论和最近测试结果决定下一步是 `EXPLORE`、`CODE`、`FINISH` 还是 `FAILED`。

程序会再次验证该决定，而不盲信模型：

- 首次决策只能是 `EXPLORE`；
- `EXPLORE` 必须携带 1 到 3 个目标，且不能同时携带 CodingTask；
- `CODE` 必须携带完整 CodingTask；
- `CodingTask.test_targets` 必须给出 1 到 10 个仓库内的 `.py` 测试文件或 pytest node ID；
- 返工时，新任务的 `allowed_scope` 必须覆盖此前累计修改的所有文件；
- 只有 Review 为 `APPROVE` 且最近一轮全部真实测试为 `PASSED` 时，才允许 `FINISH`；
- `MAX_CYCLES` 默认是 5；工具 Agent 每次默认最多执行 60 步，每轮最多探索 5 批，达到探索上限后进入编码。
- 程序以 IssueSpec 的验收条件覆盖 Coordinator 的复述，CodingTask 不能扩展原 Issue，并使用完成修复所需的最小文件和测试范围。
- 测试文件仅用于读取与执行，Coordinator 不得要求修改、新增或删除测试。
- 若定向测试已通过，而原测试对同一状态要求相反结果，Coordinator 必须按输入冲突终止，不得构造同时满足两者的任务。

`cycle` 在真实测试完成后增加，表示已经完成多少轮“修改—验证”闭环；`repair_round = cycle + 1` 用于给当前修复轮次编号。单轮内若需要再次探索或再次编码，则通过 `stage_call` 区分。

### 5.5 Explore：并行只读调查

Explore Agent 只拿到 `list_files`、`read_file`、文本/符号搜索、`git_log` 和 `git_show` 等只读工具。它输出 `ExploreReport`，其中包含相关文件、符号、代码证据、潜在根因、建议测试点和未知项。

只读目录与搜索工具同样有确定的资源和路径边界。`list_files` 默认最多返回 500 项，调用方可在 1 到 2000 项之间调整；文本和符号搜索不会读取符号链接、仓库外路径、非普通文件、超过 1 MiB 的文件、包含 NUL 的二进制内容或非 UTF-8 文件，并且单次最多检查 2500 个候选文件。符号搜索最多返回 150 条结果。工具在目录、候选文件或匹配结果超过限制时会明确标记截断；Explorer、Coder 和 Reviewer 必须缩小搜索范围后继续，不能把截断输出当作完整仓库证据。

设计上，完整 `ExploreReport` 用于审计和阶段产物落盘，不会被后续角色反复传递。Initialize 会从目标仓库的全部 `git ls-files` 收集跟踪文件数量、总字节数和扩展名分布；Coordinator 结合这份客观画像、Issue 范围和证据缺口，自主选择本批 1 至 3 个独立探索目标，而不是无理由默认三路并行。多个报告并行产生，状态中的 `explore_reports` 使用追加 reducer 聚合，避免一个并行分支覆盖另一个分支的结果。

Coordinator 每次只读取尚未摘要的新报告，并在原有决策调用中同步生成有界 `EvidenceDigest`。Coder、后续 Explorer 和 Reporter 只接收该摘要；Reviewer 仅审查 Issue、CodingTask、CodingResult、真实 Diff 与代码，从而保持独立性并避免重复输入 Token。

### 5.6 Coding：受限、可重复、可审计的写入

Coding Agent 是唯一可修改目标工作区的角色，但它并没有 Shell 权限。其读取、搜索和写入工具都绑定到程序已验证的仓库根目录；模型只提供仓库相对路径，不能传入或改写 `repo_path`。两个写入工具为：

- `apply_patch`：应用 unified diff；
- `inspect_changes`：读取相对 `base_commit` 的累计修改、文件列表和 Diff 摘要。

Coordinator 下发的 `CodingTask.allowed_scope` 会被固化为 `CodingToolContext`。该上下文同时保存仓库根、base commit、运行目录、修复轮次和阶段调用号。创建上下文时会确认 HEAD 未变化；首次写入前还要求工作区干净，返工时才允许已有的累计修改。

`apply_patch` 的核心安全检查包括：

- 仅接受受控 unified diff，不经过 Shell；
- 拒绝仓库外路径、`..` 路径、符号链接、受保护目录、超出 `allowed_scope` 的路径；
- 默认保护 `.git`、虚拟环境、缓存、构建目录、`node_modules` 和运行产物目录；
- 拒绝二进制 Patch、文件权限/类型变更、submodule、被 Git ignore 的新文件；
- 限制单 Patch 最多修改 20 个文件、文本文件最大 1 MiB、Patch 最大 100,000 字符；
- 通过临时 Git index 严格校验并应用 Patch，仅将目标路径写回工作区，遵循 `.gitattributes` 的行尾规则，避免 CRLF 差异牵连无关文件；
- 若 Patch 已部分应用后发现验证失败，则恢复这一次 Patch 前的文件状态。

模型生成的 Patch 始终作为不可信输入处理。工具会保留原文，并单独生成送给 Git 的规范化版本；只允许统一换行、移除包裹整个 Patch 的 Markdown 围栏、根据 hunk 内容重算行数以及补齐末尾换行。工具不会 URL 解码、替换全角字符、猜测或改写路径。原始 Patch、规范化 Patch、两者哈希和规范化动作都会写入 Coding audit，随后仍须通过路径范围、文件类型和 `git apply --check` 等全部程序校验。

一个 CodingTask 可以通过一次 Patch 同时修改多个相关源码文件，也可以在同一 Coding 阶段最多进行 10 次 `apply_patch` 尝试；但整个 Coding 节点是串行的。第 10 次失败后节点会终止并回滚；超过限制的 Patch 会记录但不会应用。

Coding 节点会拒绝以下情况：Agent 报告 `success=false`、在 Coding 阶段提前声称有最终 Patch、实际 Diff 为空、或 Agent 声明的文件列表与 Git 检查结果不一致。Agent 声称环境故障时，程序还会复核仓库目录、Git HEAD 和相关文件；无法复现的环境故障按模型错误处理。发生 Coding 失败时，系统记录失败产物，并在安全条件满足时回滚到 `base_commit`。

### 5.7 Review 与真实测试

Review Agent 是只读角色，能读取当前工作区、搜索代码并查看相对基线的 Git Diff。它输出 `ReviewResult`：`APPROVE` 时 `issues` 必须为空；`REQUEST_CHANGES` 时必须给出至少一个具体问题。结构校验或 Agent 失败会保存 `logs/failure_review_rXX.json`，并交给统一失败收尾机制处理工作区。

Review 后仍会执行真实测试，而不是只依赖 Review 结论。Coordinator 不执行命令，而是在 `CodingTask.test_targets` 中给出精确测试文件或 pytest node ID。Test 节点先把这些目标构造成定向命令；定向测试通过后，再执行 Initialize 检测到的全量回归命令。Review 为 `APPROVE` 且本轮测试全部通过时，系统确定性地直接进入 Finalize；任一测试失败或 Review 未批准时，结果交回 Coordinator。

测试目标只允许仓库相对 `.py` 文件和可选的 `::` 选择器。程序会拒绝绝对路径、路径穿越、Shell 控制字符和解析后逃出仓库的符号链接。目标通过校验后，逻辑命令统一解析为 argv，并绑定为：

```text
<目标虚拟环境的 python> -m pytest <原测试参数>
    --basetemp=<控制器同级 .issue-solver-runtime>/<独立测试目录>/basetemp
    -o cache_dir=<控制器同级 .issue-solver-runtime>/<独立测试目录>/cache
```

测试进程的 `cwd` 是目标仓库根目录，但 `TEMP`、`TMP`、`TMPDIR`、pytest basetemp 和 pytest cache 都指向控制器仓库外的同级 `.issue-solver-runtime` 独立目录；同时通过 `VIRTUAL_ENV` 或 `CONDA_PREFIX` 与 PATH 前缀明确使用目标环境。每条命令结束后由独立清理进程删除该目录，即使控制器被强制终止也能通过管道断开触发回收，避免目标仓库污染和嵌套 pytest 误加载控制器配置。stdout/stderr 仍保存在运行目录的 `logs/` 中。

执行器不允许管道、重定向等 Shell 控制字符，只支持 `pytest ...` 或 `python/py -m pytest ...` 形式，并以 `shell=False` 启动；`tox` 和 `python -m tox` 会被拒绝。每条测试会落盘完整 stdout/stderr；给 Coordinator 的仅是受行数和 20,000 字符限制的末尾摘要。测试前后还会计算工作区指纹，若测试本身修改了 Git 工作区，会标记为 `SAFETY_ERROR` 并走保护路径。

### 5.8 Finalize：最终 Patch 的准入门

`finalize` 不是简单把当前 Diff 写盘。只有以下两个条件同时满足，才调用 `save_final_patch`：

1. 最近 Review 的 verdict 是 `APPROVE`；
2. 最近一轮所有 TestResult 都是 `PASSED`。

保存前会再次从 `base_commit` 构建累计 Diff，并用临时 Git index 验证 Patch 可应用到该基线。之后原子写入：

- `diff.patch`：最终可应用的文本 Patch；
- `diff.json`：`base_commit`、实际 `changed_files` 和 Patch SHA-256。

若安全违规或 Coding 中途失败需要强制回滚，工具确认 HEAD 仍为本次 base，再恢复跟踪文件并安全删除本次产生的未跟踪普通文件。其他终态失败只要存在 Coding 修改，交互式 CLI 都会统一询问是否回滚，默认保留；非交互运行同样保留并记录 `KEEP_NON_INTERACTIVE`。用户选择回滚后的结果也会写入失败记录。

### 5.9 Report：最终开发报告

LangGraph 结束且交互式回滚决策完成后，CLI 执行独立的 Report 收尾。Reporter 不绑定任何工具，只生成问题、修改验证和风险总结；完整测试日志、确定性运行字段和产物地址不会进入 Reporter 上下文。

报告以 UTF-8 保存为运行目录根部的 `report.md`。终端完成时，程序在总结末尾追加 `## 运行结果`，写入总/输入/输出 Token、缓存命中以及 Parser、Coordinator、Explorer、Coder、Reviewer、Reporter 的 Token 分布、耗时和产物地址；模型未返回缓存字段时明确显示“未提供”。终端摘要显示总/输入/输出 Token；模型不可用时使用程序总结模板。

## 6. 重要数据结构

### 6.1 `ResolverState`：整个工作流的单一事实来源

`src/graph/state.py` 中的 `ResolverState` 是节点之间共享的 TypedDict。它把“当前处于什么阶段、掌握了什么证据、工作区是否有修改、是否应回滚”放进显式状态，而不是隐藏在提示词或全局变量中。

| 分组 | 代表字段 | 作用 |
| --- | --- | --- |
| 运行标识 | `run_id`、`repo_path`、`run_dir`、`issue_input` | 将一次执行与仓库、日志目录、输入绑定 |
| 生命周期 | `phase`、`status`、`next_action` | 描述当前节点与下一步路由 |
| 修复轮次 | `cycle`、`repair_round`、`explore_stage_call`、`coding_stage_call` | 区分闭环轮次、同轮多次探索/编码和产物命名 |
| 基线与环境 | `base_commit`、`project_type`、`test_commands`、`environment`、`repository_profile` | 固化可重复验证的仓库、全部 Git 跟踪文件画像、全量回归命令和运行环境 |
| 证据与任务 | `issue`、`explore_reports`、`evidence_digest`、`coding_task`、`coding_result` | 保存完整审计证据，并把自然语言任务逐步转为可执行契约 |
| 验证 | `review_result`、`latest_test_results`、`test_results` | 保存审查结论和本轮/历史测试结果 |
| 工作区与失败 | `changed_files`、`diff_path`、`rollback_required`、`rollback_success`、`failure`、`rollback_failure` | 控制 Patch 保存、回滚和结构化错误展示 |

其中 `explore_reports`、`explore_failures` 和 `test_results` 使用 LangGraph 的 `add` reducer。并行 Explore 只追加自己的结果，不会覆盖其他分支；这也是能安全使用 fan-out 的关键。

### 6.2 结构化模型契约

| 模型 | 关键字段 | 设计意义 |
| --- | --- | --- |
| `IssueSpec` | `title`、`body`、`expected_behavior`、`actual_behavior`、`acceptance_criteria` | 把输入 Issue 统一为后续角色可复用的需求视图 |
| `ExploreReport` | `relevant_files`、`relevant_symbols`、`findings`、`root_cause`、`test_targets`、`unknowns` | 把探索转为带不确定性声明的代码证据 |
| `EvidenceDigest` | `source_report_count`、`root_cause`、`key_evidence`、相关文件/符号、测试目标、未知项 | Coordinator 合并新报告后生成的有界语义摘要，供后续模型使用 |
| `RepositoryProfile` | `tracked_file_count`、`tracked_file_bytes`、`file_counts_by_extension` | 目标仓库全部 Git 跟踪常规文件的客观规模画像，不限定语言类型 |
| `CoordinatorDecision` | `next_action`、`current_summary`、`explore_focuses`、`evidence_digest`、`coding_task`、`failure` | 用互斥校验限制 Coordinator 的路由输出；只有 `FAILED` 必须携带 failure |
| `CodingTask` | `objective`、`acceptance_criteria`、`root_cause`、`relevant_files`、`allowed_scope`、`test_targets` | 将“改一下”变成带修改边界和定向测试目标的可验证任务 |
| `CodingResult` | `success`、`changed_files`、`summary`、`validation`、`remaining_risks`、`failure` | 声明 Agent 的操作结果；失败时必须给出结构化原因，成功结果再由 Git 交叉验证 |
| `ReviewResult` | `verdict`、`issues`、`suggestions`、`remaining_risks` | 强制通过/不通过与问题列表一致 |
| `EnvironmentInfo` | `kind`、`root_path`、`python_executable`、`pytest_version`、`source` | 固化测试实际使用的虚拟环境身份 |
| `TestResult` | `command`、`resolved_command`、`status`、`failure`、`exit_code`、`stdout_path`、`stderr_path`、`output_tail` | 区分逻辑命令、真实 argv、失败类别、完整证据和给模型的受限上下文 |

`CodingTask`、`CoordinatorDecision`、`ReviewResult`、`TestResult` 和 `EnvironmentInfo` 使用较严格的 Pydantic 校验：例如路径必须是仓库内相对路径、字段不能为空、额外字段被禁止或通过跨字段规则拒绝矛盾组合。它们是 LLM 输出与确定性程序之间的“类型边界”。

### 6.3 统一失败契约

失败不再通过自由文本 `error` 字段传播，而是统一使用 `FailureInfo(type, message, suggestion)`。七种类型按下一步处理方式合并：`INPUT` 表示调用输入需修正，`ENVIRONMENT` 表示 Git、虚拟环境、依赖、网络或权限问题，`MODEL` 表示模型调用或结构协议错误，`SOLUTION` 表示当前 Patch 或修复方案无效，`SAFETY` 表示路径、工作区或 Git 基线保护被触发，`LIMIT` 表示轮次、Patch 次数或时间上限，`INTERNAL` 表示工作流状态或未预期实现错误。只读工具仍返回文本，但失败文本固定包含错误类型、原因和建议；Coding 工具直接返回结构化 failure。

`TestResult.status` 与 failure 类型保持固定映射：`FAILED → SOLUTION`、`ENVIRONMENT_ERROR → ENVIRONMENT`、`TIMEOUT → LIMIT`、`SAFETY_ERROR → SAFETY`；`PASSED` 不允许携带 failure。Review 的 `REQUEST_CHANGES` 和普通测试失败仍是可返工反馈；如果流程最终失败，是否询问回滚由终态是否保留 Coding 修改决定，而不是由某个节点或普通 failure 类型单独决定。

当前运行产物统一使用 `failure`、`explore_failures` 和 `rollback_failure`，不会写入早期版本的 `error`、`explore_errors`、`rollback_reason` 或 `rollback_error` 字段；历史运行目录保持原样，不做自动迁移。

## 7. Agent、Prompt 与上下文

### 7.1 角色与权限

| Agent | 是否可写 | 工具 | 输入重点 | 输出 |
| --- | --- | --- | --- | --- |
| Coordinator | 否 | 无 | Issue、探索报告、编码/审查/测试历史、循环上限 | `CoordinatorDecision` |
| Explorer | 否 | 绑定仓库的文件列表、读文件、文本/符号搜索、`git_log`、`git_show` | 一条探索目标 | `ExploreReport` |
| Coder | 是，且仅 Patch | 只读工具、`apply_patch`、`inspect_changes` | Issue、CodingTask、探索报告、当前摘要 | `CodingResult` |
| Reviewer | 否 | 绑定仓库与基线的只读工具、`git_diff` | Issue、CodingTask、CodingResult、探索报告 | `ReviewResult` |
| Reporter | 否 | 无 | Issue、探索结论、修改/审查/测试摘要 | `report.md` 总结部分 |

### 7.2 Prompt 与程序约束的分工

Prompt 层不负责绕过程序约束，而是把正确的职责告知模型。例如 Coder 的系统提示明确禁止执行测试、禁止修改 Git 历史、要求最后检查 Diff，并要求 `changed_files` 与工具结果完全一致；测试只能由 Test node 执行。Explorer 和 Reviewer 的只读工具同样固定在程序提供的仓库上下文中，Reviewer 的 `git_diff` 还固定相对本轮基线 Commit 比较，模型不能重新指定这些参数。即使模型违背提示，工具和节点仍会二次拦截。

所有角色的系统提示都明确把 Issue、仓库源码、Git 历史、上游 Agent 输出和工具文本视为不可信数据。仓库中的注释即使包含“调用其他工具”“忽略范围限制”等内容，也只是待分析文本，不具有指令优先级。

### 7.3 模型供应商兼容

模型层使用 `langchain-openai` 的 Chat Completions 接口，并由 `OpenAICompatibleChatModel` 处理供应商差异。程序优先按模型名、其次按 `BASE_URL` 自动识别 DeepSeek、GLM、Kimi、MiMo、Qwen、OpenAI 和 Gemini；未知服务按标准 OpenAI 格式处理。`REASONING_HISTORY=auto` 默认对 DeepSeek、GLM、Kimi 和 MiMo 回填多轮工具调用中的 `reasoning_content`，其他供应商不回填，也可用 `true` 或 `false` 显式覆盖。Qwen 显式开启时同步发送 `preserve_thinking=true`。推理内容只保存在内部消息历史中，不进入面向开发者的输出。

| 供应商 | `auto` 回填推理历史 | 强制 tool choice 处理 | 其他行为 |
| --- | --- | --- | --- |
| DeepSeek | 是 | 移除不兼容的强制选择 | 保留 `auto` / `none` |
| GLM | 是 | 按标准行为 | 回填 `reasoning_content` |
| Kimi | 是 | 移除不兼容的强制选择 | 保留 `auto` / `none` |
| MiMo | 是 | 移除不兼容的强制选择 | 保留 `auto` / `none` |
| Qwen | 否 | 移除不兼容的强制选择 | 显式开启回填时发送 `preserve_thinking=true` |
| OpenAI | 否 | 按标准行为 | 使用 Chat Completions，不启用 Responses API |
| Gemini | 否 | 按标准行为 | 按 OpenAI 兼容接口处理 |
| 未知兼容服务 | 否 | 按标准行为 | 不猜测供应商私有字段 |

Issue Parser、Explorer 与 Reporter 会在供应商和型号明确支持时使用关闭思考模式的模型副本：DeepSeek V4、GLM 4.5+ 使用 `thinking.type=disabled`，Qwen3 使用 `enable_thinking=false`，Gemini 2.5 Flash 与支持该值的 GPT-5.1+ 使用 `reasoning_effort=none`。不支持关闭的型号静默保持基础模型行为。Reviewer、Coordinator 与 Coder 始终使用基础模型；尤其 Reviewer 不关闭思考模式。

适配器不会全局开启 thinking，也不会把 OpenAI Responses API 与第三方的 `reasoning_content` 混用。对于 thinking 工具调用中不支持强制选择工具的已知供应商，只移除 `required` 或指定工具名等强制 `tool_choice`，保留 `auto`、`none` 和标准服务的原始行为。Coordinator 仍使用函数调用形式的结构化输出；有工具的角色通过 `ToolStrategy` 取得 Pydantic 模型输出。

### 7.4 上下文最小化

每个角色只接收完成职责需要的信息：

- Coordinator 接收规范化 Issue、精简的 `current_summary`、结构化探索/编码/Review 结果和最近一轮测试摘要；仅非通过测试携带日志尾部，不接收完整聊天历史。
- Explorer 每次只接收一个探索重点，避免并行分支重复调查整个仓库。
- Coder 接收 CodingTask、累计探索报告和当前摘要，但没有测试执行结果之外的任意终端访问。
- Reviewer 接收累计修改上下文；仓库和 base commit 由工具绑定，必须自行调用 `git_diff`，不能信任 Coder 的 Diff 描述。
- Reporter 的上下文会过滤完整测试输出、路径和确定性运行字段，只保留问题、根因、修改、验证状态和风险；Token、耗时、状态和产物地址由程序追加。

`current_summary` 最大 2000 字符，只保留根因、已有结果和下一步原因。完整历史保存在运行产物中，不通过无限增长的模型上下文传递。

工具型 Agent 的单次模型—工具循环也使用滑动窗口：每次调用模型前，程序按 `max(1, floor(AGENT_RECURSION_LIMIT / 4))` 保留最近完成的工具批次，并移除更久远批次中的 AI 工具调用及其全部对应工具结果。未完成或消息 ID 不完整的批次不会删除，避免留下孤立工具消息；这不会影响运行产物中的完整审计记录。

## 8. 日志、审计与可追踪性

每一次运行都拥有一个 ULID 风格 `run_id` 和独立目录。持久化 JSON、audit 和完整测试输出统一写入 `run_id/logs/`；面向开发者的 `report.md` 和最终 `diff.patch`、`diff.json` 保留在 `run_id/` 根部。pytest 的 basetemp、cache 和进程临时目录位于控制器项目同级的 `.issue-solver-runtime/`，属于执行期间的隔离目录，命令结束或控制器退出后会清理，不作为运行产物保留。所有 JSON 以 UTF-8 写入，并使用排他创建（`x` 模式）避免悄悄覆盖同名证据；最终 Patch 使用原子写入。

常见产物如下：

| 文件模式 | 内容 |
| --- | --- |
| `logs/environment_result.json` / `logs/failure_environment.json` | 环境预检结果或结构化 failure；失败文件还记录未调用 LLM、未安装依赖、未修改工作区 |
| `logs/issue.json` | 规范化后的 Issue |
| `logs/explore_r01_s01_i01.json` | 第 1 修复轮、第 1 次探索调用、第 1 个并行任务的报告 |
| `logs/coding_task_r01_s01_i00.json` | 某次 Coding 前的受限任务契约 |
| `logs/coding_result_r01_s01_i02.json` | Coding 阶段第 2 次 Patch 后的结构化结果 |
| `logs/coding_audit_r01_s01.jsonl` | 每次 `apply_patch` 的原始与规范化 Patch、规范化动作、结构化 failure、触碰文件和 Diff 哈希；一行一个事件 |
| `logs/review_result_r01.json` / `logs/failure_review_r01.json` | 审查结论或结构/Agent 失败记录 |
| `logs/test_result_r01.json` | 本轮全部 TestResult；完整输出另存为 stdout/stderr 日志 |
| `logs/test_stdout_r01_i01.log`、`logs/test_stderr_r01_i01.log` | 第 1 条测试命令（通常为定向测试）的完整进程输出 |
| `logs/rollback_decision_r01.json` | 任意非安全终态失败后用户或非交互策略的保留/回滚决定 |
| `report.md` | 模型或程序生成的总结，以及程序追加的确定性运行结果 |
| `diff.patch`、`diff.json` | 仅在 Review 和测试都通过后保存的最终补丁与校验元数据 |
| `logs/finalize_result_r01.json`、`logs/failure_coding_*.json`、`logs/failure_test_r01.json` | 收尾、编码或测试失败的可追踪记录 |

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

“我做的是一个面向本地 Git 仓库的 Issue 修复工作流。它不是让一个 Agent 直接改代码，而是用 LangGraph 把流程拆成初始化、Issue 解析、并行探索、受限编码、只读 Review 和真实 pytest 测试。Coordinator 只做结构化路由，Coder 只能在 CodingTask 允许的路径里通过 unified diff 修改文件，程序会用 Git 实际 Diff 反查 Agent 的结果。测试强制使用目标仓库自己的虚拟环境和 `python -m pytest`，临时文件与缓存使用仓库外的独立目录并自动清理。只有 Review 通过且真实测试全绿才保存最终 Patch；全过程带按轮次编号的 JSON、日志和 Diff 哈希，因此失败也能回放和定位。”

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
流程一开始验证目标仓库根目录内唯一的 `.venv` / `venv` / `.conda`，验证解释器身份和 pytest；测试时始终用这个解释器执行 `-m pytest`，并把临时文件和缓存重定向到控制器仓库外的独立目录。

## 11. 当前限制与下一步可演进方向

当前实现刻意选择可靠的最小闭环，后续可在不破坏边界的前提下演进：

1. 增加 Node、Java、Go 等项目的环境发现器和受限测试适配器；每种生态仍应使用其自身解释器/运行时和非 Shell 参数化执行。
2. 允许从仓库配置读取明确声明的测试命令，但仍需要解析为白名单 argv，而不是直接执行任意命令字符串。
3. 引入 Git worktree 或临时克隆，让修复发生在隔离副本中，进一步降低对开发者工作区的要求。
4. 增加 PR 生成前的人工审批层；提交和推送仍应是显式授权动作。
5. 扩展回归测试选择和覆盖率信号，但不让模型绕过真实测试。
6. 在当前 15 个真实 GitHub Issue 的基础上继续扩展仓库、模型和重复运行次数，增加稳定性、方差、越界修改率和失败可恢复率等指标。

这些演进共同遵循同一原则：**先保证状态、权限、环境和证据链清晰，再扩大自动化能力。**

## 12. 配置与 CLI 完整参考

### 12.1 配置加载规则

配置入口固定为控制器项目根目录的 `.env`。`src/config.py` 在模块加载时调用 `load_dotenv(..., override=True)`，因此该文件中的同名值会覆盖当前进程环境变量；目标仓库中的 `.env` 不会被读取。随后不可变的 Pydantic Settings 对象读取并验证这些值。这样可以避免业务项目的密钥、代理地址或模型配置意外改变控制器行为。

除 `API_KEY`、`BASE_URL`、`MODEL_NAME`、`GITHUB_TOKEN` 外，整数配置必须大于 0，浮点配置必须大于 0；空字符串会被规范化为未配置。配置校验在启动阶段完成，非法值不会进入 LangGraph。

| 配置 | 默认值 | 作用 | 校验与注意事项 |
| --- | ---: | --- | --- |
| `API_KEY` | 无 | OpenAI Chat Completions 兼容服务密钥 | 运行模型前必须非空；不会写入运行报告 |
| `BASE_URL` | 无 | 模型服务地址 | 运行模型前必须非空；也用于辅助识别供应商 |
| `MODEL_NAME` | 无 | 默认模型名称 | 可被 `--model` 临时覆盖 |
| `REASONING_HISTORY` | `auto` | 是否在多轮工具调用中回填 `reasoning_content` | 仅允许 `auto`、`true`、`false` |
| `GITHUB_TOKEN` | 无 | 读取 GitHub Issue API 的可选令牌 | 仅加入 API 请求头，不发送给 Agent |
| `MAX_CYCLES` | `5` | 最多完成多少轮编码—测试闭环 | 正整数，可被 `--max-cycles` 覆盖 |
| `AGENT_RECURSION_LIMIT` | `60` | 每次 Explorer、Coder、Reviewer 工具 Agent 的最大图步数，并决定保留的工具历史窗口 | 正整数；不是外层工作流循环数；最多保留 `max(1, floor(AGENT_RECURSION_LIMIT / 4))` 个最新完整工具批次 |
| `MAX_EXPLORE_BATCHES` | `5` | 同一修复轮最多允许多少批探索 | 正整数；达到后程序强制 Coordinator 进入 CODE |
| `TEST_TIMEOUT` | `300` | 单条 pytest 命令的超时秒数 | 正数，可被 `--test-timeout` 覆盖 |
| `TEST_TAIL_LINES` | `100` | 非通过测试提供给 Coordinator 的日志尾部行数 | 正整数，可被 `--test-tail-lines` 覆盖 |
| `RUN_ROOT` | `.issue-solver-runs` | 持久化运行目录根路径 | 不能为空；相对路径以控制器项目根目录为基准 |

项目没有为不同 Agent 暴露独立温度配置。所有角色共享同一个 `OpenAICompatibleChatModel` 基础配置；仅 Issue Parser、Explorer 和 Reporter 会在支持时派生关闭思考模式的副本。其他角色差异来自提示词、工具权限、输入上下文和结构化输出类型，而不是隐式采样参数。这样可以减少难以复现的组合配置。

### 12.2 CLI 参数与优先级

源码入口：

```powershell
python -m cli.main run --repo <target-repo> --issue <issue-url-or-text>
```

可编辑安装入口：

```powershell
issue-solver run --issue <issue-url-or-text>
```

| 参数 | 是否必需 | 作用 | 优先级 |
| --- | --- | --- | --- |
| `--repo` | 源码入口必需；全局入口可省略 | 目标仓库或仓库子目录 | 全局入口省略时从当前目录向上寻找 Git 根 |
| `--issue` | 必需 | 普通文本、GitHub Issue URL、绝对 `.md` / `.txt` 路径 | 无默认值 |
| `--model` | 可选 | 覆盖 `MODEL_NAME` | CLI 高于 `.env` |
| `--max-cycles` | 可选 | 覆盖 `MAX_CYCLES` | CLI 高于 `.env` |
| `--test-timeout` | 可选 | 覆盖 `TEST_TIMEOUT` | CLI 高于 `.env` |
| `--test-tail-lines` | 可选 | 覆盖 `TEST_TAIL_LINES` | CLI 高于 `.env` |
| `--run-root` | 可选 | 覆盖 `RUN_ROOT` | CLI 高于 `.env`；仍必须位于目标仓库外 |
| `--quiet` | 可选 | 隐藏阶段过程，只显示最终摘要 | 不影响日志与报告生成 |

`AGENT_RECURSION_LIMIT` 和 `MAX_EXPLORE_BATCHES` 当前只能通过 `.env` 设置。所有数值型 CLI 参数在 argparse 阶段验证，0、负数和非数字会直接拒绝。

### 12.3 运行目录解析

`RUN_ROOT` 为相对路径时基于控制器项目根目录解析，为绝对路径时直接使用。解析后程序会确认它不位于目标仓库内部；否则以 `SAFETY` 失败，防止日志、测试输出或 Patch 污染业务工作区。

每次运行路径固定为：

```text
<RUN_ROOT>/<目标仓库名>/<run_id>/
```

同一个目标仓库的多次运行通过独立 `run_id` 隔离。程序不会复用或覆盖已有报告、JSON、audit、stdout、stderr 或最终 Patch。

## 13. 路由、轮次、预算与重试

### 13.1 CLI 与 StateGraph 的边界

环境预检发生在 StateGraph 之外。原因是预检失败必须保证没有模型调用、没有依赖安装、没有工作区修改，同时仍能生成一份程序模板报告。预检通过后，CLI 才创建模型、编译图并把已验证的 `EnvironmentInfo` 注入初始状态。

外层图的固定边如下：

| 当前节点 | 成功路由 | 失败路由 |
| --- | --- | --- |
| `initialize` | `parse_issue` | `END` |
| `parse_issue` | `coordinator` | `END` |
| `coordinator` | `explore`、`coding` 或 `finalize` | `finalize` |
| `explore` | 所有并行分支汇总后回到 `coordinator` | 失败写入 reducer，随后由 Coordinator 统一终止 |
| `coding` | `review` | `END` |
| `review` | `test` | `END` |
| `test` | Review APPROVE 且本轮测试全绿时 `finalize`，否则 `coordinator` | `END` |
| `finalize` | `END` | `END` |

图结束不等于 CLI 生命周期结束。CLI 还会处理可选的失败回滚、生成模型总结或程序模板、追加确定性运行结果，并输出终端摘要。这样即使某个节点直接路由到 `END`，仍然有一致的报告收尾。

### 13.2 Coordinator 的确定性约束

Coordinator Agent 只能返回 `CoordinatorDecision`，没有任何工具。节点会在模型输出之后再执行以下程序约束：

1. 没有任何 `ExploreReport` 时，第一次决策必须是 `EXPLORE`。
2. `EXPLORE` 每批必须包含 1 至 3 个非空目标；LangGraph 使用 `Send` 动态派发并行分支。
3. `CODE` 必须包含完整 `CodingTask`，且测试目标只能是 1 至 10 个仓库相对 `.py` 文件或 pytest node ID。
4. Coordinator 输出的验收条件不会成为事实来源；节点始终用 `IssueSpec.acceptance_criteria` 覆盖它。
5. 返工任务的 `allowed_scope` 必须覆盖此前累计 `changed_files`，否则按 `MODEL` 失败。
6. `FINISH` 只有在最近 Review 为 `APPROVE` 且本轮全部测试为 `PASSED` 时有效。
7. `FAILED` 必须携带结构化 `FailureInfo`，不能同时携带探索目标或 CodingTask。
8. 测试导致工作区变化时，`rollback_required` 会阻止继续决策并进入安全失败收尾。

当同一修复轮已使用的探索批次达到 `MAX_EXPLORE_BATCHES`，程序设置 `force_code`。若模型仍返回其他动作，Coordinator 会收到一次明确纠正请求；第二次仍未返回有效 `CODE` 时按 `MODEL` 失败，而不是无限请求模型。

### 13.3 cycle、repair_round、stage_call 与 index

这些计数解决“同一轮多次探索或返工时产物如何唯一命名”的问题：

| 坐标 | 含义 | 何时增加 |
| --- | --- | --- |
| `cycle` | 已完成的编码—Review—测试闭环数 | Test 节点完成后增加 |
| `repair_round` | 当前正在执行的修复轮次 | 通常为 `cycle + 1` |
| `explore_stage_call` | 当前轮第几批探索 | Coordinator 再次选择 `EXPLORE` 时增加 |
| `coding_stage_call` | 当前轮第几次 Coding 调用 | Coordinator 再次选择 `CODE` 时增加 |
| `index` | 并行探索序号或 Coding 内 Patch 尝试序号 | 每个阶段内部单独计算 |

例如 `coding_result_r02_s01_i03.json` 表示第 2 修复轮、第 1 次 Coding 阶段调用，在第 3 次 Patch 尝试后形成的结果。`test_result_r02.json` 则表示第 2 修复轮的完整测试结果集合。

### 13.4 外层递归上限

LangGraph 的外层 `recursion_limit` 不是固定写死的 60。CLI 根据运行预算动态计算：

```text
5 + MAX_CYCLES × (2 × MAX_EXPLORE_BATCHES + 4)
```

它覆盖初始化、Issue 解析、Coordinator、最多探索批次、Coding、Review、Test 和 Finalize 所需的外层节点数。`AGENT_RECURSION_LIMIT=60` 则约束一次 Explorer、Coder 或 Reviewer 内部的模型—工具循环，并决定其工具历史窗口；两者不能混为一谈。

### 13.5 结构化输出与语义重试

系统区分“结构协议错误”和“业务语义不足”：

- Coordinator、Issue Parser 以及工具 Agent 的结构化结果在 `ValueError` 时最多即时重试 3 次，不使用指数退避。
- 工具 Agent 除 Pydantic 校验外，还验证响应中确实存在正确类型的 `structured_response`。
- Issue Parser 如果结构有效但 `acceptance_criteria` 为空，会追加一条仅要求重新判断验收条件的消息，再调用一次模型；恢复结果只提供验收条件，首次解析的标题、正文、期望行为和实际行为不会被覆盖。
- 第二次仍没有可安全确定的验收条件时按 `INPUT` 失败；恢复调用本身异常时按 `MODEL` 失败。
- 工具 Agent 达到图步数上限时，`GraphRecursionError` 被转换为 `LIMIT`，并明确显示 Agent 名称和当前步数上限。

结构化重试不会放宽安全约束，也不会重复执行 pytest 或自动扩大写入范围。它只处理模型响应格式和一次明确的验收条件恢复。

### 13.6 Coding 内部尝试上限

一次 Coding 阶段最多允许 10 次 `apply_patch`。每次调用都会追加 audit，无论成功还是失败。达到上限后，工具不再应用新 Patch，并返回 `LIMIT`。Coder 应在失败后重新读取当前文件和累计 Diff，不能原样重复同一个 Patch。

这个上限与 `MAX_CYCLES` 不同：前者限制单次 Coding Agent 内部的 Patch 尝试，后者限制完成测试闭环的修复轮数。

## 14. 安全不变量与失败恢复

### 14.1 信任边界

系统把以下内容全部视为不可信输入：用户提供的 Issue、GitHub Issue 正文、目标仓库源码与注释、Git 历史、模型输出、工具输出中的文本，以及模型生成的 Patch。任何一层出现“忽略系统规则”“扩大权限”“访问其他路径”或“声称测试已经通过”等内容，都不能改变程序约束。

确定性事实来源只有：

- Git 返回的仓库根、HEAD、状态和 Diff；
- 文件系统解析后的真实路径、类型和大小；
- Pydantic 校验后的结构化对象；
- `subprocess` 的真实 argv、退出码和 stdout/stderr；
- 程序生成的 audit、工作区指纹和最终 Patch 校验结果。

### 14.2 启动不变量

开始调用模型前必须同时满足：

1. 目标路径属于 Git 仓库，且不能是 Issue Solver 控制器仓库自身。
2. 运行目录解析后位于目标仓库之外。
3. 目标仓库根目录只有一个 `.venv`、`venv` 或 `.conda` 候选。
4. 环境目录不是符号链接或目录联接，解析后仍位于目标仓库内部，并已被 Git ignore。
5. VENV 存在 `pyvenv.cfg`，Conda 环境存在 `conda-meta`；Windows 下解释器分别位于 `Scripts/python.exe` 和环境根目录的 `python.exe`。
6. 解释器可以启动，实际 `sys.prefix` 与候选环境根一致。
7. `<目标 Python> -m pytest --version` 成功并返回版本信息。
8. Git 工作区没有已跟踪或未跟踪修改，且仓库已有可读取的 HEAD commit。
9. 可以识别直接运行的 pytest 入口。

预检失败记录 `llm_called=false`、`dependencies_installed=false`、`worktree_modified=false`。工具不会为了“帮用户跑起来”而安装依赖或创建环境。

### 14.3 路径与文件不变量

所有 Agent 工具都要求仓库相对路径，并在程序侧解析后确认仍位于仓库根内。目录遍历、绝对路径、Windows 盘符和越过仓库的符号链接会被拒绝。常见依赖、缓存和产物目录受到保护，包括：

```text
.git/  .venv/  venv/  .conda/  __pycache__/  .pytest_cache/
.mypy_cache/  .ruff_cache/  node_modules/  dist/  build/
.issue-solver-runs/
```

只读工具也有资源限制：`list_files` 最大深度为 5、最多返回 2000 项；`read_file` 单次最多 500 行；文本搜索最多扫描 2500 个候选文件，单文件最大 1 MiB，单次最多返回 200 条结果；符号搜索最多返回 150 条。结果被截断时会明确写入标记，Agent 必须缩小范围继续读取。

### 14.4 Patch 不变量

`CodingToolContext` 在创建时固化 `repo_root`、`base_commit`、`run_dir`、`allowed_paths`、保护路径、修复轮次和阶段调用号。模型无法把这些值作为工具参数重新指定。

Patch 准入包括：

- unified diff 最大 100,000 字符，最多修改 20 个文件；
- 现有或新增文本文件最大 1 MiB；
- 路径必须在 `allowed_scope`，且不位于保护目录；
- 拒绝二进制变更、submodule、符号链接、文件类型或权限模式变化；
- 新文件不能被 Git ignore；
- Patch header 和 hunk 必须合法，路径不能 URL 编码或使用全角替代字符；
- 只允许移除包裹整个 Patch 的 Markdown 围栏、统一换行、重算 hunk 行数和补齐结尾换行；这些规范化动作全部写入 audit；
- 使用临时 Git index 对 `base_commit` 和累计工作区执行 `git apply --check`，通过后才把目标路径物化到真实工作区；
- 应用后再读取 Git 变更，确认 touched files、累计 Diff 和文件状态与预期一致；任何中途异常都恢复本次调用前快照。

最终 Patch 不是复用模型提供的文本，而是从真实工作区相对 `base_commit` 重新生成，并再次在临时 index 中验证可应用性。

### 14.5 测试执行不变量

测试命令只允许以下逻辑形式：

```text
pytest ...
python -m pytest ...
py -m pytest ...
```

命令不能包含管道、重定向、命令连接符或换行；程序使用 `shlex` 解析后以 `shell=False` 启动，并把逻辑可执行文件替换为已验证的目标环境 Python。即使仓库中存在 tox，也不会执行 `tox` 或 `python -m tox`。

每条命令执行前后都会计算包含 base-relative Diff 与未跟踪路径的工作区指纹。测试写入受跟踪文件或产生非忽略文件时，结果升级为 `SAFETY_ERROR`，并要求回滚；测试通过不能掩盖工作区副作用。

### 14.6 失败与回滚矩阵

| 失败位置 | 一般是否已有修改 | 默认处理 |
| --- | --- | --- |
| 环境预检、Initialize、Parse Issue | 否 | 直接失败，生成报告 |
| Explore | 否 | 聚合探索失败后由 Coordinator 终止 |
| Coding 上下文创建前 | 否 | 直接失败 |
| Coding 已进入写入阶段 | 可能有 | Coding 节点使用调用前快照和 base commit 自动回滚 |
| Review 调用或结构失败 | 是 | 图结束后，交互模式询问回滚；非交互默认保留 |
| 普通测试断言失败或超时 | 是 | 反馈 Coordinator，可继续探索或返工 |
| 测试环境失败 | 是 | 工作流终止，再按工作区状态处理 |
| 测试修改工作区 | 是且状态不可信 | 设置 `rollback_required`，进入受控回滚 |
| Coordinator 主动 `FAILED` | 可能有 | 进入 Finalize；必须回滚时自动回滚，否则交由 CLI 决定 |
| Finalize 准入或保存失败 | 是 | 工作流失败，保留证据并按策略处理工作区 |

交互终端只有在“运行失败、存在修改、且没有强制回滚要求”时询问：默认答案是保留；用户选择回滚后，结果写入 `rollback_decision_rXX.json`。非交互环境不会等待输入，默认保留现场并记录 `KEEP_NON_INTERACTIVE`。

回滚前会再次确认当前 HEAD 仍等于 `base_commit`。如果 Git 基线已被外部进程改变，系统不会强行覆盖，而是返回 `SAFETY` 回滚失败，让开发者人工处理。

## 15. 开发、测试与扩展指南

### 15.1 源码职责速查

| 目录 | 主要职责 | 修改时重点检查 |
| --- | --- | --- |
| `src/cli/` | 参数、预检编排、终端输出、报告会话、交互回滚 | 退出码、非交互行为、报告始终生成 |
| `src/graph/` | State、节点注册、条件路由、并行 Send | reducer、循环上限、成功/失败出口 |
| `src/nodes/` | 每个工作流步骤的确定性编排 | 输入 State、局部更新、失败分类、产物落盘 |
| `src/agents/` | Agent 创建、工具授权、结构化输出策略 | 每个角色只获得必要工具 |
| `src/prompts/` | 角色规则和上下文组装 | 不把程序安全约束仅留在提示词中 |
| `src/schemas/` | Pydantic/TypedDict 数据契约 | 跨字段一致性、路径规范化、额外字段策略 |
| `src/tools/` | 只读文件/Git/搜索和受限 Coding 工具 | 路径边界、资源上限、Shell 隔离 |
| `src/services/` | Issue、环境、模型、测试、报告、产物等基础设施 | UTF-8、超时、外部进程、可恢复性 |
| `tests/` | pytest 单元与集成测试 | `tmp_path`、`monkeypatch`、假模型、真实 Git fixture |

### 15.2 新增或修改工作流节点

新增节点时应按以下顺序完成：

1. 明确节点需要读取和写入的 `ResolverState` 字段；新增字段时先更新 `graph/state.py`。
2. 节点只返回局部更新，不能复制并覆盖整个 State。
3. 并行分支写入列表时使用 reducer，并且每个分支只返回自己的新增项。
4. 在 `graph/builder.py` 注册节点和成功/失败边；确认失败是否需要经过 Finalize。
5. 为节点输出定义 Pydantic 契约，不让自由文本承担路由职责。
6. 关键输入、结果或失败必须写入具有唯一坐标的 artifact。
7. 增加成功、输入错误、模型错误、安全失败和重复执行边界测试。

不要把具有外部副作用的逻辑直接塞进路由函数。路由只读取 State 并选择边；文件写入、Git、模型调用和测试执行属于节点或 service。

### 15.3 扩展 Agent 或工具

新增工具前先判断角色是否真的需要该能力：

- Coordinator 原则上保持无工具，只做结构化路由。
- Explorer 和 Reviewer 保持只读；需要新证据时优先添加受限只读工具。
- Coder 的写入仍应收敛到 `apply_patch`，不要增加任意文件写入、Shell 或 Git commit 工具。
- Reporter 保持无工具，只接收筛选后的总结上下文。

工具接口应使用仓库相对路径，内部自行解析并验证真实路径；返回内容必须有资源上限和明确截断标记。失败使用 `FailureInfo` 或稳定的 `format_failure_for_agent` 文本，不返回无法分类的异常堆栈给模型。

如果新增结构化 Agent 输出，需要同时提供：Pydantic schema、ToolStrategy 或 function calling 配置、最多 3 次结构重试、节点侧类型复核，以及缺失字段/矛盾字段测试。

### 15.4 扩展新的语言或测试框架

当前只支持 Windows 上的 Python + pytest。支持新生态不应只在 `detect_project_type` 中增加一个 marker，还需要完整实现：

1. 环境或运行时发现器，能够验证实际可执行文件身份；
2. 测试命令 schema 和白名单 argv 解析器；
3. 不经过 Shell 的执行器、超时和 stdout/stderr 落盘；
4. 临时目录、缓存和环境变量隔离；
5. 工作区执行前后指纹；
6. 对应的 `EnvironmentInfo` / `TestResult` 扩展或新契约；
7. Windows 路径、符号链接、目录联接和进程清理测试；
8. 文档中的支持范围和非目标更新。

只有 marker 而没有以上能力时，应继续明确拒绝，而不是“尽力运行”未知命令。

### 15.5 本项目测试策略

开发环境使用锁定的 uv 依赖；其中 `langchain==1.3.14` 与 `langchain-openai==1.3.5` 固定为共享 `langchain-core>=1.4.9,<2.0.0` 的已验证组合：

```powershell
uv sync
uv run ruff check src tests
uv run pyright src
uv run pytest -q --cov=src --cov-report=term-missing --cov-fail-under=80
uv run pytest tests/test_project_detector.py -q
```

测试主要采用以下隔离方式：

- `tmp_path` 创建临时仓库、运行目录和虚拟环境结构；
- `monkeypatch` 隔离环境变量、网络、subprocess 和模型；
- 假模型验证结构化输出、重试次数和传入消息；
- 临时 Git 仓库验证工作区状态、Patch、回滚、未跟踪文件和基线变化；
- Windows 专用路径与解释器布局测试 VENV、Conda 和目录联接；
- 真实子进程测试 argv 绑定、超时、日志、环境错误识别和临时目录清理。

仓库使用 Ruff 执行基础静态检查、Pyright 检查 `src/` 类型，并通过 pytest-cov 维持不低于 80% 的行覆盖率。GitHub Actions 在 Windows 和 Python 3.13 下使用锁定依赖执行同一组检查；当前没有自动格式化器。提交前还应运行相关定向测试和 `git diff --check`，修改文档时检查相对链接和 Mermaid fence 是否闭合。

### 15.6 文档与实现同步规则

以下实现变化必须同步更新 README、本文档或完整流程图：

- 支持的平台、语言、环境目录或测试框架；
- StateGraph 节点、边、循环或 Finalize 准入条件；
- 配置项、默认值和 CLI 参数；
- Agent 工具权限、Patch 上限和路径保护；
- 运行目录、临时目录、清理策略和产物命名；
- Failure 类型、回滚策略和非交互行为；
- 评测集规模、模型、日期或统计口径。

文档中的确定性数值应从配置常量、schema 或运行报告计算，不应从历史终端截图手工猜测。

## 16. 常见故障排查

### 16.1 环境预检

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 未发现 `.venv`、`venv` 或 `.conda` | 目标仓库没有受支持环境 | 在目标仓库根目录准备唯一环境，并安装项目、pytest 和测试依赖 |
| 环境未被 Git ignore | 环境目录可能污染工作区和 Diff | 加入目标仓库 `.gitignore`，用 `git check-ignore .venv` 等命令确认 |
| 存在多个环境候选 | 无法确定测试应使用哪个解释器 | 只保留一个受支持目录 |
| 缺少 `pyvenv.cfg` 或 `conda-meta` | 目录名称像环境，但不是有效环境 | 重新创建正确环境，不要用空目录占位 |
| `sys.prefix` 不匹配 | 解释器、激活信息或目录被移动 | 使用环境自身解释器重建或修复环境 |
| pytest 不可用 | 测试依赖没有装进目标环境 | 在目标环境中安装依赖并确认 `python -m pytest --version` |
| 工作区不干净 | 存在已跟踪或未跟踪修改 | 提交、暂存到其他位置或清理后重试；工具不会覆盖用户修改 |
| `RUN_ROOT` 位于目标仓库内 | 运行日志会污染业务仓库 | 改为控制器目录或其他仓库外路径 |

Conda 在 Windows 下使用环境根目录的 `python.exe`，并将环境根、`Scripts`、`Library/bin`、`Library/usr/bin` 和 `Library/mingw-w64/bin` 放到测试进程 PATH 前部；VENV 使用 `Scripts/python.exe`。系统不会调用 `conda activate`。

### 16.2 pytest 入口检测

直接 pytest 入口可以由以下任一信号确认：

- 根目录存在 `pytest.ini`；
- `pyproject.toml` 包含 `[tool.pytest.ini_options]`；
- 根目录存在 `tests/`；
- `tox.ini` 包含独立的 `[pytest]` 配置段，例如通过 `testpaths = testing` 指定非标准测试目录。

只有 `tox.ini`、没有上述 pytest 配置时会拒绝。解决方式不是让工具执行 tox，而是由开发者准备可在当前环境直接运行的 pytest 配置和依赖。

### 16.3 Issue 解析

“Issue 缺少可以安全确定的验收条件”表示标题和正文既没有明确期望，也无法在不猜测具体行为的前提下推导单一方向。系统已经自动进行一次受控恢复；继续重试相同模糊文本通常没有意义。应补充：当前错误行为、期望方向、关键输入场景，以及能够区分修复前后的可观察结果。

缺少精确返回值本身不等于歧义。“结果错误”“尺寸错误”“不应意外抛错”等单一方向可以生成同抽象层级的最小验收条件；只有存在多个互斥合理预期时才要求人工澄清。

### 16.4 Agent 步数与探索预算

“Recursion limit reached” 表示某次工具 Agent 在 `AGENT_RECURSION_LIMIT` 内没有返回结构化结果。优先检查其是否反复搜索过大目录、重复读取同一文件、收到截断结果却没有缩小范围，或模型服务没有正确完成 ToolStrategy。

增加步数并不是第一选择。先让探索目标更具体、缩小仓库范围或修复提示词循环；确认任务确实需要更多工具轮次后，再调整 `AGENT_RECURSION_LIMIT`。探索批次耗尽后系统会强制进入 Coding，因此 `MAX_EXPLORE_BATCHES` 也不应无限增加。

### 16.5 文件访问与路径错误

Coder 的 `list_files`、`read_file` 和搜索工具已经绑定目标仓库，不接受 `repo_path` 参数；只应传仓库相对 `path`。如果模型同时传入绝对仓库路径或把根路径拼进相对路径，工具会返回 `INPUT`，这属于模型参数错误，不是文件系统环境故障。

出现 `read_file/list_files/search_text ... INPUT` 时依次检查：

1. 参数是否为仓库相对路径；
2. 路径是否重复包含仓库目录名；
3. 大小写和分隔符是否与仓库一致；
4. 文件是否位于受保护目录；
5. 搜索范围是否是目录而不是文件。

### 16.6 Patch 无法应用

常见原因包括：hunk 上下文与当前文件不一致、行号或 hunk 计数错误、路径前缀不合法、前一次 Patch 已改变目标区域、模型原样重试旧 Patch。Coder 应重新 `read_file` 和 `inspect_changes`，基于当前累计工作区生成更小 Patch。

达到 10 次尝试后返回 `LIMIT` 是有意的保护，不应在工具层静默提高或绕过。需要先分析 audit 中每次失败的规范化 Patch 和 Git 错误。

### 16.7 `changed_files` 不一致

`CodingResult.changed_files` 是模型声明，真实修改来自 Git。两者必须逐项一致且顺序与 `inspect_changes` 返回相同；遗漏文件、额外文件或路径格式不同都会按 `SOLUTION` 拒绝。修复方式是要求 Coder 在返回前调用 `inspect_changes` 并直接使用其文件列表，而不是人工推测。

### 16.8 测试失败分类

| TestResult 状态 | failure.type | 含义 |
| --- | --- | --- |
| `PASSED` | 无 | 退出码为 0 |
| `FAILED` | `SOLUTION` | 普通断言或测试失败，退出码非 0 |
| `ENVIRONMENT_ERROR` | `ENVIRONMENT` | 无法启动、缺模块、权限或 DLL 导入等环境问题 |
| `TIMEOUT` | `LIMIT` | 超过单条命令超时，进程已终止 |
| `SAFETY_ERROR` | `SAFETY` | 测试前后工作区指纹不同 |

完整 stdout/stderr 才是诊断依据；仅非通过测试的 `output_tail` 会传给 Coordinator，以限制模型上下文。环境错误识别会检查 `ModuleNotFoundError`、`No module named`、权限错误和 DLL 导入失败等标记。

### 16.9 报告与产物

Reporter 输出格式错误、模型调用失败或模型不可用时，系统使用程序模板生成 `report.md`，这不会改变主工作流成功或失败事实。报告已存在时不会覆盖；如果运行目录残留同名报告或临时文件，应使用新的 run ID，而不是删除历史证据后重跑到原目录。

`diff.patch` 和 `diff.json` 只在成功终态生成。失败报告中“最终 Patch：未生成”不代表日志缺失，应继续查看 `logs/failure_*`、Coding audit、Review 和测试产物。

### 16.10 测试临时目录未清理

测试运行时位于控制器项目同级的 `.issue-solver-runtime/<run_id>/`。每条命令启动独立清理进程，主进程正常结束、超时、Ctrl+C 或被强制终止导致管道关闭时，清理进程都会尝试删除专用目录。

若仍有残留，先确认没有运行中的测试或清理进程，再检查文件占用和权限。清理失败会把测试结果升级为 `ENVIRONMENT_ERROR` 并记录 stderr，不会假装测试成功。

## 17. 真实 Issue 测评

项目当前评测集包含 15 个真实开源项目 GitHub Issue，其中普通案例 10 个、困难案例 5 个。每个案例固定修复前基线，并附带能在基线失败、修复后通过的回归测试；Issue 当前可能已经修复、关闭、重新打开或内容变化，评测结果不代表 GitHub 上的当前状态。

2026-07-21 使用 `deepseek-v4-flash` 完成的端到端结果为：

| Issue 数 | 成功修复 | 总成功率 | 普通成功率 | 困难成功率 | Patch 生成率 | 测试通过率 | 平均耗时 | 平均轮次 | 平均 Token |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 15 | 13 | 86.67% | 100.00% | 60.00% | 86.67% | 86.67% | 411.67 秒 | 1.33 | 1,576,349.93 |

测试通过率按全部 Issue 计算，只有定向测试和全量回归都通过才计为通过。两个失败案例都在 Coding 阶段终止，没有生成最终 Patch，也没有进入测试：一个因 Patch 连续无法应用达到尝试上限，另一个因模型声明的 `changed_files` 与 Git 事实不一致。

完整逐项结果、运行 ID、耗时、Token 和失败分析见[测评结果报告](../evaluation/results.md)；基线与测试约定见[评测集](../evaluation/benchmark.md)。
