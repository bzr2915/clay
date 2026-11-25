<div align="center">

<img src="docs/assets/clay-logo.png" alt="clay" width="76" />

# clay

<p><strong>English</strong> &nbsp;·&nbsp; <a href="README.zh-CN.md">简体中文</a></p>

### **Analog layout was never automatable. Until now.**

*A netlist describes structure. A layout embodies physics.*<br />
***clay*** *reads the why, keeps the why, and builds new circuits from the why. Alone.*

[![ASPDAC '27](https://img.shields.io/badge/ASPDAC_'27-Paper-111111?style=flat-square)](https://doi.org/10.1145/3840404.3860941)
[![Open PDKs](https://img.shields.io/badge/PDKs-SKY130_%7C_GF180MCU-222222?style=flat-square)](benchmarks/)
[![CI](https://img.shields.io/github/actions/workflow/status/bzr2915/clay/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/bzr2915/clay/actions/workflows/ci.yml)
[![Release](https://img.shields.io/badge/Release-v0.4.45-333333?style=flat-square)](https://github.com/bzr2915/clay/releases/tag/v0.4.45)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL--3.0-111111?style=flat-square)](LICENSE)

<br />

<a href="https://github.com/bzr2915/clay/releases/download/v0.4.45/clay-hero.mp4">
  <img src="docs/assets/clay-hero.gif" alt="clay — from declarative design intent to verified silicon" width="100%" />
</a>

<sub>From declarative design intent to verified silicon · <a href="https://github.com/bzr2915/clay/releases/download/v0.4.45/clay-hero.mp4">Watch full HD video (720p)</a></sub>

</div>

---

<table>
<tr>
<td width="33%" valign="top">

### **I · `clay` learns.**
Every good layout has its reasons. `clay` reads the reasons off the silicon and writes them down as declarations.

</td>
<td width="33%" valign="top">

### **II · `clay` builds.**
Declarations in. Verified circuits out. Sized, placed, routed. Unattended.

</td>
<td width="33%" valign="top">

### **III · `clay` creates.**
Learned once, a decision works forever. New sizes. New fabs. New circuits.

</td>
</tr>
</table>

---

## Design Intent as Code

In `clay`, analog design expertise is expressed as typed, composable **intent primitives** rather than fragile procedural scripts. The compiler binds those declarations to open-source PDKs (**GF180MCU** and **SKY130**), synthesizes physical GDSII, and checks every electrical and geometric obligation automatically.

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

## Getting Started

### 1. Install & Build

Requires **Python 3.12+** and a **C++20 compiler** (GCC 11+, Clang 14+, or MSVC 2022).

```bash
git clone https://github.com/bzr2915/clay.git
cd clay

pip install -e ".[dev]"
python cpp/clay_pnr/build.py --target all
```

### 2. Synthesize Your First Layout (GF180MCU & SKY130)

Works immediately after building—no external PDK download required for standalone GDSII synthesis:

```bash
# Synthesize a Folded-Cascode OTA on GF180MCU
./cpp/clay_pnr/build/modular/bin/clay_engine_cli synth \
    benchmarks/gf180mcu/folded_cascode_ota/source/design.clay \
    -o fc_ota_gf180.gds

# Retarget a Two-Stage OTA directly to SKY130
./cpp/clay_pnr/build/modular/bin/clay_engine_cli synth \
    benchmarks/gf180mcu/two_stage_ota/source/design.clay \
    --pdk sky130 -o two_stage_ota_sky130.gds
```

### 3. Connect Open PDKs & Run Full Signoff

For full SPICE simulation, DRC, LVS, and parasitic extraction, connect **GF180MCU** / **SKY130** locally or use the open-source [`hpretl/iic-osic-tools`](docker/README.md) Docker image (Magic, Netgen, KLayout, ngspice, SKY130A & GF180MCUD pre-installed):

```bash
# Auto-detect installed open PDKs or fetch upstream GF180MCU / SKY130
python scripts/setup_open_pdks.py --detect --write-config
# python scripts/setup_open_pdks.py --install-gf180 --install-sky130

# Validate a benchmark case
python -m clay case validate benchmarks/gf180mcu/two_stage_ota
python -m clay case validate benchmarks/sky130/five_t_ota

# Run the end-to-end physical synthesis and signoff flow
python -m clay flow run --case benchmarks/gf180mcu/two_stage_ota --force
```

---

## Open-PDK Benchmarks

Ready-to-run analog circuits targeting **GlobalFoundries GF180MCU** and **SkyWater SKY130**:

| Circuit Family | GF180MCU | SKY130 |
|---|---|---|
| **Operational Amplifiers** | [`two_stage_ota`](benchmarks/gf180mcu/two_stage_ota) · [`folded_cascode_ota`](benchmarks/gf180mcu/folded_cascode_ota) · [`telescopic_ota`](benchmarks/gf180mcu/telescopic_ota) · [`five_t_ota`](benchmarks/gf180mcu/five_t_ota) | [`quickstart_amp`](benchmarks/sky130/quickstart_amp) · [`five_t_ota`](benchmarks/sky130/five_t_ota) · [`two_stage_miller_ota`](benchmarks/sky130/two_stage_miller_ota) · [`folded_cascode_ota`](benchmarks/sky130/folded_cascode_ota) · [`telescopic_cascode_ota`](benchmarks/sky130/telescopic_cascode_ota) · [`fully_differential_ota`](benchmarks/sky130/fully_differential_ota) |
| **Voltage Regulators** | [`ldo_regulator`](benchmarks/gf180mcu/ldo_regulator) | [`ldo_regulator`](benchmarks/sky130/ldo_regulator) |
| **Voltage References** | [`bandgap_reference`](benchmarks/gf180mcu/bandgap_reference) | [`bandgap_reference`](benchmarks/sky130/bandgap_reference) |
| **Mixed-Signal / RF / Bias** | [`cs_dac_4bit`](benchmarks/gf180mcu/cs_dac_4bit) | [`lc_tank_vco`](benchmarks/sky130/lc_tank_vco) · [`bias_tree`](benchmarks/sky130/bias_tree) |

Additional curated SKY130 schematic/layout reference circuits (with provenance and SHA-256 manifests) are available in [`references/circuits/`](references/circuits/).

---

## Development & Contributing

### Repository Structure

| Directory | Role |
|---|---|
| [`clay/`](clay/) | Language frontend, intent compiler, PDK adapters (`gf180mcu`, `sky130`), and physical flow orchestration |
| [`cpp/clay_pnr/`](cpp/clay_pnr/) | C++20 geometry, MOS PCell, placement, routing, and binary GDSII engine + `clay_engine_cli` |
| [`benchmarks/`](benchmarks/) | Open-PDK circuit benchmarks (`.clay` designs, testbenches, and manifests) |
| [`references/`](references/) | Collected open-source SKY130 reference circuits with upstream provenance and licenses |
| [`data/`](data/) | Open-PDK BSIM `gm/Id` lookup tables for GF180MCU and SKY130 |
| [`tools/`](tools/) | KLayout DRC/LVS runners, intent optimizer, formatter, linter, and GDS renderer |
| [`docker/`](docker/) | Open-source signoff setup guide (`hpretl/iic-osic-tools`) |

### Running Tests & Submitting a PR

```bash
# Run the full local verification suite before opening a PR
python scripts/verify_oss_package.py
```

Contributions—new open-PDK benchmarks, design primitives, placement/routing improvements, and bug fixes—are welcome via **Fork & Pull Request**. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for guidelines.

### Authors & Maintainers

- **Zhaori Bi (毕朝日)** ([@bzr2915](https://github.com/bzr2915)) — Fudan University
- **Yuchen Jiang (姜雨辰)** ([@Jiangyc08](https://github.com/Jiangyc08)) — Fudan University
- **Rouxi Huang (黄柔希)** ([@HRXrosy](https://github.com/HRXrosy)) — Fudan University
- **Zhoushu Lu (鲁洲舒)** ([@luzhoushu](https://github.com/luzhoushu)) — Fudan University
- **Chongye Wei (魏重烨)** ([@chongyewei](https://github.com/chongyewei)) — Fudan University

---

## Citation

If you use **clay** in your research or open-source work, please cite our ASP-DAC '27 paper:

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

## License

Copyright © 2026 Zhaori Bi, Fudan University.

**clay** is licensed under the **[GNU Affero General Public License v3.0 (AGPL-3.0-only)](LICENSE)**. Derivative works, redistributions, and modified network/cloud deployments (§13) must provide complete corresponding source code under AGPL-3.0.

External open-source PDKs (installed separately):
[SkyWater SKY130](https://github.com/google/skywater-pdk) (Apache-2.0) · [GlobalFoundries GF180MCU](https://github.com/google/gf180mcu-pdk) (Apache-2.0)

<br />

<div align="center">
  <img src="docs/assets/clay-logo.png" alt="clay seal" width="24" align="absmiddle" />
  &nbsp; <em>read the why, build the how.</em> &nbsp;·&nbsp; <strong>大智不工</strong>
</div>
