# Collected SKY130 reference circuits

Four externally authored open-source circuits collected for schematic and layout study and future Clay case development. The contribution consists of reference design files, source attribution and file provenance. These samples have not been integrated into the runnable Clay benchmark suite.

| Circuit | Reference files | Upstream |
| --- | --- | --- |
| [Basic operational amplifier](sky130/01_basic_opamp/) | Xschem, Magic and GDS | [efabless/sky130_ef_ip__opamp](https://github.com/efabless/sky130_ef_ip__opamp) |
| [Instrumentation amplifier](sky130/02_instrumentation_amplifier/) | Hierarchical Xschem, schematic/layout netlists, Magic and GDS | [efabless/sky130_pa_ip__instramp](https://github.com/efabless/sky130_pa_ip__instramp) |
| [Two-stage operational amplifier](sky130/03_two_stage_opamp/) | Xschem, reference netlist and selected GDS | [SkillSurf/ttsky25_se_opamp](https://github.com/SkillSurf/ttsky25_se_opamp) |
| [Bandgap reference](sky130/04_bandgap_reference/) | GDS, extracted netlist, Magic wrapper and design documents | [guru-gitrepo/TTSKY26a-analog-bgr](https://github.com/guru-gitrepo/TTSKY26a-analog-bgr) |

## Scope and validation

Each directory retains its original license and notices, together with a `PROVENANCE.md` describing the checked upstream revision and collection limitations. `manifest.json` records source and packaged SHA-256 hashes. Two SPICE files contain documented edits to source-path comments only; circuit statements are unchanged. One SVG source-path label was also made relative, with its circuit drawing and author attribution preserved.

The four selected GDS files were checked for readable record streams and closed internal cell references. No new circuit simulation, DRC, LVS or parasitic extraction has been performed for this collection. Performance and sign-off statements in copied upstream documents belong to the original authors.

Use an installed SKY130 PDK with appropriate Xschem/Magic setup to inspect the source designs. This subset omits complete characterization environments and unrelated workflow files. The bandgap Magic source is incomplete; its GDS is included for layout inspection. Consult each sample's provenance notes before attempting reproduction.

## Licenses and attribution

The third-party circuit files retain their Apache-2.0 licenses and original attribution. Collection and packaging do not transfer authorship of the circuits. Newly written catalog and provenance documentation is contributed under the parent Clay project's AGPL-3.0-only license; it does not replace the licenses of the third-party files.
