# 附件复现布局所用原语的 U 测试指南

> 对象：亲自运行并人工核对 U 测试的验证人员。
> 范围：附件复现布局（`internal_flow_final4`，inv_2）流程中用到的 10 个原语，各对应一个可自定义的 U 测试。
> 参数取值范围和工程检查的完整列表见 [PARAMETERS_zh.md](PARAMETERS_zh.md)；每个参数的路径和类型对照见 [CASE_CATALOG_zh.md](CASE_CATALOG_zh.md)。

运行方式（把文件名换成下文任一 U 测试）：

```powershell
cd clay   # 仓库根目录
python -m primitive_probe run U01_custom_build_row --open
```

## 第一部分：附件流程中原语的先后关系

完整流程把网表变成布局，依次调用以下原语（括号内为 U 测试）：

```
网表 ──► ① 反相器结构识别 inverter_groups (U10)
          │  得到：NMOS 组、PMOS 组、输入网络、输出网络
          ▼
       ② 接触孔规划 contact_plan (U03)       ┐ 每种极性各一次
       ③ 扩散段几何规划 geometry_plan (U04)  │
       ④ 行构造 build_row (U01)              ┘ 画出 N 行、P 行
          ▼
       对 16 种朝向组合（N、P 各转 0/90/180/270°）各做一次：
       ⑤ 旋转 turn (U07)
       ⑥ 面积放置 solve_area，core_boxes 模式 (U11)
       ⑦ 组装 materialize (U08) ──► 一个候选版图
       ⑧ 局部检查 verify (U14)
          ▼
       ⑨ 关系特征测量 features (U12)  ┐ 对通过检查的候选打分，
       ⑩ 关系排序 rank (U13)          ┘ 选出最终布局（附件为 n2_p2）
```

①、⑧、⑨、⑩ 是**决策/检查类**原语，输出是数据而不是版图。它们的结果在 `REVIEW.md` 的“工程原语返回的决策/坐标”一节中查看。

## 第二部分：5 个新增原语的含义与输入参数

### 2.1 inverter_groups —— 反相器结构识别（U10）

**是什么**：流程的第一步。读入网表后，判断它是不是一个反相器，并找出各器件和网络扮演的角色。后续步骤都依赖这一结果，例如哪些管子画在 N 行、哪些网络接到栅和输出。
**被测代码**：`clay/learning/circuit.py: parse_cdl`（解析网表），以及 `clay/learning/contact_primitives.py: inverter_groups`（识别角色）。
**判定规则**（来自源码中的检查）：所有管子的栅接同一个网络；每个 NMOS 一端接地 `low`、每个 PMOS 一端接电源 `high`；另一端都接同一个输出网络；同极性的管子尺寸相同。不满足时直接报错，不会猜测。

| 参数 | 类型 | 含义 |
|---|---|---|
| `netlist` | 字符串（多行） | CDL 网表全文。每行一个 MOS：`名 漏 栅 源 衬底 模型 W=…U L=…U`；模型只能是 `nfet_05v0` / `pfet_05v0` |
| `contract.high` / `contract.low` | 字符串 | 电源 / 地网络名，必须出现在 `.SUBCKT` 端口列表中 |
| `contract.n_body` / `contract.p_body` | 字符串 | NMOS / PMOS 衬底网络名，必须与器件的衬底端一致 |
| `contract.floorplan` | 字符串 | 固定为 `free_single_pair_rotations` |
| `contract.pdk`、`mode`、`device_recipe`、`grid_nm`、`solver_seconds` | 字符串/整数 | 流程合约，保持默认即可 |

**输出**：`groups.nmos` / `groups.pmos` 是两组器件名，`gate_net` 是输入网络，`output_net` 是输出网络。

### 2.2 solve_area（core_boxes 模式）—— 面积放置（U11）

**是什么**：每种朝向组合下，决定 N 行和 P 行放在哪里。它有两级目标：
1. **第一目标**：所有图层外框的总包围面积最小，即版图面积最小；
2. **第二目标**：在面积不变的前提下，器件核心区（有源区、多晶、孔、M1）的总包围最小，让器件尽量紧凑、对齐。

约束是两个器件的有源区之间至少相距 `clearance`。流程中取 600 + 600 = 1200 nm，来自 N 阱包围 P 有源区和 N 阱到 N 有源区的规则。
**被测代码**：`clay/layout/synth/area_placement.py: solve_area(..., core_boxes=...)`，出图经 `materialize`。
**与 U05 的区别**：U05 测的是同一函数的“关系偏好”模式（对齐等），那是另一条代码分支，附件流程没有使用。

| 参数 | 类型 | 含义 |
|---|---|---|
| `items.<名>.rect` | [整数, 整数] | 有源区矩形 [宽, 高]；净距约束作用在它上面 |
| `items.<名>.outer` | [x0,y0,x1,y1] | 该器件全部图层的外框（相对矩形左下角），决定版图面积 |
| `items.<名>.core` | [x0,y0,x1,y1] | 核心框（相对矩形左下角），决定第二目标 |
| `clearance_nm` | 整数 | 有源区两两之间的最小净距 |
| `use_core_boxes` | 布尔 | `true`：流程实际模式；`false`：对照，第二目标改为“坐标和最小” |
| `seconds` | 数字 | 求解时间上限（秒） |

**输出**：`origins_nm` 是每个矩形左下角的放置坐标，`area_nm2` 是外框总包围面积。
**图中图层**（此处图层只作为载体，没有工艺含义）：22/0 为 rect，30/0 为 core，34/0 为 outer。

### 2.3 features —— 关系特征测量（U12）

**是什么**：附件标题 “Learned orientation and port relations” 中的 “relations” 就由它测量。它读入一个候选版图，计算 5 个描述 N 行与 P 行相对关系的数值（0～1），以及线长代理（HPWL）。训练阶段，它从参考版图中测得这些值的均值并“学到”；生成阶段，它对每个候选再测一次，交给 rank 比较。
**被测代码**：`clay/learning/placement_relations.py: features`。

| 输出特征 | 含义 | 理想（学到的）值 |
|---|---|---|
| `parallel_gate_axes` | 两排栅方向是否相同（1 = 平行） | 1 |
| `separation_along_gates` | 两排沿栅方向分开的程度（1 = 完全沿栅方向上下排列） | ≈1 |
| `n_gate_access_inward` | NMOS 栅接触是否朝向 PMOS 一侧（1 = 朝内） | 1 |
| `p_gate_access_inward` | PMOS 栅接触是否朝向 NMOS 一侧 | 1 |
| `output_transverse_misalignment` | 两排输出接触的横向错位，相对器件宽度（0 = 对齐） | 0 |
| `hpwl_nm` / `hpwl_total_nm` | 输入、输出网络接触点的半周长线长 | 越小越好 |

| 参数 | 类型 | 含义 |
|---|---|---|
| `layout_gds` | 字符串 | 被测量的反相器版图（工程根目录下的相对路径）。这是**输入**，运行时会复制为 `input_context.gds` |
| `netlist` | 字符串 | 该版图对应的网表，用来识别输入、输出网络 |
| `contract` | 对象 | 同 2.1 |

### 2.4 rank —— 关系排序（U13）

**是什么**：从通过检查的候选中选出最终布局。对每个候选打分，得分最低者胜出：

```
score = 面积 / Amin  +  w_rel × mean((特征 − 学到的均值)²)  +  w_wire × HPWL / HPWLmin
```

Amin、HPWLmin 只在**通过检查的**候选中取。第一项惩罚面积，第二项惩罚“不像训练样本”，第三项惩罚线长。
**被测代码**：`clay/learning/placement_relations.py: rank`。

| 参数 | 类型 | 含义 |
|---|---|---|
| `library_file` | 字符串 | 训练冻结的关系库，提供 5 个特征的学习均值 |
| `weights.relations` | 小数 | 关系项权重 w_rel（流程默认 0.10） |
| `weights.wirelength_proxy` | 小数 | 线长项权重 w_wire（流程默认 0.05） |
| `candidates[i].name` | 字符串 | 候选名 |
| `candidates[i].passed` | 布尔 | 是否通过 verify；`false` 的不参与排序 |
| `candidates[i].area_nm2` | 整数 | 候选面积 |
| `candidates[i].features` | 对象 | 5 个特征值，键名必须齐全，每项 0～1 |
| `candidates[i].hpwl_total_nm` | 整数 | 线长代理 |

**输出**：`selected_index` / `selected_name` 是胜出的候选；`ranking` 中给出每个候选的 `score`、`relation_loss`（特征偏差平方的平均）、`area_ratio` 和 `wirelength_ratio`。没有合法候选时报错。

### 2.5 verify —— 候选局部检查器（U14）

**是什么**：16 个候选组装完成后，逐个检查是否满足 GF180 局部版图规则和电路一致性。只有通过的候选才能进入排序。
**被测代码**：`clay/learning/contact_primitives.py: verify`。
**检查内容**（输出 `checks` 中的各项）：
- 制造网格；
- 孔尺寸和孔间距；
- 有源区、多晶、M1 的宽度和间距；
- 孔被有源区、多晶、M1 包围；
- 孔到栅的距离；
- N 阱包围 PMOS；
- N+/P+ 包围与间距；
- 5V 标记层包围；
- 栅端帽；
- 从版图提取的 W/L 和网络与网表一致；
- 每段孔数与期望计划一致。

| 参数 | 类型 | 含义 |
|---|---|---|
| `layout_gds` | 字符串 | 待检查的版图（输入） |
| `netlist`, `contract` | 字符串, 对象 | 同 2.1 |
| `plans.nmos` / `plans.pmos` | `{"count", "centers_nm"}` | 期望的接触计划，即 contact_plan 的输出。与版图不一致时，对应的孔数检查失败 |

**输出**：`passed`（是否全部通过）、`failed`（失败项名称列表）、`checks`（每一项的 true/false）。

## 第三部分：10 个 U 测试的输入与预期输出（默认参数）

下文的预期都是在运行前根据 PDK 规则和策略参数推导的。打开 `review.gds` 后，请加载运行目录下的 `layers.lyp`（File > Load Layer Properties），并按 `F2` 适应窗口。品红虚线框（201 层）是人工预期框，工程图形应与之重合。

### U01_custom_build_row —— 行构造（单栅 NMOS，W=1000，L=600）

**输入**（取自 `primitive_probe/cases/U01_custom_build_row.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `kind` | `"nmos"` | 器件极性，决定 N+/P+、是否生成 N 阱、栅接入朝上(NMOS)/朝下(PMOS) |
| `w_nm` | `1000` | 沟道宽 W = 有源区高度（5 的倍数） |
| `l_nm` | `600` | 沟道长 L = 每条多晶栅宽度（NMOS ≥600，PMOS ≥500） |
| `chain` | `["output", "rail"]` | 扩散段从左到右的网络角色；栅数 = 段数 − 1 |
| `contact_plan.count` | `1` | 每个 S/D 段的接触孔行数 |
| `contact_plan.centers_nm` | `[500]` | 各行接触孔中心 y 坐标（孔距通常 470；工程不检查越界） |
| `geometry_plan` | `null` | null = 默认段长（外段 440、共享段 520）；或给出段长与孔位 |
| `nets.gate` | `"IN"` | 栅网络名（仅文本标签） |
| `nets.output` | `"OUT"` | 输出网络名（仅文本标签） |
| `nets.rail` | `"VSS"` | 电源轨网络名（仅文本标签） |

**看到的图形**：一个完整的单栅 NMOS 器件。

| 图层 | 预期 |
|---|---|
| 22/0 有源区 | 一个矩形，(0,0)–(1480,1000)。宽 1480 = 左段 440 + 栅长 600 + 右段 440；段长 440 = max(DF.6 的 400, 70+220+150) |
| 30/0 多晶 | 一条竖直栅，宽 600，x 440–1040，上下都穿出有源区；下端至少伸出 220（PL.4），上端继续延伸，承接栅极接触孔 |
| 33/0 接触孔 | 3 个 220×220 的方孔：左 S/D 孔中心 (180,500)，右 S/D 孔中心 (1300,500)，均距有源区边 70、距栅 150；第 3 个是栅孔，在有源区上方的多晶上，距有源区上沿至少 170 |
| 34/0 M1 | 3 块着陆金属，分别盖住 3 个孔，四周至少余 60 |
| 32/0 N+ | 一个矩形，比有源区每边大 230：(-230,-230)–(1710,1230) |
| 21/0 N 阱 | **无**（NMOS 没有 N 阱） |
| 55/0、112/1 | 5V 标记框，包住有源区（≥240）和多晶（≥400） |
| 34/10 文本 | 左段 OUT、右段 VSS、栅 IN（链为 output→rail，自左向右） |

### U03_custom_contact_plan —— 接触孔规划（NMOS，W=1500）

**输入**（取自 `primitive_probe/cases/U03_custom_contact_plan.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `library` | 见下方策略表 | 写在用例中的接触策略库（clay.contact-primitive.v1） |
| `kind` | `"nmos"` | 使用哪个极性的策略 |
| `w_nm` | `1500` | 规划器输入：有源区宽度 W |
| `l_nm` | `600` | 仅用于出图：沟道长 L |
| `chain` | `["output", "rail"]` | 仅用于出图：扩散链 |
| `nets.gate` | `"IN"` | 仅用于出图：网络名标签 |
| `nets.output` | `"OUT"` | 仅用于出图：网络名标签 |
| `nets.rail` | `"VSS"` | 仅用于出图：网络名标签 |

策略库中实际起作用的内容：

| 策略项 | 值 | 含义 |
|---|---|---|
| `nmos` 孔行数 | W ≤ 1070 取 1 行，否则 2 行 | 学到的宽度阈值规则 |
| `nmos` 首孔位置比例 | 0.5 | 首孔在合法区间中的位置（0 最下，1 最上） |

**规划结果**（REVIEW.md 中的 `contact_plan`）：`count = 2`，`centers_nm = [515, 985]`。

推导：
- W 1500 大于阈值 1070，所以取 2 行；
- 孔中心的合法区间为 [180, 1500 − 180 − 470] = [180, 850]；
- 首孔位于区间的 0.5 处：180 + 0.5 × 670 = 515；
- 第二个孔加孔距 470，得 985。

**看到的图形**：规划结果交给 build_row 后画出的 NMOS：
- 有源区 1480×1500，比 U01 高；
- 左右两段**各有 2 个孔**，中心 y = 515 和 985，两孔之间空 250；
- 每段的 M1 着陆把本段两个孔连成一块；
- 栅孔仍在有源区上方。

**改参数时的观察点**：把 `w_nm` 改到 1070 或以下，应变为 1 行孔，位于区间中点。

### U04_custom_geometry_plan —— 扩散段几何规划（NMOS，链 output→rail）

**输入**（取自 `primitive_probe/cases/U04_custom_geometry_plan.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `library` | 见下方策略表 | 写在用例中的几何策略库（clay.geometry-primitive.v1） |
| `kind` | `"nmos"` | 规划器输入：极性 |
| `chain` | `["output", "rail"]` | 规划器输入：扩散链（每段须有对应策略键） |
| `w_nm` | `1000` | 决定孔行数并用于出图 |
| `l_nm` | `600` | 仅用于出图：沟道长 L |
| `nets.gate` | `"IN"` | 仅用于出图：网络名标签 |
| `nets.output` | `"OUT"` | 仅用于出图：网络名标签 |
| `nets.rail` | `"VSS"` | 仅用于出图：网络名标签 |

策略库中实际起作用的内容：

| 策略项 | 值 | 含义 |
|---|---|---|
| `nmos` 孔行数 | W ≤ 1070 取 1 行，否则 2 行 | 学到的宽度阈值规则 |
| `nmos` 首孔位置比例 | 0.5 | 首孔在合法区间中的位置（0 最下，1 最上） |
| `nmos:outer:output` | 超额 1.0 个孔宽；孔位比例 0.0 | 该类扩散段比 PDK 下限加长多少、孔放在段内何处（null = 无位置证据） |
| `nmos:outer:rail` | 超额 0.0 个孔宽；孔位比例 null | 该类扩散段比 PDK 下限加长多少、孔放在段内何处（null = 无位置证据） |

**规划结果**：`segment_lengths_nm = [660, 440]`，`contact_x_in_segment_nm = [180, 260]`。

推导：
- 输出段超额 1.0 个孔宽，段长 = 440 + 220 = 660；孔位比例 0，取合法区间 [180, 400] 的起点 180；
- 地段无超额，段长 440；区间退化为单点 180（自右外沿计），换算成段内自左起为 260；
- 孔行数：W 1000 小于阈值，1 行，中心 y = 500。

**看到的图形**：
- 有源区 1700×1000，比 U01 宽 220，**加长的是左边的输出段**；
- 栅在 x 660–1260；
- 左孔中心 x = 180，距栅 370，比 U01 中的 150 远很多，即孔贴外沿、不贴栅；
- 右孔中心 x = 1520，距有源区右沿 70；
- 两孔 y = 500。

### U07_custom_turn —— 旋转 270°

**输入**（取自 `primitive_probe/cases/U07_custom_turn.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `row.layers.active` | `[[0, 0, 2000, 1000]]` | 输入形状：该图层上的 bbox 列表（必须有 active） |
| `row.layers.poly` | `[[0, 0, 400, 1000]]` | 输入形状：该图层上的 bbox 列表（必须有 active） |
| `row.layers.metal1` | `[[1700, 700, 1900, 900]]` | 输入形状：该图层上的 bbox 列表（必须有 active） |
| `row.terminals` | `[["TOPRIGHT", [1700, 700, 1900, 900]]]` | 端口标签 [网络名, bbox] |
| `quarter_turns` | `3` | 逆时针旋转 90° 的次数：0–3 |
| `display_offset_nm` | `[-4000, 0]` | 未旋转输入（青色参考图）的显示偏移 [dx, dy] |

**看到的图形**：
- 左侧青色（200 层）是**未旋转的输入**：横放的 2000×1000 有源区，左端有一条多晶竖条，右上角有一个 M1 小方块（文本 TOPRIGHT）。
- 原点处是旋转后的结果。逆时针转 270°，相当于顺时针转 90°，然后把有源区左下角移回 (0,0)：

| 图形 | 预期位置 |
|---|---|
| 有源区 | (0,0)–(1000,2000)，变成竖放 |
| 多晶条（原在左侧） | 移到**顶部**：(0,1600)–(1000,2000) |
| M1 方块（原在右上） | 移到**右下**：(700,100)–(900,300)，文本 TOPRIGHT 跟着移动 |

### U08_custom_materialize —— 组装（两行上下放置，P+ 合并）

**输入**（取自 `primitive_probe/cases/U08_custom_materialize.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `rows.R1.layers.active` | `[[0, 0, 1000, 1000]]` | 某行在该图层上的 bbox 列表 |
| `rows.R1.layers.pplus` | `[[-230, -230, 1230, 1230]]` | 某行在该图层上的 bbox 列表 |
| `rows.R1.terminals` | `[["NET1", [400, 400, 600, 600]]]` | 该行的端口标签 [网络名, bbox] |
| `rows.R2.layers.active` | `[[0, 0, 1000, 1000]]` | 某行在该图层上的 bbox 列表 |
| `rows.R2.layers.pplus` | `[[-230, -230, 1230, 1230]]` | 某行在该图层上的 bbox 列表 |
| `rows.R2.terminals` | `[["NET2", [400, 400, 600, 600]]]` | 该行的端口标签 [网络名, bbox] |
| `origins.R1` | `[0, 0]` | 该行的平移量 [x, y]（每行都必须给出） |
| `origins.R2` | `[0, 3000]` | 该行的平移量 [x, y]（每行都必须给出） |

**看到的图形**：

| 图形 | 预期 |
|---|---|
| 22/0 有源区 | **两个**独立方块：(0,0)–(1000,1000) 与 (0,3000)–(1000,4000) |
| 31/0 P+ | **只有一个**矩形 (-230,-230)–(1230,4230)：两行原本各自的 P+ 被合并成外接矩形，中间 1000–3000 的空隙也被覆盖 |
| 0/0 边界 | 与 P+ 相同的外框 |
| 34/10 文本 | NET1 在 (500,500)，NET2 在 (500,3500) |

**观察点**：有源区只是平移、不合并，而 P+ 被合并。这正是该原语的设计：注入层、阱层、标记层合并为外接矩形，导电层保持独立。

### U10_custom_inverter_topology —— 结构识别（单指反相器）

**输入**（取自 `primitive_probe/cases/U10_custom_inverter_topology.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `netlist` | 见下方网表 | CDL 网表文本（输入） |
| `contract` | high=VDD, low=VSS, n_body=VPW, p_body=VNW，其余为流程默认 | 布局合约 |

网表：

```text
.SUBCKT simple_inv A Y VDD VNW VPW VSS
MN Y A VSS VPW nfet_05v0 W=1.0U L=0.6U
MP Y A VDD VNW pfet_05v0 W=1.5U L=0.5U
.ENDS
```

**无版图**，在 REVIEW.md 中查看：
- `groups.nmos = ["MN"]`，`groups.pmos = ["MP"]`；
- `gate_net = "A"`（两管栅共接的网络）；
- `output_net = "Y"`（两管非电源端共接的网络）。

**改参数时的观察点**：如果在网表中把 MP 的栅改成另一个网络，工程应报错 “Not a common-gate inverter”，不给出分组。

### U11_custom_solve_area_core —— 面积放置（core_boxes 模式）

**输入**（取自 `primitive_probe/cases/U11_custom_solve_area_core.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `items.N.rect` | `[1000, 1000]` | 有源区矩形 [宽, 高] |
| `items.N.outer` | `[-240, -240, 1240, 1240]` | 全部图层外框（相对矩形原点），决定面积 |
| `items.N.core` | `[0, 0, 1000, 1000]` | 核心框，决定第二目标 |
| `items.P.rect` | `[1000, 1000]` | 有源区矩形 [宽, 高] |
| `items.P.outer` | `[-600, -600, 1600, 1600]` | 全部图层外框（相对矩形原点），决定面积 |
| `items.P.core` | `[0, 0, 1000, 1000]` | 核心框，决定第二目标 |
| `clearance_nm` | `1200` | 矩形两两净距 |
| `use_core_boxes` | `true` | true = 流程模式；false = 对照组 |
| `seconds` | `5` | 求解时间上限（秒） |

**放置结果**：`area_nm2 = 8 888 000`。

推导：两有源区沿某个方向相距 1200 排列时，外框总包围为 2200 × (600 + 1000 + 1200 + 1000 + 240) = 2200 × 4040。其中 2200 是 P 外框宽 1000 + 2×600；N 的外框较小，落在 P 外框的横向范围内。

**看到的图形**：
- 22/0：两个 1000×1000 方块，**上下或左右**排列，相对的两边之间净距**恰为 1200**；
- 30/0 核心框：与方块重合（默认 core = rect）；两方块在另一方向上**对齐**，核心框总包围为 1000×3200（或 3200×1000）。这是第二目标的效果；
- 34/0 外框：N 外框比方块每边大 240，P 外框每边大 600；两外框可以重叠（外框只决定面积，不受净距约束）。

**对比观察点**：把 `use_core_boxes` 改为 `false`，面积应相同，但两方块可能不再对齐。

### U12_custom_relation_features —— 关系特征测量（输入为附件布局）

**输入**（取自 `primitive_probe/cases/U12_custom_relation_features.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `layout_gds` | `"data/primitive_generalization/primitive_lab/internal_flow_final4/generated/placement.gds"` | 被测量的反相器版图路径（输入） |
| `netlist` | 见下方网表 | CDL 网表文本 |
| `contract` | high=VDD, low=VSS, n_body=VPW, p_body=VNW，其余为流程默认 | 布局合约 |

网表：

```text
* Copyright 2022 GlobalFoundries PDK Authors
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
*      http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.

.SUBCKT gf180mcu_fd_sc_mcu9t5v0__inv_2 I ZN VDD VNW VPW VSS
*.PININFO I:I ZN:O VDD:P VNW:P VPW:P VSS:G
*.EQN ZN=!I
M_i_0_0 ZN I VSS VPW nfet_05v0 W=1.320000U L=0.600000U
M_i_0_1 VSS I ZN VPW nfet_05v0 W=1.320000U L=0.600000U
M_i_1_0 ZN I VDD VNW pfet_05v0 W=1.830000U L=0.500000U
M_i_1_1 VDD I ZN VNW pfet_05v0 W=1.830000U L=0.500000U
.ENDS
```

**无版图输出**。`input_context.gds` 就是附件中的布局，是输入，可以打开对照。
REVIEW.md 中的预期结果：

| 特征 | 预期 | 在 input_context.gds 中如何对照 |
|---|---|---|
| `parallel_gate_axes` | 1 | 两排多晶栅都是竖直的 |
| `separation_along_gates` | 接近 1 | N 行在下、P 行在上，沿栅方向分开 |
| `n_gate_access_inward` | 1 | NMOS 栅极接触在 N 行的上方，朝向 P 行 |
| `p_gate_access_inward` | 1 | PMOS 栅极接触在 P 行的下方，朝向 N 行 |
| `output_transverse_misalignment` | 接近 0 | 两行中间输出段的孔列 x 位置基本相同 |
| `hpwl_total_nm` | 正整数 | 栅、输出接触点的半周长之和，供 U13 使用 |

### U13_custom_relation_rank —— 关系排序（两个候选）

**输入**（取自 `primitive_probe/cases/U13_custom_relation_rank.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `library_file` | `"data/primitive_generalization/relation_training_v1/learned_primitive.json"` | 冻结关系库（提供特征学习均值） |
| `weights.relations` | `0.1` | 关系项权重 |
| `weights.wirelength_proxy` | `0.05` | 线长项权重 |
| `candidates` | 见下方候选表 | 候选列表：name / passed / area_nm2 / features / hpwl_total_nm |

候选：

| 候选 | passed | area_nm2 | hpwl_total_nm | parallel_gate_axes | separation_along_gates | n_gate_access_inward | p_gate_access_inward | output_transverse_misalignment |
|---|---|---|---|---|---|---|---|---|
| C0 | true | 10000000 | 2000 | 1 | 0.999629 | 1 | 1 | 0 |
| C1 | true | 9000000 | 2000 | 1 | 0.999629 | 0 | 1 | 0 |
| **库中学习均值**（来自 library_file，对比基准） | — | — | — | 1 | 0.999629 | 1 | 1 | 0 |

**无版图**，在 REVIEW.md 中查看：

| 候选 | 面积 | 特征 | 预期 relation_loss | 预期 score |
|---|---|---|---|---|
| C0 | 10 000 000 | 与学习均值完全相同 | 0 | 10/9 + 0 + 0.05 ≈ **1.16111** |
| C1 | 9 000 000（更小） | `n_gate_access_inward` = 0，其余同均值 | (1²)/5 = 0.2 | 1 + 0.10×0.2 + 0.05 = **1.07** |

**结论**：`selected_index = 1`，即选中 C1。面积小 10% 的优势超过了一项关系偏差的惩罚。

**改参数时的观察点**：把 C1 的面积改为 9 500 000、再把 `p_gate_access_inward` 和 `output_transverse_misalignment` 也改成不符，可以得到选中 C0 的情形。

### U14_custom_candidate_verify —— 候选检查（输入为附件布局）

**输入**（取自 `primitive_probe/cases/U14_custom_candidate_verify.json`；若改过该文件，以文件为准）：

| 参数 | 值 | 含义 |
|---|---|---|
| `layout_gds` | `"data/primitive_generalization/primitive_lab/internal_flow_final4/generated/placement.gds"` | 待检查版图路径（输入） |
| `netlist` | 见下方网表 | CDL 网表文本 |
| `plans.nmos` | `{"count": 2, "centers_nm": [515, 985]}` | 该极性期望的接触计划 {count, centers_nm} |
| `plans.pmos` | `{"count": 2, "centers_nm": [775, 1245]}` | 该极性期望的接触计划 {count, centers_nm} |
| `contract` | high=VDD, low=VSS, n_body=VPW, p_body=VNW，其余为流程默认 | 布局合约 |

网表：

```text
* Copyright 2022 GlobalFoundries PDK Authors
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
*      http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.

.SUBCKT gf180mcu_fd_sc_mcu9t5v0__inv_2 I ZN VDD VNW VPW VSS
*.PININFO I:I ZN:O VDD:P VNW:P VPW:P VSS:G
*.EQN ZN=!I
M_i_0_0 ZN I VSS VPW nfet_05v0 W=1.320000U L=0.600000U
M_i_0_1 VSS I ZN VPW nfet_05v0 W=1.320000U L=0.600000U
M_i_1_0 ZN I VDD VNW pfet_05v0 W=1.830000U L=0.500000U
M_i_1_1 VDD I ZN VNW pfet_05v0 W=1.830000U L=0.500000U
.ENDS
```

**无版图输出**。`input_context.gds` 为输入。
REVIEW.md 中的预期：
- `passed = true`，`failed = []`；
- `checks` 中每一项都是 true，包括网格、孔尺寸、各间距和包围、N 阱、注入、5V 标记、栅端帽、`target_WL_nets`（版图提取的 W/L 和网络与网表一致），以及 `nmos_contact_count`、`pmos_contact_count`（每段 2 个孔，与 plans 一致）。

**改参数时的观察点**：把 `plans.nmos.count` 改为 1，应**只有** `nmos_contact_count` 一项变为 false，`passed = false`。这说明检查器能发现孔数与计划不符，且不会牵连其它项。
