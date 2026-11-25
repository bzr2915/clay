# Experimental primitive learning / generation

> **Open-source release scope.** This repository ships the comparator layout
> flow for SKY130 and GF180MCU (placement only, no routing). The inverter
> entry scripts mentioned further below (`run_primitive_generalization.py`,
> `run_construction_search.py`, `train_open_inverter_samples.py`) and their
> data are not included; those sections are kept as design notes.
>
> Point the rule packs at the open PDK DRC decks first:
> `CLAY_SKY130_PATH` (sky130A root) and `CLAY_GF180MCU_PATH` (gf180mcu root),
> e.g. via `scripts/setup_open_pdks.py`. Without them training and generation
> stop with an explicit error. Then:
>
> ```bash
> python scripts/run_comparator_learning.py          # train, generate, evaluate
> python -m clay.learning.circuit_cli generate --netlist N --pdk sky130_1v8 \
>     --library data/primitive_generalization/comparator/library.json --output OUT.gds
> ```
>
> Reference results were produced with ortools 9.14. The pinned ortools
> 9.8.3296 gives the same area and checks on the JKU test comparator but may
> pick a different, equally good orientation among tied optima.

**Circuit-general flow (comparator learning, 2026-11-11).** `netlist.py`, `symmetry.py`,
`diffusion_chains.py`, `passive_primitives.py`, `circuit_library.py`, `circuit_verify.py`,
`circuit_generate.py` and `circuit_cli.py` accept any netlist the rule pack can construct
(MOS, poly resistor, MIM, varactor) instead of a single inverter. See
`data/primitive_generalization/comparator/` and `docs/PRIMITIVE_CATALOG_zh.md`.

Joint training on the two downloaded original cells is available via
`python scripts/train_open_inverter_samples.py`. The explicit GF180 and SKY130
CDL/GDS adapters preserve model classes and confirm physical W/L and labelled
G/S/D connectivity. The frozen v3 structural primitive contains a learned
terminal-equality partition and unanimous optional relation observations; the
operator skeleton and physical providers remain engineering priors. It has no
trained size selector. See `data/primitive_generalization/two_process_training/RESULTS_zh.md`.

SKY130 contributes to structural training and source holdout checks. It is
**not** yet connected to a validated placement/cost provider in this experiment;
attempting SKY130 generation fails explicitly. The v3 library executes through
the GF180 provider using the full 16-candidate control. Cross-PDK raw areas are
never pooled as training rewards. Two sources do not establish generalization.

The revised construction experiment is available via
`python scripts/run_construction_search.py`. It enumerates 16 independent
NMOS/PMOS rotations and free pair placement, retains PDK-derived S/D contact
rows (matching the original INV_X1 contact counts), and ranks constructions by
validated generated area **before** placement is run on the target. Use a v2
policy with `python -m clay.learning ... --candidate-budget 1` (or 4/16).
The contract must declare `floorplan: free_single_pair_rotations` and
`device_recipe: gf180_5v_contact_rows_v2`. This revised path supports exactly
one inverter; unsupported compositions fail rather than silently falling back.

The primary reachability test still finds no smaller layout for the original
INV_X1. The learned ordering is a diagnostic performance ranking, not evidence
of successful area reduction. Full search has identical capabilities for the
learned and untrained controls. See
`data/primitive_generalization/gf180_inv_1_v2/RESULTS_zh.md` for original-area
definitions, candidate results, budget comparisons and limitations. The v1
entry below remains available for historical comparisons and compositions.

This is the S1 implementation of `PRIMITIVE_GENERALIZATION_PLAN.md`, separate
from exact reference reconstruction and the production `.clay` compiler flow.

From the repository root:

```powershell
python scripts/run_primitive_generalization.py
```

The default training source is the sibling `open_source_inverters/gf180_inv_1`
directory. Override with `--source`; artifacts default to
`data/primitive_generalization/gf180_inv_1` (override with `--output`).

To generate from a **target netlist** and a **frozen library** only:

```powershell
python -m clay.learning --netlist target.cdl --library primitive_rules.json --contract contract.json --output result
```

Example contract (the port roles are supplied explicitly, never guessed):

```json
{
  "high": "VDD", "low": "VSS", "n_body": "VPW", "p_body": "VNW",
  "pdk": "gf180mcu_5v", "mode": "free_placement_external_taps",
  "floorplan": "two_polarity_bands",
  "device_recipe": "gf180_5v_single_finger_contacted_v1",
  "grid_nm": 5, "solver_seconds": 5
}
```

`solver_seconds` is the budget for **each** of the two stages: area first,
style second with the achieved area fixed. Time-limited solutions retain their
FEASIBLE status and area lower bound. The schema stores categorical preferences
only. The runtime API has no reference-path parameter. A fresh-process Python
file audit blocks training-data reads; this is not an operating-system sandbox.

Supported scope: `nfet_05v0`, `pfet_05v0`, M=1 and nf=1; unambiguous inverters
and acyclic compositions; two equipotential polarity bands; no rotation,
folding, shared active or inter-device routing. Same-polarity implants/wells
are consolidated. G/S/D have independent contact + metal1 access; body nets
remain separate and require external shared taps. Inputs outside this scope
fail explicitly. Target dimensions are never silently clamped.

The topology hypothesis, geometric constructor and PDK constraints are manual
engineering priors. Training learns which executable style relation matches
the sample, stores candidate acceptance evidence, and freezes the result.
This is not a claim of full MDL learning or automatically discovered topology.
Compare B1 (empty library), B2 (manual center alignment), B3 (learned alignment)
using the same geometry and solver. The initial INV_X1 experiment has **zero
same-contract area improvement** and no learned area advantage.

Tests:

```powershell
python -m pytest tests/test_primitive_generation.py
```

The two upstream-training integration tests require the optional source fixture;
all other tests generate their own inputs. Geometry checks are a documented local
subset, not a full foundry deck, routed LVS, PEX or manufacturability signoff.
