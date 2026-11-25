# CLAY 原语清单

> **开源发布说明：** 本仓库只发布布局（比较器生成）部分。`primitive_classification.yaml` 中登记 42 个原语；14 个学习式布线原语（§2 总览第 43–56 行、§3.8、§4.5）及 `clay/routing/` 未包含在本次发布中，相关章节仅作设计参考。
>
> 更新：2026-11-18。只收录工程中现有的原语（原工程中为 56 个条目：原有 42 个，加上 2026-11-18 新增的 14 个学习式布线原语，见 §3.8、§4.5），说明每个原语的意义、函数与输入参数，以及分类系统和反相器、比较器各用到哪些原语。布线设计方案见 [ROUTING_LEARNING_DESIGN_zh.md](ROUTING_LEARNING_DESIGN_zh.md)。分类表、检索索引、执行核心与全局优化见生成核心文档 [GLOBAL_COORDINATOR_DESIGN_zh.md](GLOBAL_COORDINATOR_DESIGN_zh.md)；动作决策见 [ACTION_DECIDER_FREEDOM_zh.md](ACTION_DECIDER_FREEDOM_zh.md)；PDK 解析见 [PDK_PARSER_WORKFLOW_zh.md](PDK_PARSER_WORKFLOW_zh.md)。

---

## 0. 三份文件的分工

| 文件 | 内容 | 给谁读 |
|---|---|---|
| 本文件 `docs/PRIMITIVE_CATALOG_zh.md` | 每个原语的意义、输入参数含义、用在哪个流程 | 人 |
| `primitive_classification.yaml` | 分类与检索：每个原语的分类轴、对应代码、能力签名 | 程序（协调核心、检索索引、一致性测试） |
| `primitive_verify/registry.py` | 单原语验证：被测函数、输出形态、登记来源与复核状态 | 单原语验证框架 |

本文件 §6 的签名一览由分类表生成；两者不一致时以分类表为准（测试会检查分类表与代码一致）。

---

## 1. 分类系统

### 1.1 分类轴（取值封闭，写错即报错）

| 轴 | 取值 | 含义 |
|---|---|---|
| `stage` 阶段 | observe 观测 / decide 决策 / plan 规划 / construct 构造 / transform 变换 / assemble 组装 / verify 检查 | 原语在生成流程中的位置 |
| `form` 形态 | layout 版图 / coords 坐标 / data 数据 | 返回什么（验证框架据此读回输出） |
| `freedom_effect` 自由度作用 | none / translation 平移 / rotation 朝向 / structure 结构 / net 网络 | 通过它改变的是哪种自由度 |
| `pdk_binding` | 语义规则键列表（如 `cut_to_active`），另有 `optional_binding`（工艺有该层时才读） | 原语需要工艺提供哪些规则；写语义键而非层号，所以换工艺不改分类表 |
| `decision_role` 决策角色 | none / admissibility 合法性 / options 选项 / reduce 等价削减 / bound 下界 / feedback 失败处理 | 只用于 decide 阶段：在协调核心的一个决策层里扮演什么角色。精确求解不是角色，求解器属于 plan 阶段 |

### 1.2 能力签名（每个条目必填）

| 字段 | 含义 |
|---|---|
| `implementation` | 对应代码 `模块:函数`；测试会导入并检查可调用 |
| `signature.inputs` | 必需的类型化输入（参数名 → 类型） |
| `signature.optional_inputs` | 可缺省的类型化输入 |
| `signature.params` | 其余参数（θ 坐标与实现设置）；用于诊断指向具体参数 |
| `signature.output` | 输出类型 |
| `composition` 组合方式 | `step` 固定步骤；`dispatch` 按器件类别分派（同一输出类型下适用器件互不相交）；`alternative` 与某类别的 dispatch 默认构造器竞争的另一种构造，由协调核心按目标比较；`engine` 只在另一个原语内部调用 |
| `applies_to` | 服务的器件类别（mos / res_poly / cap_mim / cap_var）；空表示与器件无关 |
| `options_domain` | 仅 options 角色：选项集合是完整的（complete）还是截断的（truncated）；截断的写入证书范围 |
| `determinism` | `pure`：在确定性证明（生成核心文档 §8.3）的审计范围内；`unaudited`：尚未审计，不作声明 |
| `cost` | `cheap` / `solver`（调用 CP-SAT） |
| `mutates` | 函数会改写的输入（这样的原语不是纯函数，必须声明） |

### 1.3 类型

类型名来自封闭词表（`clay/learning/primitive_classification.py` 的 `TYPES`）。几类特殊类型：

- **外部输入**（生成时给定）：网表文本、Pack、学习库（电路库、反相器库、关系库）、反相器约定 `Contract`；
- **只在训练时给定**：参考版图 `ReferenceLayout`。因此由分类表推导组合时，生成流程不可能读参考版图；
- **由协调核心自己产生**：构造候选记录 `Candidate`；
- **集合 / 部分**：如 `Rows` 是 `Row` 的集合，`ContactPolicy` 是 `Library` 的一部分（字段读取）。

### 1.4 检索与推导

`clay/decision/index.py`：按输出类型、输入类型、器件类别检索原语；从目标类型反向推导所需原语及尚未消解的选择；列出"没有原语能产出的输入类型"（即还靠手写衔接代码的地方）。

### 1.5 验证状态

`primitive_verify/registry.py` 中的条目分三种：**已冻结**（人已复核 spec）；**待复核**（验证照常运行、结果为 PASS，但 spec 的预期值尚未经人复核，不计入冻结结果）；**未登记**（还没有单原语验证）。AI 可以追加待复核条目，不能修改已冻结条目（`primitive_verify/README_zh.md` §5）。§2 的"验证"列即此状态。

---

## 2. 总览

"反相器"列：**C** = 反相器接触库流程（含几何、关系流程），**S** = 反相器结构 / 构造搜索流程，**R** = 布线实验（旧，单层正交），**L** = 学习式布线（新，剥线重布，§4.5）。"比较器"列：**G** = 生成，**T** = 训练，**G\*** = 只在特定选项下使用，**L** = 学习式布线（计划，尚未运行）。比较器的生成流程也能生成反相器（§4.3）。

| # | 原语 | 阶段 | 组合 / 角色 | 适用 | 反相器 | 比较器 | 验证 |
|---|---|---|---|---|---|---|---|
| 1 | netlist_flatten | observe | step | — | | G T | 待复核 |
| 2 | netlist_symmetry | observe | step | — | | T, G\* | 待复核 |
| 3 | netlist_symmetries | decide | options（truncated） | — | | G | 未登记 |
| 4 | symmetry_classes | decide | reduce | — | | G | 未登记 |
| 5 | inverter_topology | observe | step | mos | C | | 已冻结 |
| 6 | observe_layout | observe | step | — | | T | 待复核 |
| 7 | relation_features | observe | step | mos | C（关系流程） | | 已冻结 |
| 8 | fit_library | decide | step | — | | T | 待复核 |
| 9 | relation_rank | decide | step（改写输入） | mos | C（关系流程） | | 已冻结 |
| 10 | angle_verdicts | decide | admissibility | — | C | G | 未登记 |
| 11 | enforce | decide | admissibility | — | C | G | 未登记 |
| 12 | orientations | decide | options（complete） | — | | G | 未登记 |
| 13 | fold_candidates | decide | options（truncated） | mos, cap_var | | G | 未登记 |
| 14 | canonical_rotation_class | decide | reduce | — | C | | 未登记 |
| 15 | two_box_lower_bound | decide | bound | — | C | | 未登记 |
| 16 | verification_feedback | decide | feedback | — | | G | 未登记 |
| 17 | symmetry_placement | decide | step | mos | （`.clay` 注释流程） | | 已冻结 |
| 18 | finger_fold | plan | step | mos, cap_var | | G | 待复核 |
| 19 | diffusion_chains | plan | step | mos, cap_var | | G | 待复核 |
| 20 | contact_plan | plan | step | mos | C | G\* | 已冻结 |
| 21 | contact_policy_plan | plan | step | mos, cap_var | | G | 待复核 |
| 22 | geometry_plan | plan | step | mos | C（几何流程） | | 已冻结 |
| 23 | placement_units | plan | step | — | | G | 未登记 |
| 24 | placement_intents | plan | step | — | | G | 未登记 |
| 25 | block_from_row | plan | step | — | | G | 待复核 |
| 26 | block_space | plan | step | — | | G | 未登记 |
| 27 | mirror_space | plan | step | — | | G | 未登记 |
| 28 | place_blocks | plan | step | — | | G | 未登记 |
| 29 | solve_blocks | plan | engine | — | | G | 待复核 |
| 30 | solve_area | plan | step | — | C S | | 已冻结 |
| 31 | build_row | construct | step | mos, cap_var | C | G | 已冻结（另有 build_chain、build_varactor 两个待复核配置） |
| 32 | build_chain_row | construct | dispatch | mos, cap_var | | G | 未登记 |
| 33 | build_learned_pattern_row | construct | alternative | mos | | G\* | 未登记 |
| 34 | build_poly_resistor | construct | dispatch | res_poly | | G | 待复核 |
| 35 | build_mim_cap | construct | dispatch | cap_mim | | G | 待复核 |
| 36 | build_contacted_device | construct | step | mos | S | | 待复核 |
| 37 | orthogonal_path | construct | step | — | R | | 待复核 |
| 38 | turn | transform | step | — | C | G | 已冻结 |
| 39 | reflect | transform | step | — | | G | 待复核 |
| 40 | materialize | assemble | step | — | C | G | 已冻结（另有 materialize_close 待复核配置） |
| 41 | candidate_verify | verify | step | mos | C | | 已冻结 |
| 42 | circuit_verify | verify | step | — | | G | 待复核 |
| 43 | route_rules | observe | step | — | L | L | 未登记 |
| 44 | route_directions | plan | step | — | L | L | 未登记 |
| 45 | route_strip | transform | step（只用于训练与剥线评估） | — | L | L | 未登记 |
| 46 | route_terminals | observe | step | — | L | L | 未登记 |
| 47 | net_signature | observe | step | — | L | L | 未登记 |
| 48 | observe_routing | observe | step（训练） | — | L | L | 未登记 |
| 49 | fit_routing_library | decide | step（训练） | — | L | L | 未登记 |
| 50 | routing_policy | decide | engine（在 route_specs 内） | — | L | L | 未登记 |
| 51 | route_trunk_candidates | plan | engine（在 route_specs 内） | — | L | L | 未登记 |
| 52 | route_specs | decide | step | — | L | L | 未登记 |
| 53 | route_graph | plan | step | — | L | L | 未登记 |
| 54 | route_solve | plan | step（求解器） | — | L | L | 未登记 |
| 55 | route_materialize | construct | step | — | L | L | 未登记 |
| 56 | route_verify | verify | step | — | L | L | 未登记 |

---

## 3. 各原语的意义与输入参数

以下按阶段分组。"函数"一行给出实际调用的代码；参数表只列有含义的参数，`pack`（语义规则包，提供图层、网格与全部规则数值）不再逐一说明。

### 3.1 observe：读取网表与版图

#### netlist_flatten — 通用网表前端
- 函数：`clay.learning.netlist.parse_spice(text, pack, *, top=None, net_alias=None) -> FlatCircuit`
- 意义：把任意层次的 SPICE/CDL 展开成叶器件，按 pack 的器件类别识别 MOS、poly 电阻、MIM、变容管。未知模型、缺 W/L、不在网格上、尺寸超过 10 mm（单位约定错配）都拒绝；无模型的寄生 R/C 计数后丢弃。

| 参数 | 含义 |
|---|---|
| `text` | 网表全文；支持 `.subckt/.ends` 层次、`+` 续行、`X` 子电路实例、`M`/`X` 器件实例 |
| `top` | 顶层单元名；省略时取唯一一个未被引用的子电路 |
| `net_alias` | 正则，从每个网名中删除匹配部分（用于提取网表把一个网拆成端子桩，如 `GND__t11` 写 `"__t\d+$"`） |

- 输出：`FlatCircuit`（名字、端口、器件、丢弃的寄生、层次）；每个器件有名字、类别、模型、端子 → 网、总宽/长（nm）、`nf`、`m`、扩散极性。裸数字的单位由 pack 声明（GF180 为米，SKY130 为微米）。

#### netlist_symmetry — 按固定规则选出的网表对称
- 函数：`clay.learning.symmetry.netlist_symmetry(circuit, *, max_iterations=5000) -> Symmetry | None`
- 意义：差分电路"左右两半互换"的对称映射。是 `netlist_symmetries` 排序后的第一个（先比结构：移动的器件多者优先、交换的网多者优先、器件对的尺寸；最后才比名字）。用在训练观测中；生成时只有指定"固定映射"才用它，默认由协调核心在全部映射中按目标选择。

| 参数 | 含义 |
|---|---|
| `circuit` | 展开网表 |
| `max_iterations` | 每个种子端口对最多枚举的同构映射数 |

- 输出：`net_map`（网的映射）、`device_map`（器件的映射）、`seed`（触发的端口对，如 `inp↔inn`）、`pairs`（镜像器件对）、`fixed`（不动的器件，如尾管）。无对称时为 `None`。

#### inverter_topology — 反相器拓扑
- 函数：`clay.learning.circuit.parse_cdl(text, cell=None)`，再由 `clay.learning.contact_primitives.inverter_groups(circuit, contract, *, pack)` 分组
- 意义：读单层 CDL，按"公共栅、一个输出、电源在两侧"把器件分成 NMOS 组与 PMOS 组。只适用于反相器。

| 参数 | 含义 |
|---|---|
| `text` / `cell` | CDL 文本 / 子电路名 |
| `contract` | 反相器约定 `PlacementContract`：高、低电源网，N、P 体网，工艺，布局方式，器件构造配方，网格，求解时限 |

- 输出：反相器电路图，以及 NMOS/PMOS 并联组、栅网、输出网。

#### observe_layout — 训练观测（只在训练时）
- 函数：`clay.learning.circuit_library.observe_layout(sample_id, gds_path, circuit, pack) -> dict`
- 意义：从训练参考版图中量出可学习的信号。参考版图的类型是"只在训练时给定"，生成流程不可能用到它。

| 参数 | 含义 |
|---|---|
| `sample_id` | 样本标识 |
| `gds_path` | 训练参考版图（支持 `.gds.gz`，任意数据库单位统一换算为 nm） |
| `circuit` | 该样本的展开网表（用于对称与 PMOS 体网统计） |

- 输出：每个源漏段的接触孔行数 / 列数 / 首孔偏移；最佳镜像轴及配对率；栅方向一致性；每块有源区的栅数；阱岛数；电阻头部几何；材料外包框。

#### relation_features — 反相器两行关系特征
- 函数：`clay.learning.placement_relations.features(layout, circuit, contract, *, pack=None)`
- 意义：度量反相器两行之间的朝向、对齐与接线可达性特征（不读规则）。只用于反相器关系流程。

| 参数 | 含义 |
|---|---|
| `layout` | 已生成的候选版图 |
| `circuit` / `contract` | 反相器电路与约定 |

- 输出：特征值与线长代理（`hpwl_total_nm`）。

### 3.2 decide：选择、判定、学习

#### netlist_symmetries — 全部对称映射（对称映射层的选项）
- 函数：`clay.learning.symmetry.netlist_symmetries(circuit, *, max_iterations=5000) -> tuple[Symmetry]`
- 意义：列出全部合法对合映射，按 `netlist_symmetry` 的规则排序；同一映射被多个种子找到只保留一次。选项集合声明为截断的：只有没有种子达到 `max_iterations` 时才完整。实测：GF180 动态比较器 1 个，2AM 2 个，JKU 16 个。
- 参数：同 `netlist_symmetry`。输出：映射元组。

#### symmetry_classes — 映射去重
- 函数：`clay.learning.symmetry.symmetry_classes(circuit, symmetries) -> tuple[tuple[Symmetry]]`
- 意义：把"只差在交换孪生器件"的映射归为一类，每类只需求解一次。孪生器件：类别、模型、尺寸、倍数相同，且每个端子连的网相同（MOS 源漏、电阻两端可互换）。交换孪生本身是固定所有网的网表自同构，所以同类映射得到的构造只差相同器件的名字。可靠：证明不了等价的不合并；"把孪生配成一对"与"让孪生都不动"不会合并。实测：2AM 2 → 1 类，JKU 16 → 16 类。

| 参数 | 含义 |
|---|---|
| `circuit` | 展开网表（用于识别孪生器件） |
| `symmetries` | `netlist_symmetries` 的输出 |

#### fit_library — 学习并冻结电路库
- 函数：`clay.learning.circuit_library.fit_library(observations, packs) -> dict`
- 意义：从训练观测中拟合电路库。

| 参数 | 含义 |
|---|---|
| `observations` | `observe_layout` 的结果列表（样本版图哈希必须互不相同） |
| `packs` | 工艺名 → pack |

- 输出（库内容）：

| 键 | 含义 |
|---|---|
| `contact_policies[工艺]` | 每段接触孔数策略（固定数或容量比例）与首孔位置 |
| `relations.mirror_pairs` | enforce / off：对称器件是否必须镜像放置 |
| `relations.mirror_axis` | parallel / perpendicular（相对栅线）/ free：对称轴候选 |
| `relations.centre_fixed_devices` | soft：未配对的块软居中 |
| `relations.uniform_gate_orientation` | enforce / off：含栅的块保持栅向 |
| `resistor_style[工艺]` | 电阻头部槽孔尺寸与各包含余量 |
| `not_imitated` | 观测到但刻意不模仿的结构选择及理由 |

学到的关系如何作用于构造，由关系登记表 `clay/learning/relations.py` 规定；库中出现未登记的关系即报错。

#### relation_rank — 反相器候选排序
- 函数：`clay.learning.placement_relations.rank(trials, measurements, lib, weights, *, pack=None)`
- 意义：按面积、与学到关系的残差、线长代理给反相器候选打分并选出最好的。启发式排序，不是最优性证明。**会把每个候选的打分写回 `trials`**（不是纯函数，分类表已声明）。

| 参数 | 含义 |
|---|---|
| `trials` | 候选列表（每个含检查结果与求解面积） |
| `measurements` | 每个候选的 `relation_features` |
| `lib` | 关系库（各特征的均值） |
| `weights` | 权重 `{"relations", "wirelength_proxy"}` |

- 输出：胜出候选的序号。

#### angle_verdicts — 每个候选角度的结论（合法性）
- 函数：`clay.learning.freedom.angle_verdicts(pack, layer_roles) -> tuple[AngleVerdict]`
- 意义：一个刚性部件（由它实际画出的图层组成）的每个候选旋转角度是否合法、能否执行。候选角度取工艺中所有图层角度步长与 90 的最大公约数的倍数，所以只要工艺出现过 45° 步长，就列出全部 8 个角度。结论依次为：可执行；deck 规则禁止；网格上画不出这个方向（Niven 定理）；精确边长在该角度下无解（deck 证明不可行）；未知（某层无角度规则，未结案）；引擎不具备该方向的构造 / 放置 / 检查能力（未结案）。

| 参数 | 含义 |
|---|---|
| `layer_roles` | 部件实际画出的图层角色 |

- 输出：每个角度一个结论（角度、状态、理由、是否结案）。

#### enforce — 动作门控（合法性）
- 函数：`clay.learning.freedom.enforce(freedom, plan, predicates=None) -> None`
- 意义：拒绝自由度模型不允许的动作：部件不存在或不可用、旋转角度不在可执行集合、镜像轴不允许、平移不在网格上、耦合部件动作不一致、约束谓词不成立。只拒绝（抛出），从不修正。

| 参数 | 含义 |
|---|---|
| `freedom` | 由各部件构建的自由度模型 |
| `plan` | 部件名 → 动作（旋转角度 / 镜像轴 / 平移） |
| `predicates` | 额外的约束谓词（名字 → 条件） |

#### orientations — 一个部件的全部可执行正交朝向（选项）
- 函数：`clay.learning.freedom.orientations(freedom_of_part) -> tuple[(degrees, mirrored)]`
- 意义：矩形部件的 8 种对称（4 个四分之一圈 × 是否关于竖直轴镜像）中合法且可执行的那些，顺序固定（先不镜像、角度升序；恒等在前）。选项集合完整。
- **非正交方向是硬错误，不是过滤**：刚性部件无法转到 45° —— 格点 (x, y) 转 45° 后是 ((x−y)/√2, (x+y)/√2)，只有退化的共线形状才落在 nm 网格上；平方格点的旋转群就是 4 个四分之一圈加镜像，与工艺无关。所以若某个 Pack 声明了 `rotate_non_orthogonal`、角度判定因此把 45° 判为 `executable`，这里直接报错并指出"应在该角度**画出**几何，而不是把部件转过去"。若改为静默过滤，证书就会把一个"从未搜索过的角"当成已结案，把缺口伪装成证明。
- 参数：`freedom_of_part`（一个部件的自由度结论）。

#### fold_candidates — 折叠目标候选（选项）
- 函数：`clay.learning.circuit_generate.fold_candidates(circuit, *, pack) -> list`
- 意义：构造层要试的指宽折叠目标：不额外折叠、MOS/变容管指宽的中位数、中位数的一半（不小于两倍最小扩散宽度）。选项集合截断，证书范围注明。
- 参数：`circuit`（展开网表）。输出：目标指宽列表（`None` 表示不额外折叠）。

#### canonical_rotation_class — 整体旋转等价类（削减）
- 函数：`clay.learning.actions.canonical(plan, parts, step_deg) -> tuple`
- 意义：把整个设计一起旋转不改变可达面积，所以以"各部件相对第一个部件的角度差（按部件顺序，不排序）"作为等价类，每类只需求解一次。反相器两行时 16 个方案 → 4 类。

| 参数 | 含义 |
|---|---|
| `plan` | 每个部件的旋转动作 |
| `parts` | 部件顺序 |
| `step_deg` | 未使用（保留的参数） |

#### two_box_lower_bound — 两行外包面积下界
- 函数：`clay.learning.actions.two_box_lower_bound(outer, rects, gap) -> (int, str)`
- 意义：两行放置后外包框面积的可靠下界：两行的有源矩形必按四种先后顺序之一分开 `gap`，取四种情形下界的最小值。与 `solve_area` 实际施加的约束逐项对应，不会比真实最优大。

| 参数 | 含义 |
|---|---|
| `outer` | 两部件的外框（相对各自原点） |
| `rects` | 两部件的放置矩形（有源区宽高） |
| `gap` | 求解器对这个方案实际使用的间距 |

- 输出：下界面积与取得下界的分开方式。

#### verification_feedback — 检查失败后的处理（反馈）
- 函数：`clay.learning.circuit_construct.verification_feedback(record, *, orientation_search=True) -> dict`
- 意义：一个已求解的构造候选没通过版图检查时，决定怎么办，以数据返回，由构造搜索执行。目前规则：开启朝向搜索时返回"用固定朝向重算"（得到合法版图，但不是证明，该候选保持未结案），否则"不处理"。原因：放置模型看不到标记层之间的相互作用，块换朝向后可能留下窄条。

| 参数 | 含义 |
|---|---|
| `record` | 构造候选记录（是否通过检查、失败项） |
| `orientation_search` | 是否开启了朝向搜索 |

#### symmetry_placement — 注释驱动的对称行放置
- 函数：`clay.layout.annotate_to_solver.solve_placement_from_constraints(constraints, devs, *, tech=None, pdk=None, height_weight=1.0, grid_nm=5, time_limit_s=30.0)`
- 意义：由 `.clay` 注释约束驱动的对称混合整数规划行放置（镜像对共行、单一竖直轴、NMOS 行在 PMOS 行下方）。属于 `.clay` 语言的注释流程，反相器、比较器学习流程都不用。

| 参数 | 含义 |
|---|---|
| `constraints` | 由注释生成的求解约束 |
| `devs` | 器件 → 几何 |
| `tech` / `pdk` | 工艺几何与规则 |
| `height_weight` | 目标中高度的权重 |
| `grid_nm` / `time_limit_s` | 网格 / 求解时限 |

### 3.3 plan：规划与放置

#### finger_fold — 指折叠
- 函数：`clay.learning.diffusion_chains.fold_devices(circuit, target_nm=None, *, pack, targets=None) -> tuple[Finger]`
- 意义：把每个 MOS/变容管拆成指：网表自身的 `nf×m`，再乘一个额外折叠因子使指宽不超过目标。总 W、L 不变，只改变版图形状；目标由构造层选择。

| 参数 | 含义 |
|---|---|
| `circuit` | 展开网表 |
| `target_nm` | 目标指宽；`None` 只用网表的 nf。取满足"在网格上、能放下一列接触孔"且不超过目标的最小折叠因子 |
| `targets` | 按器件名单独指定目标 |

- 输出：指列表（器件、序号、类别、极性、模型、L、指宽、栅、源漏网、所在 n 阱的网）；同一器件相邻指源漏交替。

#### diffusion_chains — 扩散共享链
- 函数：`clay.learning.diffusion_chains.diffusion_chains(fingers, symmetry=None, *, max_fingers=None, share=True) -> tuple[Chain]`
- 意义：把同一兼容组（类别、极性、模型、L、指宽、阱网相同）的指排成尽量少的共享扩散链，只在相邻端子同网时共享（最少迹分解）。给出对称映射时，链集合对映射封闭：互为镜像的两条链，或尽量构成回文链。

| 参数 | 含义 |
|---|---|
| `fingers` | `finger_fold` 的输出 |
| `symmetry` | 构造所强制的对称映射；`None` 不考虑匹配 |
| `max_fingers` | 单链最多指数 |
| `share` | `False` 时每个器件单独成链（训练风格，用作基线） |

- 输出：链列表；每条链有组、各指、k+1 个段网、k 个栅网、镜像来源、是否自对称。

#### contact_policy_plan — 按电路库策略规划接触孔
- 函数：`clay.learning.contact_primitives.contact_plan_from_policy(policy, width, *, pack=None, rule_overrides=None) -> dict`
- 意义：按学到的接触孔策略给出一段扩散的孔数与孔中心；预测数截断到 `[1, 容量]`，所以宽样本上学到的孔数不会硬塞进窄指。

| 参数 | 含义 |
|---|---|
| `policy` | 电路库 `contact_policies[工艺]` |
| `width` | 扩散段沿栅方向的宽度（nm） |
| `rule_overrides` | 规则替换（规则键 → 替代的 pack 键），如变容管 |

- 输出：实际孔数、孔中心、截断前的预测、单列容量。

#### contact_plan — 按反相器库规划接触孔
- 函数：`clay.learning.contact_primitives.contact_plan(lib, kind, width, *, pack=None) -> dict`
- 意义：同上，但读反相器接触库（按极性给策略）。

| 参数 | 含义 |
|---|---|
| `lib` | 反相器接触库 |
| `kind` | nmos / pmos |
| `width` | 有源区宽度（nm） |

#### geometry_plan — 按反相器几何库规划段长与孔位
- 函数：`clay.learning.geometry_primitives.geometry_plan(lib, kind, chain, *, pack=None)`
- 意义：按学到的几何策略给出每段扩散长度与孔的 x 偏移（以规则归一化）。只用于反相器几何流程。

| 参数 | 含义 |
|---|---|
| `lib` | 反相器几何库 |
| `kind` | nmos / pmos |
| `chain` | 段符号序列（rail / output） |

- 输出：`segment_lengths_nm`、`contact_x_in_segment_nm`。

#### placement_units — 放置单元
- 函数：`clay.learning.circuit_construct.placement_units(chains, circuit, symmetry=None, *, chain_classes) -> tuple[Unit]`
- 意义：列出要放置的单元及构造顺序：非镜像链，再镜像链，再每个不按链构造的器件（各自紧跟它的镜像器件）。这个顺序决定交给求解器的块顺序。

| 参数 | 含义 |
|---|---|
| `chains` | `diffusion_chains` 的输出 |
| `circuit` | 展开网表（取不按链构造的器件） |
| `symmetry` | 构造所强制的对称映射；`None` 表示不强制 |
| `chain_classes` | 按链构造的器件类别；由分类表推出（以链为输入的 dispatch 构造器所服务的类别），调用方不写死 |

- 输出：单元列表；每个单元有名字、类别、内容类型（链或器件，用于匹配构造器）、镜像来源、是否自对称、是否有配对、阱网、来源器件、意图顺序。

#### placement_intents — 约束意图
- 函数：`clay.learning.circuit_construct.placement_intents(units, symmetry=None, *, axis=None, soft_centre=True) -> PlacementIntents`
- 意义：放置必须遵守的对称轴关系。无强制对称或无轴时为空；否则镜像单元与来源成对，自对称且有配对的单元硬居中，没有配对的单元软居中。

| 参数 | 含义 |
|---|---|
| `units` | `placement_units` 的输出 |
| `symmetry` | 构造所强制的对称映射 |
| `axis` | vertical / horizontal / `None` |
| `soft_centre` | 是否软居中未配对单元（学到的 `centre_fixed_devices`） |

- 输出：镜像对、硬居中、软居中三个列表，以及 `extra`（其他约束种类的数据，种类 → 数据；目前为空，§9）。

#### block_from_row — 放置块
- 函数：`clay.learning.passive_primitives.block_from_row(name, cls, row, *, pack, well=None, mirror_of=None, self_symmetric=False, sources=())`
- 意义：把一个构造包装为刚体块，给出求解器需要的各种框。

| 参数 | 含义 |
|---|---|
| `name` / `cls` | 块名 / 器件类别 |
| `row` | 构造结果 |
| `well` | 所属 n 阱网域 |
| `mirror_of` | 它镜像的块 |
| `self_symmetric` | 是否回文链 |
| `sources` | 包含的器件 |

- 输出：器件平面外框、扩散框、必须被阱覆盖的框、全部图形外框、所在平面（器件 / MIM）、光环（同平面最大间距的一半）。

#### block_space — 一个块的全部合法朝向
- 函数：`clay.learning.action_space.block_space(name, cls, row, *, pack, well=None, self_symmetric=False, sources=(), keep_gates=False, keep_axis_symmetry=False, orient=True) -> BlockSpace`
- 意义：用块实际画出的图层求出合法朝向（`angle_verdicts`、`orientations`），每个朝向先过 `enforce`，再镜像、旋转（`reflect`、`turn`）得到几何，用 `block_from_row` 算出各框，得到一个变体；框完全相同的变体合并。

| 参数 | 含义 |
|---|---|
| `name` / `cls` / `row` | 块名 / 器件类别 / 构造结果 |
| `well` / `self_symmetric` / `sources` | 同 `block_from_row` |
| `keep_gates` | 只取保持栅向的朝向（0°/180°，可镜像），对应学到的"栅向一致" |
| `keep_axis_symmetry` | 居中的自对称链只取 0°/180°，保持它关于轴的自身对称 |
| `orient` | `False` 只给恒等朝向（旧的固定朝向模式） |

- 输出：变体列表（角度、是否镜像、几何、块）与所施加约束的说明。

#### mirror_space — 镜像对的另一半
- 函数：`clay.learning.action_space.mirror_space(name, source, *, pack, axis, net_map, well=None, sources=()) -> BlockSpace`
- 意义：它的第 k 个变体就是来源第 k 个变体的镜像（网名按对称映射改名），求解时两者的变体编号绑定，保证任何朝向下都互为镜像。

| 参数 | 含义 |
|---|---|
| `name` | 镜像块名 |
| `source` | 来源块的变体空间 |
| `axis` | x / y（对应水平 / 竖直对称轴） |
| `net_map` | 对称映射中的网映射 |
| `well` / `sources` | 同 `block_from_row` |

#### place_blocks — 放置
- 函数：`clay.learning.circuit_construct.place_blocks(spaces, intents, *, pack, axis=None, seconds, workers=8, hint=None, variant_hint=None, canonical_seconds=None, defer_canonical=False, pinned_area_nm2=None)`
- 意义：朝向与位置联合求解：把变体空间转成求解器输入，加上镜像对的变体绑定、约束意图和 pack 的阱规则，调用 `solve_blocks`。

| 参数 | 含义 |
|---|---|
| `spaces` | 块名 → 变体空间 |
| `intents` | `placement_intents` 的输出 |
| `axis` | 对称轴 |
| `seconds` / `workers` / `canonical_seconds` | 求解预算 / 线程数 / 确定性阶段预算 |
| `hint` / `variant_hint` | 上一次解的原点与变体（热启动） |
| `defer_canonical` / `pinned_area_nm2` | 延迟确定性阶段（只返回已证明面积）/ 用已证明面积跳过第一阶段、直接定版图。构造层"先证面积、只落可能胜出的候选"时使用（生成核心文档 §6.2） |

#### solve_blocks — N 块精确面积放置（引擎）
- 函数：`clay.layout.synth.area_placement.solve_blocks(blocks, *, grid, well_to_opposite, well_space, axis=None, pairs=(), centred=(), soft_centred=(), seconds=30.0, workers=8, seed=0, hint=None, ties=(), variant_hint=None, deterministic=True, canonical_seconds=None, constraints=None, defer_canonical=False, pinned_area_nm2=None)`
- 意义：CP-SAT 模型，目标为外包框宽 × 高（精确乘积）。每块每个变体一个布尔变量、恰好选一个；同平面块不重叠（光环之和），不同平面互不约束；每个阱网域一个矩形 n 阱；扩散与对向阱、阱与阱的间距；镜像对关于同一条轴镜像且变体绑定；硬居中、软居中。确定性模式：多线程阶段求最优面积，再在"面积 = 最优值"下由单线程确定性阶段选定唯一版图。只在 `place_blocks` 内部调用。

| 参数 | 含义 |
|---|---|
| `blocks` | 块名 → 各框、光环、平面、变体 |
| `grid` | 网格（nm） |
| `well_to_opposite` / `well_space` | 阱到对向扩散 / 阱与阱的间距 |
| `axis` / `pairs` / `centred` / `soft_centred` | 对称轴与约束意图 |
| `ties` | 变体编号必须相等的块对（镜像对） |
| `seconds` / `workers` / `seed` | 第一阶段预算 / 线程数 / 种子 |
| `hint` / `variant_hint` | 热启动 |
| `deterministic` / `canonical_seconds` | 是否用确定性两阶段 / 第二阶段预算 |
| `constraints` | 其他已登记约束种类的数据（种类 → 数据），见 §9；内置的 `ties`、`pairs`、`centred`、`soft_centred` 用各自的参数传入 |

- 输出：各块原点、所选变体、面积、宽、高、状态、下界、阱矩形、软居中代价、确定性路径。

#### solve_area — 两行精确面积放置
- 函数：`clay.layout.synth.area_placement.solve_area(rectangles, outer_boxes, clearances, preferences=(), *, grid=5, seconds=10, bands=None, core_boxes=None)`
- 意义：少量矩形（反相器两行）的精确面积 CP-SAT 放置，间距施加在放置矩形之间；单线程、固定种子、按确定性时间停止。

| 参数 | 含义 |
|---|---|
| `rectangles` | 名 → 放置矩形宽高 |
| `outer_boxes` | 名 → 含伸出部分的外框 |
| `clearances` | 部件对 → 间距 |
| `preferences` | 面积固定后才考虑的风格偏好 |
| `bands` | 两带布局先验 |
| `core_boxes` | 面积相同时偏好紧凑核心 |

### 3.4 construct：构造

#### build_row — 接触扩散链（绘制引擎）
- 函数：`clay.learning.contact_primitives.build_row(devices, chain, plan, gate, output, rail, geometry=None, *, pack=None, gate_nets=None, segment_nets=None, kind=None, diffusion_role='active', well=None, rule_overrides=None, finger_names=None)`
- 意义：画一条接触扩散链：有源区（或 tap）、每沟道一根栅、每端子一个着陆焊盘、可选阱与注入、pack 声明的标记层。焊盘间距不够时加宽共享段；相邻栅焊盘冲突时上下交错。两种调用形式：反相器形式（一个栅网 + rail/output 段符号），通用形式（每个沟道、每段各自的网）。

| 参数 | 含义 |
|---|---|
| `devices` | 每个沟道一个对象（名字、模型、W、L）；同一链 W、L 相同 |
| `chain` / `gate` / `output` / `rail` | 反相器形式：段符号序列与三个网名；通用形式传 `None` |
| `plan` | 接触孔规划（孔数、孔中心） |
| `geometry` | 学到的段长与孔位（反相器几何流程）；通常为 `None` |
| `gate_nets` / `segment_nets` | 通用形式：k 个栅网、k+1 个段网 |
| `kind` | 扩散极性（pack 未绑定为 MOS 的模型必须给出） |
| `diffusion_role` | 扩散所在图层角色：active 或 tap |
| `well` | 是否画 n 阱（默认 PMOS 才画） |
| `rule_overrides` | 规则键 → 替代的 pack 键（只能以一条规则替换另一条） |
| `finger_names` | 每个沟道记录的器件名 |

- 输出：行结构（各图层区域、有源框、端子、孔规划、极性、沟道信息）。

#### build_chain_row — 扩散链 → 接触行（MOS/变容管的默认构造器）
- 函数：`clay.learning.circuit_construct.build_chain_row(chain, policy, *, pack)`
- 意义：按电路库的接触孔策略规划孔（`contact_policy_plan`），再调用 `build_row`。非普通 MOS 的类别如何构造来自工艺数据 `pack.chain_construction`（目前只有 SKY130 的变容管：画在 tap 上，换用变容管的孔规则）。

| 参数 | 含义 |
|---|---|
| `chain` | 一条扩散链 |
| `policy` | 电路库 `contact_policies[工艺]` |

#### build_learned_pattern_row — 反相器训练出的构造（MOS 链的替代构造器）
- 函数：`clay.learning.circuit_construct.build_learned_pattern_row(chain, library, *, pack)`
- 意义：反相器流程学到的构造——每种栅数的 rail/output 段图案、每种极性的接触孔策略——作为 `build_chain_row` 之外的另一种 MOS 链构造，由协调核心按目标比较。适用于各沟道共用一个栅网、段网能一致对应图案符号的链；其他链拒绝。只有提供了反相器库时才作为选项。

| 参数 | 含义 |
|---|---|
| `chain` | 一条 MOS 扩散链 |
| `library` | 反相器接触库（如 `data/primitive_generalization/sky130_inv1_reproduction_20261108/library.json`） |

#### build_poly_resistor — poly 电阻（res_poly 的构造器）
- 函数：`clay.learning.passive_primitives.build_poly_resistor(device, style, *, pack)`
- 意义：直条 poly 电阻加两端带槽孔的头部；polyres 标记本体，rpm/注入/npc 覆盖整体。头部风格是学到的，每个值都抬到不低于规则。

| 参数 | 含义 |
|---|---|
| `device` | 电阻器件（本体宽、长，端子 a/b） |
| `style` | 电路库 `resistor_style[工艺]`：头部槽孔尺寸、poly/金属包孔余量、标记包 poly 余量 |

#### build_mim_cap — MIM 电容（cap_mim 的构造器）
- 函数：`clay.learning.passive_primitives.build_mim_cap(device, *, pack)`
- 意义：capm 顶板 W×L、metal3 底板、居中 via3 阵列、metal4 顶端子。只占金属层平面，放置时可叠在器件上方。
- 参数：`device`（电容器件：W、L，端子 top/bot）。

#### build_contacted_device — 单指接触器件
- 函数：`clay.pdk.device_placement.build_contacted_device(device, *, contact_rows=False, pack=None)`
- 意义：单指（M = nf = 1）接触器件构造器，所有数值读自 pack。用于反相器结构 / 构造搜索流程。

| 参数 | 含义 |
|---|---|
| `device` | MOS 器件（W、L、模型、极性） |
| `contact_rows` | 是否按宽度放多排源漏孔 |

#### orthogonal_path — 正交单层路径
- 函数：`clay.learning.routing_primitives.orthogonal_path(start, goal, obstacles, width, axis_fraction, vertical, *, pack=None)`
- 意义：压缩网格上的精确正交单层路径搜索，绕开按间距扩展过的障碍。用于布线实验。

| 参数 | 含义 |
|---|---|
| `start` / `goal` | 端点 |
| `obstacles` | 已按间距扩展的障碍区域 |
| `width` | 线宽 |
| `axis_fraction` | 沿栅方向的偏好（影响方向代价） |
| `vertical` | 栅方向 |

### 3.5 transform：变换

#### turn — 四分之一圈旋转
- 函数：`clay.learning.contact_primitives.turn(row, n)`
- 意义：绕有源区原点把构造旋转 n×90°，端子（含层角色）同步变换。纯几何，不读规则。
- 参数：`row`（构造结果）、`n`（四分之一圈数）。
- **能力边界**：只做 90° 的倍数。底层 `clay/rotations.py:turn_transform` 对非 90 倍数的角度直接抛错，**不会**四舍五入成最近的 90°。做法条边界的原因是数学而非实现：顶点 (x, y) 转任意非正交角后落不到 nm 网格上（平方格点的旋转群只有四分之一圈加镜像），实测 GF180 的 220 nm 接触孔转 45° 后顶点在 ±156 nm（5 nm 网格外）、边长变成 221 nm。所以"支持任意角度"不是给 `turn` 加参数能解决的，需要的是**在该角度直接画出几何的构造器**。

#### reflect — 镜像并改名
- 函数：`clay.learning.contact_primitives.reflect(row, axis='y', net_map=None)`
- 意义：匹配对的另一半就是同一个构造的镜像，并带上映射后的网名，所以对称是构造出来的。

| 参数 | 含义 |
|---|---|
| `row` | 构造结果 |
| `axis` | `'y'` 关于竖直线左右镜像；`'x'` 上下镜像 |
| `net_map` | 网名映射（通常为对称映射） |

### 3.6 assemble：组装

#### materialize — 组装出图
- 函数：`clay.learning.contact_primitives.materialize(rows, origins, *, pack=None, cell_name='CLAY_CONTACT_GENERATED', merge='envelope')`
- 意义：把各行平移到求解原点，合并标记层，写边界与网名标签（写在端子所在导体层的文本层上）。

| 参数 | 含义 |
|---|---|
| `rows` | 名 → 构造结果（可带阱网域） |
| `origins` | 名 → 原点 |
| `cell_name` | 单元名 |
| `merge` | `envelope`：每种标记层取一个包络框（反相器流程）；`close`：每个阱网域一个阱矩形，注入、阈值标记、npc、rpm 按各自间距规则闭运算、永不覆盖对向扩散（电路流程） |

### 3.7 verify：检查

#### circuit_verify — 通用版图检查
- 函数：`clay.learning.circuit_verify.verify_circuit_layout(layout, circuit, pack, *, routed=False)`
- 意义：两部分：本地 DRC 子集（构造器读过的每条规则：网格、孔尺寸、各种间距 / 宽度 / 包含、阱与注入、阈值标记、电阻与 MIM 规则）；按标签的器件级 LVS（沟道 = poly ∩ 扩散，栅网与源漏网取自焊盘上的标签；按并联归约后比较总 W，电阻比较 W、L、两端，MIM 比较面积与两端）。范围：未布线版图，不是官方 DRC/LVS。

| 参数 | 含义 |
|---|---|
| `layout` | 读回的 GDS 版图 |
| `circuit` | 期望的展开网表 |
| `routed` | 布线版图时不检查"一焊盘一网" |

- 输出：是否通过、每项检查结果、失败项、问题位置示例、器件统计。

#### candidate_verify — 反相器候选检查
- 函数：`clay.learning.contact_primitives.verify(layout, circuit, contract, expected_plans, *, routed=False, pack=None)`
- 意义：反相器候选的本地检查与标签拓扑提取（假定两个有源岛、`2×栅+1` 个焊盘）。

| 参数 | 含义 |
|---|---|
| `layout` | 候选版图 |
| `circuit` / `contract` | 反相器电路与约定 |
| `expected_plans` | 各行期望的接触孔规划 |
| `routed` | 是否为布线版图 |

### 3.8 学习式布线（2026-11-18 新增，`clay/routing/`）

这 14 个原语组成可学习、全局最优、连续空间的布线器（设计见 [ROUTING_LEARNING_DESIGN_zh.md](ROUTING_LEARNING_DESIGN_zh.md)）。共同约束（由 `tests/test_routing_generalization.py` 强制）：

- 源码中不出现任何工艺名、电路类型名、网络名（P1）；
- 不出现物理尺寸字面量，所有尺寸来自 Pack 或调用方（P2）；
- 网络全部改名后生成的几何逐字节不变（P3）；
- GF180 与 SKY130 走完全相同的原语序列，差异只来自 Pack（P4）。

层一律用 Pack 的语义角色（`poly`、`metal1`、`metal2`…）；学到的规则里层还记成"从底往上第几层 / 从顶往下第几层"，所以 GF180 的 Metal1 与 SKY130 的 met1 可以落到同一条规则上。

#### route_rules — 工艺的布线视图
- 函数：`clay.routing.rules.routing_rules(pack) -> RoutingRules`
- 意义：从 `Pack.cut_stack` 推出可布线的导电层及其层级（GF180：poly → metal1；SKY130：poly → li1(`metal1`) → met1(`metal2`)），读出每层最小宽度 / 间距 / 面积、布线器可插入的通孔（大小、间距、上下包含）、器件接触孔周围必须保留的导体包含、场区 poly 到扩散的间距。某层缺规则时该层标为不可布并写明缺哪个键，**从不猜值**。
- 规则键：第一层导体与 poly 沿用 Pack 原有键名（`metal_width`、`poly_space`…），其余按统一模式 `{层}_width`、`{孔}_space`、`{导体}_over_{孔}`。为此 SKY130 Pack 补了 `metal2_*`、`cut2*`、`metal1_over_cut2`、`metal2_over_cut2`，三个 Pack 补了 `poly_to_active`（§7）。

#### route_directions — 每层可走的方向
- 函数：`clay.routing.directions.route_directions(rules, pack, *, allow_diagonal=True) -> Directions`
- 意义：方向 = deck 的角度步长（`Pack.freedom`）∩ 整数网格上画得出的方向（只有 0/45/90/135，Niven 定理）。今天的结论：两种工艺的金属层都是八方向，SKY130 poly 只有正交。某层没有角度规则时只给正交并列入 `open`；步长画不出的角度列入 `grid_infeasible`。方向集合是 Pack 数据：改 Pack 的步长即改方向，原语代码不动（测试锁定）。

| 参数 | 含义 |
|---|---|
| `allow_diagonal` | 关闭斜向（用于对照实验）；关闭时写入 `open` |

#### route_strip — 剥线
- 函数：`clay.routing.strip.route_strip(reference, pack, rules) -> StripResult`
- 意义：把版图分成"器件"和"互连"两部分，只用三个通用概念：落在扩散上的接触孔（保留，连同各导体按包含规则的最小着陆区）、沟道（poly ∩ 扩散，保留并沿栅宽方向加 poly 端头）、所有非导体层（原样保留）。其余导体图形与布线通孔、端口标签全部剥离。只用于训练与剥线重布评估，生成流程不读参考版图。
- 输出：剥后版图、被剥离的几何（按层）、布线边框（单元边界层）、被移除的标签、未在 Pack 中声明的保留图层。

#### route_terminals — 端子
- 函数：`clay.routing.strip.route_terminals(layout, pack, rules, resolve) -> Tuple[Terminal]`
- 意义：保留下来的导体按连通分量分组，每个分量是一个端子（含各层几何）；端子按几何排序，不按名字。

| 参数 | 含义 |
|---|---|
| `resolve` | 分量几何 → 网名。剥线评估时用 `reference_resolver` 读参考版图标签；以后接协调核心时由构造的端子绑定给出 |

#### net_signature — 网络的结构签名
- 函数：`clay.routing.learn.net_signature(circuit) -> {网: (是否端口, {(器件类别, 极性, 端子类别)})}`
- 意义：学习与查规则的唯一索引，不读网名。可互换端子合并（MOS 源/漏 → `sd`、电阻两端 → `ends`，数据见 `semantic.INTERCHANGEABLE_TERMINALS`）；端口标志来自 `.subckt` 端口表。

#### observe_routing — 观测一个参考的布线
- 函数：`clay.routing.learn.observe_routing(reference, strip, terminals, circuit, pack, rules, *, sample_id, params=None)`
- 意义：对每个网络记录（不输出坐标）：连接层（单靠该层布线就能连通至少两个端子的层）、连接宽度（相对最小宽度 / 相对所连端子宽度两种比例，按层级）、边缘主干（横跨边框 ≥90% 并含边框一条边的导体带：层、宽度比例、中心偏移、所在边是否"离端子最近"或"该方向上离端子最近"）、正交比例。

#### fit_routing_library — 拟合布线库
- 函数：`clay.routing.learn.fit_routing_library(observations, *, params=None) -> dict`
- 意义：按完整签名、按签名分量各存一份规则。每个量按声明顺序尝试多种表示（层：从顶往下 → 从底往上 → 角色名；宽度：相对端子 → 相对最小宽度；主干边：最近边 → 某方向最近边），取第一个让全部证据一致的表示；都不一致则"自由"（宽度取观测到的最小比例并标"不一致"）。不取平均。

| 参数 | 含义 |
|---|---|
| `params.numeric_tolerance` | 数值视为一致的相对误差（0.15） |
| `params.trunk_span` | 认作边缘主干需横跨边框的比例（0.9） |

- **三个反相器训练样本（GF180 7t inv_1、GF180 9t inv_2、SKY130 hd inv_1）学到的规则**（`data/routing_learning_v1/library.json`）：

| 网络签名（端口） | 连接层 | 宽度 | 主干 |
|---|---|---|---|
| {N 源漏}、{P 源漏} | 从顶往下第 0 层 | 等于端子宽度 | **必须**；从顶往下第 0 层（GF180 Metal1、SKY130 met1）；水平方向离端子最近的边；以边为中心；宽度比例两工艺不一致，取较小的 2.6 |
| {N 源漏, P 源漏} | 从底往上第 1 层（GF180 Metal1、SKY130 li1） | 约等于端子宽度（不一致） | 无 |
| {N 栅, P 栅} | 自由（GF180 用 poly + Metal1 引脚，SKY130 只用 poly） | 约等于端子宽度（不一致） | 无 |

全部签名上参考布线都是纯正交（`direction: axis`），所以斜线只在最后的风格层被计为偏离（§3.8 route_solve 第 4 层），线长更短时仍会使用。

#### routing_policy — 一个网络的规则（engine）
- 函数：`clay.routing.learn.routing_policy(library, signature, rules, terminal_widths) -> NetPolicy`
- 意义：签名见过就用完整签名的规则；没见过就组合各分量的规则，只有每个分量都有证据且一致才生效，否则该项自由并写入 `open`。输出偏好层、各层线宽、主干要求、是否偏好正交。只在 `route_specs` 内调用。

#### route_trunk_candidates — 主干候选（engine）
- 函数：`clay.routing.graph.trunk_candidates(spec_trunk, terms, rules, frame, grid)`
- 意义：按学到的层、边、宽度、偏移生成主干矩形；学到"自由"的维度全部枚举交给求解器。只在 `route_specs` 内调用。

#### route_specs — 每个网络要做到什么
- 函数：`clay.routing.specs.route_specs(library, terminals, circuit, rules, *, frame, use_style=True) -> [NetSpec]`
- 意义：对每个有端子的网络调用 `routing_policy` 与 `route_trunk_candidates`，按本例端子宽度算出线宽。网络按几何排序。

| 参数 | 含义 |
|---|---|
| `frame` | 布线边框 |
| `use_style` | 关闭学到的层偏好与方向偏好（对照实验）；宽度与主干仍按库 |

#### route_graph — 连续空间候选图
- 函数：`clay.routing.graph.route_graph(layout, specs, all_terminals, rules, directions, pack, frame) -> RouteGraph`
- 意义：坐标不是均匀网格，而是来自几何特征：端子接入点、各网禁布区边界外偏半线宽、边框内缩、主干中线。每层在这些坐标上建正交边；允许斜向的层再从每个接入点发 45° 射线，与坐标线及同网其他射线相交处成为节点（层级 1）。上下层重合节点之间建通孔边。每个候选按网络各自判合法：去掉本网导体后不得碰到该网禁布区（异网导体按欧氏间距用八边形膨胀；poly 层另加：不得与扩散形成新沟道、与非本栅扩散保持 `poly_to_active`、与扩散上的接触孔保持 `cut_to_gate`）。斜线本体按要求宽度画、两端加八边形帽；允许斜向的层上正交线宽取斜线网格取整后的宽度（最多宽两个网格），保证斜线不会从正交线侧面伸出。接入点要求线端与端子方正重叠，避免拐角处颈缩。
- 建图阶段只剔除**无条件**非法的候选（异网障碍、栅极禁区、越出边框）。跨网间距与同网凹口都是选择的联合性质，一概交给 `route_solve` 精确判定。
- 每个网的候选只在自己的窗口内生成：端子与主干候选的包围框外扩 `params.window_margin_pitches`（默认 2）个本网线距。证书范围因此是"在每网窗口内最优"。

| 参数 | 含义 |
|---|---|
| `params.window_margin_pitches` | 窗口外扩的本网线距个数 |
| `params.self_spacing` | 逐候选的同网预过滤（默认关）。打开可缩小图，但会砍掉可能属于合法解的候选，证书 `scope.self_spacing` 会写明范围收窄。见布线设计文档 §5.6.2 |

#### route_solve — 全局最优求解
- 函数：`clay.routing.solve.route_solve(graph, rules, *, params=None) -> Solution`
- 意义：所有网络放进一个混合整数规划（SCIP，单线程，相对间隙 0）。`x` 选候选、`y` 选主干；每个候选再拆成正反两个方向变量（有方向写法：`x = 正向 + 反向`，每个节点最多一条选中入边），连通性用多商品流（根端子向每个其他端子和选中的主干各送一单位，只能沿选中方向走）；跨网间距与通孔间距惰性生成（先把解精简成树，只检查真正要画的候选，冲突对加入后重解，直到无冲突，不损失最优性）；求解前用最短路贪心树作初始解。目标按声明顺序逐层优化：① 非偏好层线长（没有代价项时跳过）→ ② `V × 总线长 + 通孔数`（严格等价于先线长后通孔）→ ③ 偏好正交网络上的斜线长 → ④（可选）内容序平局。证书记录每层值、最优界、是否证明最优、候选图与窗口范围、方向集合、贴到窗口边界的网络；任何一层超时则状态为 FEASIBLE，不声称最优。性能与写法选择见布线设计文档 §5.6。
- **同网合法性**（2026-11-20）：成对互斥只用于**跨网**间距与任意两个通孔。同网不是成对性质——两块金属被第三块填住就合成一块，反之窄 U 形回绕会形成 deck 会报的凹口（notch）。所以同网按**合并几何**判定：把该网选中的候选（每个候选的多个多边形先合并）与其输入金属合并，跑 `space_check`，与剥线输入的违例计数比增量——与 `route_verify` 同一判据；每个新增凹口加一条**覆盖约束** `x_a + x_b − Σ_{f∈fill} x_f ≤ 1`（`fill` = 能在两块之间走廊里画出金属的候选；为空时退化为 `x_a + x_b ≤ 1`）。这是精确编码，不会砍掉"用第三块填上"的合法解。证书里 `same_net_covers` 记录条数、每轮 `notches` 记录新增凹口数。详见布线设计文档 §5.6.2。
- **约减代价固定**：求线长层之前，先解当前模型的 LP 松弛（HiGHS），按对偶弱对偶公式重算下界（不依赖 LP 求解器容差），把"固定为 1 后必然劣于已知无冲突解"的二元变量固定为 0。已知解优先取前一层的，否则用贪心构造。证书 `model_fixing` 记录固定个数、界间隙、LP 用时。

| 参数 | 含义 |
|---|---|
| `params.seconds_per_solve` | 每次求解时限（120） |
| `params.max_lazy_rounds` | 惰性冲突轮数上限（200） |
| `params.backend` | MIP 后端（SCIP） |
| `params.reduced_cost_fixing` | 是否在求线长层前做约减代价固定（默认开） |
| `params.fixing_margin` | 固定判据的整数裕度（0.5，吸收 LP 数值误差） |
| `params.content_tie_break` | 是否加第 ④ 层内容序平局（默认关；确定性由单线程保证，不依赖它） |
| `params.verbose` | 打印 SCIP 日志与每轮进度 |

#### route_materialize — 落地成版图
- 函数：`clay.routing.materialize.route_materialize(layout, graph, solution, rules, pack, ports=()) -> (Layout, report)`
- 意义：把选中的导线、通孔焊盘、通孔、主干画出来与端子合并；再做三种通用修补，每种都先检查不侵犯本网禁布区和其他网新画的导线（否则跳过并记录）：同网凹口填充（deck 的间距检查不分网）、颈缩填充、最小面积扩展。端口标签打在主干或端子的标注层上。报告记录每段几何来自哪条候选边。

#### route_verify — 独立核验
- 函数：`clay.routing.verify.route_verify(routed, unrouted, terminals, rules, directions, pack, frame, trunk_boxes=())`
- 意义：只读最终版图、剥后版图、Pack，不信任布线器的内部记录。检查：每网连通、无短路、无悬空导体；各导体层宽度 / 间距（欧氏、合并后、不分网）/ 面积；布线通孔尺寸、间距、上下包含；无新沟道、新 poly 与非本栅扩散的间距；非布线图层与输入完全一致；新边方向都在允许集合内；顶点在网格上；不出边框（主干除外）。范围：Pack 的本地规则子集，不是官方 DRC/LVS。

---

## 4. 反相器与比较器分别用到哪些原语

### 4.1 比较器（默认生成：协调核心）

入口 `python -m clay.learning.circuit_cli generate`（`clay.decision.core.coordinate`）。箭头左侧先执行；括号内是在另一个原语内部调用的原语。

```
训练   netlist_flatten → netlist_symmetry → observe_layout → fit_library

生成   netlist_flatten
       → netlist_symmetries → symmetry_classes                       对称映射层：每类一个选项
       → fold_candidates × 对称轴（学到的 mirror_axis）                构造层的候选
       → finger_fold → diffusion_chains → placement_units
       → 按器件类别分派构造器：
            mos / cap_var : build_chain_row（contact_policy_plan → build_row）
            res_poly      : build_poly_resistor
            cap_mim       : build_mim_cap
            mos 的替代    : build_learned_pattern_row（contact_plan → build_row），仅 --inverter-library
       → block_space（angle_verdicts、orientations、enforce、reflect、turn、block_from_row）
         mirror_space（enforce、reflect、block_from_row）
       → placement_intents → place_blocks（solve_blocks）
       → materialize（merge='close'）→ circuit_verify
       检查失败时：verification_feedback 决定是否用固定朝向重算
       --fixed-symmetry 时：netlist_symmetry 代替 netlist_symmetries + symmetry_classes
```

共 31 个原语：netlist_flatten、netlist_symmetry、netlist_symmetries、symmetry_classes、observe_layout、fit_library、fold_candidates、finger_fold、diffusion_chains、placement_units、build_chain_row、contact_policy_plan、build_row、build_poly_resistor、build_mim_cap、block_space、angle_verdicts、orientations、enforce、reflect、turn、block_from_row、mirror_space、placement_intents、place_blocks、solve_blocks、materialize、circuit_verify、verification_feedback，以及只在提供反相器库时使用的 build_learned_pattern_row、contact_plan。

### 4.2 反相器（专用流程，用反相器训练出的库）

入口 `python -m clay.learning`（按库的 schema 选择流程）。保留用于复现以往的反相器实验。

```
接触库流程（clay.contact-primitive）
       inverter_topology → contact_plan → build_row
       → angle_verdicts → enforce                           每行每个角度的结论与门控
       → [去重模式] canonical_rotation_class、two_box_lower_bound（16 方案 → 4 类，按下界最优优先）
       → turn → solve_area → materialize（merge='envelope'）→ candidate_verify
几何流程（clay.geometry-primitive）   在接触库流程中加入 geometry_plan
关系流程（clay.relation-primitive）   在几何流程上加入 relation_features、relation_rank
布线实验                              orthogonal_path
结构 / 构造搜索流程                   build_contacted_device → solve_area（组装与检查用该流程自己的函数）
```

共 16 个原语：inverter_topology、contact_plan、geometry_plan、build_row、angle_verdicts、enforce、canonical_rotation_class、two_box_lower_bound、turn、solve_area、materialize、candidate_verify、relation_features、relation_rank、orthogonal_path、build_contacted_device。

### 4.3 反相器经通用流程（协调核心）

反相器网表也可以走 §4.1 的生成流程（同一组原语），提供反相器库（`--inverter-library`）时，反相器训练出的构造 `build_learned_pattern_row` 作为替代构造参与比较；电路库没有该工艺的接触孔策略时（如 GF180 5 V），由它生成。阶段实测（`data/primitive_generalization/stage_report_20261115/`）：GF180 `mcu9t5v0__inv_1` 14.73 µm²（替代构造胜出）、SKY130 `hd__inv_1` 3.50 µm²（默认构造胜出），均为范围内已证明最优。

### 4.4 共用与独有

| | 原语 | 数量 |
|---|---|---|
| 两者共用 | build_row、angle_verdicts、enforce、turn、materialize（模式不同）、contact_plan（比较器侧只在提供反相器库时） | 6 |
| 只有比较器 / 通用流程 | netlist_flatten、netlist_symmetry、netlist_symmetries、symmetry_classes、observe_layout、fit_library、fold_candidates、finger_fold、diffusion_chains、contact_policy_plan、placement_units、placement_intents、build_chain_row、build_learned_pattern_row、build_poly_resistor、build_mim_cap、orientations、block_space、mirror_space、block_from_row、reflect、place_blocks、solve_blocks、circuit_verify、verification_feedback | 25 |
| 只有反相器专用流程 | inverter_topology、geometry_plan、canonical_rotation_class、two_box_lower_bound、solve_area、candidate_verify、relation_features、relation_rank、build_contacted_device、orthogonal_path | 10 |
| 都不用 | symmetry_placement（`.clay` 注释流程） | 1 |

### 4.5 学习式布线（反相器剥线重布；比较器计划复用同一组原语）

入口 `python -m clay.routing.experiment --out data/routing_learning_v1`（实验入口，不是原语）。训练集：GF180 7t inv_1、GF180 9t inv_2、SKY130 hd inv_1；测试集：GF180 9t inv_1。测试参考只作为剥线输入和输出端评估，其布线从不进入布线器。

```
训练   route_rules → route_strip → route_terminals → net_signature → observe_routing   （每个训练样本，两种工艺序列相同）
       → fit_routing_library

重布   route_rules → route_directions
       → route_strip → route_terminals
       → route_specs（routing_policy、route_trunk_candidates）
       → route_graph → route_solve → route_materialize → route_verify
```

共 14 个原语。与 §4.1–4.4 的流程目前没有共用原语：协调核心定稿版图到 `route_terminals` 的接入（由构造结果给出端子与网名，替代 `reference_resolver`）属于端到端阶段，尚未实现。比较器阶段要求原样复用这 14 个原语，只允许新增（例如对称布线约束种类），不允许修改。

阶段实测（2026-11-19，提速改动 A–E 之后的数字见布线设计文档 §5.6；下表为 2026-11-18 首次实测，留作对照）：

| 样本 | 方向 | 状态 | 总线长 | 核验 | 用时 |
|---|---|---|---|---|---|
| 测试 GF180 9t inv_1 | 正交 | OPTIMAL | 8805 nm | 通过 | 5 s |
| 测试 GF180 9t inv_1 | 八方向 | OPTIMAL | 8641 nm（斜线比正交短 1.9%） | 通过 | 220 s |
| 训练 GF180 7t inv_1（自洽） | 正交 | OPTIMAL | 7630 nm | 通过 | 4 s |
| 训练 GF180 9t inv_2（自洽） | 正交 | FEASIBLE（线长层 300 s 未证最优，间隙 5.5%） | 16100 nm | 通过 | 970 s |
| 训练 SKY130 hd inv_1（自洽） | 正交 | OPTIMAL | 4475 nm，2 个通孔 | 通过 | 18 s |

完整实验（全部样本、开斜向、学习风格与关闭风格两种）结果写在 `data/routing_learning_v1/summary.json`。核验是本地规则子集，官方 DRC/LVS 尚未运行。

---

## 5. 入口与工具（不是原语）

| 入口 | 作用 |
|---|---|
| `clay.decision.core.coordinate` | 协调核心：对称映射层 × 构造层 × 朝向位置层，按声明的目标（area / matched_pairs / centring）选择，写 GDS 与全局证书 |
| `python -m clay.learning.circuit_cli train / generate / evaluate` | 训练只读训练集；生成只读网表与冻结库（走协调核心；`--objective`、`--fixed-symmetry`、`--inverter-library`）；评估在生成之后读参考 |
| `clay.learning.circuit_generate.generate_circuit` | 固定对称映射的生成（协调核心的固定映射模式与它逐字节相同） |
| `python -m clay.learning` | 反相器专用流程 |
| `scripts/run_comparator_learning.py` | 比较器一键流程：训练 → 审计隔离的生成 → 训练风格基线 → 评估 → 报告 |
| `scripts/compare_layouts.py` | 生成版图与参考版图同比例并排对比图 |
| `scripts/run_symmetry_objectives.py` | 对称映射在不同目标下的胜者（生成核心文档 §5.2） |
| `scripts/regression_fingerprint.py` | 回归指纹（重构是否改变输出） |
| `scripts/run_stage_report.py` | 阶段成果：两个比较器与两个反相器（训练 / 测试、GF180 / SKY130）的生成与参考版图对比 |
| `scripts/profile_generation.py` | 生成耗时分解（求面积阶段 / 确定性阶段 / 其他） |
| `python -m primitive_verify run <原语>` | 单原语验证 |
| `python -m clay.routing.experiment` | 学习式布线实验：训练布线库 → 剥线重布测试与训练样本（学习风格 / 关闭风格）→ 写 GDS、`library.json`、`summary.json` |

## 6. 能力签名一览（由 `primitive_classification.yaml` 生成）

| 条目 | 必需输入 | 可选输入 | 输出 | 组合方式 | 适用器件 | 确定性 | 代价 |
|---|---|---|---|---|---|---|---|
| `inverter_topology` | NetlistText | — | InverterCircuit | step | mos | pure | cheap |
| `relation_features` | Layout, InverterCircuit, Contract | Pack | RelationFeatures | step | mos | unaudited | cheap |
| `relation_rank` | Trials, RelationFeatures, RelationLibrary | Pack | Ranking | step | mos | unaudited（改写 trials） | cheap |
| `contact_plan` | InverterLibrary | Pack | ContactPlan | step | mos | pure | cheap |
| `geometry_plan` | InverterLibrary | Pack | GeometryPlan | step | mos | unaudited | cheap |
| `solve_area` | Rectangles, Boxes, Clearances | — | Placement | step | — | pure | solver |
| `build_row` | Devices, ContactPlan, Pack | GeometryPlan | Row | step | mos, cap_var | pure | cheap |
| `build_contacted_device` | Device | Pack | DeviceCell | step | mos | unaudited | cheap |
| `orthogonal_path` | Region | Pack | Path | step | — | unaudited | cheap |
| `turn` | Row | — | Row | step | — | pure | cheap |
| `materialize` | Rows, Placement | Pack | Layout | step | — | pure | cheap |
| `candidate_verify` | Layout, InverterCircuit, Contract, ContactPlans | Pack | Verification | step | mos | pure | cheap |
| `symmetry_placement` | SolverConstraints, Devices | — | Placement | step | mos | unaudited | solver |
| `netlist_flatten` | NetlistText, Pack | — | Netlist | step | — | pure | cheap |
| `netlist_symmetry` | Netlist | — | Symmetry | step | — | pure | cheap |
| `finger_fold` | Netlist, Pack | — | Fingers | step | mos, cap_var | pure | cheap |
| `diffusion_chains` | Fingers | Symmetry | Chains | step | mos, cap_var | pure | cheap |
| `contact_policy_plan` | ContactPolicy | Pack | ContactPlan | step | mos, cap_var | pure | cheap |
| `reflect` | Row | — | Row | step | — | pure | cheap |
| `build_poly_resistor` | Device, ResistorStyle, Pack | — | Row | dispatch | res_poly | pure | cheap |
| `build_mim_cap` | Device, Pack | — | Row | dispatch | cap_mim | pure | cheap |
| `block_from_row` | Row, Pack | — | Block | step | — | pure | cheap |
| `solve_blocks` | Blocks | — | Placement | engine | — | pure | solver |
| `circuit_verify` | Layout, Netlist, Pack | — | Verification | step | — | pure | cheap |
| `observe_layout` | ReferenceLayout, Netlist, Pack | — | Observation | step | — | unaudited | cheap |
| `fit_library` | Observations, Packs | — | Library | step | — | unaudited | cheap |
| `angle_verdicts` | Pack | — | AngleVerdicts | step | — | pure | cheap |
| `enforce` | Freedom, ActionPlan | — | Admission | step | — | pure | cheap |
| `orientations` | PartFreedom | — | Orientations | step | — | pure | cheap |
| `fold_candidates` | Netlist, Pack | — | FoldTargets | step | mos, cap_var | pure | cheap |
| `canonical_rotation_class` | ActionPlan | — | RotationClass | step | — | pure | cheap |
| `two_box_lower_bound` | Boxes, Rectangles | — | Bound | step | — | pure | cheap |
| `block_space` | Row, Pack | — | BlockSpace | step | — | pure | cheap |
| `mirror_space` | BlockSpace, Pack | — | BlockSpace | step | — | pure | cheap |
| `build_chain_row` | Chain, ContactPolicy, Pack | — | Row | dispatch | mos, cap_var | pure | cheap |
| `build_learned_pattern_row` | Chain, InverterLibrary, Pack | — | Row | alternative | mos | pure | cheap |
| `placement_units` | Chains, Netlist | Symmetry | Units | step | — | pure | cheap |
| `placement_intents` | Units | Symmetry | PlacementIntents | step | — | pure | cheap |
| `place_blocks` | BlockSpaces, PlacementIntents, Pack | — | Placement | step | — | pure | solver |
| `netlist_symmetries` | Netlist | — | Symmetries | step | — | pure | cheap |
| `symmetry_classes` | Netlist, Symmetries | — | SymmetryClasses | step | — | pure | cheap |
| `verification_feedback` | Candidate | — | Feedback | step | — | pure | cheap |
| `route_rules` | Pack | — | RoutingRules | step | — | unaudited | cheap |
| `route_directions` | RoutingRules, Pack | — | WireDirections | step | — | unaudited | cheap |
| `route_strip` | ReferenceLayout, Pack, RoutingRules | — | StripResult | step | — | unaudited | cheap |
| `route_terminals` | Layout, Pack, RoutingRules | — | Terminals | step | — | unaudited | cheap |
| `net_signature` | Netlist | — | NetSignatures | step | — | unaudited | cheap |
| `observe_routing` | ReferenceLayout, StripResult, Terminals, Netlist, Pack, RoutingRules | — | RoutingObservation | step | — | unaudited | cheap |
| `fit_routing_library` | RoutingObservations | — | RoutingLibrary | step | — | unaudited | cheap |
| `routing_policy` | RoutingLibrary, RoutingRules | — | NetPolicy | engine | — | unaudited | cheap |
| `route_trunk_candidates` | RoutingRules | — | TrunkCandidates | engine | — | unaudited | cheap |
| `route_specs` | RoutingLibrary, Terminals, Netlist, RoutingRules | — | NetSpecs | step | — | unaudited | cheap |
| `route_graph` | Layout, NetSpecs, Terminals, RoutingRules, WireDirections, Pack | — | RouteGraph | step | — | unaudited | cheap |
| `route_solve` | RouteGraph, RoutingRules | — | RouteSolution | step | — | unaudited | solver |
| `route_materialize` | Layout, RouteGraph, RouteSolution, RoutingRules, Pack | — | RoutedLayout | step | — | unaudited | cheap |
| `route_verify` | RoutedLayout, Layout, Terminals, RoutingRules, WireDirections, Pack | — | RouteVerification | step | — | unaudited | cheap |

布线原语都标 `unaudited`：它们尚未进入确定性证明的审计范围（生成核心文档 §8.3）。求解单线程、网络按几何排序，设计上是确定的；端到端测试 P3（网络全部改名后几何逐字节相同）、P4（两种工艺同一原语序列）与测试样本剥线重布核验已于 2026-11-19 通过（`CLAY_ROUTING_E2E=1`）。布线类型（`RoutingRules`、`RouteGraph` 等）与原有类型分开命名，所以检索索引不会把布线结果当成放置流程的 `Layout` / `Verification`。

## 7. 与原语相关的 PDK pack 字段

| 字段 | 含义 |
|---|---|
| `layers` / 语义规则键 | 图层角色与规则数值；原语只读语义键（分类表 `pdk_binding`） |
| `device_classes` | 模型 → 器件类别（mos / res_poly / cap_mim / cap_var）；全局表 `DEVICE_CLASS_TERMINALS` 给出各类别的端子顺序 |
| `chain_construction` | 非普通 MOS 的类别如何画成接触扩散链（扩散图层角色、规则替换）；目前只有 SKY130 的 `cap_var` |
| `freedom` / `exact_edges` / `orthogonal_isometries` / `direction_capability` | 角度步长、精确边长、正交等距声明、引擎可执行方向；供 `angle_verdicts`、`enforce` 使用 |
| `frame_layers` | 只构成边框的图层，测材料面积时排除 |
| `cut_stack` | 每种孔连接哪些导体；布线原语据此推出导体层级与可插入的通孔（`route_rules`） |
| `annotation_layers` / `boundary_layer` | 端口标签层、单元边界层；`route_materialize` 打标签、`route_strip` 取布线边框 |
| `semantic.INTERCHANGEABLE_TERMINALS`（全局表） | 可互换端子（MOS 源/漏、电阻两端）；`net_signature` 使用。2026-11-18 新增 |
| 布线规则键（2026-11-18 新增） | 三个 Pack 加 `poly_to_active`（GF180 取 PL.5a 对应电压版本，SKY130 取 poly.4）；SKY130 加 `metal2_width/space/area`（m1.1/m1.2/m1.6）、`cut2`、`cut2_space`（ct.1/ct.2）、`metal1_over_cut2`（0，约定值）、`metal2_over_cut2`（m1.5）。只加新键，不改已有键，已有原语行为不变 |

## 8. 待人工复核

18 个验证条目处于待复核状态（§2"验证"列）：比较器学习中由 AI 登记的 16 个，以及 `build_contacted_device`、`orthogonal_path` 的验证提交。它们的单原语验证均为 PASS，每个 spec 的 `derivation` 写明了规则号与推导公式，需要人复核预期值后改为冻结。另有 30 个原语（§2 中"未登记"，含 14 个新布线原语）还没有单原语验证。

布线原语需要人复核的内容：`library.json` 中学到的规则（§3.8 表）是否符合预期；`route_strip` 对器件 / 互连的划分；`route_graph` 的 poly 禁区规则；`route_materialize` 的三种修补是否可以接受；`route_verify` 的检查范围。

## 9. 放置约束种类（`solve_blocks` 的插件，不是原语）

`solve_blocks` 能施加的电路约束登记在 `clay/layout/synth/placement_constraints.py`。新增一种约束 = 登记一个插件，不改求解器；数据经 `placement_intents` 输出的 `extra` 由 `place_blocks` 传入。阱与间距规则是工艺规则，不在这里。

| 种类 | 阶段 | 目标级 | 数据 | 含义 |
|---|---|---|---|---|
| `ties` | variants | 硬约束 | 块对 `(a, b)` | 镜像对的两块选相同的变体编号 |
| `mirror_pairs` | placement | 硬约束 | 块对 `(a, b)` | 两块中心关于对称轴对称，并在轴的垂直方向对齐 |
| `centred` | placement | 硬约束 | 块名 | 块中心在对称轴上 |
| `soft_centred` | placement | 软代价（第 2 级） | 块名 | 块中心到对称轴的距离，在面积之后最小化 |
| `max_separation` | placement | 硬约束 | `(a, b, 距离 nm)` | 两块外框中心的曼哈顿距离不超过给定值（匹配距离上限）；尚无流程使用 |

内置的前四种按上表顺序、在求解器原来的位置添加；其他种类在其后按名字排序添加。没有数据的种类不添加任何东西，所以登记新种类不会改变已有结果。
