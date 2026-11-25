"""Circuit-general primitives (comparator learning): parse, symmetry, chains,
construction, placement and verification on small, self-contained circuits.

Every input is generated here; no dataset file is needed.
"""
from __future__ import annotations

import inspect

import pytest

from clay.learning import circuit_generate as cg
from clay.learning.circuit_library import fit_library
from clay.learning.diffusion_chains import diffusion_chains, fold_devices
from clay.learning.netlist import parse_spice
from clay.learning.symmetry import netlist_symmetry
from clay.pdk import semantic


SKY = semantic.get_pack("sky130_1v8")
GF = semantic.get_pack("gf180mcu_3v3")

DIFF = """.subckt dp inp inn outp outn bias vdd vss
M1 outn inp tail vss nfet_01v8 W=2 L=0.15 nf=2
M2 outp inn tail vss nfet_01v8 W=2 L=0.15 nf=2
M3 tail bias vss vss nfet_01v8 W=1 L=0.15
M4 outn outp vdd vdd pfet_01v8 W=1 L=0.15
M5 outp outn vdd vdd pfet_01v8 W=1 L=0.15
.ends
"""

PASSIVE = """.subckt pc inp inn outp outn vdd vss
M1 outn inp vss vss nfet_01v8 W=1 L=0.15
M2 outp inn vss vss nfet_01v8 W=1 L=0.15
XC1 outn vss vss sky130_fd_pr__cap_var_lvt W=2 L=0.5
XC2 outp vss vss sky130_fd_pr__cap_var_lvt W=2 L=0.5
XM1 outn vss sky130_fd_pr__cap_mim_m3_1 W=3 L=2
XM2 outp vss sky130_fd_pr__cap_mim_m3_1 W=3 L=2
XR1 outn vdd vss sky130_fd_pr__res_high_po_0p35 l=3 w=0.35
XR2 vdd outp vss sky130_fd_pr__res_high_po_0p35 l=3 w=0.35
.ends
"""


def _library(pdks=("sky130_1v8", "gf180mcu_3v3")):
    obs = []
    for i, pdk in enumerate(pdks):
        pack = semantic.get_pack(pdk)
        lo = pack.nm("cut_to_active")
        obs.append({
            "sample_id": f"S{i}", "pdk": pdk, "gds_sha256": f"sha{i}", "gates": 2,
            "gate_orientation_uniformity": 1.0, "gates_vertical": True,
            "contacts": [{"width_nm": 1000, "count": 1, "columns": 1,
                          "first_offset_nm": lo + 100}],
            "gates_per_island": [1, 1], "nwell_islands": 1, "pmos_body_domains": 1,
            "pmos_devices": 1, "netlist_symmetry": {"pairs": [["A", "B"]]},
            "mirror_exact": {"vertical": {"paired_fraction": 1.0, "centred_fraction": 0.0},
                             "horizontal": {"paired_fraction": 0.0, "centred_fraction": 1.0}},
            "mirror_tolerant": {"vertical": {"paired_fraction": 1.0, "centred_fraction": 0.0},
                                "horizontal": {"paired_fraction": 0.0, "centred_fraction": 1.0}},
            "best_axis": "vertical", "axis_relative_to_gates": "parallel",
            "resistors": [{"head_cut_nm": [190, 2000], "poly_over_head_cut_nm": 100,
                           "metal_over_head_cut_nm": 100, "marker_over_poly_nm": 200,
                           "relation_to_channel_length": "parallel"}] if pdk == "sky130_1v8" else []})
    return fit_library(obs, {p: semantic.get_pack(p) for p in pdks})


def test_parse_flattens_hierarchy_and_drops_parasitics():
    text = (".subckt inv a y vdd vss\nXP y a vdd vdd pfet_01v8 W=0.84 L=0.15 nf=2\n"
            "XN y a vss vss nfet_01v8 W=0.42 L=0.15\n.ends\n"
            ".subckt top i o vdd vss\nx1 i m vdd vss inv\nx2 m o vdd vss inv\nR9 m o 12\n.ends\n")
    c = parse_spice(text, SKY)
    assert c.name == "top" and len(c.devices) == 4 and c.dropped_parasitics == 1
    assert c.devices[0].name == "x1/XP" and c.devices[0].net("d") == "m"
    assert c.devices[0].nf == 2 and c.devices[0].w_nm == 840


@pytest.mark.parametrize("bad", [
    ".subckt t a\nM1 a a a a unknown_model W=1 L=0.15\n.ends\n",       # unknown model
    ".subckt t a\nM1 a a a a nfet_01v8 W=1.0003 L=0.15\n.ends\n",       # off grid
    ".subckt t a\nM1 a a a a nfet_01v8 L=0.15\n.ends\n",                # missing W
    ".subckt t a\nM1 a a a a nfet_01v8 W=20000 L=0.15\n.ends\n",        # implausible size
])
def test_parse_fails_closed(bad):
    with pytest.raises(ValueError):
        parse_spice(bad, SKY)


def test_symmetry_finds_the_differential_swap_and_rejects_asymmetry():
    s = netlist_symmetry(parse_spice(DIFF, SKY))
    assert s is not None and ("M1", "M2") in s.pairs and ("M4", "M5") in s.pairs
    assert s.fixed == ("M3",) and s.net_map["outp"] == "outn"
    asym = DIFF.replace("M5 outp outn vdd vdd pfet_01v8 W=1", "M5 outp outn vdd vdd pfet_01v8 W=2")
    assert netlist_symmetry(parse_spice(asym, SKY)) is None


@pytest.mark.parametrize("share", [True, False])
def test_chains_share_diffusion_only_at_equal_nets(share):
    c = parse_spice(DIFF, SKY)
    fingers = fold_devices(c, 500, pack=SKY)
    chains = diffusion_chains(fingers, netlist_symmetry(c), share=share)
    assert sorted(f.key for ch in chains for f in ch.fingers) == sorted(f.key for f in fingers)
    for ch in chains:
        for f, a, b in zip(ch.fingers, ch.segment_nets, ch.segment_nets[1:]):
            assert {f.source, f.drain} == {a, b}
    # Mirror chains carry the mate devices.
    by = {ch.name: ch for ch in chains}
    for ch in chains:
        if ch.mirror_of:
            assert len(ch.fingers) == len(by[ch.mirror_of].fingers)


def test_fold_respects_the_contactable_minimum():
    c = parse_spice(".subckt t a y vss\nM1 y a vss vss nfet_01v8 W=1 L=0.15\n.ends\n", SKY)
    widths = {f.w_nm for f in fold_devices(c, 100, pack=SKY)}
    assert min(widths) >= SKY.nm("cut") + 2 * SKY.nm("cut_to_active")


@pytest.mark.parametrize("text,pack", [(DIFF, SKY), (PASSIVE, SKY),
                                       (DIFF.replace("nfet_01v8", "nfet_03v3")
                                        .replace("pfet_01v8", "pfet_03v3")
                                        .replace("L=0.15", "L=0.28u").replace("W=2 ", "W=2u ")
                                        .replace("W=1 ", "W=1u "), GF)])
def test_generated_layout_passes_its_own_verification(tmp_path, text, pack):
    c = parse_spice(text, pack)
    result = cg.generate_circuit(c, _library(), pack, tmp_path / "out.gds", quick_seconds=3,
                                 final_seconds=5, finalists=1, workers=4, log=lambda *_: None)
    best = result["best"]
    assert best["verification"]["passed"], best["verification"]["failed"]
    assert best["verification"]["checks"]["devices_match"]
    assert best["area_matches_solver"]
    assert (tmp_path / "out.gds").exists()


def test_generation_api_takes_no_reference_layout():
    params = set(inspect.signature(cg.generate_circuit).parameters)
    assert not any("ref" in p or "gds" in p for p in params), params


def test_library_records_what_it_does_not_imitate():
    lib = _library()
    assert lib["relations"]["mirror_pairs"]["decision"] == "enforce"
    assert "reason" in lib["not_imitated"]
    assert lib["contact_policies"]["sky130_1v8"]["rows"] == {"kind": "constant", "count": 1}
