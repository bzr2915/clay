<div align="center">

<img src="docs/assets/clay-logo.png" alt="clay" width="76" />

# clay

<p><a href="README.md">English</a> &nbsp;·&nbsp; <strong>简体中文</strong></p>

### **大智不工**

*识其形，存其道，无人值守生成模拟电路。*

[![ASPDAC '27](https://img.shields.io/badge/ASPDAC_'27-论文-111111?style=flat-square)](https://doi.org/10.1145/3840404.3860941)
[![开源工艺](https://img.shields.io/badge/开源工艺-SKY130_%7C_GF180MCU-222222?style=flat-square)](benchmarks/)
[![CI](https://img.shields.io/github/actions/workflow/status/bzr2915/clay/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/bzr2915/clay/actions/workflows/ci.yml)
[![Release](https://img.shields.io/badge/Release-v0.4.45-333333?style=flat-square)](https://github.com/bzr2915/clay/releases/tag/v0.4.45)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL--3.0-111111?style=flat-square)](LICENSE)

<br />

<a href="https://github.com/bzr2915/clay/releases/download/v0.4.45/clay-hero.mp4">
  <img src="docs/assets/clay-hero.gif" alt="clay —— 从声明式设计意图到可验证版图" width="100%" />
</a>

<sub>从声明式设计意图到可验证硅片版图 · <a href="https://github.com/bzr2915/clay/releases/download/v0.4.45/clay-hero.mp4">观看 720p 高清视频</a></sub>

</div>

---

<table>
<tr>
<td width="33%" valign="top">

### **一 · `clay` 会学。**
版图意图，识之于硅，凝练为道。

</td>
<td width="33%" valign="top">

### **二 · `clay` 会建。**
道即入，电路自成。

</td>
<td width="33%" valign="top">

### **三 · `clay` 会创造。**
尺寸可易，工艺可迁，电路可新。

</td>
</tr>
</table>

---

## 设计意图即代码

在 `clay` 中，模拟电路设计经验被表达为类型化、可组合的**设计意图原语**，而非脆弱的过程式脚本。编译器将这些声明绑定到开源工艺库（**GF180MCU** 与 **SKY130**），自动完成版图综合并检查全部物理与电学约束。

```clay
pdk "gf180mcu";

layout_intent two_stage_ota {
  @objective(area, minimize);
  @place_group(nmos_band, devices=(MN3, MN4, MN6, MNBIAS, MNSINKP));
  @place_group(pmos_band, devices=(MP1, MP2, MP6, MPBIAS, MPTAIL));
  @band(top=vdd, bottom=vss, center=passive_bank);

  @array(load_mirror_2, style=shared_diffusion, order=ratio);
  @guard_ring(group=(MP1, MP2), well=n, tie=vdd, hug=tight, role=tap);
  @trunk(vdd, layer=M3, orient=H, width=0.6, edge=top);
  @wire_width(vout, 0.6u);
}

quality two_stage_ota {
  @kpi(dc_gain_db, ">=", 70, unit="dB");
  @kpi(gbw_hz, ">=", 15e6, unit="Hz");
  @kpi(pm_deg, ">=", 60, unit="deg");
  @require(drc_clean);
}

flow two_stage_ota {
  pdk "gf180mcu";
  steps (compile, layout, drc, lvs, pex, post, intent, sign);
}
```

---

## 快速开始

### 1. 安装与构建

需要 **Python 3.12+** 与 **C++20 编译器**（GCC 11+、Clang 14+ 或 MSVC 2022）。

```bash
git clone https://github.com/bzr2915/clay.git
cd clay

pip install -e ".[dev]"
python cpp/clay_pnr/build.py --target all
```

### 2. 生成你的第一份 GDSII 版图（GF180MCU & SKY130）

构建完成后即可直接运行独立综合，无需预先下载外部 PDK：

```bash
# 在 GF180MCU 上综合折叠共源共栅运放
./cpp/clay_pnr/build/modular/bin/clay_engine_cli synth \
    benchmarks/gf180mcu/folded_cascode_ota/source/design.clay \
    -o fc_ota_gf180.gds

# 直接将两级运放迁移综合至 SKY130
./cpp/clay_pnr/build/modular/bin/clay_engine_cli synth \
    benchmarks/gf180mcu/two_stage_ota/source/design.clay \
    --pdk sky130 -o two_stage_ota_sky130.gds
```

### 3. 接入开源工艺库并运行完整验证流

如需运行完整 SPICE 仿真、DRC、LVS 与寄生参数提取（PEX），可在本地配置 **GF180MCU** / **SKY130**，或直接使用开源 [`hpretl/iic-osic-tools`](docker/README.md) Docker 镜像（预装 Magic、Netgen、KLayout、ngspice 及 SKY130A / GF180MCUD）：

```bash
# 自动探测本机开源 PDK，或拉取上游 GF180MCU / SKY130
python scripts/setup_open_pdks.py --detect --write-config
# python scripts/setup_open_pdks.py --install-gf180 --install-sky130

# 校验基准电路包
python -m clay case validate benchmarks/gf180mcu/two_stage_ota
python -m clay case validate benchmarks/sky130/five_t_ota

# 运行端到端物理综合与签核流程
python -m clay flow run --case benchmarks/gf180mcu/two_stage_ota --force
```

---

## 开源工艺基准与参考电路

覆盖 **GlobalFoundries GF180MCU** 与 **SkyWater SKY130** 的开箱即用模拟电路基准：

| 电路类型 | GF180MCU | SKY130 |
|---|---|---|
| **运算放大器（OTA）** | [`two_stage_ota`](benchmarks/gf180mcu/two_stage_ota) · [`folded_cascode_ota`](benchmarks/gf180mcu/folded_cascode_ota) · [`telescopic_ota`](benchmarks/gf180mcu/telescopic_ota) · [`five_t_ota`](benchmarks/gf180mcu/five_t_ota) | [`quickstart_amp`](benchmarks/sky130/quickstart_amp) · [`five_t_ota`](benchmarks/sky130/five_t_ota) · [`two_stage_miller_ota`](benchmarks/sky130/two_stage_miller_ota) · [`folded_cascode_ota`](benchmarks/sky130/folded_cascode_ota) · [`telescopic_cascode_ota`](benchmarks/sky130/telescopic_cascode_ota) · [`fully_differential_ota`](benchmarks/sky130/fully_differential_ota) |
| **线性稳压器（LDO）** | [`ldo_regulator`](benchmarks/gf180mcu/ldo_regulator) | [`ldo_regulator`](benchmarks/sky130/ldo_regulator) |
| **带隙基准（Bandgap）** | [`bandgap_reference`](benchmarks/gf180mcu/bandgap_reference) | [`bandgap_reference`](benchmarks/sky130/bandgap_reference) |
| **数模转换 / 射频 / 偏置** | [`cs_dac_4bit`](benchmarks/gf180mcu/cs_dac_4bit) | [`lc_tank_vco`](benchmarks/sky130/lc_tank_vco) · [`bias_tree`](benchmarks/sky130/bias_tree) |

另收录了 4 套带完整溯源记录与 SHA-256 校验清单的开源 SKY130 原理图/版图参考电路，详见 [`references/circuits/`](references/circuits/)。

---

## 开发与贡献

### 仓库目录

| 目录 | 说明 |
|---|---|
| [`clay/`](clay/) | 语言前端、意图编译器、开源 PDK 适配器（`gf180mcu`、`sky130`）与物理流程编排 |
| [`cpp/clay_pnr/`](cpp/clay_pnr/) | C++20 几何引擎、MOS PCell、布局、布线、二进制 GDSII 模块及独立命令行 `clay_engine_cli` |
| [`benchmarks/`](benchmarks/) | 开源工艺电路基准（`.clay` 源码、仿真网表与清单） |
| [`references/`](references/) | 收录的开源 SKY130 参考电路、溯源文档与原始开源协议 |
| [`data/`](data/) | GF180MCU 与 SKY130 开源 BSIM `gm/Id` 查找表 |
| [`tools/`](tools/) | KLayout DRC/LVS、意图优化器、代码格式化、静态检查与版图渲染工具 |
| [`docker/`](docker/) | 开源签核环境说明（`hpretl/iic-osic-tools`） |

### 运行测试与提交 PR

```bash
# 提交 Pull Request 前运行本地全量自检
python scripts/verify_oss_package.py
```

欢迎通过 **Fork & Pull Request** 贡献新的开源工艺基准、设计原语、布局布线算法改进或问题修复，详情参见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

### 作者与维护者

- **毕朝日（Zhaori Bi）** ([@bzr2915](https://github.com/bzr2915)) — 复旦大学
- **姜雨辰（Yuchen Jiang）** ([@Jiangyc08](https://github.com/Jiangyc08)) — 复旦大学
- **黄柔希（Rouxi Huang）** ([@HRXrosy](https://github.com/HRXrosy)) — 复旦大学
- **鲁洲舒（Zhoushu Lu）** ([@luzhoushu](https://github.com/luzhoushu)) — 复旦大学
- **魏重烨（Chongye Wei）** ([@chongyewei](https://github.com/chongyewei)) — 复旦大学

---

## 论文引用

如果 **clay** 对您的研究或开源项目有所帮助，欢迎引用我们的 ASP-DAC '27 论文：

```bibtex
@inproceedings{bi2027clay,
  author    = {Zhaori Bi},
  title     = {CLAY: A Programming Model for Intent-Driven Analog Circuit Design Automation},
  booktitle = {Proceedings of the 32nd Asia and South Pacific Design Automation Conference (ASPDAC '27)},
  year      = {2027},
  month     = jan,
  location  = {Tokyo, Japan},
  publisher = {ACM},
  doi       = {10.1145/3840404.3860941},
  isbn      = {979-8-4007-2923-2/2027/01}
}
```

---

## 开源协议

Copyright © 2026 毕朝日（Zhaori Bi），复旦大学。

**clay** 采用强传染开源协议 **[GNU Affero General Public License v3.0 (AGPL-3.0-only)](LICENSE)**。任何衍生作品、再分发或面向外部用户的网络/云端服务部署（§13）均须在 AGPL-3.0 协议下开放完整对应源代码。

外部开源工艺库（独立安装，遵循原协议）：
[SkyWater SKY130](https://github.com/google/skywater-pdk) (Apache-2.0) · [GlobalFoundries GF180MCU](https://github.com/google/gf180mcu-pdk) (Apache-2.0)

<br />

<div align="center">
  <img src="docs/assets/clay-logo.png" alt="clay seal" width="24" align="absmiddle" />
  &nbsp; <strong>clay</strong> —— <em>识其形，成其道。</em>
</div>
