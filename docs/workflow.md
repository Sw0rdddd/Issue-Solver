# Issue Solver 完整流程

下图覆盖从 CLI 输入、环境预检到最终 Patch、报告和失败处理的完整运行路径。

```mermaid
flowchart TD
    A[CLI 输入目标仓库与 Issue] --> B[创建独立运行目录]
    B --> C[环境预检]
    C -->|失败| X[记录结构化失败]
    C -->|通过| D[初始化仓库<br/>确认 Git 基线与 pytest 入口]

    D -->|失败| X
    D -->|通过| E[解析并规范化 Issue]
    E -->|验收条件为空| E1[受控重试一次]
    E1 -->|仍无法确定| X
    E1 -->|恢复成功| F{Coordinator 决策}
    E -->|成功| F

    F -->|EXPLORE| G[并行只读探索<br/>每批 1 至 3 个目标]
    G -->|探索失败| X
    G -->|汇总代码证据| F

    F -->|CODE| H[生成受限 CodingTask]
    H --> I[Coder 读取代码并应用 Patch]
    I -->|失败| X
    I -->|成功| J[程序核对真实 Git Diff]
    J -->|不一致| X
    J -->|一致| K[只读 Review]
    K -->|调用或结构失败| X
    K -->|完成| L[运行定向 pytest]

    L -->|用例失败或超时| F
    L -->|环境或安全失败| X
    L -->|通过| M[运行全量回归]
    M -->|Review APPROVE 且全部通过| N[Finalize 再次校验]
    M -->|Review 未批准或用例失败/超时| F
    M -->|环境或安全失败| X

    F -->|继续调查| G
    F -->|返工| H
    F -->|FINISH| N
    F -->|FAILED| N
    N -->|Review 与本轮测试均通过| O[保存 diff.patch 与 diff.json]
    N -->|准入失败| X
    N -->|工作流已失败| X
    X --> P{工作区是否存在修改}
    P -->|否| R[生成 report.md]
    P -->|是且必须回滚| Q[受控回滚到 base commit]
    P -->|是且可由用户决定| Q1[交互选择回滚或保留<br/>非交互默认保留]
    Q --> R
    Q1 --> R
    O --> R
    R --> S[追加确定性运行结果<br/>角色 Token 分布、总/输入/输出、耗时与产物地址]
    S --> T[输出终端摘要]
    T --> U[结束]
```

## 关键准入条件

- 首次 Coordinator 决策必须先探索仓库，不能直接修改代码。
- Explorer 和 Reviewer 的只读工具固定在当前仓库，Reviewer 的 `git_diff` 还固定相对基线 Commit；Coder 没有 Shell 权限，只能在 `allowed_scope` 内应用 Patch。
- Coding Agent 声明的修改文件必须与 Git 检测到的累计 Diff 完全一致。
- Test 节点先执行定向测试，只有通过后才执行全量回归；Review 为 `APPROVE` 且本轮测试全绿时直接进入 Finalize，其余结果返回 Coordinator 决定继续探索或返工。
- Finalize 只在 Review 为 `APPROVE` 且本轮所有测试为 `PASSED` 时保存最终 Patch，并再次执行相同准入校验。
- 成功运行保存最终 Patch；失败运行根据失败类型和工作区状态自动回滚、询问用户，或保留现场供开发者检查。
