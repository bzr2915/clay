# 生成核心：分类表、检索索引、执行核心与全局优化

> 本文说明版图生成的组织方式与全局优化的现状：原语如何登记与检索、执行核心如何把原语组合成流程、在哪些层次上做全局最优搜索、如何保证结果唯一可复现、如何加速。
> 以前的"全局协调层"已不再是单独的一层：协调工作由**分类表 + 检索索引 + 执行核心**完成，决策逻辑都写成登记在分类表中的原语。文件名保留以免断链。
> 相关文档：原语清单 [PRIMITIVE_CATALOG_zh.md](PRIMITIVE_CATALOG_zh.md)、PDK 解析 [PDK_PARSER_WORKFLOW_zh.md](PDK_PARSER_WORKFLOW_zh.md)、动作决策 [ACTION_DECIDER_FREEDOM_zh.md](ACTION_DECIDER_FREEDOM_zh.md)。
> 依据 2026-11-15 的代码与实测。命令在仓库根目录下执行。
> **开源发布说明：** 本仓库只发布布局部分；文中涉及布线（`clay/routing/`、布线原语、布线层）的内容未包含在本次发布中，仅作设计参考。

---

## 1. 目标与原则

### 1.1 两条验收目标

| 编号 | 目标 | 检验方式 |
|---|---|---|
| **G1 只加原语** | 学一个新电路或接入一个新工艺时，只增加原语文件、分类条目、学习库（新工艺再加一个 Pack 适配器），不改流程代码 | `assemble` 中不出现任何器件类别名（`test_assemble_names_no_device_class`）；新构造器、新约束、新关系都通过登记接入 |
| **G2 新选项不扰动老结果** | 登记一个不会胜出的新选项后，老电路的 GDS 逐字节不变 | 平局规则只按内容比较，不按登记顺序或列表位置；回归指纹（§9） |

### 1.2 原则

1. **原语是纯函数**：相同输入得到逐字节相同的输出（§8 给出成立条件）。
2. **目标完整定义**：凡是有多个解的地方，都由显式声明的优先级决定，不由线程调度、哈希顺序或登记顺序决定。
3. **执行核心只编排、不求解**：核心不实现具体的构造、求解或决策算法，它们都是原语。
4. **能联合求解的就联合求解**：两个选择能写进同一个求解模型（如朝向与位置），就放进同一个模型，而不是在外层枚举。
5. **最优性必须带范围**：证书写明在什么目标、什么搜索空间内最优；范围由登记信息生成。
6. **学到的东西以声明的约束或优先级进入**，不变成隐藏权重。

### 1.3 什么样的选择需要在核心中搜索

同时满足三条：有多个合法选项；哪个更好取决于全局目标；后果只能在下游看出来、又无法与下游写进同一个求解模型。缺第 1 条没有选择；缺第 2 条可以在那一层固定优先级；缺第 3 条应当联合求解。

实例：JKU 比较器有 16 个合法的网表对称映射，面积、镜像对数、居中质量三个目标各选出不同的映射（§5.2），而映射的优劣要到构造与放置之后才看得出来，所以映射是核心中的一个搜索层。

---

## 2. 总体结构

```
          设计意图（S：原语组合，θ：参数，u：实现设置）
                  │                         ▲ 证书（范围、未结案项）
                  ▼                         │
 ┌────────────────────────────────────────────────────────────┐
 │ 执行核心  clay/decision/core.py、circuit_generate.py         │
 │  按决策层调用原语 → 按目标比较 → 分支定界 → 处理反馈 → 证书   │
 └──────┬──────────────────────────────────────────▲──────────┘
        │ dispatch / call（按类型查找与调用）         │ derive / gaps（推导与缺口）
        ▼                                          │
 ┌──────────────────────────┐        ┌─────────────┴──────────────┐
 │ 分类表                    │ ─────► │ 检索索引 clay/decision/index.py │
 │ primitive_classification  │  build │ by_output / by_input / …      │
 │ .yaml（42 个原语）         │        └────────────────────────────┘
 └──────────┬───────────────┘
            │ implementation: 模块:函数
            ▼
   原语（纯函数）：解析、构造、变换、放置求解、决策（admissibility / options / reduce / bound / feedback）、检查
   登记表（数据，不是原语）：关系登记表 relations.py、约束登记表 placement_constraints.py、Pack
```

| 组成 | 文件 | 是什么 |
|---|---|---|
| 分类表 | [primitive_classification.yaml](../primitive_classification.yaml)，加载与校验 [clay/learning/primitive_classification.py](../clay/learning/primitive_classification.py) | 每个原语一条：分类轴、能力签名、PDK 绑定、实现位置 |
| 检索索引 | [clay/decision/index.py](../clay/decision/index.py) | 从分类表建立的查询结构；推导组合、列出缺口 |
| 类型化调用 | [clay/decision/invoke.py](../clay/decision/invoke.py) | `dispatch(index, 输出类型, 器件类别)` 找到构造器；`call(条目, 按类型的值, 参数)` 按签名绑定参数调用 |
| 执行核心（代码注释与原语清单中也称"协调核心"） | [clay/decision/core.py](../clay/decision/core.py)（映射层、目标、全局证书）、[clay/learning/circuit_generate.py](../clay/learning/circuit_generate.py)（组装、构造层搜索、定稿） | 编排；不在分类表中 |
| 登记表 | [clay/learning/relations.py](../clay/learning/relations.py)、[clay/layout/synth/placement_constraints.py](../clay/layout/synth/placement_constraints.py) | 学习关系的效果、放置约束的种类 |
| 单原语验证 | `primitive_verify/`（`registry.py`、`frozen_entries.json`） | 每个原语的独立验证规格；冻结条目由人批准 |

### 2.1 为什么执行核心不是原语

1. **无限回溯**：核心靠检索分类表组装流程；它自己若在表里，就需要另一个东西去检索并调用它。
2. **它是高阶的**：输入是"其他原语加组合方式"，不符合"类型化数据进、数据出"的原语契约，也无法单独做原语验证。
3. **它必须几乎不变**：G1 要求有一个不随原语增加而改动的部分。

同理，**搜索策略不是可选的原语**（否则又要决定用哪个策略）；**决策原语的输入输出只能是数据**（`options` 返回选项列表，`bound` 返回数值，`feedback` 返回"做什么"的描述），执行都由核心完成。

### 2.2 一个新东西应放在哪里

| 新东西 | 放在哪里 |
|---|---|
| 新器件类别的画法 | 构造原语，`composition: dispatch`，`applies_to` 写该类别 |
| 同一类别的另一种画法（如反相器训练出的构造、以后的共质心、斜向构造） | 构造原语，`composition: alternative`，与默认构造器在构造层竞争 |
| 能写成放置模型中的约束（如"两块对齐""距离上限"） | 约束登记表加一种约束；学习关系登记表说明哪个关系产生它 |
| 某动作是否合法 | `decision_role: admissibility` 原语 |
| 新的选项来源、等价削减、下界、失败处理 | `decision_role: options / reduce / bound / feedback` 原语 |
| 新工艺 | Pack 适配器（PDK 解析文档 §7） |

---

## 3. 分类表与能力签名

### 3.1 分类轴

| 轴 | 取值 | 含义 |
|---|---|---|
| `stage` | parse / construct / transform / plan / decide / assemble / verify / learn | 在流程中的位置 |
| `form` | 封闭词表 | 输出的形态（几何、坐标、数据……） |
| `freedom_effect` | 封闭词表 | 对自由度的影响（构造、平移、旋转、结构……） |
| `pdk_binding` / `optional_binding` | 语义规则键 | 必需读取 / 工艺有该层时读取的 Pack 键 |
| `decision_role` | none / admissibility / options / reduce / bound / feedback | 只有 `stage: decide` 能取 none 以外的值 |

`decision_role` 的现有成员：

| 取值 | 原语 |
|---|---|
| `admissibility` | `angle_verdicts`、`enforce` |
| `options` | `orientations`（complete）、`fold_candidates`（truncated）、`netlist_symmetries`（truncated：每个种子端口对最多 `max_iterations` 个映射） |
| `reduce` | `canonical_rotation_class`（反相器整体旋转等价类）、`symmetry_classes`（孪生交换去重） |
| `bound` | `two_box_lower_bound` |
| `feedback` | `verification_feedback` |

精确求解不在 `decide` 中：`solve_blocks`、`solve_area` 是 `stage: plan`，作为某一层的求解器被调用。

### 3.2 能力签名

```yaml
build_mim_cap:
  stage: construct
  implementation: clay.learning.passive_primitives:build_mim_cap
  signature:
    inputs: {device: Device, pack: Pack}      # 必需的类型化输入（参数名 → 类型）
    optional_inputs: {}                       # 可缺省的类型化输入
    params: []                                # 其余参数：θ 坐标与实现设置 u
    output: Row                               # 唯一输出类型
  composition: dispatch                       # step | dispatch | alternative | engine
  applies_to: [cap_mim]                       # 服务的器件类别；空 = 与器件无关
  determinism: pure                           # pure | unaudited
  cost: cheap                                 # cheap | solver
  # alternative_group: <组名>                 # 仅 alternative
  # options_domain: complete | truncated      # 仅 decision_role: options
  # mutates: [<输入名>]                       # 会改写的输入（不能声明 pure）
```

`composition` 的四种取值：

| 取值 | 含义 | 例子 |
|---|---|---|
| `step` | 流程中的普通一步 | `netlist_flatten`、`build_row`（反相器流程直接调用它，所以不是 engine） |
| `dispatch` | 按器件类别各管一类，同一输出类型的 `applies_to` 必须互不相交 | `build_chain_row`（mos、cap_var）、`build_poly_resistor`（res_poly）、`build_mim_cap`（cap_mim） |
| `alternative` | 与 dispatch 默认构造器竞争的另一种构造，同组成员构成构造层的选项 | `build_learned_pattern_row`（组 `mos_chain_constructor`） |
| `engine` | 只在另一个原语内部调用，推导组合时不提供 | `solve_blocks`（只在 `place_blocks` 内调用） |

**`determinism: pure` 只能用于确定性证明审计过的模块**（§8.3），其余为 `unaudited`，测试强制这一点。

### 3.3 封闭的类型词表

`TYPES`、`EXTERNAL_TYPES`、`TRAINING_TYPES`、`CORE_TYPES`、`COLLECTIONS`、`PARTS`（都在 `primitive_classification.py`），写错即报错：

- **外部输入**（生成时给定）：`NetlistText`、`Pack`、`Packs`、`Library`、`InverterLibrary`、`RelationLibrary`、`Contract`；
- **只在训练时给定**：`ReferenceLayout`。生成阶段不提供，所以类型系统保证生成流程推不出"读参考版图"的组合（`test_generation_cannot_reach_a_library_from_a_reference_layout`）；
- **核心产生**：`Candidate`（构造层的候选）；
- **集合**：元素可得则集合可得（`Rows` ← `Row`），收集由核心完成；
- **部分**：整体可得则部分可得（`ContactPolicy` ← `Library`，`Device` ← `Netlist`），即字段读取。

### 3.4 一致性校验（测试强制）

| 校验 | 内容 |
|---|---|
| 实现可导入 | `implementation` 可导入且可调用 |
| 签名与代码一致 | `check_signature`：签名中的参数名都是函数的真实参数，无默认值的参数都已声明 |
| 纯度声明 | `pure` 只出现在已审计模块 |
| 分派无冲突 | `dispatch_conflicts`：同一输出类型的 dispatch 构造器服务的类别不相交 |
| 规则键已声明 | `scan_rule_keys` 静态扫描函数与同模块辅助函数（深度 2）中 `nm("…")`、`has("…")`、`r("…")` 读取的键，必须都在 `pdk_binding` / `optional_binding` 中。扫描是下界，只能发现漏声明 |
| PDK 可运行 | `check_pdk_bindings` / `runnable_on(pack)`：必需键在 Pack 中都存在 |
| 三方一致 | 分类表、`primitive_verify/registry.py`、`primitive_probe` 三者条目一致 |

### 3.5 检索索引

`index.build()` 建立：`by_output`、`by_input`、`by_device_class`、`alternatives`、`producers(类型, 器件类别)`。在此之上：

| 函数 | 回答的问题 |
|---|---|
| `producible(given)` | 从给定类型出发能得到哪些类型（沿集合、部分关系与可选输入展开） |
| `gaps(given)` | 有哪些输入类型没有任何原语能产出，即仍需手写衔接代码的位置；`given` 缺省为外部 + 训练 + 核心类型 |
| `derive(目标类型, 器件类别)` | 反向推导得到目标需要哪些原语，以及尚未消解的选择（替代构造器与同类的 dispatch 构造器一起列为选项）；跳过 engine |
| `dispatch_conflicts()` | 分派冲突 |

查找本身约 1 µs，不是瓶颈。价值在于：求解前排除类型不匹配的组合；"哪些原语为含 `cap_mim` 的电路产出 `Row`"之类的问题不需要读代码；证书范围由登记信息生成。

---

## 4. 组装：一个构造候选如何变成版图

### 4.1 流程

一个构造候选 = 对称映射 × 折叠目标 × 对称轴 × 构造器变体。`circuit_generate.assemble` 与 `evaluate` 把它变成版图，每一步都是登记的原语：

```
网表 ──► fold_devices（按折叠目标拆指）──► diffusion_chains（扩散共享链，按对称映射配对）
     ──► placement_units（列出放置单元：链或单个器件，及其镜像关系）
     ──► 逐单元：镜像单元 → mirror_space；其余 → dispatch(index, "Row", 类别) 或替代构造器，call(...) 按类型调用
              │                  build_chain_row（mos、cap_var）→ build_row
              │                  build_poly_resistor（res_poly）、build_mim_cap（cap_mim）
              │                  build_learned_pattern_row（替代：反相器训练出的构造）
              └─► block_space（每个合法朝向一个变体，经 enforce 门控）
     ──► placement_intents（镜像对 / 硬居中 / 软居中 / 扩展约束）
     ──► place_blocks → solve_blocks（朝向 + 位置联合求解）
     ──► materialize（合成版图，close 模式填缝）──► verify_circuit_layout（本地 DRC 子集 + 器件级 LVS）
```

- **哪些类别按链构造**由分类表推出：以 `Chain` 为输入的 dispatch 构造器所服务的类别按链构造（`_chain_classes`），其余按器件构造。
- **变容管等非普通 MOS 的链**怎么画是工艺数据：`Pack.chain_construction` 声明扩散所在图层与规则替换（目前只有 SKY130 `cap_var`），`build_chain_row` 读取它。
- `assemble` 中不出现任何器件类别名（测试强制）；它只是把原语按类型连起来。

### 4.2 关系登记表

`relations.RELATIONS` 为每种学习关系登记接受的 decision 及其效果（`RelationEffects`：`enforce_mirror_pairs`、`axis_rule`、`keep_gates`、`soft_centre_unpaired`）。学习库中出现未登记的关系或 decision 即报错。现有 4 种：`mirror_pairs`、`mirror_axis`、`uniform_gate_orientation`、`centre_fixed_devices`（翻译规则见动作决策文档 §3.3）。

### 4.3 约束登记表

`placement_constraints.REGISTRY` 把 `solve_blocks` 的电路约束做成插件。每种约束登记：名字、阶段（`variants`：变体选择变量之后；`placement`：外包框与对称轴之后）、影响的目标级（0 硬约束 / 2 软代价）和一个向 CP-SAT 模型添加变量与约束的函数。

- 内建 4 种，按固定顺序 `BUILTIN_ORDER` 添加：`ties`（镜像对变体编号相等）、`mirror_pairs`、`centred`、`soft_centred`；
- 其他种类在内建之后**按名字排序**添加（与登记顺序无关，满足 G2）；没有数据的种类不添加任何东西；
- 数据经 `PlacementIntents.extra`（种类 → 数据）由 `place_blocks` 传给求解器；
- 已登记的扩展种类：`max_separation`（两块中心的曼哈顿距离上限，用于匹配），有测试，尚无学习关系产生它；
- 阱与间距规则是工艺规则，不是电路要求，仍写在求解器内。

新增一种约束 = 登记一个插件；新增一种关系 = 在关系登记表中登记，并说明它产生哪种约束。流程代码不改。

---

## 5. 决策层与全局最优搜索

### 5.1 三个决策层

| 层（从外到内） | 选项 | 等价削减 | 下界 | 求解 | 代码 |
|---|---|---|---|---|---|
| **1. 对称映射** | `netlist_symmetries`：全部合法对合映射，按固定规则排序 | `symmetry_classes`：只合并"只差在交换孪生器件"的映射（孪生 = 标签与连接都相同，交换孪生本身是网表自同构），可靠 | 无廉价下界 | 下一层 | `core.mapping_options`、`evaluate_options` |
| **2. 构造** | 折叠目标（`fold_candidates`：不折叠、指宽中位数、中位数一半）× 对称轴（由 `mirror_axis` 关系给出）× 构造器变体（默认 dispatch，加上输入已提供的 alternative） | — | 下一层的 CP-SAT 下界 | 下一层 | `circuit_generate.search_constructions` |
| **3. 朝向 + 位置** | 单个模型（朝向是模型内部变量） | 框相同的变体合并；保持栅向 | CP-SAT 自身下界 | `place_blocks` → `solve_blocks` | 动作决策文档 §3–§4 |

反相器专用流程另有一个"两行"决策器（16 方案 → 4 等价类 → 两框下界 → 最优优先，动作决策文档 §7）；在通用流程中，反相器训练出的构造是构造层的一个替代构造器变体。

**替代构造器何时参与**：只有它需要的类型化输入都已提供时（例如通过 `--inverter-library` 给了 `InverterLibrary`），`alternative_constructors` 才把它加为构造器变体，每个候选多出一个变体；未提供时候选列表与没有替代构造器时完全相同（G2）。

### 5.2 目标

目标是字典序的优先级向量，小者优先（`core.OBJECTIVES`）：

| 目标名 | 各级 | 用途 |
|---|---|---|
| `area`（默认） | 面积 → 软关系代价 | 最紧凑 |
| `matched_pairs` | 镜像放置的块对数（多者优先）→ 面积 → 软关系代价 | 匹配优先 |
| `centring` | 软关系代价 → 面积 | 居中优先 |

所有目标之前隐含第 0 级：硬约束（局部 DRC、连通、学到的 `enforce` 关系）必须满足，只有通过本地检查的候选参与比较。各级的值都在每个候选的**面积最优放置**上计算（构造层只产生这一种放置），这一点写进证书的 `objective.evaluated_on`。

JKU 比较器（竖直轴、不额外折叠）上三个目标的选择：

| 映射类 | 面积 | 镜像放置对数 | 软代价 | 被哪个目标选中 |
|---|---|---|---|---|
| 映射 4 | **389.30 µm²** | 3 | 32760 | `area` |
| 映射 9 | 392.04 µm² | 1 | **0** | `centring` |
| 映射 0（固定规则选中的映射） | 401.34 µm² | **4** | 19600 | `matched_pairs` |
| 映射 2 | 405.56 µm² | 2 | 11400 | 无 |

（16 个映射的结果分成这 4 组，每组 4 个；数据 `data/primitive_generalization/s4_symmetry_objectives/`，`scripts/run_symmetry_objectives.py`。）

### 5.3 平局规则

目标各级都相同时，依次比较：

1. 映射的规则排名（由映射的结构与器件名决定：移动器件数 → 交换网络数 → 器件对的类别与尺寸 → 名字），与发现顺序无关；
2. 候选内容 `candidate_key`：不额外折叠优先 → 目标指宽大者优先 → 对称轴按 `AXIS_ORDER`（竖直、水平、无）→ 构造器变体（默认构造器排在前面）；
3. 候选内部：`solve_blocks` 第二阶段的规范化平局项（坐标之和、变体编号之和）。

都不使用登记顺序或列表位置，所以登记一个新选项不会改变老选项之间的胜负（G2）。

### 5.4 分支定界

构造层（每个映射内）：

1. 每个候选先做一次短时求解（`quick_seconds`），得到面积、CP-SAT 下界、状态、检查结果；
2. 一个候选**结案**的条件：已证明最优（OPTIMAL、通过检查、不是后备）；CP-SAT 证明无解；构造规则拒绝；或（目标第一级是面积时）其下界**严格大于**当前最优面积；
3. 未结案的候选按下界从小到大，用更长时间（`final_seconds`）重新求解，最多 `max_rounds` 轮；更长的重解失败时保留之前的结果，两个下界取较强者；
4. **排名靠后本身不淘汰任何候选。**

**剪枝必须严格**：下界等于当前最优面积的候选可能在后一级（软代价、平局规则）胜出，不能剪。**只有目标第一级是面积时才用面积下界剪枝**（`prune = objective == "area"`）；其他目标下面积下界什么也证明不了，每个候选都求解到底。

映射层：对每个映射类运行构造层，再按目标在全部映射的全部已通过检查的候选中取最小（`core.choose`）。映射层目前没有廉价下界，每个映射类都要运行构造层。

### 5.5 反馈

失败后做什么由 feedback 原语决定，以数据形式返回，执行由搜索循环完成：`verification_feedback` 在朝向最优解没通过本地检查时返回 `retry_fixed_orientation`，构造层用固定朝向重算该候选作为后备，候选保持"未结案"（动作决策文档 §6）。以后布线、电性能检查的失败以同样方式接入：新增一个 feedback 原语。

### 5.6 证书

输出 JSON 中有两份证书：

- `decider`：胜出映射的构造层证书（候选表、每块每个角度的结论、范围、平局、确定性），字段见动作决策文档 §8；
- `coordinator`：全局证书。

| 全局证书字段 | 内容 |
|---|---|
| `objective` | 目标名、各级、`evaluated_on`（每个候选的面积最优放置）、平局规则 |
| `scope` | 由分类表生成：找到多少个映射、去重后多少类（`netlist_symmetries` 声明为 truncated）；折叠目标（`fold_candidates`，truncated）；构造器（每类的 dispatch 构造器，加上参与的替代构造器）；朝向（`orientations`，complete，与位置联合）；未搜索：不共享扩散、斜向构造、布线、电性能 |
| `levels.symmetry_mapping` | 每个映射类：成员数、配对数、自对称数、本类最佳候选、面积、软代价、镜像放置对数、状态、未结案候选数、结案方式 |
| `winner` | 胜出映射与候选、目标向量、面积、映射内容 |
| `open` | 未结案项 |
| `proven_optimal_in_scope` | 每个映射的每个候选都已结案，胜者 OPTIMAL、不是后备，且胜者没有未结案的角度 |

"已证明最优"只在 `scope` 声明的范围内成立。

---

## 6. 求解调度与加速

这一节讲的是全局优化在**什么时候做哪一部分计算**。它与 §5 的搜索结构是同一件事的两面：搜索定义了完整的选项空间，调度决定这个空间里的哪些部分值得把最贵的计算花上去。调度**不改变**选中的解（§6.3 的验收）。

### 6.1 时间花在哪里

`scripts/profile_generation.py` 在 JKU 比较器的一个对称映射类（6 个构造候选）上的分解（`data/primitive_generalization/stage_report_20261115/profile_jku_1mapping.json`）：

| 部分 | 调用次数 | 耗时 | 占比 |
|---|---|---|---|
| 第一阶段：多线程求最优面积并证明最优 | 6 | 1.2 s | 1% |
| 第二阶段：单线程确定性阶段（在面积相等的解中定版图） | 5 | 121.5 s | 94% |
| 构造、组装、版图检查 | — | 6.7 s | 5% |

结论很明确：

- **求最优面积几乎不花时间**，94% 花在"从面积相等的众多最优版图中确定性地选出一个"——这一阶段存在的唯一理由是可复现（§8）。
- **每个候选都做了第二阶段，包括不可能胜出的候选**。JKU 有 16 个映射类、每类 6 个候选，约 80 次第二阶段、每次约 24 s，这就是整体耗时的来源。

### 6.2 方案 A：只为可能胜出的候选做第二阶段

**论证**：第二阶段在"面积 = 已证明的最优值"约束下求解，只改变面积相等的解之间的选择。所以它**只可能改变"谁在面积上打平时胜出"**，绝不可能把一个面积更大的候选变成胜者。因此：一个候选的第二阶段结果，只在这个候选的面积等于全体最小面积时才影响结论；面积严格更大的候选，无论第二阶段选出哪一个，都不能胜出。

**做法**（`search_constructions(defer_area=True)`，默认开启）：

1. 每个候选的第一阶段照常运行（多线程、`quick_seconds`）。第一阶段证明最优时，`solve_blocks(defer_canonical=True)` 立即返回：面积（唯一值）与下界是最终结论，版图来自多线程阶段、标记 `determinism='deferred'`，**不用于输出**，`evaluate` 也不组装、不检查。
2. 全部候选的面积都拿到后，取最小值 `floor`。面积严格大于 `floor` 的候选**以"已证明面积更大"结案**（证书写作 `area X > best Y: canonical placement not run`），不做第二阶段、不做组装与检查。
3. 面积等于 `floor` 的候选逐个真正落版（`pinned_area_nm2 = 该候选的已证明面积`）：跳过第一阶段，直接做第二阶段——模型与不做延迟时完全相同（提示本来就会清除），所以结果逐项相同——然后组装、检查、记录软代价。
4. 结案之后按 §5.4 的原有规则挑选胜者。若某个平局候选没通过检查，下一个平局候选接着落版。

**为什么不损最优性**：步骤 2 排除的候选，其面积（已证明最优、与运行无关）严格大于最终胜者的面积，所以在目标的第 1 级就输了；第 2 级、平局规则都轮不到它。§5.4 的"剪枝必须严格"正是这个条件。

**推论的附带效果**：

- 落选候选不再有版图与软代价，所以它们的 `determinism` 字段不再写入证书——证书的可复现性不再受落选候选第二阶段超时影响，2AM 的 `reproducible=false`（§8.4 的 C4′ 例外）随之消失。
- 面积不是第一级的目标（`matched_pairs`、`centring`）不能这样剪枝（面积下界证明不了第 1 级的比较），此时 `prune=False`，本来就不延迟。

**放弃第二阶段不会丢的东西 / 会丢的东西**：

| 保留 | 失去 |
|---|---|
| 每个候选的已证明最优面积、下界、状态 | 落选候选的具体版图（它本来也不输出） |
| 胜出者与平局竞争者的完整版图、检查结果、软代价 | 落选候选的软代价、朝向选择（在目标第 2 级及以后才用到，而它们在第 1 级已输） |
| 胜者的可复现性与"范围内已证明最优" | 无 |

### 6.3 验收

| 验收项 | 方法 | 结果 |
|---|---|---|
| 输出版图不变 | 回归指纹（§9.1）：46 个构造配置、3 个测试电路 + 6 个生成电路、2 个反相器 | 构造指纹与 GDS 逐字节相同；证书只在落选候选的结案字段上不同（见下） |
| 证书的差异只有结案方式 | 同一电路分别在延迟开 / 关下生成，逐字段比较 `decider.candidates` | 只有落选候选的 `settled`、`verified`、`orientation`、`determinism` 四项不同，胜者与其余字段相同 |
| 完整测试套件 | `python -m pytest tests` | 387 通过，3 跳过（新增 3 个：`tests/test_plan_a.py`） |
| JKU 实际耗时 | `scripts/time_plan_a.py`（延迟开 / 关，同一进程、同一参数） | **1657 s → 386.6 s，加速 4.3 倍**；胜出映射的构造层第二阶段调用从 5 次降到 1 次，4 个候选以"已证明面积更大"结案；GDS SHA-256 相同（`cb74b22a…`）、389.30 µm²、`proven_optimal_in_scope=true`、未结案 0 项。数据 `data/primitive_generalization/accel_plan_a/timing.json` |

### 6.4 其余方案

| 方案 | 做法 | 状态 | 说明 |
|---|---|---|---|
| **B. 第二阶段并行到多个进程** | 各候选的第二阶段互相独立、各自单线程，可同时在多个进程中运行 | 未实施 | 不影响结果；A 之后剩余的阶段 2 已经很少（只有平局候选），收益有限 |
| **C. 结果缓存** | 按"原语 + 实现源码哈希 + 输入的规范化哈希"缓存 | 未实施 | 只加速重复运行：第一次生成新电路时各映射、各候选的模型互不相同，没有可复用的求解 |
| **D. 减小第二阶段本身的代价** | 例如更短的平局目标、分层固定变量 | 不建议 | 会改变面积相等时选中的版图，需要重新冻结结果 |

其余已实施的小项：第二阶段带上一轮的解作热启动（`hint` / `variant_hint`）；加时重解复用上一轮结果；两个下界取较强者。

### 6.5 调度与目标的耦合

延迟只对"第一级是面积"的目标安全（§6.2 的论证依赖"面积是第 1 级"）。目标由任务声明，调度随之切换：

- `area`：`prune=True`，延迟开启；
- `matched_pairs`、`centring`：`prune=False`，不延迟——每个候选都要完整落版，因为面积更小的候选可能在镜像对数或软代价上输掉。

这是"最优性必须带范围"（§1.2 原则 5）在调度上的体现：加速不能改变证明确立的范围，只能跳过与结论无关的计算。


---

## 7. 入口

| 入口 | 做什么 |
|---|---|
| `core.coordinate(circuit, library, pack, output, objective="area", search_symmetry=True, held=None, ...)` | 默认生成入口：三层搜索，写出 GDS，返回结果（含 `decider` 与 `coordinator`） |
| `core.evaluate_options(...)` + `core.conclude(..., objective=)` | 一次评估、按多个目标分别结案（评估不剪枝时评估结果与目标无关；`run_symmetry_objectives.py` 用它） |
| `circuit_generate.generate_circuit(...)` | 映射固定为 `netlist_symmetry` 规则选出的那个，只搜构造层与朝向位置层。`coordinate(search_symmetry=False, objective="area")` 与它逐字节相同（`test_fixed_mapping_mode_is_generate_circuit`） |
| `python -m clay.learning.circuit_cli generate --netlist … --pdk … --library … --output … [--objective area\|matched_pairs\|centring] [--fixed-symmetry] [--inverter-library …]` | 命令行；`--fixed-symmetry` 不搜映射；`--inverter-library` 提供替代构造器的输入 |
| `python -m clay.learning.circuit_cli train / evaluate` | 训练电路库；生成结果与参考版图比较 |
| `scripts/run_comparator_learning.py` | 比较器完整流程：训练 → 隔离进程中生成（审计钩子拒绝打开参考版图）→ 基线 → 评估 |
| `scripts/run_stage_report.py` | 四个样本的生成与对比图 |
| `python -m clay.learning` | 反相器专用流程（反相器训练出的库，两行决策器） |

实现设置 `u`：`quick_seconds`、`final_seconds`、`workers`、`max_rounds`、`orientation_search`、`axes`、`fold_targets`。

---

## 8. 确定性：同一输入得到同一版图

### 8.1 契约

> **相同输入（网表、学习库、PDK deck、参数 θ、实现设置 u）⇒ 逐字节相同的 GDS**，与进程、`PYTHONHASHSEED`、线程数和机器负载无关（在 §8.2 的条件下）。

只要求**可复现**，不要求**唯一**（等价输入，如器件改名，可能得到不同的解：平局规则最后才用名字）。也不是双射：不同输入可以得到同一张版图。

| 位置 | 做法 |
|---|---|
| `netlist_symmetries` / `netlist_symmetry` | 图节点重编号为整数（整数哈希不受哈希种子影响，VF2 遍历顺序固定）；映射按全序键排序 |
| `solve_blocks` | 两阶段：多线程阶段求最优面积；单线程、固定种子、确定性时间的第二阶段，在"面积 = 最优值"下按软代价与规范化平局项定版图（动作决策文档 §4） |
| `solve_area` | 单线程、固定种子、确定性时间 |
| 候选与映射之间 | 按目标向量 + 内容平局规则取最小（§5.3） |
| GDS 写出 | 不写时间戳（`gds_write.py`） |

OR-Tools 9.14 的"确定性并行"（`interleave_search` + 确定性时间）实测不可复现，未采用；纯单线程可复现但太弱（2AM 只到 421.1 µm²，最优 396.3），所以用两阶段。

### 8.2 成立条件

| 条件 | 内容 |
|---|---|
| **C1 同一环境** | 代码版本、Python、OR-Tools 9.14、KLayout、networkx 3.5 相同 |
| **C2 路径稳定** | 每次 `solve_blocks` 的第一阶段是否在墙钟时限内证明最优，各次运行一致（唯一依赖墙钟时间的分支） |
| **C3 默认确定性模式** | `solve_blocks(deterministic=True)`；`solve_area` 单线程 |
| **C4 胜者规范化完成** | 胜者的 `determinism` 不含 `tiebreak_failed`（否则 `finalize` 用更长预算重做一次） |
| **C4′ 证书所需的规范化都完成** | 证书引用了版图的候选都不含 `tiebreak_failed`（证书也可复现的条件） |

### 8.3 证明要点

1. **复合**：每一步都是其输入的函数，则整条流程是函数。
2. **哈希种子只能经由集合遍历或 `hash()` 起作用**（字典按插入顺序遍历是语言规范）。
3. **审计**：生成流程执行的模块中没有直接调用 `hash()`；每个 `set`/`frozenset` 的用法属于四类之一：只做成员判断或集合比较；先排序再使用；结果与顺序无关的聚合（`any`、`len`、并、交、`Counter`）；先确认恰好一个元素再取。审计过的模块：`semantic.py`、`netlist.py`、`circuit.py`、`symmetry.py`、`diffusion_chains.py`、`contact_primitives.py`、`passive_primitives.py`、`action_space.py`、`circuit_generate.py`、`circuit_construct.py`、`relations.py`、`freedom.py`、`actions.py`、`circuit_verify.py`、`contact_observations.py`、`area_placement.py`、`placement_constraints.py`、`rotations.py`、`gds_write.py`、`clay/decision/index.py`、`invoke.py`、`core.py`。分类表中 `determinism: pure` 只允许出现在这些模块。
4. **对称识别**：VF2 在固定遍历顺序下枚举有限个映射；全序键的最后两项（器件对名字、网络映射）保证互不相等，排序唯一。
5. **构造与动作空间**：朝向按 `orientations()` 的固定顺序生成；变体去重保留首次出现者；块按确定的插入顺序进入求解器。
6. **放置求解**：第一阶段证明最优时，最优面积 A* 由模型唯一确定；第二阶段模型 = 原模型 + {面积 = A*}，清除所有提示，单线程、固定种子、确定性时间，输出是模型的函数。未证明最优时丢弃第一阶段结果，单线程从头求解。走哪条路径由 C2 保证一致。
7. **候选间与映射间**：全序键取最小，唯一。
8. **组装、检查与写出**：KLayout 多边形运算对确定输入是函数；GDS 不写时间戳。

### 8.4 实验佐证与已知例外

| 实验 | 结果 |
|---|---|
| 测试电路与两个反相器：种子 0/1/2、线程 1/4/8、独立进程 | GDS 与证书相同（`tests/test_determinism.py`） |
| 三个真实比较器：两个哈希种子完整回归 | GDS SHA-256 相同 |
| 对称识别：三个比较器，种子 0/1/2/7/13 | 映射相同 |

| 违反的条件 | 已观察到的现象 |
|---|---|
| C2 | 重负载下 JKU 曾改走"单线程重解"路径，版图相同但证书路径标签不同 |
| C4′ | 2AM 有落选候选第二阶段超时（`canonical+tiebreak_failed`），GDS 相同，证书 `determinism.reproducible` 为 false，运行间不同 |
| 审计范围外 | 反相器专用流程的 geometry、relation、routing、structural 等库分支未审计 |

---

## 9. 回归与实测结果

### 9.1 回归指纹

`scripts/regression_fingerprint.py` 记录三组指纹，每次重构前后比较：

| 组 | 内容 |
|---|---|
| `assemble` | 46 个构造配置的构造结果哈希（链、行、块、约束意图） |
| `generate` | 6 个生成电路的 GDS 与证书哈希（三个比较器、三个测试电路） |
| `inverter` | 两个反相器的 GDS 与证书 |

指纹存于本地指纹目录（不随仓库发布）。2AM 的证书因 §8.4 的 C4′ 例外运行间不同，比较时只比 GDS。

### 9.2 实测

| 样本 | 工艺 | 面积 | 胜出 | 全局证书 |
|---|---|---|---|---|
| GF180 动态比较器（训练集） | GF180 3.3V | 65.93 µm² | 唯一映射；不额外折叠、竖直轴 | 范围内已证明最优 |
| SKY130 2AM 比较器（训练集） | SKY130 | 396.27 µm² | 两个映射为一类；折叠 8 µm、竖直轴 | 范围内已证明最优 |
| SKY130 JKU 比较器（测试集） | SKY130 | 389.30 µm²（固定规则映射为 401.34，−3.0%） | 16 个映射类中的第 4 个；不额外折叠、竖直轴 | 范围内已证明最优 |
| GF180 反相器 `mcu9t5v0__inv_1`（测试集） | GF180 5V | 14.73 µm² | 替代构造器（反相器训练出的构造）；电路库没有 5V 接触孔策略 | 范围内已证明最优 |
| SKY130 反相器 `hd__inv_1`（训练集） | SKY130 | 3.50 µm² | 默认构造器（替代构造器参与但未胜出） | 范围内已证明最优 |

与参考版图的对比见 `data/primitive_generalization/stage_report_20261115/RESULTS_zh.md`。生成结果只含器件放置，未布线；本地检查不是官方 DRC/LVS。

**耗时**（方案 A 实施前后，JKU 比较器，`scripts/time_plan_a.py`，同一进程、同一参数）：

| | 耗时 | 胜出构造层的第二阶段调用 | 版图 |
|---|---|---|---|
| 方案 A 前 | 1657 s | 5 | 389.30 µm²，SHA-256 `cb74b22a…` |
| 方案 A 后 | **386.6 s（4.3 倍）** | 1（另 4 个以已证明面积结案） | 相同，SHA-256 相同 |

完整测试套件：387 通过，3 跳过。

---

## 10. 局限

1. **映射层没有廉价下界**：每个映射类都要运行构造层。`symmetry_classes` 只合并可证明等价的孪生交换；JKU 的 16 个映射分成 4 组结果完全相同，但它们配对的器件数从 27 到 23 不等，不是孪生交换，现有规则无法证明等价，所以仍要求解 16 次。
2. **折叠目标只试三个值**（`truncated`），扩散固定为共享；都写在证书 `scope` 中。
3. **非面积目标的各级在面积最优放置上计算**：例如 `centring` 比较的是各候选面积最优放置的软代价，不是"在所有放置中软代价最小"。
4. **匹配质量用几何替代量**（镜像对数、软居中代价），没有寄生提取与电性能验证。
5. **布线与电性能反馈**尚未接入；子模块作为独立协调运行（层次化）的接口只在 `core` 的注释中预留。
6. **组合规模**：多层选项相乘时需要层次化、层间独立性声明和廉价下界先粗剪，目前都没有实现。
7. **确定性的例外**见 §8.4。
8. **证据有限**：比较器测试集 1 个、反相器测试集 1 个；"核心按目标选择带来的收益"只在 JKU 一个电路上观察到。

---

## 11. 论文表述中需要注意的地方

| # | 问题 | 论文位置 | 说明 | 本实现的处理 |
|---|---|---|---|---|
| 1 | 确定性与时间预算矛盾 | §1 "deterministic engines"；§3.3 `P=(S,θ,u)`；图 2 `time_budget_sec=900` | 墙钟预算使 `compile(P)` 依赖机器负载；多线程 CP-SAT 在等面积最优解间随机落点 | 两阶段求解与成立条件 C1–C4（§8） |
| 2 | 有预算时的"字典序最小"不是真正的最小 | §3.3 "until the candidate set or evaluation budget is exhausted" | 预算先用完时只是"已评估候选中的最优" | 证书区分"范围内已证明最优"与"未结案" |
| 3 | 图 1 的"p ≈ 1"是期望而非保证 | 图 1 | 需要规格可行且修改不循环 | 建议改为有条件表述 |
| 4 | 几何目标与电性能目标的冲突未交代 | §3.3 式 (4)(5)；表 3 | 面积最小的方案电性能可能不合格 | 反馈原语的接口（§5.5），尚未实现电性能检查 |
| 5 | 布线到放置的反馈只有一次 | §3.1 | 单次迭代不保证收敛 | 反馈可迭代，尚未接入布线 |

"生成核心按目标选择"在 OTA、ADC 实验完成之前，宜作为面向复杂电路的系统架构来写，不宜作为已证实的贡献。若要作为贡献，需要消融：同一组电路分别以"固定优先级"和"按目标选择"运行，比较可证明最优的比例、求解次数与耗时。

---

## 12. 已确认的决定

| # | 问题 | 决定 |
|---|---|---|
| 1 | 目标优先级 | 由任务声明，默认面积第一；电性能为生成后检查，失败走反馈 |
| 2 | 可复现与唯一 | 只要求可复现；平局先比结构与内容，最后比名字 |
| 3 | 输出字节变化 | 接受；变化的 GDS 必须重新通过官方 DRC/LVS，冻结的单原语验证规格由人复核后重新冻结 |
| 4 | 剪枝 | 严格大于；只在目标第一级是面积时用面积下界剪枝 |
| 5 | 45° 斜向构造 | 推后；今后作为新的构造原语（alternative）加入 |
| 6 | 核心与原语的关系 | 决策逻辑写成 `stage: decide` 的原语；核心不是原语；搜索策略固定在核心中 |
| 7 | 单原语验证登记表 | LLM 可以追加条目（`provenance='ai'`、`status='pending_review'`），不能修改已冻结条目；由人批准（改为 `frozen` 并写入 `frozen_entries.json`）。见 `primitive_verify/README_zh.md` §5 |
