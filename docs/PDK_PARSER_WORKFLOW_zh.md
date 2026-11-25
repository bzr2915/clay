# PDK 解析器：工作流程与中间层 Pack

> 本文说明 PDK 解析器的现状：它读什么、按什么步骤生成 `Pack`、`Pack` 包含什么、原语如何读取它，以及接入新工艺的方法。动作决策（角度、朝向、门控）见 [ACTION_DECIDER_FREEDOM_zh.md](ACTION_DECIDER_FREEDOM_zh.md)；生成核心见 [GLOBAL_COORDINATOR_DESIGN_zh.md](GLOBAL_COORDINATOR_DESIGN_zh.md)；原语清单见 [PRIMITIVE_CATALOG_zh.md](PRIMITIVE_CATALOG_zh.md)。
> 依据 2026-11-15 的代码与实测。命令在 `clay_code/` 下执行。

---

## 1. 作用

PDK 解析器把**代工厂的 KLayout DRC 规则文件（deck）**和**一个按工艺编写的适配器（builder）**，转换成与工艺无关的数据对象 **`Pack`（语义规则包）**。

所有原语只通过 `Pack` 的**语义名称**读取工艺信息，例如 `pack.nm("cut_to_gate")`，而不是 "GF180 的 CO.7"。因此 `Pack` 是 PDK 与原语之间的中间层：换工艺只换 `Pack`，原语与流程代码不变。

```
 ┌──────────────────────┐     ┌───────────────────────────────┐
 │ DRC deck 目录         │     │ 适配器 build_<工艺>()           │
 │ *.drc / *.lydrc       │     │ ・规则 ID → 语义键 对照表         │
 │ (代工厂原文)           │     │ ・GDS 层号、器件模型、器件类别 …  │
 └─────────┬────────────┘     │ ・角度 token → 图层角色           │
           │                  │ ・链构造方式、方向能力、等距声明   │
           ▼                  └───────────────┬───────────────┘
  ① 解析数值规则                                │
  ② 解析角度规则                                │
  ③ 解析精确尺寸规则                            │
           └───────────────┬──────────────────┘
                           ▼
  ④ 绑定语义键（量纲检查、来源标注）
  ⑤ 计算派生量（记录公式）
  ⑥ 映射角度自由度
  ⑦ 组装 Pack 并校验（缺项即报错）
  ⑧ 记录 deck 指纹、按 pdk_id 缓存
                           │
                           ▼
               ┌──────────────────────┐
               │  Pack（中间层）        │──► 构造原语（build_row、build_chain_row、电阻、MIM …）
               │  get_pack(pdk_id)     │──► 放置（place_blocks、solve_area）、组装、检查
               └──────────────────────┘──► 网表解析、学习库校验、自由度模型（动作决策）
```

代码：[clay/pdk/semantic.py](../clay/pdk/semantic.py)。唯一入口：`semantic.get_pack(pdk_id)`。

---

## 2. 输入

| 输入 | 内容 | 来源 |
|---|---|---|
| **DRC deck 目录** | 递归读取所有 `.drc` / `.lydrc` 文件（子目录与文件名都排序后读取，结果与遍历顺序无关） | GF180：`pdks/gf180mcu/libraries/gf180mcu_fd_pr/latest/rules/klayout/drc/rule_decks`<br>SKY130：`pdks/sky130_pdk/libs.tech/klayout/drc` |
| **适配器** | 每个工艺一个函数：`build_gf180_5v`、`build_gf180_3v3`、`build_sky130_1v8` | `semantic.py`，由人编写 |
| ├ 规则绑定表 | `语义键 → (deck 规则 ID, 后备值, 说明)`，例如 `rule("cut_to_gate", "CO.7", ...)` | 适配器内 |
| ├ 角度 token 表 | deck 自己的层名 → 语义图层角色，例如 SKY130 `licon → contact`、`li → metal1`、`m1 → metal2` | `_ANGLE_TOKEN_ROLES` |
| └ 非 deck 元数据 | GDS 层号、器件模型与极性、器件类别、最小沟道长度、标注层、边界层、接触孔堆叠、CDL 单位、标记层、链构造方式、方向能力、正交等距声明 | 适配器内 `Pack(...)` 的参数 |

**谁选择工艺**：网表生成时由命令行 `--pdk`（电路流程）或契约文件的 `pdk` 字段（反相器专用流程）决定。

**已注册的 `pdk_id`**：`gf180mcu_5v`（别名 `gf180mcu`）、`gf180mcu_3v3`、`sky130_1v8`（别名 `sky130`、`sky130a`）。

---

## 3. 工作流程

### ① 解析数值规则：`parse_deck` / `parse_deck_dir`

- 用正则匹配 deck 的输出语句，取出**规则 ID** 与**描述末尾的数值**：

  ```ruby
  m11_l1.output("M1.1", "M1.1 : min. metal1 width : 0.23um")            # GF180
  m1.width(0.14).output("m1.1", "m1.1 : min. m1 width : 0.14um")        # SKY130
  df9.output("DF.9_5V", "DF.9_5V : Min. COMP area (um2). : 0.2025um2")  # 面积规则
  ```
- 统一换算为整数：长度 → nm，面积 → nm²，并记下是否为面积规则。输出 `{规则ID: (数值, 是否面积)}`。
- 描述末尾没有数值的纯拓扑规则跳过；同一 ID 出现多次时第一次出现的值生效。
- 实测：GF180 deck 解析出 368 条带数值的规则，SKY130 解析出 301 条。
- **注意**：读取的是描述字符串里的数值，不是检查表达式里的阈值。GF180 实际使用的 29 条已逐条核对一致；SKY130 尚无同等的逐条核对（§7）。

### ② 解析角度规则：`parse_deck_angles` / `parse_deck_angles_dir`

- 匹配 `x.with_angle(a .. b).output("…", "… non N degree angle …")`，得到 `{token: (边角步长, with_angle 上限)}`。
  - 步长 45：该层图形的边可以是 45° 的整数倍（可以有斜边）；步长 90：只能有水平、竖直边。
- 同一 token 有多条规则时（同一文件或不同文件），所有规则都必须成立，取步长的**最小公倍数**（45 与 90 → 90），防止局部区域的放宽被当成全局自由度。
- 实测：GF180 deck 中所有层都是 45；SKY130 中 `diff/tap/poly/licon/ct(mcon)/via*` 是 90，`nwell/nsdm/psdm/li/m1/m2/hvtp/lvtn/npc/…` 是 45。

### ③ 解析精确尺寸规则：`parse_deck_exact_edges`

有些规则要求某层图形**每条边都正好等于某长度**，deck 写作"把长度不等于 x 的边报出来"：

```ruby
co1_l1 = contact.edges.without_length(0.22.um).extended(0, 0, 0.001, 0.001)        # GF180
co1_l1.output("CO.1", "CO.1 : Min/max contact size. : 0.22µm")
rectLICON.not(rpm.or(urpm)).edges.without_length(0.17).output("licon.1_a/b", ...)  # SKY130
```

- 读**检查表达式本身**（不是描述文字），只认最外层的 `without_length(单个数值)`；`without_length(nil, x)`、嵌在包围检查内部的、注释掉的都不算。
- 适配器把规则绑定到图层角色，写入 `Pack.exact_edges`：GF180 `contact ← CO.1 = 220 nm`；SKY130 `contact ← licon.1_a/b = 170 nm`。
- 用途：动作决策据此严格证明含接触孔的部件不能整体斜放（[ACTION_DECIDER_FREEDOM_zh.md](ACTION_DECIDER_FREEDOM_zh.md) §2.4）。

### ④ 绑定语义键：`_deck_or`

对适配器里的每一项 `语义键 ← 规则 ID`：

1. deck 中**有**该规则：先做**量纲检查**（以 `_area` 结尾的键只能绑定面积规则，其余只能绑定长度规则，不符即报错），再取 deck 值，来源标为 `deck`，记录规则 ID。
2. deck 中**没有**该规则：用适配器给的后备值，来源标为 `convention`，写明理由。
3. 能由其他规则严格推出的值，来源标为 `derived`（例如 SKY130 没有 n/psdm 最小面积规则，用 `nsd.1` 最小宽度的平方）。

结果是 `Pack.r`：`语义键 → RuleValue(value, provenance, source)`。`RuleValue` 自身检查：值为非负整数，来源是三种之一，`source` 非空。

**规则 ID 与语义键的对应由人编写**，解析器不理解陌生 token 的物理含义，这是需要人工把关的环节。

### ⑤ 计算派生量：`_derive`

构造器需要的组合尺寸，按**只含语义键的公式**计算，公式写入 `source`，对任何提供这些键的工艺都成立（单位 nm，实测值）：

| 派生量 | 公式 | GF180 5V | GF180 3.3V | SKY130 |
|---|---|---|---|---|
| `cut_half` | `cut // 2` | 110 | 110 | 85 |
| `cut_pitch` | `cut + cut_space` | 470 | 470 | 340 |
| `pad_half_x` | `cut//2 + metal_over_cut` | 170 | 170 | 165 |
| `pad_half_y` | 让落点金属满足最小面积 `metal_area` | 215 | 215 | 165 |
| `gate_pad_half` | `grid_up(sqrt(metal_area)/2)` | 190 | 190 | 165 |
| `poly_over_cut` | `cut//2 + cut_to_poly` | 180 | 180 | 135 |
| `gate_clear_active` | `max(poly_endcap + metal_over_cut, gate_cut_to_active + cut//2)` | 280 | 280 | 275 |
| `first_sd_contact_x` | `cut_to_active + cut//2` | 180 | 180 | 125 |
| `side_extension` | `max(sd_overhang, cut_to_active + cut + cut_to_gate)` | 440 | 440 | 265 |
| `pad_spacing_width` | `2*first_sd_contact_x + 2*pad_half_x + metal_space` | 930 | 930 | 750 |
| `shared_segment` | `2*cut_to_gate + cut` | 520 | 520 | 280 |
| `opposite_row_clearance` | `max(阱包有源区 + 阱到对向有源区, 注入包有源区 + 注入到对向有源区)` | 1200 | 860 | 520 |

### ⑥ 映射角度自由度：`_freedom`

- 用 `_ANGLE_TOKEN_ROLES[pdk_id]` 把步骤 ② 的 token 翻译成语义图层角色，生成 `Pack.freedom`：`图层角色 → LayerFreedom(step_deg, 来源, 镜像轴)`。
- 多个 token 映射到同一角色时取步长的最小公倍数，并记录全部来源。
- **deck 中没有角度规则的图层不生成条目**，表示"自由度未知"。下游必须把"未知"当作"不能斜放"，不能当作"任意角度"（例如 SKY130 的 `polyres`、`capm`）。
- 描述里的步长大于 `with_angle` 的上限说明 deck 自相矛盾，直接报错；步长必须整除 360°。

### ⑦ 组装与校验：`Pack.__post_init__`

缺任何一项都直接报错，**不用默认值顶替**：

- 必需图层角色 5 个：`active`、`poly`、`contact`、`metal1`、`nwell`；其他角色必须在 `OPTIONAL_ROLES` 白名单内；
- 必需语义键 25 个（`REQUIRED_KEYS`）；
- 声明的标记层必须有层号；同一个键不能既是规则又是派生量；
- 必须给出 NMOS、PMOS 两种极性的最小沟道长度。

### ⑧ 指纹与缓存

- `Pack.deck_sha256`：所用 deck 文件的哈希。学习库与生成结果都记录它，用来追溯"这个版图基于哪一份规则"。
- `get_pack` 按 `pdk_id` 缓存，同一进程内只解析一次。

---

## 4. 输出：Pack 的完整内容

按"它回答原语的什么问题"分组：

| 类别 | 字段 | 含义 | 例子 |
|---|---|---|---|
| **A. 规则数值** | `r` | 语义键 → `RuleValue`（值 + 来源 + 规则 ID） | `cut_to_gate = 150 (deck, CO.7)` |
| **B. 派生量** | `derived` | 由规则按公式算出的组合尺寸（§3 ⑤） | `cut_pitch = 470 (derived, cut + cut_space)` |
| **C. 图层映射** | `layers` | 语义图层角色 → GDS (layer, datatype) | `active` GF180=(22,0)，SKY130=(65,20) |
| | `markers` / `device_markers` | 该工艺器件必须覆盖的标记层 | GF180 5V：`dualgate`、`v5_xtor` |
| | `model_markers` | 某个模型额外需要的标记层 | SKY130 `pfet_01v8_hvt → hvtp` |
| | `annotation_layers` | 网名文字层 | GF180 metal1 → (34,10)；SKY130 li → (67,5) |
| | `boundary_layer` / `frame_layers` | 单元边界层、只构成边框的层（测材料面积时排除） | SKY130 prBoundary (235,4)、(236,0) |
| | `cut_stack` | 每一级通孔连接哪些导体 | SKY130：licon 连扩散/poly↔li，mcon 连 li↔met1 |
| **D. 器件与网表** | `model_polarity` | 模型名 → nmos/pmos，不在表中的模型被拒绝 | `nfet_05v0 → nmos` |
| | `device_classes` | 模型 → 器件类别（mos / res_poly / cap_mim / cap_var）；全局表 `DEVICE_CLASS_TERMINALS` 给出各类别的端子顺序 | SKY130 `sky130_fd_pr__res_high_po → res_poly` |
| | `min_length` | 每种极性的最小沟道长度 | GF180 5V：N 600 / P 500 |
| | `cdl_units`、`cdl_extra_params` | CDL 中裸数字的单位、需原样保留的额外参数 | GF180：米；SKY130：微米 |
| **E. 构造方式** | `chain_construction` | 非普通 MOS 的器件类别如何画成接触扩散链：扩散所在图层角色、规则替换（规则键 → 替代的 pack 键，只在 pack 定义了替代键时生效） | SKY130 `cap_var`：画在 tap 上，`cut_to_gate → var_cut_to_gate`、`cut_to_active → tap_cut_enclosure`；GF180 两个 pack 为空 |
| **F. 几何自由度** | `freedom` | 图层角色 → 允许的边角步长 | GF180 全部 45；SKY130 active 90 |
| | `exact_edges` | 图层角色 → 精确边长 | GF180 contact = 220（CO.1） |
| | `orthogonal_isometries` | 正交旋转与镜像保持合法性的声明及理由（§4.1） | 点名 GF180 的 `O.PL.ORT` 例外 |
| | `direction_capability` | 引擎在"构造 / 放置 / 检查"三方面各能执行的方向；三项都具备的方向才可执行 | 三个 pack 都只有 0/90/180/270 |
| | `capability` | 引擎能执行的操作 | `rotate_orthogonal`；GF180 5V 另有 `structural_lowering` |
| **G. 溯源** | `pdk_id`、`family`、`grid_nm`、`recipe_prefix`、`deck_sha256`、`source` | 标识、网格、配方名前缀、deck 哈希与路径 | — |

读取接口：`pack.nm(键)` → 整数 nm；`pack.rv(键)` → 带来源的完整记录；`pack.has(键)` → 可选键是否存在；`pack.layer(角色)`；`pack.polarity(模型)`；`pack.device_class(模型)`；`pack.length_rule(极性)`；`pack.report()` → 来源统计。

### 4.1 正交等距声明

适配器用一段文字声明：**把 DRC 合法的刚性部件绕网格点旋转 90° 的整数倍或镜像，仍然合法**（宽度、间距、包围、面积、网格、角度集合在这些变换下都不变）。声明必须点名 deck 中依赖方向的规则并说明为什么不适用：

- GF180："宽度方向""平行于栅""垂直于 Poly2"都是相对器件定义的；唯一的绝对方向规则 `O.PL.ORT`（栅宽沿 X 轴）只作用于 `otp_mk` 层，本引擎从不画这一层。
- SKY130：deck 中没有提到绝对方向的规则。

来源性质为"约定"（由人核对 deck 后写下），官方 DRC 仍是最终裁判。有了这个声明，没有角度规则的图层（如 SKY130 的 `capm`）也可以做正交旋转与镜像。

### 4.2 来源统计（`pack.report()`，实测）

| 工艺 | 规则 + 派生总数 | deck | derived | convention |
|---|---|---|---|---|
| GF180 5V | 42 | 29 | 12 | 1（网格 5 nm） |
| GF180 3.3V | 39 | 26 | 12 | 1（网格） |
| SKY130 1.8V | 63 | 49 | 13 | 1（网格） |

除网格这一条约定外，所有数值要么来自代工厂 deck 原文，要么由它们按公式推出。

---

## 5. 原语如何读取 Pack

每个原语需要的语义规则键声明在分类表 `primitive_classification.yaml` 的 `pdk_binding`（必需）与 `optional_binding`（工艺有该层时才读）中，完整列表见原语清单。两项检查保证声明与代码一致：

- `check_pdk_bindings`：原语声明的必需键在给定 pack 中都存在；`runnable_on(pack)` 由此回答"这个原语能不能在这个工艺上运行"；
- `scan_rule_keys`：静态扫描原语函数及其同模块辅助函数中 `nm("…")`、`has("…")`、`r("…")` 读取的键，测试要求扫描到的键都已声明。扫描是下界（经变量传入的键、跨模块调用看不到），只能发现漏声明。

主要读取者：

| 原语 / 步骤 | 从 Pack 读取的内容 |
|---|---|
| 网表展开 `netlist_flatten` | `grid`（W/L 必须在网格上）、`model_polarity`、`device_classes`、`cdl_units` |
| 接触孔规划 `contact_policy_plan` / `contact_plan` | `cut`、`cut_space`、`cut_to_active`、`cut_pitch`、`grid` |
| 链构造 `build_chain_row` → `build_row` | 上一行，加 `cut_to_gate`、`cut_to_poly`、`sd_overhang`、`pad_half_x/y`、`gate_pad_half`、`metal_space`、`poly_space`、`gate_clear_active`、`poly_endcap`、`poly_over_cut`、`implant_over_active`、`well_over_active`、`width_min`、`length_rule`、`model_markers`、`layers`；可选 `oxide_over_*`、`vt_marker_over_gate`、`npc_over_cut`、变容管键；**`chain_construction`**（非普通 MOS 的类别） |
| 指折叠、折叠目标候选 | `width_min`、`cut`、`cut_to_active`、`grid`；变容管 `tap_cut_enclosure`、`var_min_width` |
| 电阻、MIM、放置块 | `res_*`、`rpm_*`、`npc_over_res`、`cap_*`、`plate_*`、`via3*`、`metal4_over_via3`、`active_space`、`poly_space`、`metal_space`、`grid` |
| 动作决策 `angle_verdicts`、`enforce`、`block_space` | `freedom`、`exact_edges`、`orthogonal_isometries`、`direction_capability`、`capability`、`grid_nm`、`layers` |
| 放置 `place_blocks` → `solve_blocks` | `grid`、`well_to_opposite_active`、`well_space` |
| 两行放置（反相器专用流程）`solve_area` | `opposite_row_clearance`、`poly_space`、`metal_space`、`grid` |
| 组装 `materialize` | `layers`、`annotation_layers`、`boundary_layer`、`implant_to_opposite_active`；`close` 模式还有各标记层间距 |
| 检查 `circuit_verify` / `candidate_verify` | 几乎全部必需键及可选键 |
| 学习库校验 | `pdk_id`、`cut_pitch`、`deck_sha256` |

### 5.1 端到端例子：一条规则如何变成几何

以 GF180 的 `CO.7`（接触孔到栅的最小间距）为例：

1. deck 原文 `... .output("CO.7", "CO.7 : Space from COMP contact to Poly2 on COMP. : 0.15µm")` → 步骤 ① 得到 `("CO.7": (150, False))`。
2. 适配器 `rule("cut_to_gate", "CO.7", ...)` → 步骤 ④ 得到 `cut_to_gate = RuleValue(150, deck, "CO.7")`。
3. 步骤 ⑤：`side_extension = max(sd_overhang 400, cut_to_active 70 + cut 220 + cut_to_gate 150) = 440`，`shared_segment = 2×150 + 220 = 520`。
4. `build_row` 用这些值决定扩散链端部段与共享段的长度，画出有源区、多晶、接触孔、metal1。
5. 有源区尺寸变化 → 放置块的框变化 → 放置求解的结果随之变化。

变异实验验证过这条链路：把 deck 副本中 `CO.7` 改为 0.05 µm 后，生成的版图在**原版**官方 DRC 中只报 `CO.7` 一处违例。

### 5.2 Pack 不包含的东西：学习库

"每段放几排接触孔""电阻头部做多大"这类风格参数不是解析器的输出，而是从参考版图学到的，存在学习库中。

| | 来源 | 例子 | 能否跨工艺 |
|---|---|---|---|
| Pack | DRC deck + 适配器 | 接触孔尺寸、间距、一列能放下几排的**上限** | 每个工艺各一份 |
| 学习库 | 参考版图拟合 | 在上限之内**实际**放几排、首孔位置、电阻头部风格 | 不能。库中记录工艺、`cut_pitch_nm`、`deck_sha256`，与当前 Pack 不一致就要求重新拟合 |

Pack 给出合法范围，学习库在范围内给出风格选择；接触孔规划会把学到的排数裁剪到 `[1, 容量]`。

---

## 6. 解析器不做什么

- **不运行 DRC**。版图是否合法以代工厂官方 DRC 为准；内部本地检查读的是同一个 Pack，不能证明 Pack 本身正确。
- **不是 Ruby/KLayout 语法解释器**，只认 `.output(ID, "…: 数值um")`、`.with_angle(a..b).output(…)`、最外层 `without_length(x)` 三种写法。
- **不理解陌生 token**：规则 ID 对应哪个语义键、角度 token 对应哪个图层角色，都由人写在适配器里。
- **Pack 不是完整规则集**：只包含现有原语需要的规则（GF180 deck 有 368 条，Pack 用了 29 条）。

---

## 7. 接入一个新 PDK

1. 把 deck 放到 `pdks/<新工艺>/.../drc/`，确认规则写法符合 §3 ①②③；不符合就先扩展正则。
2. 在 `semantic.py` 中仿照 `build_sky130_1v8` 写 `build_<新工艺>()`：
   - `layers`：至少 5 个必需角色；
   - `r`：25 个必需语义键，**逐条确认规则 ID 与语义键是同一个物理量**；
   - 非 deck 元数据：`min_length`、`model_polarity`、`device_classes`、`annotation_layers`、`boundary_layer`、`cut_stack`、`cdl_units`、`model_markers`；
   - 如有非普通 MOS 的链构造器件（如变容管），在 `chain_construction` 中声明其扩散图层与规则替换；
   - 在 `_ANGLE_TOKEN_ROLES` 中加入角度 token 映射，每个会出现在器件中的图层都要映射；
   - `orthogonal_isometries`（核对 deck 中依赖方向的规则后写）、`direction_capability`、`capability`。
3. 在 `_BUILDERS` 中注册。
4. 运行 §8 的检查。缺键、缺层、面积/长度混绑会直接报错；`runnable_on(pack)` 列出哪些原语可以在新工艺上运行。
5. 准备该工艺的学习库（训练得到；`cut_pitch_nm` 与 `deck_sha256` 必须与新 Pack 一致）。
6. 生成版图后**用该工艺的官方 DRC/LVS 检查**，这是判断适配器正确性的最终标准。

---

## 8. 已知限制与检查方法

**已知限制**：

- 数值规则读的是描述文字而不是检查阈值（§3 ①）。GF180 用到的 29 条已人工核对一致；SKY130 尚无同等的逐条核对。
- 正交等距声明、规则 ID 与语义键的对应、角度 token 映射都由人编写，正确性靠官方 DRC 复核。

**检查方法**：

```powershell
# 单元测试：数值解析、来源、缺键报错、量纲检查、自由度、精确尺寸、规则键声明
python -m pytest tests/test_pdk_binding.py tests/test_pdk_semantic.py tests/test_freedom.py tests/test_pdk_registry.py tests/test_action_space.py tests/test_primitive_classification.py

# 来源统计
python -c "from clay.pdk import semantic as s; import json; print(json.dumps(s.get_pack('sky130_1v8').report(), indent=1, ensure_ascii=False))"

# 自由度、派生量、链构造方式
python -c "from clay.pdk import semantic as s; p=s.get_pack('sky130_1v8'); print({k:(v.step_deg,v.source) for k,v in p.freedom.items()}); print({k:v.value for k,v in p.derived.items()}); print(dict(p.chain_construction))"

# 哪些原语能在某个工艺上运行
python -c "from clay.pdk import semantic as s; from clay.learning import primitive_classification as pc; print(pc.runnable_on(pc.load(), s.get_pack('gf180mcu_5v')))"
```
