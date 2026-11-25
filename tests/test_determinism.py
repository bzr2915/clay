"""P1 determinism contract: same program, same layout (byte for byte).

GLOBAL_COORDINATOR_DESIGN_zh.md §2. Every primitive must be a function of its
input: independent of the Python hash seed, the CP-SAT thread count and the
time the GDS was written. Measured failures this guards against:

* ``netlist_symmetry`` picked the first VF2 involution, whose order followed
  the per-process string hash (JKU comparator: seeds 0/1 vs 2 differed);
* multi-threaded ``solve_blocks`` returned a different equal-area placement on
  every run (three runs, three layouts);
* KLayout writes the current time into every GDS.

Cross-process checks run in subprocesses so that PYTHONHASHSEED really varies.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from test_circuit_primitives import DIFF, PASSIVE, SKY, _library  # noqa: E402

from clay.layout.synth.area_placement import solve_blocks  # noqa: E402
from clay.learning import circuit_generate as cg  # noqa: E402
from clay.learning.action_space import solver_blocks, ties  # noqa: E402
from clay.learning.actions import decide_actions, prune  # noqa: E402
from clay.learning.freedom import (  # noqa: E402
    GRID_UNREALIZABLE, NOT_EXECUTABLE, Part, angle_verdicts, build_freedom, grid_direction)
from clay.learning.netlist import parse_spice  # noqa: E402
from clay.learning.symmetry import netlist_symmetry  # noqa: E402
from clay.pdk import semantic  # noqa: E402


def _run(code: str, seed: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": str(seed),
           "PYTHONPATH": str(ROOT) + os.pathsep + str(ROOT / "tests")}
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT),
                         capture_output=True, text=True, timeout=900)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


# ---------------------------------------------------------------------------
# netlist_symmetry: independent of the hash seed
# ---------------------------------------------------------------------------


def test_netlist_symmetry_is_independent_of_the_hash_seed():
    code = (
        "import json\n"
        "from test_circuit_primitives import DIFF, PASSIVE, SKY\n"
        "from clay.learning.netlist import parse_spice\n"
        "from clay.learning.symmetry import netlist_symmetry\n"
        "print(json.dumps([netlist_symmetry(parse_spice(t, SKY)).as_dict()"
        " for t in (DIFF, PASSIVE)], sort_keys=True))\n")
    results = {_run(code, seed) for seed in (0, 1, 2, 3)}
    assert len(results) == 1, results


def test_symmetry_choice_prefers_structure_before_names():
    sym = netlist_symmetry(parse_spice(DIFF, SKY))
    # The differential swap moves the input pair and the cross-coupled load.
    assert len(sym.pairs) == 2 and sym.fixed == ("M3",)


# ---------------------------------------------------------------------------
# solve_blocks: independent of the thread count and of repetition
# ---------------------------------------------------------------------------


def _passive_candidate(axis="vertical"):
    c = parse_spice(PASSIVE, SKY)
    sym = netlist_symmetry(c)
    a = cg.assemble(c, _library(), SKY, fold_target=None, axis=axis, symmetry=sym)
    sp = a["spaces"]
    kw = dict(grid=SKY.nm("grid"), well_to_opposite=SKY.nm("well_to_opposite_active"),
              well_space=SKY.nm("well_space"), axis=axis, pairs=a["pairs"],
              centred=a["centred"], soft_centred=a["soft"], seconds=20, ties=ties(sp))
    return solver_blocks(sp), kw


def _signature(r):
    return (r.area_nm2, sorted(r.origins_nm.items()), sorted(r.variant_index.items()))


def test_solve_blocks_returns_the_same_placement_for_any_thread_count():
    blocks, kw = _passive_candidate()
    results = [solve_blocks(blocks, workers=w, **kw) for w in (8, 8, 1, 4)]
    assert all(r.determinism == "canonical" for r in results), [r.determinism for r in results]
    assert len({json.dumps(_signature(r)) for r in results}) == 1


def test_free_mode_is_still_available_and_says_so():
    blocks, kw = _passive_candidate()
    r = solve_blocks(blocks, workers=8, deterministic=False, **kw)
    assert r.determinism == "free"


# ---------------------------------------------------------------------------
# end to end: identical GDS bytes and certificate across seeds and threads
# ---------------------------------------------------------------------------


_GEN = (
    "import hashlib, json, sys, tempfile, pathlib\n"
    "from test_circuit_primitives import PASSIVE, SKY, _library\n"
    "from clay.learning import circuit_generate as cg\n"
    "from clay.learning.netlist import parse_spice\n"
    "out = pathlib.Path(tempfile.mkdtemp()) / 'o.gds'\n"
    "r = cg.generate_circuit(parse_spice(PASSIVE, SKY), _library(), SKY, out, quick_seconds=10,\n"
    "                        final_seconds=20, workers={workers}, log=lambda *_: None)\n"
    "cert = r['decider']\n"
    "keep = {{k: cert[k] for k in ('winner', 'best_area_nm2', 'candidates', 'determinism',\n"
    "        'proven_optimal_in_model', 'open_candidates')}}\n"
    "print(json.dumps([hashlib.sha256(out.read_bytes()).hexdigest(), keep], sort_keys=True,\n"
    "                 default=str))\n")


def test_generated_gds_is_byte_identical_across_seeds_and_threads():
    results = [json.loads(_run(_GEN.format(workers=w), seed))
               for seed, w in ((0, 8), (1, 8), (2, 4))]
    hashes = {r[0] for r in results}
    certs = {json.dumps(r[1], sort_keys=True) for r in results}
    assert len(hashes) == 1, hashes
    assert len(certs) == 1
    assert results[0][1]["determinism"]["reproducible"]
    assert results[0][1]["proven_optimal_in_model"]


_INV = (
    "import hashlib, sys, tempfile, pathlib\n"
    "sys.path.insert(0, 'tests')\n"
    "from test_pdk_semantic import _smoke_library, _contract, GF180_CDL, SKY130_CDL\n"
    "from clay.learning import contact_primitives as contacts\n"
    "from clay.learning.circuit import parse_cdl\n"
    "from clay.pdk import semantic\n"
    "out = pathlib.Path(tempfile.mkdtemp()) / 'o.gds'\n"
    "pack = semantic.get_pack('{pdk}')\n"
    "cdl = GF180_CDL if '{pdk}' == 'gf180mcu_5v' else SKY130_CDL\n"
    "contacts.generate(_smoke_library(pack), parse_cdl(cdl), _contract(pack), out, pack=pack,\n"
    "                  candidate_budget=1, dedupe_rotations=True)\n"
    "print(hashlib.sha256(out.read_bytes()).hexdigest())\n")


@pytest.mark.parametrize("pdk", ["gf180mcu_5v", "sky130_1v8"])
def test_inverter_gds_is_byte_identical_across_seeds(pdk):
    hashes = {_run(_INV.format(pdk=pdk), seed) for seed in (0, 1, 2)}
    assert len(hashes) == 1, hashes


# ---------------------------------------------------------------------------
# priority objective: strict pruning
# ---------------------------------------------------------------------------


def test_a_bound_equal_to_the_best_area_is_not_pruned():
    """Lexicographic objective: an equal area may still win on a later level."""
    pack = semantic.get_pack("gf180mcu_5v")
    roles = ("active", "poly", "contact", "metal1")
    freedom = build_freedom(pack, [Part("nmos", roles), Part("pmos", roles)])
    outs, rects = {}, {}
    for rn in range(4):
        for rp in range(4):
            key = (("nmos", rn * 90), ("pmos", rp * 90))
            outs[key] = {"nmos": (0, 0, 1000, 1000), "pmos": (0, 0, 1000, 1000)}
            rects[key] = {"nmos": (1000, 1000), "pmos": (1000, 1000)}
    d = decide_actions(freedom, {"nmos": (0, 90, 180, 270), "pmos": (0, 90, 180, 270)},
                       outs, 100, parts=("nmos", "pmos"), rects=rects)
    lb = d.order[0].lower_bound_nm2
    assert all(c.evaluated for c in prune(d, lb).order)          # equal: kept
    assert not any(c.evaluated for c in prune(d, lb - 1).order)  # strictly worse: pruned


# ---------------------------------------------------------------------------
# directions no grid can carry are closed, not left open
# ---------------------------------------------------------------------------


def test_grid_direction_follows_niven():
    for d in range(0, 360):
        assert (grid_direction(d) is not None) == (d % 45 == 0), d


def test_a_15_degree_process_closes_its_unrealisable_angles():
    base = semantic.get_pack("gf180mcu_3v3")
    freedom = dict(base.freedom)
    freedom["metal1"] = semantic.LayerFreedom(15, semantic.Provenance.DECK, "hypothetical")
    pack = dataclasses.replace(base, pdk_id="hypo15", freedom=freedom)
    by = {}
    for v in angle_verdicts(pack, ("metal1",)):
        by.setdefault(v.status, []).append(v.degrees)
    assert len(sum(by.values(), [])) == 24
    assert by[GRID_UNREALIZABLE] == [d for d in range(0, 360, 15) if d % 45]
    assert by[NOT_EXECUTABLE] == [45, 135, 225, 315]    # realisable, engine lacks it
