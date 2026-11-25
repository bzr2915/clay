# primitive_verify — 通用单原语自动验证

输入 = 一份"数据 + 薄绑定"的提交；输出 = 一个由框架机械判定的 PASS / FAIL / INCONCLUSIVE。
AI 只负责填参数、指认被测函数、调用命令；**判定代码不在 AI 手里**。

## 1. 一次验证包含什么

```
primitive_verify/primitives/<原语名>/
    bind.py    由 AI 填写：只调用工程函数，登记返回值（禁止任何计算）
    spec.json  由 AI 填写：输入参数 + 预期结果（纯数据，逐条写明依据）
    input/     可选：需要版图输入时放这里
```

框架侧（人维护，AI 不得修改；`registry.py` 例外，见 §5）：

| 文件 | 作用 |
|---|---|
| `registry.py` | 原语登记：被测工程函数、输出形态（layout / data / coords）、登记来源与复核状态。AI 可以追加，不能修改已冻结条目（§5） |
| `frozen_entries.json` | 已冻结条目的内容记录，只有人可以修改（§5） |
| `resolve.py` | 按路径取实际值，如 `plan.count`、`layout.polygons[22/0][0]` |
| `compare.py` | **唯一判定处**：eq / num / bbox / set / count / reject / eq_other |
| `bindcheck.py` | bind.py 的 AST 检查：禁止算术、分支、内建调用 |
| `expected_schema.py` | spec.json 的字段白名单与结构检查 |
| `views.py` | 框架自有的"测量视图"：把工程返回对象换算成标准量（如对称轴到两侧的距离） |
| `severance.py` | 切断测试：断开被测函数后测试必须失败 |
| `runner.py` | 执行、比对、出报告 |

## 2. 命令

```powershell
cd clay   # 仓库根目录
python -m primitive_verify init  <原语名>    # 生成提交目录（新原语时用）
python -m primitive_verify check <原语名>    # 静态检查：bind.py 是否含计算、spec 是否合法
python -m primitive_verify run   <原语名>    # 执行 + 比对 + 切断测试 + 报告
```

`run` 的产物在 `data\primitive_generalization\primitive_verify_runs\<原语名>_<时间>\`：
`REPORT.md`（逐项对比表 + 切断测试 + 哈希）、`RESULT.json`、`production_only.gds`（出图类）。

## 3. 新增一个原语

1. 在 `registry.py` 追加：原语名、被测函数的 `模块:函数名`、输出形态。人登记时用默认值；AI 登记时必须写 `provenance='ai', status='pending_review'`。
2. `python -m primitive_verify init <原语名>` 生成目录。
3. 把 `templates/bind.py` 顶部的说明发给 AI，让它填 `bind.py` 与 `spec.json`。
4. 人工复核 `spec.json` 的 `expected` 与 `derivation`，确认预期不是抄运行结果。复核通过后，把条目改为 `status='frozen'`，并写入 `frozen_entries.json`（§5）。
5. `check` 通过后 `run`，看 `REPORT.md` 的逐项对比表。

若原语需要从返回值派生的量（例：轴到两侧的距离），在 `views.py` 登记一个测量视图，
不要写进 `bind.py`——绑定文件里的算术会被静态检查拒绝。

## 4. 为什么这样能排除"AI 过拟合"

| 机制 | 证据位置 |
|---|---|
| AI 的产物里没有判定代码 | `compare.py` 哈希写入每次 `REPORT.md` |
| 绑定文件不能计算或分支 | `bindcheck.py`：算术、if/for、推导式、round/max/min 一律拒绝 |
| 实际值由框架按路径取，AI 只能指认位置 | `resolve.py` |
| 预期是字面量，必须写明推导依据 | `expected_schema.py` 强制 `derivation` |
| 结果必须依赖被测函数 | `severance.py`：切断后若仍产出结果 → FAIL |
| 声明的被测函数必须全部被调用 | `runner.py` 的 TARGETS 检查 |
| 提交与框架的哈希固化在报告里 | `runner._hashes` |

自动检查只能发现"规则型"的越界（计算、分支、缺字段、同名函数）。改写过的等价实现由
切断测试排除：若结果来自副本，断开工程函数后它仍会产出。

## 5. 登记表的修改规则（2026-11-15）

目标：LLM 学到新原语后可以自己完成登记，让学习流程自动走完；同时避免"AI 自己登记、自己写预期、自己判通过"。

| 规则 | 如何保证 |
|---|---|
| AI 只能**追加**条目，并标明 `provenance='ai'`、`status='pending_review'` | 约定；`registry.py` 文件头写明 |
| 任何人不得修改或删除 `status='frozen'` 的条目 | `frozen_entries.json` 记录冻结条目的 `targets` 与 `form`；`tests/test_primitive_classification.py` 比对两者，任何改动都让测试失败 |
| 只有人可以批准：把条目改为 `frozen` 并写入 `frozen_entries.json` | 约定；`frozen_entries.json` 与判定代码同属人维护的文件 |
| 待复核条目照常运行验证，但**不计入冻结结果** | `runner.py` 在 `REPORT.md`、`RESULT.json`（`review_status`、`provenance`）和终端输出中注明"待复核" |

当前状态：29 个条目中 11 个已冻结；18 个待复核——比较器学习中由 AI 登记的 16 个（`provenance='ai'`），以及由人登记、但验证提交由 AI 补齐的 `build_contacted_device`、`orthogonal_path`（`provenance='human'`）。它们的单原语验证均为 PASS，但 spec 的预期值尚未经人复核（见 `docs/PRIMITIVE_CATALOG_zh.md` §5）。
