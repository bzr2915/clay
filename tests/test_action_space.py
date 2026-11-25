"""Generalised action decider: per-angle verdicts, joint orientation search,
the gate-orientation symmetry breaking, and the comparator certificate.

Every input is generated here; no dataset file is needed. The exhaustive
cross-checks are the point: the joint CP-SAT model must reach exactly the
minimum that enumerating every orientation combination reaches.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_circuit_primitives import DIFF, GF, PASSIVE, SKY, _library  # noqa: E402

from clay.layout.synth.area_placement import solve_blocks  # noqa: E402
from clay.learning import circuit_generate as cg  # noqa: E402
from clay.learning.action_space import solver_blocks, ties  # noqa: E402
from clay.learning.freedom import (  # noqa: E402
    DECK_FORBIDDEN, DECK_INFEASIBLE, EXECUTABLE, NOT_EXECUTABLE, Part, angle_verdicts,
    build_freedom, grid_edge)
from clay.learning.netlist import parse_spice  # noqa: E402
from clay.learning.symmetry import netlist_symmetry  # noqa: E402
from clay.pdk import semantic  # noqa: E402

#: Deck-dependent tests run when the open PDK decks are found
#: (CLAY_GF180MCU_PATH / CLAY_SKY130_PATH, or ./pdks); skipped otherwise.
needs_decks = pytest.mark.skipif(
    not (semantic.get_pack("gf180mcu_5v").deck_sha256 and semantic.get_pack("sky130_1v8").deck_sha256),
    reason="open PDK DRC decks not found; set CLAY_GF180MCU_PATH and CLAY_SKY130_PATH")

GF_DIFF = (DIFF.replace("nfet_01v8", "nfet_03v3").replace("pfet_01v8", "pfet_03v3")
           .replace("L=0.15", "L=0.28u").replace("W=2 ", "W=2u ").replace("W=1 ", "W=1u "))


# ---------------------------------------------------------------------------
# every candidate angle is closed with a reason
# ---------------------------------------------------------------------------


def test_grid_edge_arithmetic():
    # 45 degrees: length a*g*sqrt(2) is never an integer multiple of g.
    for length in (170, 220, 260, 1000):
        assert grid_edge(length, 5, 45) is None
        assert grid_edge(length, 5, 135) is None
    # Irrational tangent (30, 60, ...): no lattice direction at all (Niven).
    for d in (15, 30, 60, 75):
        assert grid_edge(220, 5, d) is None
    # Quarter turns always work; the length must itself sit on the grid.
    assert grid_edge(220, 5, 0) == (44, 0) and grid_edge(220, 5, 90) == (44, 0)
    assert grid_edge(222, 5, 0) is None


@needs_decks
def test_exact_edge_rules_are_read_from_check_expressions():
    gf = semantic.parse_deck_exact_edges_dir(semantic.get_pack("gf180mcu_5v").source)
    assert gf["CO.1"] == 220
    sk = semantic.parse_deck_exact_edges_dir(semantic.get_pack("sky130_1v8").source)
    assert sk["licon.1_a/b"] == 170
    assert "ct.1_a/b" not in sk          # commented out in the deck: not a rule
    assert "via.4a" not in sk            # without_length inside an enclosure check
    assert "via3.1_b" not in sk          # without_length(nil, x): a maximum only
    assert semantic.get_pack("gf180mcu_5v").exact_edges["contact"].value == 220


@needs_decks
def test_gf180_diagonal_angles_are_proven_infeasible_for_a_contacted_part():
    pack = semantic.get_pack("gf180mcu_3v3")
    verdicts = {v.degrees: v for v in angle_verdicts(pack, ("active", "poly", "contact", "metal1"))}
    assert sorted(verdicts) == [0, 45, 90, 135, 180, 225, 270, 315]
    for d in (45, 135, 225, 315):
        assert verdicts[d].status == DECK_INFEASIBLE
        assert "CO.1" in verdicts[d].reason
    for d in (0, 90, 180, 270):
        assert verdicts[d].status == EXECUTABLE


@needs_decks
def test_sky130_diagonal_angles_are_closed_by_the_angle_rule():
    pack = semantic.get_pack("sky130_1v8")
    verdicts = angle_verdicts(pack, ("active", "poly", "contact", "metal1"))
    assert len(verdicts) == 8
    assert {v.status for v in verdicts if v.degrees % 90} == {DECK_FORBIDDEN}


@needs_decks
def test_a_part_without_cuts_keeps_its_diagonal_angles_open():
    """No rule refutes a diagonal metal1 strap: it is open, never silently closed."""
    pack = semantic.get_pack("gf180mcu_3v3")
    f = build_freedom(pack, [Part("strap", ("metal1",))])
    open_ = [v for v in f.part("strap").verdicts if not v.closed]
    assert [v.degrees for v in open_] == [45, 135, 225, 315]
    assert all(v.status == NOT_EXECUTABLE for v in open_)
    assert f.report()["parts"]["strap"]["open_angles"] == [45, 135, 225, 315]


# ---------------------------------------------------------------------------
# joint orientation model == exhaustive enumeration
# ---------------------------------------------------------------------------


def _assemble(text, pack, fold, axis):
    c = parse_spice(text, pack)
    sym = netlist_symmetry(c)
    a = cg.assemble(c, _library(), pack, fold_target=fold, axis=axis, symmetry=sym)
    kw = dict(grid=pack.nm("grid"), well_to_opposite=pack.nm("well_to_opposite_active"),
              well_space=pack.nm("well_space"), axis=axis, pairs=a["pairs"],
              centred=a["centred"], seconds=60, workers=4)
    return a, kw


@pytest.mark.parametrize("text,pack,fold,axis", [
    (PASSIVE, SKY, None, "vertical"),
    (PASSIVE, SKY, None, "horizontal"),
    (GF_DIFF, GF, None, "vertical"),
    (GF_DIFF, GF, 500, "horizontal"),
])
def test_joint_orientation_solve_matches_exhaustive_enumeration(text, pack, fold, axis):
    a, kw = _assemble(text, pack, fold, axis)
    spaces = a["spaces"]
    joint = solve_blocks(solver_blocks(spaces), ties=ties(spaces), **kw)
    assert joint.status == "OPTIMAL"
    blocks = solver_blocks(spaces)
    free = [n for n, s in spaces.items() if not s.mirror_of]
    mates = {s.mirror_of: n for n, s in spaces.items() if s.mirror_of}
    assert any(len(spaces[n].variants) > 1 for n in free)     # there is something to decide
    best = None
    for combo in itertools.product(*(range(len(spaces[n].variants)) for n in free)):
        idx = dict(zip(free, combo))
        idx.update({mate: idx[src] for src, mate in mates.items()})
        fixed = {}
        for n, s in spaces.items():
            e = {k: v for k, v in blocks[n].items() if k != "variants"}
            e.update(s.variants[idx[n]].boxes())
            fixed[n] = e
        r = solve_blocks(fixed, **kw)
        assert r.status == "OPTIMAL"
        best = r.area_nm2 if best is None else min(best, r.area_nm2)
    assert joint.area_nm2 == best


def test_keeping_gates_as_built_loses_nothing():
    """Gates turned a quarter on one axis == gates as built on the other axis."""
    def solve(axis):
        a, kw = _assemble(PASSIVE, SKY, None, axis)
        spaces = a["spaces"]
        return solve_blocks(solver_blocks(spaces), ties=ties(spaces), **kw).area_nm2

    original = cg.block_space

    def turned(*args, keep_gates=False, keep_axis_symmetry=False, **kwargs):
        s = original(*args, **kwargs)
        if keep_gates or keep_axis_symmetry:
            s.variants = [v for v in s.variants if v.degrees % 180 == 90]
            assert s.variants
        return s

    built = {ax: solve(ax) for ax in ("vertical", "horizontal")}
    cg.block_space = turned
    try:
        twin = {ax: solve(ax) for ax in ("vertical", "horizontal")}
    finally:
        cg.block_space = original
    assert built["vertical"] == twin["horizontal"]
    assert built["horizontal"] == twin["vertical"]


# ---------------------------------------------------------------------------
# the comparator flow uses the decider and certifies its answer
# ---------------------------------------------------------------------------


@needs_decks
@pytest.mark.parametrize("text,pack", [(PASSIVE, SKY), (GF_DIFF, GF)])
def test_generate_circuit_certificate(tmp_path, text, pack):
    c = parse_spice(text, pack)
    r = cg.generate_circuit(c, _library(), pack, tmp_path / "o.gds", quick_seconds=10,
                            final_seconds=20, workers=4, log=lambda *_: None)
    cert = r["decider"]
    assert r["best"]["verification"]["passed"]
    assert cert["proven_optimal_in_model"], cert["open_candidates"]
    assert not cert["open_angles"]
    for row in cert["candidates"]:
        assert row["settled"] != "open"
        if row["settled"].startswith("bound"):
            assert row["lower_bound_nm2"] >= cert["best_area_nm2"]
        if row.get("area_nm2") is not None and row["verified"]:
            assert row["area_nm2"] >= cert["best_area_nm2"]
    for name, block in cert["blocks"].items():
        assert len(block["angle_verdicts"]) == 8, name
    # Orientation is decided, not fixed: the result records the chosen variant.
    assert set(r["best"]["orientation"]) == set(cert["blocks"])
    # The symmetry mapping is fixed by rule, not searched, so the scope of the
    # proof must name it (GLOBAL_COORDINATOR_DESIGN_zh.md §8.1, step S0).
    if r["symmetry"] is not None and r["learned_used"]["relations"]["mirror_pairs"] == "enforce":
        assert cert["fixed_choices"]["symmetry_mapping"]["pairs"] == r["symmetry"]["pairs"]
        assert "other netlist symmetry mappings" in cert["scope"]
    else:
        assert "symmetry_mapping" not in cert["fixed_choices"]
        assert "symmetry mappings" not in cert["scope"]


def test_scope_names_the_fixed_symmetry_mapping(tmp_path):
    """A differential pair has a symmetry, so the branch above must not be the
    vacuous one for at least this circuit."""
    c = parse_spice(GF_DIFF, GF)
    r = cg.generate_circuit(c, _library(), GF, tmp_path / "o.gds", quick_seconds=10,
                            final_seconds=20, workers=4, log=lambda *_: None)
    assert r["symmetry"] is not None
    assert "symmetry_mapping" in r["decider"]["fixed_choices"]


def test_orientation_search_never_does_worse_than_fixed_orientation(tmp_path):
    c = parse_spice(PASSIVE, SKY)
    lib = _library()
    new = cg.generate_circuit(c, lib, SKY, tmp_path / "n.gds", quick_seconds=10,
                              final_seconds=20, workers=4, log=lambda *_: None)
    old = cg.generate_circuit(c, lib, SKY, tmp_path / "o.gds", quick_seconds=10,
                              final_seconds=20, workers=4, log=lambda *_: None,
                              orientation_search=False)
    assert new["best"]["area_nm2"] <= old["best"]["area_nm2"]


def test_a_failed_oriented_layout_falls_back_and_is_not_claimed_optimal(tmp_path, monkeypatch):
    """If the oriented optimum fails a layout check, the fixed orientation the
    flow used before is the fallback - never a worse outcome than before - and
    the certificate refuses to call the result proven."""
    real = cg.evaluate

    def failing(*args, orient=True, **kwargs):
        r, layout, a = real(*args, orient=orient, **kwargs)
        if orient and not r.get("deferred"):   # deferred: no layout or check yet
            r = {**r, "verification": {**r["verification"], "passed": False,
                                       "failed": ["synthetic_check"]}}
        return r, layout, a

    monkeypatch.setattr(cg, "evaluate", failing)
    c = parse_spice(GF_DIFF, GF)
    out = cg.generate_circuit(c, _library(), GF, tmp_path / "o.gds", quick_seconds=10,
                              final_seconds=10, workers=4, log=lambda *_: None)
    cert = out["decider"]
    assert out["best"]["verification"]["passed"]
    assert not cert["proven_optimal_in_model"]
    assert any("fallback" in row for row in cert["candidates"])
    assert all(row["fallback"]["oriented_failed"] == ["synthetic_check"]
               for row in cert["candidates"] if "fallback" in row)
