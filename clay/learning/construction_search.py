"""Performance-trained construction ranking BEFORE placement (S1 revision).

Finite search: two independent single-finger MOS rotations (4x4); free relative
placement under opposite-well clearance. Every candidate is materialized and
checked. Learning orders this SAME candidate alphabet under a declared candidate
budget. An exhaustive control is mandatory; it is never weakened for learning.
"""
from dataclasses import asdict
from itertools import product
import time
import klayout.db as db

from clay.learning.circuit import match_inverters
from clay.learning.generate import box_tuple, materialize, validate_layout, measure_layout
from clay.pdk.device_placement import build_contacted_device, rotate_device
from clay.pdk.semantic import default_pack
from clay.layout.synth.area_placement import solve_area

CANDIDATES = tuple(f"n{n}_p{p}" for n, p in product(range(4), repeat=2))
SCHEMA = "clay.construction-policy.v2"


def _pack(pack):
    return default_pack() if pack is None else pack


def validate_policy(policy):
    if set(policy) != {"schema", "pattern", "ranking", "objective"}:
        raise ValueError("Construction policy has unknown/observation fields")
    if policy["schema"] != SCHEMA or policy["pattern"] != "single_complementary_inverter_5v":
        raise ValueError("Unsupported construction policy")
    if policy["objective"] != "validated_material_bbox_area":
        raise ValueError("Unsupported learning objective")
    ranking = policy["ranking"]
    if not isinstance(ranking, list) or len(ranking) != len(CANDIDATES) or set(ranking) != set(CANDIDATES):
        raise ValueError("Policy must rank each member of the fixed candidate alphabet exactly once")
    return tuple(ranking)


def make_policy(ranking=CANDIDATES):
    policy = dict(schema=SCHEMA, pattern="single_complementary_inverter_5v",
                  ranking=list(ranking), objective="validated_material_bbox_area")
    validate_policy(policy)
    return policy


def evaluate_construction(circuit, contract, candidate, output=None, *, pack=None):
    p = _pack(pack)
    if contract.floorplan != "free_single_pair_rotations":
        raise ValueError("Construction search requires explicit free_single_pair_rotations contract")
    pairs = match_inverters(circuit, contract)
    if len(pairs) != 1:
        raise ValueError("v2 construction experiment supports exactly one inverter; use v1 for compositions")
    if candidate not in CANDIDATES:
        raise ValueError("Unknown construction")
    n, p_dev = pairs[0]
    rn, rp = int(candidate[1]), int(candidate[4])
    rows = contract.device_recipe.endswith("contact_rows_v2")
    devs = [rotate_device(build_contacted_device(d, contact_rows=rows), rot)
            for d, rot in ((n, rn), (p_dev, rp))]
    rectangles = {d.device.name: (d.active.width(), d.active.height()) for d in devs}
    outer = {d.device.name: box_tuple(d.bbox()) for d in devs}
    core = {d.device.name: box_tuple((d.regions["active"]+d.regions["poly"]+d.regions["contact"]+d.regions["metal1"]).bbox()) for d in devs}
    placement = solve_area(rectangles, outer, {(n.name, p_dev.name): p.nm("opposite_row_clearance")},
                           grid=contract.grid_nm, seconds=contract.solver_seconds, bands=None, core_boxes=core)
    layout, top, bindings = materialize(devs, placement.origins_nm, circuit, contract, pack=p)
    verification = validate_layout(layout, top, circuit, contract, bindings, pack=p)
    result = {"candidate": candidate, "passed": verification["passed"], "verification": verification,
              "measurements": measure_layout(layout, top, pack=p), "bindings": bindings,
              "solver": asdict(placement)}
    if output is not None and result["passed"]:
        layout.write(str(output))
        reread = db.Layout(); reread.read(str(output))
        verification = validate_layout(reread, reread.top_cell(), circuit, contract, bindings)
        if not verification["passed"]:
            raise ValueError("Serialized construction failed geometry checks")
        result["verification"] = verification
    if round(result["measurements"]["material_bbox"]["area_um2"]*1e6) != placement.area_nm2:
        raise ValueError("Materialized geometry disagrees with solver objective")
    return result


def search_constructions(circuit, contract, policy, *, budget=16, output=None):
    """Ranking controls geometry BEFORE any area is solved, not a tie-break."""
    ranking = validate_policy(policy)
    if type(budget) is not int or not 1 <= budget <= len(CANDIDATES):
        raise ValueError("Candidate budget must be an integer in 1..16")
    started = time.perf_counter()
    trials = []
    for candidate in ranking[:budget]:
        result = evaluate_construction(circuit, contract, candidate)
        trials.append(result)
    good = [r for r in trials if r["passed"]]
    if not good:
        raise ValueError("No verified candidate within declared budget")
    best = min(good, key=lambda r: (r["measurements"]["material_bbox"]["area_um2"], CANDIDATES.index(r["candidate"])))
    if output is not None:
        best = evaluate_construction(circuit, contract, best["candidate"], output)
    return {"circuit": asdict(circuit), "contract": asdict(contract), "best": best,
            "trials": trials, "candidate_budget": budget, "seconds": time.perf_counter()-started,
            "policy": policy, "reference_inputs": [], "residual_pins": 0,
            "scope": "16 single-finger rotation combinations; free relative x/y; 1.2um axis-separating active clearance; no folding/routing; local validation only",
            "optimality": "minimum among validated solutions of the evaluated candidate models; not full-process/global optimum"}


def fit_construction_policy(training_circuit, contract):
    """Single-sample supervised ranking by actual generated area and legality.

    Training evidence is saved separately; runtime policy contains only discrete
    candidate tokens. Original-coordinate/style consistency is NOT an objective.
    """
    before = make_policy()
    sweep = search_constructions(training_circuit, contract, before)
    scores = {r["candidate"]: r for r in sweep["trials"]}
    ranking = sorted(CANDIDATES, key=lambda c: (not scores[c]["passed"],
                     scores[c]["measurements"]["material_bbox"]["area_um2"] if scores[c]["passed"] else float("inf"),
                     CANDIDATES.index(c)))
    after = make_policy(ranking)
    return after, {"before": before, "after": after, "sweep": sweep,
                   "updated": before != after,
                   "learning": "finite construction performance ranking from one training circuit; manual candidate alphabet/topology/PDK",
                   "acceptance": "local geometry legality, then actual generated area; no original alignment or coordinates in score"}
