# 比较器参考数据集 v1

**状态：样本已收集；未做训练、拟合、特征抽取或网表转换。** `INDEX.json` 是 split 索引，各样本目录的 `SOURCE.json` 记录固定上游 commit、文件来源与 SHA-256。上游 `LICENSE` 随样本保留。

## 数据划分

| Split | 样本 | 工艺 | 主版图 | 设计资料 |
|---|---|---|---|---|
| training | `gf180_ishi_kai_chipathon2023_dynamic_comparator` | GF180MCU 3.3 V | `layout/comp_20240331.gds` | xschem schematic、Magic CDL、PEX SPICE |
| training | `sky130_2amlogic_dynamic_latched` | SKY130 | `layout/comparator.gds` | xschem schematic、layout-extracted SPICE |
| test | `sky130_jku_sar_adc1_adc_comp_latch` | SKY130 | `gds/adc_comp_latch.gds.gz` | xschem schematic、schematic SPICE、layout-extracted LVS SPICE、Magic layout |

JKU 样本严格留在 test split，不得用于拟合或模型选择。其 GDS 来自用户指定的 `iic-jku/SKY130_SAR-ADC1` commit `3cec5823e64cf1ea5c625615ad71f2a505cf27bc`。论文资料：[JKU ePUB](https://epub.jku.at/obvulihs/content/titleinfo/8694768)；上游 README 给出的论文引用见该样本的 `README.md`。

GF180 训练样本来自 Apache-2.0 授权的 [ISHI-KAI Chipathon2023_ADC](https://github.com/ishi-kai/Chipathon2023_ADC/tree/3dce439b2125a9d628e43be40c65cf8fce5d795c/gitefu/comp_20240331)，包含独立比较器的 GDS、Xschem、Magic CDL 和 PEX SPICE。SKY130 训练样本来自 Apache-2.0 授权的 [2AMLogic SKY130 comparator](https://github.com/2AMLogic/sky130-comparator/tree/e90075dd75f5dde7f386326186d8ef3842ace0dd)，上游描述为动态锁存比较器，含版图和提取网表。SKY130 项目的上游 README 称其设计和证据由 AI agents 构建，并报告已提交 DRC/LVS 证据；这些成熟度陈述只作为来源信息记录，本数据集没有重新运行 DRC、LVS 或仿真。

## 使用边界

这是一份原始来源样本集，不是已训练的比较器策略库。每种工艺目前只有一个训练来源，不能单凭这两个样本声称具有泛化能力。文件按上游内容保存；该数据集没有将各样本转换为统一 CDL，也没有证明现有反相器训练/生成入口能够消费比较器网表。后续比较器训练前，需要单独确定拓扑、端口映射、网表规范化及几何观测契约。
