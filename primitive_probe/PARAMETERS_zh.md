# 各原语探针的参数说明

本文说明 `primitive_probe` 中每个原语的参数是什么意思、可以填哪些值。参数可以写在 `cases/*.json` 的 `params` 里，也可以用命令行修改：

```powershell
python -m primitive_probe show <用例id>                               # 查看参数及当前值
python -m primitive_probe try <用例id> --set 路径=值 --expect "预期" --open
```

“工程检查”一栏写的是**工程代码自己会拒绝的情况**（依据源码）。如果填了非法值，报告会显示 `production raised`，错误信息记录在 `traceback.txt` 中。这本身也是一个可以观察的验证结果。“未检查”的情况，工程不会报错，但输出可能没有物理意义。

每个测试文件逐参数的“路径 / 类型 / 当前值 / 作用”对照见 [CASE_CATALOG_zh.md](CASE_CATALOG_zh.md)。

## 0. 通用约定

| 项目 | 约定 |
|---|---|
| 单位 | 全部为 nm（整数）；GDS 中 1 DBU = 1 nm |
| 网格 | 尺寸和坐标应为 5 的倍数（GF180 制造网格）。部分原语对不在网格上的值直接报错 |
| 矩形 `bbox` | `[x0, y0, x1, y1]`，即左下角和右上角，须满足 x1 > x0、y1 > y0 |
| 器件类型 `kind` | `"nmos"` 或 `"pmos"` |
| 图层名 | `active`(22/0)、`poly`(30/0)、`contact`(33/0)、`metal1`(34/0)、`nwell`(21/0)、`nplus`(32/0)、`pplus`(31/0)、`dualgate`(55/0)、`v5_xtor`(112/1)；不能使用其它名称 |
| `--set` 路径 | 用 `.` 分隔字典键，用数字表示列表下标。例如 `devices.A.w=1500`、`clearances.0.2=600`、`contact_plan.centers_nm.0=700` |
| `--set` 的值 | 按 JSON 解析：`1500`、`true`、`null`、`[680,1150]`；无法解析时当作字符串，如 `kind=pmos` |

## 1. build_row — 扩散链器件行构造器（构造类）

被测代码为 `clay/learning/contact_primitives.py: build_row`，出图经 `materialize`。它把同一极性、并联的 1～n 个 MOS 画成一条共享扩散的长条：有源区、多晶栅、S/D 接触孔、M1 着陆、注入层、N 阱和 5V 标记层。

| 参数 | 含义 | 可填值 | 工程检查 |
|---|---|---|---|
| `kind` | 器件极性 | `"nmos"` / `"pmos"` | PMOS 会额外生成 N 阱；NMOS 栅接入朝上，PMOS 朝下 |
| `w_nm` | 沟道宽度 W，即有源区高度 | 5 的倍数 | 不在 5 nm 网格上则报错。最小值未检查，但必须放得下 `contact_plan` 中的孔 |
| `l_nm` | 沟道长度 L，即每条多晶栅的宽度 | 5 的倍数；NMOS ≥600，PMOS ≥500 | 低于 5V 最小沟道长度或不在网格上则报错 |
| `chain` | 扩散段从左到右的网络角色序列；栅数 = 长度 − 1 | 例如 `["output","rail"]`（单栅）、`["rail","output","rail"]`（双栅） | 段数须与 `geometry_plan` 一致。相邻段相同（如 `["rail","rail"]`）不会被检查 |
| `contact_plan.count` | 每个 S/D 段的接触孔行数 | 正整数 | 由下面的 `centers_nm` 实际决定画几个 |
| `contact_plan.centers_nm` | 各行接触孔中心的 y 坐标 | 列表，长度 = count；合法孔距为 470（220 + 250） | **不检查**是否超出有源区或间距是否合法，需自行判断 |
| `geometry_plan` | 各扩散段长度和段内孔位置 | `null` 表示使用默认段长（外段 440、共享段 520，孔居中或贴边）；或填一个字典，见下两行 | 填字典时会严格检查 |
| `geometry_plan.segment_lengths_nm` | 每段扩散的长度（x 方向） | 列表，长度 = 段数；外段 ≥440，共享段 ≥520，5 的倍数 | 不满足则报错 |
| `geometry_plan.contact_x_in_segment_nm` | 每段孔中心距本段左边的距离 | 列表；须落在合法区间：距外边 ≥180、距栅 ≥260 | 越界则报错 |
| `nets.gate / output / rail` | 栅、输出、电源轨的网络名，只用于 34/10 文本标签 | 任意字符串，如 `"IN"`、`"OUT"`、`"VSS"` | 无 |

## 2. build_contacted_device — 单个带接触的 MOS 器件构造器（构造类）

被测代码为 `clay/pdk/device_placement.py: build_contacted_device`，出图经 `clay/learning/generate.py: materialize`。用于两带布局流程，每次只画一个器件。

| 参数 | 含义 | 可填值 | 工程检查 |
|---|---|---|---|
| `device.name` | 器件实例名 | 字符串 | 无 |
| `device.model` | 器件模型，决定极性 | `"nfet_05v0"` / `"pfet_05v0"` | 其它值报错 |
| `device.drain / gate / source / body` | 四个端口的网络名（标签用） | 字符串 | 无 |
| `device.w_nm` | 沟道宽 W | 5 的倍数，≥360（220 + 2×70，放得下一个孔） | 过窄或不在网格上则报错 |
| `device.l_nm` | 沟道长 L | NMOS ≥600、PMOS ≥500，5 的倍数 | 不满足则报错 |
| `device.m`, `device.nf` | 并联倍数、指数 | 只能为 1（可省略） | 非 1 则报错 |
| `contact_rows` | S/D 接触孔方案 | `false`：每端 1 个孔（v1）；`true`：按 W 放尽可能多的孔（v2） | 无 |

## 3. contact_plan — 接触孔规划器（规划类，不直接出图）

被测代码为 `contact_primitives.py: contact_plan`。它根据**学到的策略**和宽度 W，决定每个 S/D 段放几行孔、孔放在什么高度。为了能在 GDS 中看到结果，规划输出会接着交给 `build_row` 出图。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `kind` | 使用哪种极性的策略 | `"nmos"` / `"pmos"` |
| `w_nm` | **规划器的输入**：有源区宽度 W | 5 的倍数 |
| `library` | 写在用例里的策略库（`clay.contact-primitive.v1` 格式），见下表 | 与 `library_file` 二选一 |
| `library_file` | 改为读取工程冻结的策略库文件（相对工程根目录） | 例如 `data/primitive_generalization/relation_training_v1/learned_primitive.json` |
| `library_key` | 在库文件中取哪一层 | 默认 `["geometry","contacts"]` |
| `l_nm`, `chain`, `nets` | 仅用于出图，含义同 build_row | 同 build_row |

`library.contact_policies.<kind>` 中可以修改的策略：

| 字段 | 含义 | 可填值 |
|---|---|---|
| `rows.kind` | 行数预测方式 | `"constant"` 或 `"width_stump"` |
| `rows.count` | constant 方式下的固定行数 | 正整数 |
| `rows.threshold_nm`, `rows.low`, `rows.high` | width_stump 方式：W ≤ 阈值时取 low 行，否则取 high 行 | 阈值 > 0；low、high 为正整数 |
| `first_contact_free_margin_fraction` | 首孔在合法区间中的位置比例：0 为最下，1 为最上 | 0～1 |

计算方式（供写预期使用）：合法区间为 `[180, W − 180 − (n−1)×470]`，首孔 = 区间下限 + 比例 × 区间长度，按 5 nm 取整，之后每行加 470。
工程检查：库的格式不对、字段缺失或多余，以及 **n 行孔放不下**（区间上限小于下限）时报错，见用例 P04。
`library` 必须包含 nmos 和 pmos 两套 `patterns` 与 `contact_policies`，即使只测其中一种。

## 4. geometry_plan — 扩散段几何规划器（规划类）

被测代码为 `geometry_primitives.py: geometry_plan`。它根据学到的策略，决定每个扩散段比 PDK 最小值长多少，以及孔在段内的 x 位置。同时调用 `contact_plan` 决定孔的行数，最后经 `build_row` 出图。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `kind`, `chain` | **规划器的输入**：极性和扩散链 | 同 build_row |
| `library` / `library_file` / `library_key` | 策略库（`clay.geometry-primitive.v1` 格式）；`library_key` 默认 `["geometry"]` | 同上 |
| `w_nm`, `l_nm`, `nets` | 用于孔行数规划和出图 | 同 build_row |

`library.geometry_policies` 的键形如 `"<kind>:<outer|shared>:<rail|output>"`，例如 `"pmos:shared:output"`。`chain` 中每一段都必须有对应的键，否则报错。outer 指链两端的段，shared 指中间段。

| 字段 | 含义 | 可填值 |
|---|---|---|
| `excess_contact_units` | 段长超过 PDK 下限的量，以孔宽 220 为单位（如 0.5 表示多 110 nm） | ≥0 |
| `contact_fraction` | 孔在合法区间中的位置比例；`null` 表示训练中无法辨识 | 0～1 或 `null`。为 `null` 且区间有余量时报错 |
| `island_count` | 训练证据数 | 正整数 |
| `position_island_count` | 有位置证据的数目；须与 `contact_fraction` 是否为 null 对应（null ⇔ 0） | 0～island_count |

## 5. solve_area_relation — 面积放置器与放置关系（关系类）

被测代码为 `clay/layout/synth/area_placement.py: solve_area`。输入几个矩形（作为输入夹具），放置器先求出**面积最小**的摆放，再在面积不变的前提下尽量满足关系偏好。出图经 `materialize`，矩形画在 22/0 层，名字标在 34/10。

| 参数 | 含义 | 可填值 | 工程检查 |
|---|---|---|---|
| `rectangles` | 待放置的矩形：名字 → [宽, 高] | 5 的倍数 | 不在网格上则报错 |
| `clearances` | 两两之间的最小净距：`[名1, 名2, nm]` | **每一对矩形都必须给出**，且名字顺序须与 `rectangles` 中的先后顺序一致 | 缺失或顺序颠倒会报 KeyError |
| `preferences` | 关系偏好列表：`[名a, 名b, 关系]` | 见下表；可为空列表 `[]` | 未知关系报错 |
| `bands` | 可选，分带：名字 → 带标签 | 恰好 2 种标签；同标签的矩形 y 相同，两带上下分开 | 标签不是 2 种则报错 |
| `seconds` | 求解时间上限（秒） | 正数，默认 5 | 超时则给出可行解（FEASIBLE） |

| 关系 | 含义 |
|---|---|
| `align:left` | a、b 左边缘 x 相等 |
| `align:right` | a、b 右边缘 x 相等 |
| `align:center` | a、b 水平中心相等 |
| `p_above_n` | b 在 a 的上方（a 视为 NMOS，b 视为 PMOS） |

注意：关系是**软约束**，只在面积最优的解中挑选。如果满足关系会增大面积，工程会放弃关系，此时 `preference_cost` > 0。建议同时运行无偏好的对照组（R00）进行比较。

## 6. symmetry_placement — 镜像对称放置（关系类）

被测代码为 `clay/layout/annotate_to_solver.py: solve_placement_from_constraints`（MILP 放置器）。它返回的是坐标而不是版图：GDS 中矩形画在 22/0 层，名字和 mirror 标志写在 203/0，橙色竖线（202/0）是工程返回的对称轴 `axis_x`。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `devices` | 待放置的矩形器件：名字 → `{"w","h","kind"}` | w、h 为 5 的倍数；kind 为 `"nmos"`/`"pmos"`（决定分行：NMOS 行在下，PMOS 行在上） |
| `symmetry_pairs` | 镜像对列表：`[左, 右]` | 名字须在 devices 中；前一个放在轴左侧 |
| `on_axis_devices` | 必须以对称轴平分的器件 | 名字列表，可为空 |
| `symmetry_axis` | 对称轴方向 | `"x"`，即竖直的镜像线。这是唯一验证过的取值 |
| `time_limit_s` | 求解时间上限（秒） | 正数，默认 10 |

固有行为（不能通过参数关闭）：GF180 器件间最小间距 1400 nm；以面积最小为目标；按器件类型分行；5 nm 网格。坐标可能整体为负值，这是放置器的坐标系，判断对称时只看相对距离。

## 7. turn — 行旋转（变换类）

被测代码为 `contact_primitives.py: turn`。它把一个“行”整体逆时针旋转 n×90°，再平移，使有源区的左下角回到 (0,0)。青色图形（200/0）是未旋转的输入，平移到一旁供对照。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `row.layers` | 输入形状：图层名 → bbox 列表 | 图层名见第 0 节；**必须包含 `active`** |
| `row.terminals` | 端口标签：`[网络名, bbox]` 列表 | 可省略 |
| `quarter_turns` | 逆时针旋转的 90° 次数 | 0、1、2、3 |
| `display_offset_nm` | 未旋转输入的显示位置偏移 `[dx, dy]` | 仅影响青色参考图，默认 `[-5000, 0]` |

建议使用不对称的形状（如左侧一条多晶、右上角一个小方块），这样才能看出旋转方向。

## 8. materialize — 组装（组装类）

被测代码为 `contact_primitives.py: materialize`。它把多个“行”按原点平移后放进同一版图。注入、阱和标记层（nwell、nplus、pplus、dualgate、v5_xtor）会**合并成一个外接矩形**，其它层只做平移；同时生成 0/0 边界框和 34/10 文本。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `rows` | 名字 → `{"layers": {...}, "terminals": [...]}` | 格式同 turn 的 row；此处 active 可省略 |
| `origins` | 名字 → `[x, y]`，即每个行的平移量 | 每个 row 都必须给出 |

## 9. orthogonal_path — 单层正交寻路（布线类）

被测代码为 `clay/learning/routing_primitives.py: orthogonal_path`。它在障碍物之间找一条只走水平和竖直线段的路径，再由 `segment` 把路径变成指定宽度的金属线（34/0）。

| 参数 | 含义 | 可填值 | 工程检查 |
|---|---|---|---|
| `start`, `goal` | 起点、终点坐标 `[x, y]` | 不应位于障碍内 | 无路可走时报错 |
| `obstacles` | 禁区矩形列表（视为已包含间距余量的 keep-out） | bbox 列表 | 无 |
| `width_nm` | 金属线宽 | 建议取 10 的倍数，使线边落在 5 nm 网格上；M1 最小 230 | 无 |
| `axis_fraction` | 走向偏好：越接近 1，越偏好沿 `vertical` 指定方向走线 | 0～1 | 无 |
| `vertical` | 栅极方向是否竖直，与 `axis_fraction` 配合确定“沿栅方向” | `true` / `false` | 无 |

工程限制：搜索网格超过 100 000 个节点时报错（障碍过多或坐标跨度过大时可能出现）。

## 10. 用例文件中参数以外的字段

| 字段 | 含义 |
|---|---|
| `id` | 用例名，须唯一，建议使用“字母前缀 + 编号 + 描述” |
| `adapter` | 原语名，即 `list` 中列出的名字 |
| `title`, `purpose` | 标题和目的，会显示在 REVIEW.md 中 |
| `expect_rejection` | `true` 表示预期工程会拒绝该输入（负对照） |
| `expected.statements` | **必填**，人工预期的逐条描述，KLayout 核对时按 E1、E2… 逐条打勾 |
| `expected.shapes` | 可选，`[{"bbox_nm": [...], "label": "..."}]`，在 201 层画成品红虚框，供与工程图形比对 |
| `expected.derivation` | 预期依据，例如引用的 PDK 规则编号或计算过程 |

> 第 11–15 节原语的作用说明、在流程中的位置，以及 U 测试的预期输出，见 [U_TESTS_GUIDE_zh.md](U_TESTS_GUIDE_zh.md)。

## 11. inverter_topology — 反相器结构识别（决策类，无图形）

被测代码：`clay/learning/circuit.py: parse_cdl` → `contact_primitives.py: inverter_groups`。对应完整流程中的 `bind_inverter_topology` 步骤。

| 参数 | 含义 | 可填值 | 工程检查 |
|---|---|---|---|
| `netlist` | CDL 网表文本（一个 .SUBCKT） | 器件模型 nfet_05v0/pfet_05v0；W/L 写成 `W=1.32U` 形式且落在 5 nm 网格 | 尺寸格式或网格不对报错 |
| `contract.high/low` | 电源/地网络名 | 必须是网表端口 | 缺失报错 |
| `contract.n_body/p_body` | NMOS/PMOS 衬底网络名 | 必须是网表端口，且与器件 body 一致 | 不一致报错 |
| `contract.floorplan` | 必须为 `"free_single_pair_rotations"` | — | 否则报错 |
| `contract.grid_nm` / `solver_seconds` | 5 / 0～120 | — | 否则报错 |
| 其余 `contract.*` | pdk=`gf180mcu_5v`，mode=`free_placement_external_taps`，device_recipe | — | — |

输出：NMOS/PMOS 分组、输入网络、输出网络。非共栅反相器（如两输入门）会被拒绝（用例 I02）。

## 12. solve_area_core — 面积放置（流程实际调用方式，带 core_boxes）

被测代码：`area_placement.py: solve_area(..., core_boxes=...)`，出图经 `materialize`。完整流程对 16 个朝向候选各调用一次：先使**外框总包围面积**最小，面积相同时再使**核心框总包围面积**最小。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `items.<名>.rect` | 有源区矩形 [宽, 高]；净距约束作用在它上面 | 5 的倍数 |
| `items.<名>.outer` | 该器件全部图层的外框（相对矩形原点），决定面积 | bbox，须包含 rect |
| `items.<名>.core` | 核心框（有源区/多晶/孔/M1 的外框），决定第二目标 | bbox |
| `clearance_nm` | 每对矩形之间的净距（流程中为 600 + 600 = 1200） | 5 的倍数 |
| `use_core_boxes` | `true` = 流程模式；`false` = 对照组（第二目标改为坐标和最小） | 布尔 |
| `seconds` | 求解时间上限 | 正数 |

图中：22/0 = rect，30/0 = core，34/0 = outer（这里图层只作为载体，没有工艺含义）。

## 13. relation_features — 关系特征测量（决策类）

被测代码：`placement_relations.py: features`。对应完整流程 `score_candidates` 中的“测量”部分，图标题 “Learned orientation and port relations” 指的就是这些特征。

| 参数 | 含义 |
|---|---|
| `layout_gds` | 要测量的反相器版图（工程根目录下的相对路径），作为**输入**；运行时会复制为 `input_context.gds` |
| `netlist`, `contract` | 同 inverter_topology |

输出 5 个特征（取值 0～1）：`parallel_gate_axes` 两排栅方向是否相同；`separation_along_gates` 两排是否沿栅方向分开；`n_gate_access_inward` / `p_gate_access_inward` 栅接触是否朝向另一排；`output_transverse_misalignment` 两排输出接触的横向错位（相对器件宽度）。另外输出栅和输出网络的 HPWL。

## 14. relation_rank — 关系排序（决策类）

被测代码：`placement_relations.py: rank`。对应 `score_candidates` 中的“选择”部分：得分 = 面积/Amin + w_rel × 特征与学习均值之差平方的平均 + w_wire × HPWL/HPWLmin，取得分最小的合法候选。

| 参数 | 含义 | 可填值 |
|---|---|---|
| `library_file` | 冻结的关系库（提供 5 个特征的学习均值） | 例如 relation_training_v1/learned_primitive.json |
| `weights.relations` / `weights.wirelength_proxy` | 两项权重（工程默认 0.10 / 0.05） | 非负数 |
| `candidates[i].name` | 候选名 | 字符串 |
| `candidates[i].passed` | 是否通过局部检查；未通过的不参与排序，也不参与 Amin | 布尔 |
| `candidates[i].area_nm2` | 候选面积 | 正整数 |
| `candidates[i].features` | 5 个特征值，键名必须齐全 | 每项 0～1 |
| `candidates[i].hpwl_total_nm` | 线长代理 | 正整数 |

没有任何合法候选时报错（用例 K03）。

## 15. candidate_verify — 候选局部检查器（决策类）

被测代码：`contact_primitives.py: verify`。对应完整流程中 16 次 `verify_candidate`。

| 参数 | 含义 |
|---|---|
| `layout_gds` | 待检查的版图（输入） |
| `netlist`, `contract` | 同 inverter_topology |
| `plans.nmos / plans.pmos` | 期望的接触计划 `{"count", "centers_nm"}`；与版图不符时对应检查项失败（用例 V02） |

输出：`passed`、失败项列表 `failed`、全部检查项 `checks`（GF180 局部规则、W/L 与网络、孔数等）。

## 16. 无法单独测试的步骤

`select_diffusion_chain`（扩散链选择）写在 `contact_primitives._generate` 内部，只有一行 `supported[0]`，没有独立函数可调用。按“只调用、不复制”的原则，**不为它编写探针**；它的效果通过 B02、H03、H04 中链的结构，以及完整流程记录中的 `select_diffusion_chain` 事件间接观察。
