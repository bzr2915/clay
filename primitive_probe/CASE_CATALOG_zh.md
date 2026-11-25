# 原语 — 测试文件 — 参数 对照表

> 本文件由 `python -m primitive_probe catalog` 根据 `primitive_probe/cases/*.json` 自动生成；新增或修改用例后请重新生成。参数的取值范围与工程检查详见 [PARAMETERS_zh.md](PARAMETERS_zh.md)。

## 总览

| 原语（适配器） | 类别 | 被测工程函数 | 测试文件 |
|---|---|---|---|
| [inverter_topology](#原语-inverter-topology) | decision | `clay.learning.circuit.parse_cdl`<br>`clay.learning.contact_primitives.inverter_groups` | [U10_custom_inverter_topology](#u10_custom_inverter_topology) |
| [solve_area_core](#原语-solve-area-core) | relation | `clay.layout.synth.area_placement.solve_area`<br>`clay.learning.contact_primitives.materialize` | [U11_custom_solve_area_core](#u11_custom_solve_area_core) |
| [relation_features](#原语-relation-features) | decision | `clay.learning.placement_relations.features`<br>`clay.learning.circuit.parse_cdl` | [U12_custom_relation_features](#u12_custom_relation_features) |
| [relation_rank](#原语-relation-rank) | decision | `clay.learning.placement_relations.rank` | [U13_custom_relation_rank](#u13_custom_relation_rank) |
| [candidate_verify](#原语-candidate-verify) | decision | `clay.learning.contact_primitives.verify`<br>`clay.learning.circuit.parse_cdl` | [U14_custom_candidate_verify](#u14_custom_candidate_verify) |
| [build_row](#原语-build-row) | constructor | `clay.learning.contact_primitives.build_row`<br>`clay.learning.contact_primitives.materialize` | [U01_custom_build_row](#u01_custom_build_row) |
| [build_contacted_device](#原语-build-contacted-device) | constructor | `clay.pdk.device_placement.build_contacted_device`<br>`clay.learning.generate.materialize` | [U02_custom_build_contacted_device](#u02_custom_build_contacted_device) |
| [contact_plan](#原语-contact-plan) | planner | `clay.learning.contact_primitives.contact_plan`<br>`clay.learning.contact_primitives.build_row`<br>`clay.learning.contact_primitives.materialize` | [U03_custom_contact_plan](#u03_custom_contact_plan) |
| [geometry_plan](#原语-geometry-plan) | planner | `clay.learning.geometry_primitives.geometry_plan`<br>`clay.learning.contact_primitives.contact_plan`<br>`clay.learning.contact_primitives.build_row`<br>`clay.learning.contact_primitives.materialize` | [U04_custom_geometry_plan](#u04_custom_geometry_plan) |
| [solve_area_relation](#原语-solve-area-relation) | relation | `clay.layout.synth.area_placement.solve_area`<br>`clay.learning.contact_primitives.materialize` | [U05_custom_solve_area_relation](#u05_custom_solve_area_relation) |
| [turn](#原语-turn) | transform | `clay.learning.contact_primitives.turn`<br>`clay.learning.contact_primitives.materialize` | [U07_custom_turn](#u07_custom_turn) |
| [materialize](#原语-materialize) | assembly | `clay.learning.contact_primitives.materialize` | [U08_custom_materialize](#u08_custom_materialize) |
| [orthogonal_path](#原语-orthogonal-path) | routing | `clay.learning.routing_primitives.orthogonal_path`<br>`clay.learning.routing_primitives.segment`<br>`clay.learning.contact_primitives.materialize` | [U09_custom_orthogonal_path](#u09_custom_orthogonal_path) |
| [symmetry_placement](#原语-symmetry-placement) | relation | `clay.layout.annotate_to_solver.solve_placement_from_constraints` | [U06_custom_symmetry_control_no_pair](#u06_custom_symmetry_control_no_pair) |

## 原语 inverter_topology

- 作用：Netlist -> inverter roles: NMOS/PMOS groups, input (gate) net and output net.
- 出图路径：`circuit.parse_cdl -> contact_primitives.inverter_groups (data only)`
- 详细说明：PARAMETERS_zh.md 中的 inverter_topology 一节

### U10_custom_inverter_topology

文件：`primitive_probe/cases/U10_custom_inverter_topology.json` — 自定义起点：反相器结构识别（单指）

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `netlist` | 字符串 | `".SUBCKT simple_inv A Y VDD VNW VPW VSS\nMN Y A VSS VPW nfet_05v0 W…` | CDL 网表文本（输入） |
| `contract.high` | 字符串 | `"VDD"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.low` | 字符串 | `"VSS"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.n_body` | 字符串 | `"VPW"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.p_body` | 字符串 | `"VNW"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.pdk` | 字符串 | `"gf180mcu_5v"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.mode` | 字符串 | `"free_placement_external_taps"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.floorplan` | 字符串 | `"free_single_pair_rotations"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.device_recipe` | 字符串 | `"gf180_5v_learned_geometry_v1"` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.grid_nm` | 整数 | `5` | 布局合约字段（电源/衬底网络名、PDK、模式等） |
| `contract.solver_seconds` | 整数 | `3` | 布局合约字段（电源/衬底网络名、PDK、模式等） |


## 原语 solve_area_core

- 作用：Area placer as the inverter flow calls it: material-bbox area first, then compact core bbox.
- 出图路径：`area_placement.solve_area(core_boxes) -> contacts.materialize(fixture boxes, solver origins)`
- 详细说明：PARAMETERS_zh.md 中的 solve_area_core 一节

### U11_custom_solve_area_core

文件：`primitive_probe/cases/U11_custom_solve_area_core.json` — 自定义起点：面积放置（流程调用方式，带 core_boxes）

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `items.N.rect` | 列表 | `[1000, 1000]` | 有源区矩形 [宽, 高] |
| `items.N.outer` | 列表 | `[-240, -240, 1240, 1240]` | 全部图层外框（相对矩形原点），决定面积 |
| `items.N.core` | 列表 | `[0, 0, 1000, 1000]` | 核心框，决定第二目标 |
| `items.P.rect` | 列表 | `[1000, 1000]` | 有源区矩形 [宽, 高] |
| `items.P.outer` | 列表 | `[-600, -600, 1600, 1600]` | 全部图层外框（相对矩形原点），决定面积 |
| `items.P.core` | 列表 | `[0, 0, 1000, 1000]` | 核心框，决定第二目标 |
| `clearance_nm` | 整数 | `1200` | 矩形两两净距 |
| `use_core_boxes` | 布尔 | `true` | true = 流程模式；false = 对照组 |
| `seconds` | 整数 | `5` | 求解时间上限（秒） |


## 原语 relation_features

- 作用：Measures the learned relation features (gate axes, gate-access inwardness, output alignment) on an inverter layout.
- 出图路径：`placement_relations.features(input layout) (data only; input copied as input_context.gds)`
- 详细说明：PARAMETERS_zh.md 中的 relation_features 一节

### U12_custom_relation_features

文件：`primitive_probe/cases/U12_custom_relation_features.json` — 自定义起点：关系特征测量

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `layout_gds` | 字符串 | `"data/primitive_generalization/primitive_lab/internal_flow_final4/g…` | 被测量的反相器版图路径（输入） |
| `netlist` | 字符串 | `"* Copyright 2022 GlobalFoundries PDK Authors\n*\n* Licensed under …` | CDL 网表文本 |
| `contract.high` | 字符串 | `"VDD"` | 布局合约字段 |
| `contract.low` | 字符串 | `"VSS"` | 布局合约字段 |
| `contract.n_body` | 字符串 | `"VPW"` | 布局合约字段 |
| `contract.p_body` | 字符串 | `"VNW"` | 布局合约字段 |
| `contract.pdk` | 字符串 | `"gf180mcu_5v"` | 布局合约字段 |
| `contract.mode` | 字符串 | `"free_placement_external_taps"` | 布局合约字段 |
| `contract.floorplan` | 字符串 | `"free_single_pair_rotations"` | 布局合约字段 |
| `contract.device_recipe` | 字符串 | `"gf180_5v_learned_geometry_v1"` | 布局合约字段 |
| `contract.grid_nm` | 整数 | `5` | 布局合约字段 |
| `contract.solver_seconds` | 整数 | `3` | 布局合约字段 |


## 原语 relation_rank

- 作用：Selects a candidate by area/Amin + w_rel*mean squared relation error + w_wire*HPWL/HPWLmin.
- 出图路径：`placement_relations.rank (data only)`
- 详细说明：PARAMETERS_zh.md 中的 relation_rank 一节

### U13_custom_relation_rank

文件：`primitive_probe/cases/U13_custom_relation_rank.json` — 自定义起点：关系排序

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `library_file` | 字符串 | `"data/primitive_generalization/relation_training_v1/learned_primiti…` | 冻结关系库（提供特征学习均值） |
| `weights.relations` | 小数 | `0.1` | 关系项权重 |
| `weights.wirelength_proxy` | 小数 | `0.05` | 线长项权重 |
| `candidates` | 列表 | `[{"name": "C0", "passed": true, "area_nm2": 10000000, "features": {…` | 候选列表：name / passed / area_nm2 / features / hpwl_total_nm |


## 原语 candidate_verify

- 作用：Local GF180 rule / topology checker applied to each placement candidate.
- 出图路径：`contact_primitives.verify(input layout) (data only; input copied as input_context.gds)`
- 详细说明：PARAMETERS_zh.md 中的 candidate_verify 一节

### U14_custom_candidate_verify

文件：`primitive_probe/cases/U14_custom_candidate_verify.json` — 自定义起点：候选局部检查器

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `layout_gds` | 字符串 | `"data/primitive_generalization/primitive_lab/internal_flow_final4/g…` | 待检查版图路径（输入） |
| `netlist` | 字符串 | `"* Copyright 2022 GlobalFoundries PDK Authors\n*\n* Licensed under …` | CDL 网表文本 |
| `contract.high` | 字符串 | `"VDD"` | 布局合约字段 |
| `contract.low` | 字符串 | `"VSS"` | 布局合约字段 |
| `contract.n_body` | 字符串 | `"VPW"` | 布局合约字段 |
| `contract.p_body` | 字符串 | `"VNW"` | 布局合约字段 |
| `contract.pdk` | 字符串 | `"gf180mcu_5v"` | 布局合约字段 |
| `contract.mode` | 字符串 | `"free_placement_external_taps"` | 布局合约字段 |
| `contract.floorplan` | 字符串 | `"free_single_pair_rotations"` | 布局合约字段 |
| `contract.device_recipe` | 字符串 | `"gf180_5v_learned_geometry_v1"` | 布局合约字段 |
| `contract.grid_nm` | 整数 | `5` | 布局合约字段 |
| `contract.solver_seconds` | 整数 | `3` | 布局合约字段 |
| `plans.nmos` | 对象 | `{"count": 2, "centers_nm": [515, 985]}` | 该极性期望的接触计划 {count, centers_nm} |
| `plans.pmos` | 对象 | `{"count": 2, "centers_nm": [775, 1245]}` | 该极性期望的接触计划 {count, centers_nm} |


## 原语 build_row

- 作用：Diffusion-chain MOS row constructor (active/poly/contact/M1/implant/well/marker).
- 出图路径：`contacts.build_row -> contacts.materialize -> Layout.write`
- 详细说明：PARAMETERS_zh.md 中的 build_row 一节

### U01_custom_build_row

文件：`primitive_probe/cases/U01_custom_build_row.json` — 自定义起点：单栅 NMOS 行构造器

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `kind` | 字符串 | `"nmos"` | 器件极性，决定 N+/P+、是否生成 N 阱、栅接入朝上(NMOS)/朝下(PMOS) |
| `w_nm` | 整数 | `1000` | 沟道宽 W = 有源区高度（5 的倍数） |
| `l_nm` | 整数 | `600` | 沟道长 L = 每条多晶栅宽度（NMOS ≥600，PMOS ≥500） |
| `chain` | 列表 | `["output", "rail"]` | 扩散段从左到右的网络角色；栅数 = 段数 − 1 |
| `contact_plan.count` | 整数 | `1` | 每个 S/D 段的接触孔行数 |
| `contact_plan.centers_nm` | 列表 | `[500]` | 各行接触孔中心 y 坐标（孔距通常 470；工程不检查越界） |
| `geometry_plan` | null | `null` | null = 默认段长（外段 440、共享段 520）；或给出段长与孔位 |
| `nets.gate` | 字符串 | `"IN"` | 栅网络名（仅文本标签） |
| `nets.output` | 字符串 | `"OUT"` | 输出网络名（仅文本标签） |
| `nets.rail` | 字符串 | `"VSS"` | 电源轨网络名（仅文本标签） |


## 原语 build_contacted_device

- 作用：Single contacted MOS device constructor of the two-band (v1/v2 recipe) flow.
- 出图路径：`device_placement.build_contacted_device -> generate.materialize -> Layout.write`
- 详细说明：PARAMETERS_zh.md 中的 build_contacted_device 一节

### U02_custom_build_contacted_device

文件：`primitive_probe/cases/U02_custom_build_contacted_device.json` — 自定义起点：单器件构造器

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `device.name` | 字符串 | `"M1"` | 器件实例名 |
| `device.model` | 字符串 | `"nfet_05v0"` | 模型：nfet_05v0 / pfet_05v0（决定极性） |
| `device.drain` | 字符串 | `"OUT"` | 漏极网络名 |
| `device.gate` | 字符串 | `"IN"` | 栅极网络名 |
| `device.source` | 字符串 | `"VSS"` | 源极网络名 |
| `device.body` | 字符串 | `"VPW"` | 衬底网络名 |
| `device.w_nm` | 整数 | `1000` | 沟道宽 W（≥360，5 的倍数） |
| `device.l_nm` | 整数 | `600` | 沟道长 L（NMOS ≥600，PMOS ≥500） |
| `contact_rows` | 布尔 | `false` | false = 每端 1 个孔（v1）；true = 按 W 放满多行孔（v2） |


## 原语 contact_plan

- 作用：Learned S/D contact count + position planner; rendered through build_row.
- 出图路径：`contacts.contact_plan -> contacts.build_row -> contacts.materialize`
- 详细说明：PARAMETERS_zh.md 中的 contact_plan 一节

### U03_custom_contact_plan

文件：`primitive_probe/cases/U03_custom_contact_plan.json` — 自定义起点：接触孔规划器

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `library` | 对象 | `{"schema": "clay.contact-primitive.v1", "pdk": "gf180mcu_5v", "patt…` | 写在用例中的接触策略库（clay.contact-primitive.v1） |
| `kind` | 字符串 | `"nmos"` | 使用哪个极性的策略 |
| `w_nm` | 整数 | `1500` | 规划器输入：有源区宽度 W |
| `l_nm` | 整数 | `600` | 仅用于出图：沟道长 L |
| `chain` | 列表 | `["output", "rail"]` | 仅用于出图：扩散链 |
| `nets.gate` | 字符串 | `"IN"` | 仅用于出图：网络名标签 |
| `nets.output` | 字符串 | `"OUT"` | 仅用于出图：网络名标签 |
| `nets.rail` | 字符串 | `"VSS"` | 仅用于出图：网络名标签 |


## 原语 geometry_plan

- 作用：Learned diffusion segment length / contact x planner; rendered through build_row.
- 出图路径：`geometry.geometry_plan (+contact_plan) -> contacts.build_row -> contacts.materialize`
- 详细说明：PARAMETERS_zh.md 中的 geometry_plan 一节

### U04_custom_geometry_plan

文件：`primitive_probe/cases/U04_custom_geometry_plan.json` — 自定义起点：扩散段几何规划器

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `library` | 对象 | `{"schema": "clay.geometry-primitive.v1", "pdk": "gf180mcu_5v", "con…` | 写在用例中的几何策略库（clay.geometry-primitive.v1） |
| `kind` | 字符串 | `"nmos"` | 规划器输入：极性 |
| `chain` | 列表 | `["output", "rail"]` | 规划器输入：扩散链（每段须有对应策略键） |
| `w_nm` | 整数 | `1000` | 决定孔行数并用于出图 |
| `l_nm` | 整数 | `600` | 仅用于出图：沟道长 L |
| `nets.gate` | 字符串 | `"IN"` | 仅用于出图：网络名标签 |
| `nets.output` | 字符串 | `"OUT"` | 仅用于出图：网络名标签 |
| `nets.rail` | 字符串 | `"VSS"` | 仅用于出图：网络名标签 |


## 原语 solve_area_relation

- 作用：Area placer with clearance and soft relations (align:left/right/center, p_above_n).
- 出图路径：`area_placement.solve_area -> contacts.materialize(fixture rects, solver origins)`
- 详细说明：PARAMETERS_zh.md 中的 solve_area_relation 一节

### U05_custom_solve_area_relation

文件：`primitive_probe/cases/U05_custom_solve_area_relation.json` — 自定义起点：面积放置与对齐关系

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `rectangles.A` | 列表 | `[2000, 2000]` | 一个待放矩形：[宽, 高]（5 的倍数） |
| `rectangles.B` | 列表 | `[1000, 500]` | 一个待放矩形：[宽, 高]（5 的倍数） |
| `clearances` | 列表 | `[["A", "B", 300]]` | 每对矩形的最小净距 [名1, 名2, nm]；须覆盖所有对，顺序同 rectangles |
| `preferences` | 列表 | `[["A", "B", "align:left"]]` | 关系偏好 [a, b, 关系]，关系为 align:left / align:right / align:center / p_above_n；可为空 |
| `seconds` | 整数 | `5` | 求解时间上限（秒） |


## 原语 turn

- 作用：Quarter-turn rotation of a row with active-origin normalization.
- 出图路径：`contacts.turn -> contacts.materialize`
- 详细说明：PARAMETERS_zh.md 中的 turn 一节

### U07_custom_turn

文件：`primitive_probe/cases/U07_custom_turn.json` — 自定义起点：行旋转 270°（3 个四分之一圈）

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `row.layers.active` | 列表 | `[[0, 0, 2000, 1000]]` | 输入形状：该图层上的 bbox 列表（必须有 active） |
| `row.layers.poly` | 列表 | `[[0, 0, 400, 1000]]` | 输入形状：该图层上的 bbox 列表（必须有 active） |
| `row.layers.metal1` | 列表 | `[[1700, 700, 1900, 900]]` | 输入形状：该图层上的 bbox 列表（必须有 active） |
| `row.terminals` | 列表 | `[["TOPRIGHT", [1700, 700, 1900, 900]]]` | 端口标签 [网络名, bbox] |
| `quarter_turns` | 整数 | `3` | 逆时针旋转 90° 的次数：0–3 |
| `display_offset_nm` | 列表 | `[-4000, 0]` | 未旋转输入（青色参考图）的显示偏移 [dx, dy] |


## 原语 materialize

- 作用：Row assembly: translation by origins and same-layer marker/implant consolidation.
- 出图路径：`contacts.materialize -> Layout.write`
- 详细说明：PARAMETERS_zh.md 中的 materialize 一节

### U08_custom_materialize

文件：`primitive_probe/cases/U08_custom_materialize.json` — 自定义起点：两行上下组装与 P+ 合并

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `rows.R1.layers.active` | 列表 | `[[0, 0, 1000, 1000]]` | 某行在该图层上的 bbox 列表 |
| `rows.R1.layers.pplus` | 列表 | `[[-230, -230, 1230, 1230]]` | 某行在该图层上的 bbox 列表 |
| `rows.R1.terminals` | 列表 | `[["NET1", [400, 400, 600, 600]]]` | 该行的端口标签 [网络名, bbox] |
| `rows.R2.layers.active` | 列表 | `[[0, 0, 1000, 1000]]` | 某行在该图层上的 bbox 列表 |
| `rows.R2.layers.pplus` | 列表 | `[[-230, -230, 1230, 1230]]` | 某行在该图层上的 bbox 列表 |
| `rows.R2.terminals` | 列表 | `[["NET2", [400, 400, 600, 600]]]` | 该行的端口标签 [网络名, bbox] |
| `origins.R1` | 列表 | `[0, 0]` | 该行的平移量 [x, y]（每行都必须给出） |
| `origins.R2` | 列表 | `[0, 3000]` | 该行的平移量 [x, y]（每行都必须给出） |


## 原语 orthogonal_path

- 作用：Single-layer obstacle-aware orthogonal path search + wire segment constructor.
- 出图路径：`routing.orthogonal_path -> routing.segment -> contacts.materialize (metal1)`
- 详细说明：PARAMETERS_zh.md 中的 orthogonal_path 一节

### U09_custom_orthogonal_path

文件：`primitive_probe/cases/U09_custom_orthogonal_path.json` — 自定义起点：无障碍正交寻路

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `start` | 列表 | `[0, 0]` | 起点 [x, y] |
| `goal` | 列表 | `[3000, 2000]` | 终点 [x, y] |
| `obstacles` | 列表 | `[]` | 禁区矩形 bbox 列表（可为空） |
| `width_nm` | 整数 | `230` | 金属线宽（建议 10 的倍数，≥230） |
| `axis_fraction` | 小数 | `0.5` | 0–1，越大越偏好沿 vertical 指定方向走线 |
| `vertical` | 布尔 | `true` | 栅方向是否竖直（true/false） |


## 原语 symmetry_placement

- 作用：Mirror-pair / on-axis symmetry constraint of the MILP placer.
- 出图路径：`annotate_to_solver.solve_placement_from_constraints -> returned extents/axis copied to GDS`
- 详细说明：PARAMETERS_zh.md 中的 symmetry_placement 一节

### U06_custom_symmetry_control_no_pair

文件：`primitive_probe/cases/U06_custom_symmetry_control_no_pair.json` — 自定义起点：对称放置的对照组（不声明对称对）

| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |
|---|---|---|---|
| `devices.A.w` | 整数 | `1000` | 矩形宽 |
| `devices.A.h` | 整数 | `600` | 矩形高 |
| `devices.A.kind` | 字符串 | `"nmos"` | nmos / pmos（决定分行：NMOS 在下、PMOS 在上） |
| `devices.B.w` | 整数 | `1000` | 矩形宽 |
| `devices.B.h` | 整数 | `600` | 矩形高 |
| `devices.B.kind` | 字符串 | `"nmos"` | nmos / pmos（决定分行：NMOS 在下、PMOS 在上） |
| `symmetry_pairs` | 列表 | `[]` | 镜像对 [[左, 右], ...]；空列表 = 不声明对称（对照组） |
| `symmetry_axis` | 字符串 | `"x"` | 对称轴方向，"x" = 竖直镜像线（唯一验证过的取值） |
| `time_limit_s` | 整数 | `10` | 求解时间上限（秒） |

