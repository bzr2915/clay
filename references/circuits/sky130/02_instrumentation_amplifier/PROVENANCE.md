# Source and collection notes

- Upstream: [https://github.com/efabless/sky130_pa_ip__instramp](https://github.com/efabless/sky130_pa_ip__instramp)
- Checked revision: [`67a57bbfeef03e70fdaea05ec05004bdb09a8e51`](https://github.com/efabless/sky130_pa_ip__instramp/tree/67a57bbfeef03e70fdaea05ec05004bdb09a8e51)
- Attribution: Phil Allen. Original notices are retained.
- Third-party license: [Apache-2.0](LICENSE).
- Original collection inventory date: 2026-11-11. Source comparison date: 2026-11-24.

The main GDS is a lossless decompression of the upstream .gds.gz. Magic also requires the external SKY130 HVL standard-cell library. Absolute source-path comments in the schematic netlist were made relative without changing circuit statements. The displayed source-path label in the schematic SVG was also made relative, retaining the circuit drawing and author name.

This directory is a selected reference subset. See the upstream repository for omitted characterization tools, workflows and documents. Results described in the copied README or documents are reports from the original authors. This contribution does not add new simulation, DRC, LVS, PEX or Clay-flow validation results.
