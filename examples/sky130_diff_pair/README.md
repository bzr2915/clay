# SKY130 differential-pair placement and verification example

This deliberately limited example exercises the current public `LayoutTech.resolve('sky130')` and `build_device` interfaces. It generates independent thin-oxide PMOS units in two complementary rows, with an edge dummy at each row end. No private input files, model cards, rule decks or reference layouts are required.

## Run from the repository root

```sh
python examples/sky130_diff_pair/generate.py --output work/pair
python examples/sky130_diff_pair/generate.py --output work/pair_wide --width-um 2 --repeats 2
python -m pytest tests/test_sky130_diff_pair_example.py -q
```

Use a new output directory. W and L are per-unit micrometres, not total width. `repeats` repeats ABBA/BAAB blocks: each side has 4*repeats active units, plus four edge dummies overall. Defaults are illustrative independent SKY130 inputs, not a migrated circuit sizing solution.

Active gates/drains are declared INP/OUTP or INN/OUTN. All active sources declare TAIL, while their bodies declare VDD. Every dummy declares all four terminals VDD. These are terminal metadata assignments: the example does NOT physically route these nets. Dummy policy is intentionally explicit and differs from circuits that connect edge dummy drains to signal nets.

## What is actually checked

`placement.gds` is reopened. Per-instance intersections of SKY130 active (65/20) and poly (66/20) must contain one channel. Channel dimensions and measured centers are reported. The complementary equal-size pattern must have coincident active channel centers. Four-terminal metadata and repeat/width changes are tested. Existing output directories are never overwritten.

`report.json` distinguishes these checks from electrical extraction. This is a placement example, NOT a functioning differential input stage or a DRC/LVS-clean benchmark. It uses separate unit wells and does not implement shared diffusion, a common well ring, routing, PEX or simulation. Geometric centroid equality does not prove mismatch or parasitic balance. No source/body short is inferred from a declared common source.

## Current limitations and observed blocker

The public portable device generator emits off-grid geometry for the default input: 568 local polygon vertex occurrences fail a 5 nm screen. The report preserves FAIL; no snap, tolerance relaxation, or rule edit is applied. These are counted before instance translation, so this is not a complete placed manufacturing-grid audit. Full independent DRC/LVS is NOT_RUN. Native SKY130 materialization is a separate interface requiring a running Docker engine, a pinned tool image and its installed SKY130 PDK. A resolved portable adapter alone does not establish native PDK availability.

Before extending this into a routed benchmark, use and validate the native geometry path, recheck actual source and bulk conductors, derive access layers from the producer, then verify routed connectivity with independent extraction. Only allow diffusion sharing after comparing adjacent terminal nets and confirming the producer supports it. Do not copy a layout-specific route or ring recipe into this example.
