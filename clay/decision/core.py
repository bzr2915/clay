"""The coordinator core, minimal form (docs/GLOBAL_COORDINATOR_DESIGN_zh.md §6, step S4).

Not a primitive (design doc §1.1): it runs decision levels made of classified
primitives and chooses by a declared objective. Levels, outermost first:

1. **symmetry mapping** - options from ``netlist_symmetries`` (role
   ``options``), reduced by ``symmetry_classes`` (role ``reduce``, sound:
   only provably equivalent mappings are merged);
2. **construction** - fold target x axis, by ``search_constructions``;
3. **orientation + position** - jointly, inside each construction candidate.

Why level 1 lives here and not in the action decider: which mapping is best
depends on the task's objective (area, matched pairs, centring) and is only
visible after levels 2-3 have run on it (design doc §8.1, JKU).

Objectives are declared, lexicographic, and compared by content only; the
last tie-break is the mapping's rule rank and the candidate's content
(:func:`clay.learning.circuit_generate.candidate_key`), never a list position.
Each objective's levels are evaluated on every candidate's area-optimal
placement (the only placement the construction level produces); that is part
of the stated scope.

Not here yet (later steps): routing and electrical feedback levels;
sub-blocks solved as their own coordination run (the ``solve`` of a level
being a nested ``coordinate`` call is the intended hook, design doc §6.6).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional, Tuple

from clay.decision import index as primitive_index
from clay.learning import circuit_generate as cg
from clay.learning.relations import relation_effects
from clay.learning.symmetry import netlist_symmetries, netlist_symmetry, symmetry_classes


def _metrics(rec) -> Dict[str, int]:
    r = rec["result"]
    return {"area": rec["area"], "soft": max(r["solver"].get("soft_symmetry_cost", -1), 0),
            "pairs": r["pairs"]}


#: name -> (levels, key). The key is lexicographic, smaller is better. The
#: first level is what the task asks for; area always follows so that a tie on
#: the task's own measure is broken by size, not by chance.
OBJECTIVES: Mapping[str, Tuple[Tuple[str, ...], Callable[[Dict[str, int]], tuple]]] = {
    "area": (("area", "soft_relation_cost"),
             lambda m: (m["area"], m["soft"])),
    "matched_pairs": (("mirror_placed_pairs (more first)", "area", "soft_relation_cost"),
                      lambda m: (-m["pairs"], m["area"], m["soft"])),
    "centring": (("soft_relation_cost", "area"),
                 lambda m: (m["soft"], m["area"])),
}


def mapping_options(circuit, library, *, search_symmetry=True):
    """Level 1 options: one representative per class of equivalent mappings.

    With ``search_symmetry=False`` the single option is the mapping the fixed
    rule picks (the behaviour of ``generate_circuit``). Without an enforced
    symmetry there is one option, ``None``.
    """
    if not relation_effects(library).enforce_mirror_pairs:
        return [None], []
    if not search_symmetry:
        fixed = netlist_symmetry(circuit)
        return [fixed], [[fixed]] if fixed else []
    classes = symmetry_classes(circuit, netlist_symmetries(circuit))
    if not classes:
        return [None], []
    return [members[0] for members in classes], [list(m) for m in classes]


def evaluate_options(circuit, library, pack, *, objective="area", search_symmetry=True,
                     log=print, **search):
    """Run the construction level for every mapping option.

    Area bounds prune only when the objective's first level is area; for any
    other objective every candidate is solved to the end.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective {objective!r}; declared: {sorted(OBJECTIVES)}")
    reps, classes = mapping_options(circuit, library, search_symmetry=search_symmetry)
    out = []
    for k, rep in enumerate(reps):
        log(f"mapping option {k}/{len(reps)}: "
            + (f"{len(rep.pairs)} pairs, {len(rep.fixed)} fixed" if rep else "no symmetry"))
        try:
            s = cg.search_constructions(circuit, library, pack, symmetry=rep, log=log,
                                        prune=(objective == "area"), **search)
            out.append({"option": k, "symmetry": rep,
                        "members": len(classes[k]) if classes else 1, "search": s, "error": None})
        except ValueError as e:
            out.append({"option": k, "symmetry": rep,
                        "members": len(classes[k]) if classes else 1, "search": None,
                        "error": str(e)})
    return out


def choose(options, objective="area"):
    """The best verified candidate over all options, by the objective's key and
    then content: mapping rule rank, then candidate content."""
    _, key = OBJECTIVES[objective]
    best = None
    for o in options:
        if o["search"] is None:
            continue
        for rec in o["search"]["records"]:
            if rec["kind"] != "solved" or not rec["verified"]:
                continue
            k = key(_metrics(rec)) + (o["option"], cg.candidate_key(rec["candidate"]))
            if best is None or k < best[0]:
                best = (k, o, rec)
    return best


def _scope(options, search_symmetry, table) -> List[str]:
    """Generated from the classification: each option set and whether the
    primitive that supplies it declares it complete or truncated."""
    out = []
    if search_symmetry:
        n = sum(o["members"] for o in options)
        out.append(f"symmetry mappings: {n} found by netlist_symmetries "
                   f"({table['netlist_symmetries'].options_domain}: within max_iterations per "
                   f"seed pair), {len(options)} classes after symmetry_classes")
    else:
        out.append("symmetry mapping: one, fixed by netlist_symmetry's rule (not searched)")
    out.append(f"fold targets: fold_candidates ({table['fold_candidates'].options_domain})")
    alts = sorted({n for o in options if o["search"] for r in o["search"]["records"]
                   for n in (r["candidate"].get("constructors") or {}).values()})
    out.append("constructors: the dispatch constructor of each device class"
               + (f"; alternatives {alts}" if alts else "; no alternative constructor applicable"))
    out.append(f"orientations: orientations ({table['orientations'].options_domain}), "
               "jointly with position")
    out.append("not searched: non-shared chains, diagonal constructions, routing, "
               "electrical performance")
    return out


def coordinate(circuit, library, pack, output, *, objective="area", search_symmetry=True,
               quick_seconds=15.0, final_seconds=90.0, workers=8, axes=None, fold_targets=None,
               orientation_search=True, max_rounds=2, held=None, log=print):
    """Choose mapping, construction and placement by ``objective``; write the GDS.

    Returns the ``generate_circuit`` result of the winner (its local
    certificate stays in ``decider``) plus the global certificate in
    ``coordinator``. ``search_symmetry=False`` with ``objective='area'`` and no
    ``held`` reproduces ``generate_circuit`` exactly. ``held`` supplies typed
    inputs of alternative constructors (e.g. ``{"InverterLibrary": lib}``);
    each applicable alternative becomes a construction option (S5).
    """
    search = dict(quick_seconds=quick_seconds, final_seconds=final_seconds, workers=workers,
                  axes=axes, fold_targets=fold_targets, orientation_search=orientation_search,
                  max_rounds=max_rounds, held=held)
    options = evaluate_options(circuit, library, pack, objective=objective,
                               search_symmetry=search_symmetry, log=log, **search)
    return conclude(circuit, library, pack, output, options, objective=objective,
                    search_symmetry=search_symmetry, quick_seconds=quick_seconds,
                    final_seconds=final_seconds, workers=workers)


def conclude(circuit, library, pack, output, options, *, objective="area", search_symmetry=True,
             quick_seconds=15.0, final_seconds=90.0, workers=8):
    """Choose among evaluated ``options`` by ``objective``, finalise, certify.

    Separate from :func:`coordinate` so that one evaluation can be concluded
    under several objectives (the evaluation does not depend on the objective
    when it was run without pruning).
    """
    picked = choose(options, objective)
    if picked is None:
        raise ValueError("no mapping option produced a verified construction: "
                         + str([o["error"] for o in options]))
    key, winner_option, winner = picked
    result = cg.finalize(circuit, library, pack, output, winner_option["search"], winner,
                         quick_seconds=quick_seconds, final_seconds=final_seconds,
                         workers=workers)
    table = primitive_index.build().table
    levels, open_, all_proven = [], [], True
    for o in options:
        row = {"option": o["option"], "members": o["members"],
               "pairs": len(o["symmetry"].pairs) if o["symmetry"] else 0,
               "fixed": len(o["symmetry"].fixed) if o["symmetry"] else 0}
        s = o["search"]
        if s is None or s["best"] is None:
            row["settled"] = "rejected"
            row["reason"] = o["error"] or "no verified candidate"
            levels.append(row)
            continue
        recs = s["records"]
        unsettled = [r["candidate"] for r in recs if not s["settled"](r, s["best"])]
        if unsettled:
            all_proven = False
            open_.extend(f"option {o['option']}: candidate {c} open" for c in unsettled)
        own = choose([o], objective)
        m = _metrics(own[2])
        row.update({"best_candidate": own[2]["candidate"], "area_nm2": m["area"],
                    "soft_relation_cost": m["soft"], "mirror_placed_pairs": m["pairs"],
                    "status": own[2]["status"], "open_candidates": len(unsettled),
                    "settled": "winner" if o is winner_option else
                    f"objective {list(OBJECTIVES[objective][1](m))} not better"})
        levels.append(row)
    local = result["decider"]
    # In scope means: every candidate of every option settled (solved to
    # optimality, refused, or - when the objective is area - excluded by a
    # sound bound), the winner itself optimal and not a fallback, and no
    # orientation of the winner left open.
    proven = (all_proven and winner["status"] == "OPTIMAL" and not winner.get("fallback")
              and not local["open_angles"])
    result["coordinator"] = {
        "objective": {"name": objective, "levels": list(OBJECTIVES[objective][0]),
                      "evaluated_on": "each candidate's area-optimal placement",
                      "tie_break": "mapping rule rank, then candidate content"},
        "scope": _scope(options, search_symmetry, table),
        "levels": {"symmetry_mapping": levels},
        "winner": {"option": winner_option["option"], "candidate": winner["candidate"],
                   "objective": list(key[:len(OBJECTIVES[objective][0])]),
                   "area_nm2": winner["area"],
                   "symmetry": winner_option["symmetry"].as_dict()
                   if winner_option["symmetry"] else None},
        "open": open_,
        "proven_optimal_in_scope": bool(proven),
    }
    return result


__all__ = ["OBJECTIVES", "mapping_options", "evaluate_options", "choose", "conclude",
           "coordinate"]
