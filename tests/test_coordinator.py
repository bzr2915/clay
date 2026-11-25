"""Step S4: the coordinator core (docs/GLOBAL_COORDINATOR_DESIGN_zh.md §6).

Pinned here: the symmetry-mapping options and their sound reduction; that the
objective, not a fixed rule, picks among options; that the tie-break depends
on content only (G2: adding a never-winning option, or reordering options,
leaves the winner unchanged); and that the fixed-mapping mode is exactly
``generate_circuit``.
"""
import hashlib
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_circuit_primitives import PASSIVE, SKY, _library  # noqa: E402

from clay.decision import core  # noqa: E402
from clay.learning import circuit_generate as cg  # noqa: E402
from clay.learning.netlist import parse_spice  # noqa: E402
from clay.learning.symmetry import (Symmetry, netlist_symmetries, netlist_symmetry,  # noqa: E402
                                    symmetry_classes)


# ---------------------------------------------------------------- options


def test_the_fixed_rule_mapping_is_the_first_option():
    c = parse_spice(PASSIVE, SKY)
    found = netlist_symmetries(c)
    assert found and found[0] == netlist_symmetry(c)
    keys = [(tuple(sorted(s.device_map.items())), tuple(sorted(s.net_map.items())))
            for s in found]
    assert len(set(keys)) == len(keys)


TWINS = (".subckt tw inp inn outp outn vss\n"
         "M1 outn inp vss vss nfet_01v8 W=1 L=0.15\n"
         "M2 outp inn vss vss nfet_01v8 W=1 L=0.15\n"
         "MD1 vss vss vss vss nfet_01v8 W=1 L=0.15\n"
         "MD2 vss vss vss vss nfet_01v8 W=1 L=0.15\n"
         ".ends\n")


def _sym(pairs, fixed):
    dev = {a: b for a, b in pairs} | {b: a for a, b in pairs} | {f: f for f in fixed}
    nets = {"inp": "inn", "inn": "inp", "outp": "outn", "outn": "outp", "vss": "vss"}
    return Symmetry(nets, dev, ("inp", "inn"), tuple(sorted(pairs)), tuple(sorted(fixed)))


def test_twin_exchange_is_merged_and_nothing_else_is():
    c = parse_spice(TWINS, SKY)
    a = _sym([("M1", "M2"), ("MD1", "MD2")], [])          # dummies swapped
    b = _sym([("M1", "M2")], ["MD1", "MD2"])              # dummies fixed
    # MD1 and MD2 are twins, so a mapping that names MD2 where another names
    # MD1 is the same construction ...
    a2 = _sym([("M1", "M2"), ("MD2", "MD1")], [])
    classes = symmetry_classes(c, [a, a2, b])
    # ... but pairing the twins and fixing them are different constructions.
    assert [len(k) for k in classes] == [2, 1]


# ---------------------------------------------------------------- choice


def _rec(area, soft, pairs, fold=None, axis="vertical"):
    return {"kind": "solved", "verified": True, "area": area, "status": "OPTIMAL",
            "candidate": {"fold_target": fold, "axis": axis},
            "result": {"solver": {"soft_symmetry_cost": soft}, "pairs": pairs}}


def _option(k, recs):
    return {"option": k, "symmetry": None, "members": 1, "error": None,
            "search": {"records": recs, "best": recs[0]}}


# The JKU pattern of design doc §8.1: three mapping classes, each best under
# one objective.
A = _option(0, [_rec(389_300_000, 32760, 3)])
B = _option(1, [_rec(392_040_000, 0, 1)])
C = _option(2, [_rec(401_340_000, 19600, 4)])


@pytest.mark.parametrize("objective,expected", [("area", 0), ("centring", 1),
                                                ("matched_pairs", 2)])
def test_the_objective_picks_the_mapping(objective, expected):
    assert core.choose([A, B, C], objective)[1]["option"] == expected


def test_unknown_objective_is_refused():
    with pytest.raises(ValueError, match="unknown objective"):
        core.evaluate_options(None, _library(), SKY, objective="beauty")


def test_tie_break_is_by_content_not_position():
    """G2: equal on every objective level, the winner is decided by the
    candidate's content; list order and extra losing options do not matter."""
    x = _rec(100, 0, 2, fold=None)
    y = _rec(100, 0, 2, fold=4000)
    first = core.choose([_option(0, [x, y])], "area")[2]
    assert core.choose([_option(0, [y, x])], "area")[2] is first is x
    loser = _option(1, [_rec(999, 0, 0)])
    assert core.choose([loser, _option(0, [y, x])], "area")[2] is x


def test_candidate_key_is_the_enumeration_order_of_the_generator():
    """For the lists the generator builds, content order equals position, so
    results did not change when position was replaced by content."""
    folds, axes = [None, 8000, 4000], ["vertical", "horizontal"]
    cands = [{"fold_target": f, "axis": a} for f in folds for a in axes]
    assert sorted(cands, key=cg.candidate_key) == cands


# ---------------------------------------------------------------- end to end


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_fixed_mapping_mode_is_generate_circuit():
    c = parse_spice(PASSIVE, SKY)
    d = Path(tempfile.mkdtemp())
    kw = dict(quick_seconds=10, final_seconds=20, workers=4, log=lambda *_: None)
    old = cg.generate_circuit(c, _library(), SKY, d / "old.gds", **kw)
    new = core.coordinate(c, _library(), SKY, d / "new.gds", search_symmetry=False, **kw)
    assert _sha(d / "old.gds") == _sha(d / "new.gds")
    assert new["best"]["area_nm2"] == old["best"]["area_nm2"]
    g = new["coordinator"]
    assert g["scope"][0].startswith("symmetry mapping: one, fixed")
    assert g["proven_optimal_in_scope"] == old["decider"]["proven_optimal_in_model"]


def test_searching_mappings_never_does_worse_on_area():
    c = parse_spice(PASSIVE, SKY)
    d = Path(tempfile.mkdtemp())
    kw = dict(quick_seconds=10, final_seconds=20, workers=4, log=lambda *_: None)
    fixed = cg.generate_circuit(c, _library(), SKY, d / "fixed.gds", **kw)
    searched = core.coordinate(c, _library(), SKY, d / "s.gds", **kw)
    assert searched["best"]["area_nm2"] <= fixed["best"]["area_nm2"]
    levels = searched["coordinator"]["levels"]["symmetry_mapping"]
    assert len(levels) == len(symmetry_classes(c, netlist_symmetries(c)))
    assert sum(1 for row in levels if row["settled"] == "winner") == 1
