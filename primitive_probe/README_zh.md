# primitive_probe：单个版图原语的人工验证系统

## 1. 解决的问题

审稿人要求给出**单个原语**的人工验证结果，并要求证明结果来自工程自身的 primitive 代码，而非另写的 AI 脚本。本系统的做法是：

- 把**一个原语**作为输入，交给工程现有的流程函数执行；输出经工程自己的 `materialize` 组装，写成 GDS。
- 同时给出**人工编写的预期结果**（文字清单和可选的预期框）。
- 预期与实际图形由人在 KLayout 中对比，结论填入表格。程序不做通过/不通过判定。

## 2. 防止“验证的是探针脚本而不是工程代码”的四道约束

| 约束 | 实现 | 审核人如何核实 |
|---|---|---|
| 只能调用工程代码 | 适配器必须通过 `ctx.call(fn, …)` 调用函数，且 `fn` 必须定义在 `clay.*` 模块中，否则抛出 `PermissionError` | `manifest.json` → `production_calls` |
| 调用可追溯 | 每次调用记录模块、函数名、源文件:行号、**文件 SHA-256** 和**函数源码 SHA-256**，以及完整参数和返回值 | 用 `certutil -hashfile <file> SHA256` 与报告比对 |
| 必须真正执行被测原语 | 注册时声明 `targets`；运行后若有任一 target 未被调用，该用例直接失败 | `REVIEW.md` 中的调用表 |
| 探针不能自己画输出、不能自算预期 | `audit` 静态检查 `adapters/`：禁止导入 klayout、gdstk 等几何库或 render；禁止读取 `expected`。输入图形只能来自 `fixtures.py`，并单独画在 200 层 | `python -m primitive_probe audit`；`production_only.gds` 中不含探针层 |

预期结果只以字面文字或坐标写在 `cases/*.json` 中，任何代码都不计算预期值。

## 3. 目录

```
primitive_probe/
  core.py            注册表、生产调用记录、隔离审计
  fixtures.py        唯一允许构造“输入”图形的地方（如两个矩形）
  render.py          写 GDS、图层文件、REVIEW.md、汇总表
  adapters/          每个原语的薄适配器：组装输入 → 调用工程函数
    learning_flow.py   clay/learning 反相器流程中的原语
    symmetry_placer.py MILP 对称放置器（annotate_to_solver）
  cases/*.json       人工编写的用例：输入参数 + 预期结果
tests/test_primitive_probe.py   框架自检（不判断原语对错）
```

## 4. 使用

```powershell
cd clay   # 仓库根目录
python -m primitive_probe list            # 已注册原语、对应工程函数、用例数（无用例会提示）
python -m primitive_probe audit           # 隔离审计
python -m primitive_probe run             # 运行 cases/ 中全部 U 用例；加 --archive 再包括归档证据用例
python -m primitive_probe run U06_custom_symmetry_control_no_pair turn     # 按用例 id 或适配器名选择
python -m primitive_probe run --open      # 运行后用 D:\Klayout\klayout_app.exe 打开
```

输出默认位于 `data/primitive_generalization/primitive_probe_runs/<时间戳>/`：

| 文件 | 内容 |
|---|---|
| `<case>/production_only.gds` | **只有**工程代码返回的版图，未作任何修改 |
| `<case>/review.gds` | 同一份版图，叠加探针层：200 输入夹具（青）、201 人工预期框（品红虚线）、202 工程返回值标记，例如对称轴（橙） |
| `<case>/REVIEW.md` | 调用链、输入、工程返回值、预期清单，以及留空的“实测”和“通过?”两栏 |
| `<case>/manifest.json` | 完整机读记录 |
| `layers.lyp` | KLayout 图层名称和颜色（File > Load Layer Properties） |
| `review_sheet.csv` | 所有用例的预期条目汇总，可用 Excel 逐条填写实测值、Y/N、审核人和日期 |

人工核对步骤：在 KLayout 中打开 `review.gds` 并加载 `layers.lyp`，按 REVIEW.md 中的 E1…En 逐条用 Ruler（K）测量，再填写 `review_sheet.csv`。

各原语参数的含义与可填值见 [PARAMETERS_zh.md](PARAMETERS_zh.md)；每个测试文件的参数路径、类型、当前值与作用见 [CASE_CATALOG_zh.md](CASE_CATALOG_zh.md)（`python -m primitive_probe catalog` 生成）。

## 5. 现有原语与用例
附件复现布局所用 10 个原语的含义、参数，以及对应 U 测试的预期输出见 [U_TESTS_GUIDE_zh.md](U_TESTS_GUIDE_zh.md)。


日常使用的是 `cases/` 中的 U 系列**自定义起点**，每个原语一个，参数简单、便于修改：

| 用例 | 被测工程函数 | 是否属于附件复现布局的流程 |
|---|---|---|
| U01_custom_build_row | `contact_primitives.build_row` | 是 |
| U02_custom_build_contacted_device | `device_placement.build_contacted_device` | 否（两带流程） |
| U03_custom_contact_plan | `contact_primitives.contact_plan` | 是 |
| U04_custom_geometry_plan | `geometry_primitives.geometry_plan` | 是 |
| U05_custom_solve_area_relation | `area_placement.solve_area`（关系偏好模式） | 否 |
| U06_custom_symmetry_control_no_pair | `annotate_to_solver.solve_placement_from_constraints` | 否 |
| U07_custom_turn | `contact_primitives.turn` | 是 |
| U08_custom_materialize | `contact_primitives.materialize` | 是 |
| U09_custom_orthogonal_path | `routing_primitives.orthogonal_path` | 否（布线） |
| U10_custom_inverter_topology | `contact_primitives.inverter_groups` | 是 |
| U11_custom_solve_area_core | `area_placement.solve_area`（core_boxes 模式） | 是 |
| U12_custom_relation_features | `placement_relations.features` | 是 |
| U13_custom_relation_rank | `placement_relations.rank` | 是 |
| U14_custom_candidate_verify | `contact_primitives.verify` | 是 |

扩散链选择是 `_generate` 中的内联代码，无独立函数，不单独测试。

其余用例（原 B/D/P/G/R/S/T/M/W/A/I/F/K/V/H 系列，包括泛化、负对照、对照组）已移到 `cases_archive/`。它们不出现在 `list` 中，用 `run --archive` 运行，或用 `run <id> --archive` 单独运行；`show`/`try` 可直接使用。

## 6. 为新原语添加 probe（对今后的 primitive 通用）

1. 在 `adapters/` 中新增或修改一个模块。所有 U 测试共用同一骨架，区别只在输出形式（A/B/C 三种）。以下模板均摘自现有适配器：

**共同骨架**（所有测试必须遵守）：
- `@register(..., targets=(被测工程函数, ...))`：声明测哪些工程函数；运行后若有任何一个未被执行，测试判失败。
- `def probe(ctx, p)`：`p` 就是测试文件中的 `params`。**输入一律取自 `p`，不得在适配器中写死。**
- 适配器只做输入组装，即把 JSON 数据转成工程函数要求的对象。工程函数一律经 `ctx.call(...)` 调用。
- 返回的 `ProbeOutput` 只放工程函数返回的东西；适配器不得导入 klayout，也不得自己画图或计算结果。

**A. 出图类**：工程函数最终返回 Layout。摘自 U07 的 `turn_probe`；U01、U03、U04、U08、U11 同理。

```python
from clay.learning import contact_primitives as contacts        # 被测工程模块
from primitive_probe.core import register, ProbeOutput
from primitive_probe import fixtures

@register('turn', targets=(contacts.turn, contacts.materialize), category='transform',
          summary='一句话说明', params_doc='参数说明',
          render_path='contacts.turn -> contacts.materialize')
def turn_probe(ctx, p):
    src = fixtures.row(p['row']['layers'], p['row'].get('terminals', ()))   # 输入组装：JSON → 工程的 row 格式
    turned = ctx.call(contacts.turn, src, p['quarter_turns'])                # 被测原语
    layout = ctx.call(contacts.materialize, {'turned': turned}, {'turned': (0, 0)})  # 工程代码出图
    return ProbeOutput(layout=layout)
```

**B. 决策类**：工程函数返回数据，没有版图。摘自 U10 的 `inverter_topology_probe`；U12、U13、U14 同理。注意加 `geometry=False`。

```python
from clay.learning import contact_primitives as contacts
from clay.learning.circuit import parse_cdl, PlacementContract

@register('inverter_topology', targets=(parse_cdl, contacts.inverter_groups), category='decision',
          geometry=False, summary='…', params_doc='…', render_path='parse_cdl -> inverter_groups (data only)')
def inverter_topology_probe(ctx, p):
    circuit = ctx.call(parse_cdl, p['netlist'])                     # 工程的网表解析
    contract = PlacementContract(**p['contract'])                   # 输入组装：工程自己的数据类
    groups, gate, output = ctx.call(contacts.inverter_groups, circuit, contract)
    return ProbeOutput(outputs={'groups': {k: [d.name for d in v] for k, v in groups.items()},
                                'gate_net': gate, 'output_net': output})
```

   若输入是一个版图（如 U12、U14），用 `fixtures.load_gds(p['layout_gds'])` 读入，并在返回值中写 `context_gds=p['layout_gds']`，说明它是输入而非输出。

**C. 坐标类**：工程函数只返回坐标。摘自 U06 的 `symmetry_probe`。返回的坐标**原样**放进 `extents`，标量（如对称轴）放进 `markers`，不做任何换算：

```python
    sol = ctx.call(solve_placement_from_constraints, constraints, devs, time_limit_s=p.get('time_limit_s', 10))
    extents = [{'layer': (22, 0), 'bbox_nm': list(pd.extent), 'label': name} for name, pd in sol.placed.items()]
    markers = [{'kind': 'vline', 'at_nm': sol.axis_x, 'label': f'axis_x={sol.axis_x}'}]
    return ProbeOutput(extents=extents, markers=markers, outputs={...})
```
2. `python -m primitive_probe template new_primitive` 会生成用例骨架，存为 `cases/<id>.json`，再填写 `params` 和 `expected`（statements 必填；shapes 可选；derivation 写明依据的 PDK 规则或设计意图）。建议加一个对照组和一个负对照（`"expect_rejection": true`）。
3. 运行 `audit`、`run <id>` 和 `pytest tests/test_primitive_probe.py`。如果某个适配器没有用例，测试会失败，`list` 也会提示。

## 7. 边界与需要人工注意的事项

- **当前用例中的预期值由 AI 起草**，依据是 PDK 规则编号和函数文档中的设计意图，未运行工程代码去反推。但用作审稿证据前，必须由人工逐条复核或重新推导 `expected`，并在审核记录中注明复核人。
- 适配器是唯一的信任边界：它决定把什么输入交给工程函数。适配器每个只有几行，审核时应阅读 `adapters/*.py`，确认输入组装没有篡改语义。
- 对称放置器返回的是坐标而非 Layout，其 GDS 由 `render.py` 按返回的 extent 原样写出。这条链路不经过版图 `materialize`，报告中已注明。
- 未单独覆盖的环节：`_generate` 内联的扩散链选择（`supported[0]`），它没有独立函数可调用；`placement_relations.features/rank`，它们是测量和排序，不是生成；`route_layout` 整体布线需要完整电路，不属于单原语。
- 单原语通过只说明“参数到该原语的几何或决策”符合预期，不代表完整 DRC、LVS 或可布线性通过。层间接口关系见 `docs/ATOMIC_PRIMITIVES_KLAYOUT_MANUAL.md` 第 4 节。
