# Issue Solver 评测集

> **状态声明：** 表中问题当前可能已经修复、关闭、重新打开或内容发生变化。评测仅以固定的修复前基线和附带回归测试为准，不代表 GitHub 问题的当前状态。

本评测集选取 10 个真实开源项目的 GitHub Issue。完整结果见 [results.md](results.md)，测试源码见 [tests.md](tests.md)。

最近一次测评运行时间：**2026-07-22 至 2026-07-23**。

## 使用约定

- 目标仓库使用独立且已准备好的虚拟环境，工作区必须干净。
- `基线` 的原有测试应通过；在基线之上加入问题测试后，该测试应失败。
- 问题修复后，新增测试与原有测试均应通过，不得删除、跳过或弱化断言。

## 评测案例

| 问题 | 基线 | 测试文件 | 评测点 |
|---|---|---|---|
| [cachetools #387](https://github.com/tkem/cachetools/issues/387) | `8011b71` | `tests/test_issue_387.py` | `cachedmethod` 支持 autospec 检查 |
| [itsdangerous #429](https://github.com/pallets/itsdangerous/issues/429) | `096c8d4` | `tests/test_itsdangerous/test_issue_429.py` | 非定时序列化器拒绝 `max_age` |
| [cattrs #761](https://github.com/python-attrs/cattrs/issues/761) | `f8663b3` | `tests/test_issue_761.py` | JSON/PyYAML 精确处理 `Decimal` |
| [attrs #1245](https://github.com/python-attrs/attrs/issues/1245) | `9e443b1` | `tests/test_issue_1245.py` | 嵌套验证错误包含成员下标 |
| [Click #2740](https://github.com/pallets/click/issues/2740) | `874ca2b` | `tests/test_issue_2740.py` | 命令行输出异常注释 |
| [boltons #261](https://github.com/mahmoud/boltons/issues/261) | `81326f4` | `tests/test_issue_261.py` | 正确传递仅关键字参数 |
| [python-dateutil #1508](https://github.com/dateutil/dateutil/issues/1508) | `5b0cdde` | `tests/test_issue_1508.py` | 拒绝非法时区偏移 |
| [pytest-rerunfailures #270](https://github.com/pytest-dev/pytest-rerunfailures/issues/270) | `5ef1dd0` | `tests/test_issue_270.py` | 清理阶段异常参与重跑排除判断 |
| [more-itertools #1204](https://github.com/more-itertools/more-itertools/issues/1204) | `64be96c` | `tests/test_issue_1204.py` | 新增惰性 `duplicates` API |
| [humanize #214](https://github.com/python-humanize/humanize/issues/214) | `073a00b` | `tests/test_issue_214.py` | `natural_list` 支持 “or” |
