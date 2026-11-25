# 动作决策：图层自由度、动作空间与朝向位置联合求解

> 本文说明动作决策的现状：每个块可以做哪些动作（旋转、镜像、平移）、这些动作的合法性如何从 PDK 得出、学到的关系如何约束动作、朝向与位置如何一起求解，以及结果的证书。
> PDK 解析见 [PDK_PARSER_WORKFLOW_zh.md](PDK_PARSER_WORKFLOW_zh.md)；动作决策在整个生成流程中的位置（构造选项、对称映射、目标、加速）见生成核心文档 [GLOBAL_COORDINATOR_DESIGN_zh.md](GLOBAL_COORDINATOR_DESIGN_zh.md)；各原语的签名见 [PRIMITIVE_CATALOG_zh.md](PRIMITIVE_CATALOG_zh.md)。
> 依据 2026-11-15 的代码与实测。命令在 `clay_code/` 下执行。

---

## 1. 作用与边界

动作决策回答的问题是：**一个已经构造好的块，在版图中以什么朝向、放在哪里**。它由一组登记在分类表中的原语组成，不是独立的一层：

| 负责 | 不负责（由生成核心负责） |
|---|---|
| 每个块每个候选角度的合法性结论 | 选哪个对称映射 |
| 每个块的合法朝向变体、变体之间的绑定 | 折叠目标、对称轴、构造器变体的选择 |
| 学到的关系翻译成的约束 | 目标的定义与候选间比较 |
| 朝向 + 位置的 CP-SAT 联合求解，给出面积与可靠下界 | 全局证书 |

"最优"的含义因此是：**在一个构造候选内，对全部可执行朝向组合 × 全部位置，面积可证明最优**。跨构造候选、跨对称映射的最优由生成核心在此基础上给出。

```
 Pack.freedom / exact_edges / orthogonal_isometries / direction_capability / capability
                                       │
                                       ▼  angle_verdicts：每个候选角度一个结论
                     部件（= 块实际画出的图层）的合法动作
                                       │
   学到的关系（relations 登记表）──────────┤
                                       ▼  block_space / mirror_space：每个合法朝向一个变体（经 enforce 门控）
                     每块若干变体 + 变体绑定 + 约束意图
                                       │
                                       ▼  place_blocks → solve_blocks：朝向布尔变量 + 位置，CP-SAT 联合求解
                     面积 + 下界 + 状态 + 选中的变体
```

| 部分 | 代码 | 分类表条目（stage / decision_role） |
|---|---|---|
| 角度结论 | [clay/learning/freedom.py](../clay/learning/freedom.py) `angle_verdicts` | `angle_verdicts`（decide / admissibility） |
| 门控 | `freedom.enforce` | `enforce`（decide / admissibility） |
| 正交朝向枚举 | `freedom.orientations` | `orientations`（decide / options，complete） |
| 块的动作空间 | [clay/learning/action_space.py](../clay/learning/action_space.py) `block_space`、`mirror_space` | plan / — |
| 关系翻译 | [clay/learning/relations.py](../clay/learning/relations.py) | 登记表，不是原语 |
| 约束意图 | [clay/learning/circuit_construct.py](../clay/learning/circuit_construct.py) `placement_units`、`placement_intents` | plan / — |
| 联合求解 | `circuit_construct.place_blocks` → [clay/layout/synth/area_placement.py](../clay/layout/synth/area_placement.py) `solve_blocks` | `place_blocks`（place）；`solve_blocks`（engine） |
| 验证失败的处理 | `circuit_construct.verification_feedback` | decide / feedback |
| 反相器专用决策器 | [clay/learning/actions.py](../clay/learning/actions.py) | `canonical_rotation_class`（reduce）、`two_box_lower_bound`（bound）；搜索循环 `search` 不登记 |

---

## 2. 自由度：从 PDK 得到每个角度的结论

### 2.1 图层步长

PDK 解析器从 deck 的 `with_angle` 规则读出每个图层的边角步长，写入 `Pack.freedom`（同一角色多条规则取最小公倍数）：

| 图层角色 | GF180 5V | GF180 3.3V | SKY130 1.8V（来源 token） |
|---|---|---|---|
| `active` | 45 | 45 | **90**（diff） |
| `tap` | — | — | **90**（tap） |
| `poly` | 45 | 45 | **90**（poly） |
| `contact` | 45 | 45 | **90**（licon） |
| `metal1` | 45 | 45 | 45（li） |
| `cut2` / `metal2` | — | — | **90**（ct）/ 45（m1） |
| `cut3` / `metal3` / `metal4` | — | — | **90**（via3）/ 45 / 45 |
| `nwell`、`nplus`、`pplus` | 45 | 45 | 45 |
| `dualgate`、`v5_xtor` | 45 | — | — |
| `hvtp`、`lvtn`、`npc`、`rpm` | — | — | 45 |
| `polyres`、`capm` | — | — | **无条目（deck 没有角度规则）** |

`step_deg` 约束的是**图形的边**能取的方向：90 只能画水平、竖直边；45 允许 45° 斜边。

### 2.2 部件

部件 = 一个块**实际画出且非空的图层**（`action_space.drawn_roles`；反相器 `row_layer_roles`）。整个部件一起转，只要有一层不允许某个角度，部件就不能取这个角度。没有角度规则的图层同样计入部件，它的"未知"不会被忽略。

### 2.3 每个候选角度的结论（`angle_verdicts`）

**候选角度**是"该工艺所有图层步长与 90 的最大公约数"的整数倍。工艺中只要出现过 45° 步长，每个部件都列出全部 8 个角度，即使它自己的图层只允许 90°，没有角度在判断前被删掉。

| 顺序 | 结论 | 条件 | 是否结案 |
|---|---|---|---|
| 正交角（0/90/180/270） | `executable` | 有正交等距声明，或每层都有角度规则且步长整除 90；并且该方向在 `direction_capability` 的构造、放置、检查三项中都具备 | 是（进入搜索） |
| ① | `deck_forbidden` | 某层的角度规则不允许这个角度 | 是（deck 规则证明） |
| ② | `deck_infeasible` | 某层有精确边长，而网格上不存在这个角度、这个长度的边（§2.4） | 是（数学证明） |
| ③ | `unknown` | 某层没有角度规则，既没有允许也无法否定 | **否** |
| ④ | `not_executable` | 规则允许、也无法否定，但引擎不具备该方向的能力 | **否** |
| ⑤ | `executable` | 以上都不是 | 是 |

**实测结论**：

| 部件 | 0/90/180/270 | 45/135/225/315 |
|---|---|---|
| GF180 MOS 链（active、poly、contact、metal1、阱、注入、标记层） | executable | **deck_infeasible**（CO.1 + 5 nm 网格） |
| SKY130 MOS 链、变容管、电阻 | executable | **deck_forbidden**（diff/poly/licon 只允许 90） |
| SKY130 MIM（metal3、capm、via3、metal4） | executable（capm 无角度规则，正交动作由等距声明给出） | **deck_forbidden**（via3 只允许 90） |
| 只含 nwell 或只含 metal1 的部件（目前不作为放置块出现） | executable | **not_executable**（未结案） |

未结案的角度会写入证书的 `open_angles`，并让证书不声称最优。

### 2.4 GF180 的 45° 为什么被严格证明不可行

deck 有两条规则：`contact.edges.without_length(0.22.um)`（CO.1，接触孔每条边正好 0.22 µm）与 `contact.ongrid(0.005)`（顶点在 5 nm 网格上）。

1. 两个端点在 5 nm 网格上的边，横纵跨度 (5a, 5b)，长度 5·√(a² + b²)，方向 atan2(b, a)。
2. 45° 要求 a = b，长度 5a·√2。√2 是无理数，5a·√2 永不等于 220（最接近 219.20 与 226.27）。
3. 其他非正交整数度角的正切是无理数（Niven 定理），网格上画不出这个方向的边。
4. 所以不存在斜放的合法接触孔；任何含接触孔的部件整体斜放后接触孔也是斜的，45°/135°/225°/315° 没有合法实现。

`grid_edge(长度, 网格, 角度)` 用整数枚举做这个判断，不针对 GF180 写死，任何有精确尺寸规则的工艺都适用。

**证明的范围**：它排除的是"刚性部件整体斜放"。"接触孔正放、有源区和栅斜画"是一种新的**构造方式**，不是旋转动作，不在此证明范围内；目前没有这种构造器，证书 `scope` 写明"未包含斜向构造"。SKY130 的有源区与多晶只允许 90°，不存在这种构造。

### 2.5 三类动作的合法性

| 动作 | 合法条件 | 现状 |
|---|---|---|
| 旋转 θ | θ 的结论为 `executable` | 三个 pack 都是正交 4 个可执行 |
| 镜像（x / y / xy） | 工艺有正交等距声明 | GF180、SKY130 均可 |
| 平移 | 在网格上 | CP-SAT 在网格单位上求解 |

一个块的全部正交朝向是 8 个：4 个旋转 × 是否镜像（`freedom.orientations`）。自由度只回答"哪些朝向可以考虑"，不回答"哪个更好"；好坏完全由几何与放置求解决定。

`enforce` 只做拒绝：动作不合法就抛出异常，从不把非法动作"修正"成合法动作。所有变体在变换前都过 `enforce`。

### 2.6 为什么"支持所有角度"不能靠旋转实现

这一条决定了泛化到新 PDK 时该补什么，所以单独说明。

**刚性旋转只可能取 90° 的倍数，与 PDK 无关。** 顶点 (x, y) 转 θ 后是 (x·cosθ − y·sinθ, x·sinθ + y·cosθ)。要在整数 nm 网格上闭合，就必须有网格步长 g 使坐标仍是 g 的整数倍：

- θ 是 90° 的倍数：cos、sin ∈ {0, ±1}，坐标仍是整数，成立；
- θ = 45°：坐标是 (x−y)/√2 与 (x+y)/√2。要等于 5k，需要 x−y = 5k√2，√2 无理 ⇒ 只可能 k = 0，即所有顶点共线（退化形状），不成立；
- 其他整数度角：正切是无理数（Niven 定理），网格上画不出该方向的边，同样不成立。

所以平方格点的旋转群只有 4 个四分之一圈加 4 个镜像，共 8 个元素。这是格点的性质，不是 KLayout 的限制，也不是某个工艺的规则——**换任何 PDK 都一样**。

**实测**（GF180 的接触孔，CO.1 要求每条边正好 220 nm，5 nm 网格）：

| | 顶点 | 边长 |
|---|---|---|
| 原向 | (0,0)(220,0)(220,220)(0,220) | 220 nm，全部在 5 nm 网格上 |
| 转 45° | (0,0)(−156,156)(0,311)(156,156) | 220/221 nm，**没有顶点在网格上** |

所以"给 `turn` 加一个任意角度参数"这条路在数学上封死，而不是等待实现。

**那"可执行的非正交布局"是什么**：是**画**出来的，不是**转**出来的。构造器直接按 45° 方向放置图形——顶点取整数网格点，边长为 g·k·√2——这对"最小宽度/最小间距"类规则合法，对"每条边必须正好等于某个值"的规则（接触孔）永远不合法。所以斜向构造只能覆盖扩散、阱、金属、标记层，覆盖不了接触孔。

**两个概念必须分开**，否则会把不可达的目标写成待办：

| | 是什么 | 现状 |
|---|---|---|
| **旋转到非正交角** | 把已构造好的刚性部件整体转过去 | **数学上不可能**，任何 PDK 都不行。`turn` / `turn_transform` 对非 90 倍数抛错 |
| **斜向构造** | 构造器直接按 45° 方向画出图形 | **尚未实现**。需要新的 `composition: alternative` 构造器；放置模型用 bbox 是保守可行的（bbox 分离 ⇒ 多边形分离），要紧凑再上 x/y/x+y/x−y 四方向分离约束；本地检查器的 `width_check`/`space_check` 支持任意多边形，`cut_size` 的 `is_box()` 会正确拒绝斜接触孔 |

因此当某个 PDK 允许 45° 时，正确的补法是加斜向构造器，而不是扩展 `turn`。分类表把"该角度是否合法"（`angle_verdicts`）与"引擎能否执行"（`direction_capability` 的 construct/place/verify 三项）分开，正是为了让这两件事可以分别推进。

### 2.7 一个必须大声失败的漏洞（已修复）

若某个 Pack 声明了 `rotate_non_orthogonal`，`angle_verdicts` 会把 45° 判为 `executable`（即"已结案"，不进 `open_angles`）。此时下游若把非正交角**静默过滤**，证书就会声称"45° 已搜索并结案"，而实际从未搜索——缺口伪装成证明，这比未结案严重得多。

现在的处理是硬失败：

- `freedom.orientations`：部件存在非正交的可执行方向时抛 `ValueError`，指出"应在该角度画出几何，而不是把部件转过去"；
- `action_space.transform_row`：`degrees % 90` 非零时抛 `ValueError`，不再用 `degrees // 90` 把 45 静默算成 0 并返回原几何。

这两条路径今天不可达（三个 pack 的 `direction_capability` 都只有正交方向），但由测试锁定（`test_a_diagonal_executable_direction_is_an_error_not_a_silent_filter`、`test_a_built_row_cannot_be_turned_to_a_non_orthogonal_angle`）。

---

## 3. 动作空间：块 → 朝向变体

### 3.1 `block_space`

对每个构造好的块：

1. 用实际画出的图层建立部件，得到 8 个角度的结论（§2.3）。
2. 对每个可执行的正交朝向（最多 8 个）：先过 `enforce`，再 `turn` / `reflect` 生成几何，用 `block_from_row` 算出各平面的框，得到一个**变体**。
3. 框完全相同的变体合并（对放置问题它们等价）。
4. 若学到"保持栅向"（§3.3），含栅块只保留 0°/180°（± 镜像）的变体。

输出 `BlockSpace`：变体列表、每个变体的来源朝向、约束来源、角度结论、未结案角度。`report()` 原样写入证书的 `blocks`。

### 3.2 `mirror_space`：镜像对

镜像对的另一半不单独枚举：它的第 k 个变体就是原块第 k 个变体的镜像。求解时两者的变体编号绑定（`ties`），所以在任何朝向下两者都互为镜像。

### 3.3 学到的关系如何变成约束

学习完成后，关系的使用是机械式的：关系登记表 `relations.RELATIONS` 规定每种关系接受哪些 decision、每个 decision 产生什么效果（`RelationEffects`）。学习库里出现未登记的关系或 decision 直接报错，不会被悄悄忽略。

| 关系 | decision | 效果字段 | 翻译成的约束 |
|---|---|---|---|
| `mirror_pairs` | `enforce` / `off` | `enforce_mirror_pairs` | 网表对称的器件用 `mirror_space` 构造；配对块位置关于同一条轴镜像（硬约束） |
| `mirror_axis` | `parallel` / `perpendicular` / `free` | `axis_rule` | 构造层要试的对称轴（相对栅线）：`free` → 竖直、水平两条 |
| `uniform_gate_orientation` | `enforce` / `off`（缺省 off） | `keep_gates` | 含栅块（MOS、变容管）只取保持栅向的朝向 |
| `centre_fixed_devices` | `soft`（缺省 soft） | `soft_centre_unpaired` | 没有镜像伙伴的块软居中（代价进入目标的后一级） |

此外，网表对称中**自对称且配对**的链硬居中，并且只取 0°/180°（保持它关于轴的自身对称）。

哪些块成对、哪些硬居中、哪些软居中，由两个原语决定：`placement_units`（把构造好的行组成放置单元）与 `placement_intents`（得到 `PlacementIntents(pairs, centred, soft, extra)`）。电阻、MIM 没有栅，取全部 8 个正交朝向（合并后通常剩 2–3 个不同的框）。

新增一种关系的做法：在 `RELATIONS` 中登记它；若需要新类型的放置约束，再在约束登记表 `placement_constraints.REGISTRY` 中登记一种约束（生成核心文档"组装"一节）。流程代码不改。

---

## 4. 朝向与位置联合求解（`solve_blocks`）

`place_blocks` 把放置单元、变体、约束意图和 Pack 的阱规则（`grid`、`well_to_opposite_active`、`well_space`）交给引擎 `solve_blocks`：

- 每个块的每个变体一个布尔变量，恰好选一个；
- 块在每个平面上的框 = 位置 + Σ(变体布尔量 × 该变体的常数框)，仍是线性表达式；
- 同平面不重叠（`NoOverlap2D`，区间长度随变体变化）、阱间距、对称轴约束都按同样方式改写；
- 镜像对的变体编号相等，位置关于轴镜像；
- 内建约束（顺序固定：变体绑定、镜像对、硬居中、软居中）之后，按名称顺序执行登记的扩展约束（`run_phase("variants")`、`run_phase("placement")`）；
- 目标是材料外包框面积（宽 × 高的精确乘积）。

CP-SAT 返回 OPTIMAL，就是**全部朝向组合 × 全部位置**内的最优；它的下界一起返回，供生成核心在候选间剪枝。只有一个变体时，模型与不搜索朝向时逐条相同。

**两阶段求解（确定性）**：第一阶段多线程求最优面积并证明最优；第二阶段把面积固定为该值，单线程、固定种子、确定性时间，按"软代价 → 坐标与变体编号"选出唯一的版图。多线程求解每次落在不同的等面积解上，第二阶段保证同一输入得到同一版图。结果中 `solver.determinism` 记录走了哪条路径（`canonical` / `single_thread` / 其他）。第二阶段占生成时间的绝大部分，加速方法见生成核心文档的"求解调度与加速"一节。

---

## 5. 对称性削减：保持栅向不损失最优

学习库要求栅向一致时，含栅块只取栅向不变的朝向。理由：

- 把整个版图转 90°，合法性不变（等距声明）、面积不变、镜像对仍然成立，对称轴与栅的相对关系也不变；
- 对称轴候选是相对栅定义的（`free` 时两条轴都试）；
- 所以任何"栅全部转 90°"的解，都对应一个面积相同、栅保持原向、轴换成另一条的解。

实测：7 个构造候选（SKY130 差分对、SKY130 无源电路、GF180 差分对）上，把含栅块强制改为栅转 90° 后在轴 A 上的最优面积，与原方向在另一条轴上的最优面积逐一相等。测试：`test_keeping_gates_as_built_loses_nothing`。

---

## 6. 版图检查失败时的处理（`verification_feedback`）

朝向搜索得到的最优解偶尔会通不过本地版图检查（§11 第 4 条：放置模型看不到标记层之间的相互作用）。`verification_feedback` 是一个 `decision_role: feedback` 原语，只返回决定（数据），不执行：

- 已求解、未通过检查且开启了朝向搜索 → `retry_fixed_orientation`，并给出失败的检查项；构造层用固定朝向重算该候选，作为后备；
- 其他情况 → `none`。

后备版图是合法的，但它不是证明：该候选在证书中仍为"未结案"（除非其下界已大于当前最优），并记录失败的检查项、朝向解面积和后备面积。这保证开启朝向搜索后，每个候选都不会比固定朝向差。

---

## 7. 反相器专用决策器（`actions.py`）

反相器专用流程（`python -m clay.learning`，使用反相器训练出的库）只有两个部件（NMOS 行、PMOS 行），用外层枚举：

1. **枚举**：每行 4 个可执行朝向 → 16 个方案，每个都先过 `enforce`。
2. **整体旋转等价类**（`canonical`）：类标识是各部件相对第一个部件的角度差，**按部件顺序**（不排序；排序会在 ≥3 个部件时把不等价方案合并）。两部件时 16 → 4 类。
3. **可靠下界**（`two_box_lower_bound`）：两个有源区矩形必须按四种次序之一分开，下界取四种次序各自下界的最小值，用该方案实际交给求解器的间距。
4. **最优优先的分支定界**（`search`）：每轮只精确求解下界最小的一个存活类，再用已达面积剪枝。
5. **证书**：`classes`（每类的下界与结案方式）、`angle_verdicts`（每行 8 个角度）；穷举模式也输出证书，并要求 16 个内层求解都是 OPTIMAL。

实测：

| 工艺 | 胜者 | 其余三类 | 求解次数 |
|---|---|---|---|
| GF180 5V | 相对 0°：16.9912 µm²（OPTIMAL） | 180°：下界 17.6344；90°、270°：下界 17.9992 → 排除 | 1 |
| SKY130 | 相对 180°：3.86835 µm²（OPTIMAL） | 0°：下界 3.90165；90°、270°：下界 4.3639 → 排除 | 1 |

每类的下界恰好等于该类真实最优面积（与穷举逐一核对），所以只需求解 1 次。

在通用流程中，反相器训练出的构造以替代构造器 `build_learned_pattern_row` 参与比较（生成核心文档"构造器变体"一节），之后的朝向与位置决策与其他电路相同，走 §3–§4。

---

## 8. 证书字段

构造层对每个对称映射给出一份证书（`result["decider"]`，由 `circuit_generate._certificate` 生成）；生成核心再把各映射汇总成全局证书 `result["coordinator"]`（见生成核心文档）。

| 字段 | 含义 |
|---|---|
| `scope` | 搜索了什么：哪些折叠目标 × 哪些轴；每块全部可执行朝向，与位置联合求解；目标为材料外包框面积；明确列出**未搜索**的内容（其他折叠目标、不共享扩散、斜向构造、布线；强制镜像对时还有其他对称映射） |
| `fixed_choices` | 本层没有搜索、由上层固定的选择。目前是 `symmetry_mapping`（选取规则与配对列表）；生成核心在映射层搜索它 |
| `search` | 候选内与候选间的搜索方式 |
| `objective` / `winner_objective` | 比较用的优先级（硬约束 → 面积 → 软关系代价 → 规范化平局）及胜者在各级的取值 |
| `determinism` | 输出版图是否可复现（`output_reproducible`）、整份证书是否可复现、各候选的求解路径 |
| `orientation_search`、`symmetry_breaking` | 是否搜索朝向；削减理由（§5） |
| `trace` | 每轮求解了多少候选 |
| `candidates` | 每个候选：面积、状态、下界、是否通过检查、选中的朝向、覆盖的朝向组合数、结案方式、后备信息、求解路径、软代价 |
| `winner` / `best_area_nm2` | 胜出候选与面积 |
| `blocks` | 每个块：类别、图层、变体、约束来源、镜像来源、8 个角度的结论、未结案角度 |
| `open_candidates` / `open_angles` | 未结案的候选与角度 |
| `proven_optimal_in_model` | 胜者 OPTIMAL、不是后备，且没有未结案的候选与角度 |

---

## 9. 验证结果

**(1) 联合模型 = 穷举**：对每个构造候选把所有朝向组合逐一固定、分别求解取最小，与联合求解对照：

| 电路 | 候选数 | 每个候选的组合数 | 结果 |
|---|---|---|---|
| SKY130 差分对 | 4 | 4–8 | 4/4 相等 |
| SKY130 无源电路（变容管、MIM、电阻） | 6 | 24 | 6/6 相等 |
| GF180 3.3V 差分对 | 4 | 4–8 | 4/4 相等 |

测试：`test_joint_orientation_solve_matches_exhaustive_enumeration`。

**(2) 三个真实比较器**（默认生成入口，结果 `data/primitive_generalization/comparator_v4_coordinator/`）：

| 比较器 | 面积 | 胜出构造 | 块数 | 变体总数 | 角度结论 | 全局证书 |
|---|---|---|---|---|---|---|
| GF180 动态比较器（训练集） | 65.93 µm² | 不额外折叠、竖直轴 | 7 | 14 | 28 executable，28 **deck_infeasible** | 范围内已证明最优 |
| SKY130 2AM（训练集） | 396.27 µm² | 折叠 8 µm、竖直轴 | 12 | 24 | 48 executable，48 **deck_forbidden** | 范围内已证明最优 |
| SKY130 JKU（测试集） | 389.30 µm² | 16 个映射中的第 4 个；不额外折叠、竖直轴 | 18 | 38 | 72 executable，72 **deck_forbidden** | 范围内已证明最优（16 个映射全部结案） |

所有块的 8 个角度都已结案。与"不搜索朝向"相比：GF180 持平；2AM 小 1.9%（来自三个电阻块的朝向与链的 180° 变体）；JKU 在同一映射下持平。JKU 的 389.30 µm² 来自生成核心的映射层（按固定规则选的映射为 401.34 µm²）。

---

## 10. 如何避免局部最优

"局部最优"只在**搜索不完整**时出现。这里不用重启、退火之类的启发式，而是让搜索在声明的模型内本身完整，用可靠下界减少实际求解次数，并用证书把"完整"变成可以检查的结论。

| 来源 | 对策 | 位置 |
|---|---|---|
| 某些角度在判断前被删掉 | 列出工艺出现过的全部候选角度，逐一给结论；未结案即不声称最优 | `angle_verdicts` |
| 只看了部分朝向组合 | 朝向作为 CP-SAT 变量与位置联合求解（已与穷举核对） | `solve_blocks` |
| 预算浪费在等价方案上 | 框相同的变体合并、保持栅向削减（有证明与实测）；反相器整体旋转等价类 | `block_space`、`actions.canonical` |
| 不等价方案被合并 | 等价类按部件顺序比较，不排序 | `actions.canonical` |
| 用排名或估计淘汰候选 | 只用可靠下界排除 | `actions.search`、`search_constructions` |
| 已测得的结果被事后排除 | 更长的重解失败时保留之前的结果；两个下界取较强者 | `search_constructions` |
| 检查失败导致候选丢失 | 固定朝向后备，但候选保持未结案 | `verification_feedback` |
| 没跑完就当最优 | 证书要求胜者 OPTIMAL；只有 FEASIBLE 的候选被重解，仍未结案则如实列出 | 证书 |
| 非法动作被悄悄修正 | `enforce` 只拒绝不钳制 | `enforce` |
| 学到的约束没有生效 | 关系登记表强制每种关系有定义的效果，未登记即报错 | `relations` |

---

## 11. 局限

1. **斜向构造**（接触孔正放、其余图层斜画）不在构造空间中，需要专门的构造器。
2. **不含接触孔的部件的非正交角度**（如以后把纯金属或阱作为放置块）是 `not_executable`，证书会显示未证明最优。真正实现需要斜向构造器、能处理八边形的放置模型（x、y、x+y、x−y 四个方向的分离约束，仍是线性的）和斜向检查器，具备后在 Pack 的 `direction_capability` 中声明对应方向。
3. **布线**不在模型中：180° 旋转与原朝向外框相同、只换了端子伸出方向，它对后续布线的影响没有计入。
4. **放置模型看不到标记层之间的相互作用**：`materialize` 的 `close` 模式先填平小于间距规则的缝隙，再从对向扩散附近切回注入层；块换朝向后相邻块的 npc 与注入层可能不再对齐，留下窄条或窄缝（曾在非胜出候选上出现 `npc_width`、`nplus_space` 违例）。目前由 §6 的后备与"未结案"标记处理；根本解决需要在 `solve_blocks` 中为标记层加约束，或在 `materialize` 中处理窄条，都会改变已冻结的 `materialize_close` 验证规格。
5. **求解时间**：块很多时联合模型可能在时限内求不到 OPTIMAL，证书如实写"未证明"；有热启动，结果不会比固定朝向差。
6. **正交等距声明**由人核对 deck 后写下，官方 DRC 仍是最终裁判。
7. `actions.canonical` 与 `representative_index` 的 `step_deg` 参数未使用（调用处传入的是网格纳米数），不影响结果。

---

## 12. 检查方法

```powershell
# 自由度、动作空间、反相器决策器、联合模型与穷举一致、保持栅向不损失最优、后备
python -m pytest tests/test_action_space.py tests/test_actions.py tests/test_freedom.py tests/test_pdk_semantic.py tests/test_circuit_primitives.py tests/test_circuit_construct.py tests/test_placement_constraints.py

# 分类登记、签名与检索
python -m pytest tests/test_primitive_classification.py tests/test_decision_index.py
python -c "from clay.learning import primitive_classification as pc; print(pc.by_decision_role(pc.load()))"

# 某个部件 8 个角度的结论
python -c "from clay.pdk import semantic as s; from clay.learning.freedom import angle_verdicts; [print(v.degrees, v.status, v.reason[:80]) for v in angle_verdicts(s.get_pack('gf180mcu_3v3'), ('active','poly','contact','metal1'))]"

# 非正交方向的可执行声明会硬失败（§2.7）；今天三个 pack 都不可达，由测试锁定
python -m pytest tests/test_freedom.py -k "diagonal_executable or non_orthogonal" -v

# 反相器专用流程：预算 1 与 16 面积相同，证书含每类下界
python -m clay.learning --netlist my_inv.cdl --library library.json --contract contract.json --output out_b1 --candidate-budget 1

# 比较器：完整流程，generated/<样本>/layout.json 的 decider 与 coordinator 字段即证书
python scripts/run_comparator_learning.py --output data/primitive_generalization/<新目录>
```
