# Issue Solver 评测集

> **状态声明：** 表中问题当前可能已经修复、关闭、重新打开或内容发生变化。评测仅以固定的修复前基线、评测提交和附带回归测试为准，不代表 GitHub 问题的当前状态。

本评测集包含 10 个普通案例和 5 个困难案例。测试源码见 [tests.md](tests.md)。

测评时间：**2026-07-21**。

## 使用约定

- 目标仓库使用独立且已准备好的虚拟环境，工作区必须干净。
- `基线` 的原有测试应通过；`评测提交` 在基线之上加入问题测试，该测试应失败。
- 问题修复后，新增测试与原有测试均应通过，不得删除、跳过或弱化断言。

## 普通案例

| 问题 | 基线 | 评测提交 | 测试文件 | 评测点 |
|---|---|---|---|---|
| [cachetools #387](https://github.com/tkem/cachetools/issues/387) | `8011b71` | `d281247` | `tests/test_issue_387.py` | `cachedmethod` 支持 autospec 检查 |
| [itsdangerous #429](https://github.com/pallets/itsdangerous/issues/429) | `096c8d4` | `b0d8eec` | `tests/test_itsdangerous/test_issue_429.py` | 非定时序列化器拒绝 `max_age` |
| [cattrs #761](https://github.com/python-attrs/cattrs/issues/761) | `f8663b3` | `44a83a7` | `tests/test_issue_761.py` | JSON/PyYAML 精确处理 `Decimal` |
| [attrs #1245](https://github.com/python-attrs/attrs/issues/1245) | `9e443b1` | `c29e557` | `tests/test_issue_1245.py` | 嵌套验证错误包含成员下标 |
| [Click #2740](https://github.com/pallets/click/issues/2740) | `874ca2b` | `b63898a` | `tests/test_issue_2740.py` | 命令行输出异常注释 |
| [boltons #261](https://github.com/mahmoud/boltons/issues/261) | `81326f4` | `deac26d` | `tests/test_issue_261.py` | 正确传递仅关键字参数 |
| [python-dateutil #1508](https://github.com/dateutil/dateutil/issues/1508) | `5b0cdde` | `a6c1b59` | `tests/test_issue_1508.py` | 拒绝非法时区偏移 |
| [pytest-rerunfailures #270](https://github.com/pytest-dev/pytest-rerunfailures/issues/270) | `5ef1dd0` | `d8dba81` | `tests/test_issue_270.py` | 清理阶段异常参与重跑排除判断 |
| [more-itertools #1204](https://github.com/more-itertools/more-itertools/issues/1204) | `64be96c` | `914f930` | `tests/test_issue_1204.py` | 新增惰性 `duplicates` API |
| [humanize #214](https://github.com/python-humanize/humanize/issues/214) | `073a00b` | `4fd8e51` | `tests/test_issue_214.py` | `natural_list` 支持 “or” |


## 困难案例

| 问题 | 基线 | 评测提交 | 测试文件 | 评测点 |
|---|---|---|---|---|
| [Rich #3299](https://github.com/Textualize/rich/issues/3299) | `7912306` | `02f1800` | `tests/test_issue_3299.py` | 混合宽度字符切分 |
| [Click #2614](https://github.com/pallets/click/issues/2614) | `333c28d` | `922ad8f` | `tests/test_issue_2614.py` | 补全时不执行可调用默认值 |
| [Werkzeug #3156](https://github.com/pallets/werkzeug/issues/3156) | `1b00618` | `9bda10d` | `tests/test_issue_3156.py` | 路由状态机保持稳定优先级 |
| [Pluggy #681](https://github.com/pytest-dev/pluggy/issues/681) | `c1a5f3e` | `2edd5ec` | `testing/test_issue_681.py` | 追踪输出安全处理代理字符 |
| [Jinja #2069](https://github.com/pallets/jinja/issues/2069) | `5ef7011` | `1bbf685` | `tests/test_issue_2069.py` | 正确分析分支内声明变量 |
