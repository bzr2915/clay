"""Target netlist + frozen circuit library -> placement GDS (circuit-general).

This is the generator the comparator work needed: it accepts any netlist the
rule pack can construct and never reads a reference layout. The pipeline, one
classified primitive per step (since S3, docs/GLOBAL_COORDINATOR_DESIGN_zh.md §5):

    parse_spice -> netlist_symmetry -> fold_devices -> diffusion_chains
      -> placement_units -> (per unit, the dispatch constructor of its device
         class: build_chain_row / build_poly_resistor / build_mim_cap)
      -> block_space / mirror_space -> placement_intents -> place_blocks
      -> materialize -> verify_circuit_layout

What the library decides (learned): contact rows per segment, whether matched
devices are mirror-placed, whether gates share one orientation, resistor head
style. What the *decision step* chooses per target (not imitated): the fold
target, the symmetry-axis direction (the library says ``free``), the MIM
orientation. Each candidate is solved briefly, the best few again with a longer
budget, and the smallest layout that passes verification wins. Every candidate
and its outcome is recorded, so the choice can be audited.
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional

import klayout.db as db

from clay.decision import index as primitive_index
from clay.decision.invoke import call, dispatch
from clay.layout.gds_write import write_gds
from clay.learning.action_space import (GATED, block_space, chosen, count_combinations,
                                        mirror_space)
from clay.learning.circuit_construct import (build_chain_row, place_blocks,  # noqa: F401
                                             placement_intents, placement_units,
                                             verification_feedback)
from clay.learning.circuit_library import material_bbox, validate_library
from clay.learning.circuit_verify import verify_circuit_layout
from clay.learning.contact_primitives import materialize, turn
from clay.learning.diffusion_chains import chains_report, diffusion_chains, fold_devices
from clay.learning.relations import relation_effects
from clay.learning.symmetry import netlist_symmetry

SCHEMA = "clay.circuit-generation.v1"


@lru_cache(maxsize=None)
def _index():
    """The classification index; read once per process."""
    return primitive_index.build()


def _chain_classes(index) -> frozenset:
    """Device classes built as diffusion chains: those a dispatch constructor
    consuming a ``Chain`` serves. Every other class is built per device."""
    return frozenset(c for n in index.by_output.get("Row", ())
                     if index.table[n].composition == "dispatch"
                     and "Chain" in index.table[n].signature.inputs.values()
                     for c in index.table[n].applies_to)


def fold_candidates(circuit, *, pack) -> List[Optional[int]]:
    """Fold targets worth trying: the netlist's own fingers, its median finger
    width, and half of it (never below twice the diffusion minimum)."""
    widths = [d.w_nm // d.nf for d in circuit.devices if d.cls in ("mos", "cap_var")]
    if not widths:
        return [None]
    grid = pack.nm("grid")
    floor = 2 * pack.nm("width_min")
    med = int(median(widths)) // grid * grid
    out = [None]
    for t in (med, med // 2 // grid * grid):
        if t >= floor and t < max(widths) and t not in out:
            out.append(t)
    return out


def assemble(circuit, library, pack, *, fold_target=None, axis=None, symmetry=None,
             share=True, orient=True, turn_classes=(), constructors=None, held=None):
    """Chains, rows and the per-block action space for one candidate construction.

    A composition of classified primitives (design doc §5, step S3): the
    learned relations are read through the relation registry; every unit is
    built by the classification's ``dispatch`` constructor for its device
    class, called by type; mirror pairs and centring come from
    :func:`placement_intents`. No device class is named here.

    With ``orient`` (default) every block offers each legal orientation as a
    variant (:mod:`clay.learning.action_space`), and the placement solve picks
    orientations jointly with positions. ``orient=False`` reproduces the
    previous behaviour exactly: identity orientation, and a quarter turn for
    the device classes in ``turn_classes`` (the old fixed-orientation MIM
    candidate; see :func:`evaluate`).

    ``constructors`` (device class -> classified primitive name) replaces the
    dispatch constructor of those classes by an ``alternative`` one (S5);
    ``held`` adds typed inputs it needs (e.g. ``{"InverterLibrary": lib}``).
    """
    # A missing contact policy is refused by the constructor that needs it
    # (call() names the missing ContactPolicy), not up front: an alternative
    # constructor with its own library may not need it at all (S5).
    policy = library["contact_policies"].get(pack.pdk_id)
    effects = relation_effects(library)
    keep_gates = orient and effects.keep_gates
    sym = symmetry if effects.enforce_mirror_pairs else None
    fingers = fold_devices(circuit, fold_target, pack=pack)
    chains = diffusion_chains(fingers, sym, share=share)
    index = _index()
    units = placement_units(chains, circuit, sym, chain_classes=_chain_classes(index))
    held = {**(held or {}), "Pack": pack}
    if policy is not None:
        held["ContactPolicy"] = policy
    style = library["resistor_style"].get(pack.pdk_id)
    if style is not None:
        held["ResistorStyle"] = style
    constructors = dict(constructors or {})
    mirror_axis = "y" if axis == "vertical" else "x"
    spaces = {}
    for u in units:
        if u.mirror_of:
            spaces[u.name] = mirror_space(u.name, spaces[u.mirror_of], pack=pack,
                                          axis=mirror_axis, net_map=sym.net_map, well=u.well,
                                          sources=list(u.sources))
            continue
        maker = (index.table[constructors[u.cls]] if u.cls in constructors
                 else dispatch(index, "Row", u.cls))
        row = call(maker, {**held, u.kind: u.payload})
        if u.cls in turn_classes and not orient:
            row = turn(row, 1)
        is_centred = sym is not None and axis is not None and u.self_symmetric and u.paired
        spaces[u.name] = block_space(
            u.name, u.cls, row, pack=pack, well=u.well, self_symmetric=u.self_symmetric,
            sources=list(u.sources), keep_gates=keep_gates and u.cls in GATED,
            keep_axis_symmetry=orient and is_centred, orient=orient)
    intents = placement_intents(units, sym, axis=axis, soft_centre=effects.soft_centre_unpaired)
    identity = chosen(spaces, {})
    return {"fingers": fingers, "chains": chains, "units": units, "spaces": spaces,
            "rows": {n: v.row for n, v in identity.items()},
            "blocks": {n: v.block for n, v in identity.items()},
            "intents": intents, "pairs": intents.pairs, "centred": intents.centred,
            "soft": intents.soft, "symmetry": sym, "chains_report": chains_report(chains)}


def evaluate(circuit, library, pack, candidate, *, seconds, workers=8, hint=None,
             output=None, symmetry=None, orient=True, variant_hint=None,
             canonical_seconds=None, held=None, defer_canonical=False,
             pinned_area_nm2=None):
    """Assemble, place (orientation + position jointly), materialise and verify one candidate.

    ``defer_canonical`` / ``pinned_area_nm2``: two-stage placement for the
    construction search (design doc §6, plan A). A deferred call returns the
    proven area without layout, verification or soft cost; the candidate is
    placed for real only if it can still win.
    """
    # Legacy fixed-orientation mode only: the old flow tried the MIM turned by
    # a quarter as a candidate ("mim_turn"). With orientation search the turn
    # is a variable inside the placement model and this is unused.
    turn_classes = ("cap_mim",) if candidate.get("mim_turn", False) else ()
    a = assemble(circuit, library, pack, fold_target=candidate["fold_target"],
                 axis=candidate["axis"], turn_classes=turn_classes,
                 symmetry=symmetry, share=candidate.get("share", True), orient=orient,
                 constructors=candidate.get("constructors"), held=held)
    spaces = a["spaces"]
    placed = place_blocks(spaces, a["intents"], pack=pack, axis=candidate["axis"],
                          seconds=seconds, workers=workers, hint=hint,
                          variant_hint=variant_hint, canonical_seconds=canonical_seconds,
                          defer_canonical=defer_canonical, pinned_area_nm2=pinned_area_nm2)
    if placed.determinism == "deferred":
        # Proven area only; no layout, verification or soft cost (plan A).
        return {"candidate": candidate, "chains": a["chains_report"],
                "blocks": len(a["blocks"]), "pairs": len(a["pairs"]),
                "centred": len(a["centred"]), "soft_centred": len(a["soft"]),
                "solver": {k: v for k, v in asdict(placed).items()
                           if k not in ("origins_nm", "variant_index")},
                "orientation_combinations": count_combinations(spaces),
                "area_nm2": placed.area_nm2,
                "deferred": True}, None, a
    pick = chosen(spaces, placed.variant_index)
    a["rows"] = {n: v.row for n, v in pick.items()}
    a["blocks"] = {n: v.block for n, v in pick.items()}
    layout = materialize(a["rows"], placed.origins_nm, pack=pack, cell_name=circuit.name,
                         merge="close")
    result = {"candidate": candidate, "chains": a["chains_report"],
              "blocks": len(a["blocks"]), "pairs": len(a["pairs"]),
              "centred": len(a["centred"]), "soft_centred": len(a["soft"]),
              "solver": {k: v for k, v in asdict(placed).items()
                         if k not in ("origins_nm", "variant_index")},
              "orientation": {n: v.name for n, v in pick.items()},
              "orientation_combinations": count_combinations(spaces),
              "variant_index": dict(placed.variant_index),
              "origins_nm": placed.origins_nm}
    if output is not None:
        write_gds(layout, output)
        layout = db.Layout()
        layout.read(str(output))
    v = verify_circuit_layout(layout, circuit, pack)
    bbox = material_bbox(layout, pack)
    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    result.update({"verification": v, "material_bbox_nm": bbox, "area_nm2": area,
                   "area_matches_solver": area == placed.area_nm2})
    return result, layout, a


def _failure_kind(message: str) -> str:
    """How a candidate that produced no placement is settled.

    ``infeasible``: CP-SAT *proved* no placement exists (closed). ``timeout``:
    no solution within the time limit (open: nothing is known). ``rejected``:
    the construction itself was refused by a rule before placement (closed,
    with the rule's message as the reason).
    """
    if "INFEASIBLE" in message:
        return "infeasible"
    if "UNKNOWN" in message or "MODEL_INVALID" in message:
        return "timeout"
    return "rejected"


#: Content order of symmetry axes in the canonical tie-break (design doc §2.1
#: level 4): it must depend on what a candidate is, never on where it sits in
#: an enumeration, so registering a new option cannot reorder the old ones.
AXIS_ORDER = ("vertical", "horizontal", None)

_RULE = object()   # "use the mapping netlist_symmetry picks by its fixed rule"


def candidate_key(candidate) -> tuple:
    """Canonical tie-break of a construction candidate, by content: no extra
    folding first, then larger target finger width (less folding), then the
    axis in ``AXIS_ORDER``, then the legacy MIM turn. For every candidate list
    the generator builds this is the enumeration order, so existing results
    are unchanged; unlike a position it does not move when options are added.
    """
    fold = candidate.get("fold_target")
    # Constructor variants last: the dispatch default (no entry) sorts first.
    return (fold is not None, -(fold or 0), AXIS_ORDER.index(candidate.get("axis")),
            bool(candidate.get("mim_turn", False)),
            tuple(sorted((candidate.get("constructors") or {}).items())))


def alternative_constructors(circuit, held_types) -> List[Dict[str, str]]:
    """Constructor variants beyond the dispatch default (design doc §6.3, S5).

    One variant per classified ``alternative`` constructor of ``Row`` whose
    typed inputs are available (``held_types`` plus what ``assemble`` itself
    holds) and which serves a device class present in ``circuit``. Sorted by
    content, so the order does not depend on registration order.
    """
    index = _index()
    available = set(held_types) | {"Pack", "ContactPolicy", "ResistorStyle", "Chain", "Device"}
    classes = {d.cls for d in circuit.devices}
    out = []
    for name in index.by_output.get("Row", ()):
        item = index.table[name]
        if item.composition != "alternative":
            continue
        if not set(item.signature.inputs.values()) <= available:
            continue
        served = sorted(set(item.applies_to) & classes)
        if served:
            out.append({c: name for c in served})
    return sorted(out, key=lambda v: sorted(v.items()))


def search_constructions(circuit, library, pack, *, symmetry, quick_seconds=15.0,
                         final_seconds=90.0, workers=8, axes=None, fold_targets=None,
                         log=print, orientation_search=True, max_rounds=2, prune=True,
                         held=None, defer_area=True):
    """The construction level (fold target x axis) for one symmetry mapping.

    ``symmetry`` is the mapping the construction enforces (``None``: none).
    Every candidate gets a short solve; a candidate is settled when it is
    proven optimal, proven infeasible, rejected by a construction rule, or -
    with ``prune`` - its sound CP-SAT lower bound is above the best verified
    area. Unsettled candidates get the longer budget, up to ``max_rounds``.
    ``prune=False`` is for objectives whose first level is not area: an area
    bound then proves nothing, so every candidate is solved to the end.
    Ranking alone never discards a candidate.

    ``held`` supplies typed inputs for alternative constructors (S5); each
    applicable alternative adds one constructor variant of every candidate.
    Without ``held`` the candidate list is exactly the pre-S5 one.

    ``defer_area`` (plan A, design doc §6.2): with ``prune``, every candidate's
    area is proven first and the expensive canonical stage, assembly and layout
    checks run only for candidates that can still win. The chosen layout is
    unchanged - a candidate whose proven area is strictly larger cannot win on
    any later level of the objective either. ``defer_area=False`` reproduces
    the previous behaviour exactly (every candidate placed).
    """
    validate_library(library)
    sym = symmetry
    effects = relation_effects(library)
    enforce_pairs = effects.enforce_mirror_pairs and sym is not None
    if not enforce_pairs:
        axes = [None]
    elif axes is None:
        axes = list(effects.axes())
    folds = fold_targets if fold_targets is not None else fold_candidates(circuit, pack=pack)
    has_mim = any(d.cls == "cap_mim" for d in circuit.devices)
    # With orientation search the MIM's orientation is a variable inside the
    # model; without it, the old 0/90 MIM candidates are kept.
    mims = [False, True] if (has_mim and not orientation_search) else [False]
    variants = [None] + alternative_constructors(circuit, held or {})
    candidates = [{"fold_target": f, "axis": ax, **({"mim_turn": mt} if not orientation_search else {}),
                   **({"constructors": v} if v else {})}
                  for f in folds for ax in axes for mt in mims for v in variants]

    records: List[Dict[str, object]] = []

    def run(c, seconds, previous=None, defer=False, pinned=None):
        p = previous["result"] if previous is not None else None
        hint = p.get("origins_nm") if p is not None else None
        vhint = p.get("variant_index") if p is not None else None
        try:
            r, layout, a = evaluate(circuit, library, pack, c, seconds=seconds, workers=workers,
                                    symmetry=sym, orient=orientation_search, hint=hint,
                                    variant_hint=vhint, held=held, defer_canonical=defer,
                                    pinned_area_nm2=pinned)
        except ValueError as e:
            if previous is not None and previous.get("result") is not None:
                # A longer re-solve that fails cannot unmeasure the earlier result.
                return {**previous, "retry_error": str(e)}
            return {"candidate": c, "result": None, "kind": _failure_kind(str(e)),
                    "error": str(e), "bound": None, "seconds": seconds}
        if r.get("deferred"):
            # Area proven optimal; the canonical stage, layout and checks are
            # deferred until this candidate is known to be able to win.
            return {"candidate": c, "result": r, "layout": None, "assembly": a,
                    "kind": "solved", "area": r["area_nm2"], "status": r["solver"]["status"],
                    "bound": r["solver"]["lower_bound_nm2"], "verified": False,
                    "seconds": seconds, "fallback": None, "deferred": True}
        rec = {"candidate": c, "result": r, "layout": layout, "assembly": a,
               "kind": "solved", "area": r["area_nm2"], "status": r["solver"]["status"],
               "bound": r["solver"]["lower_bound_nm2"], "verified": r["verification"]["passed"],
               "seconds": seconds, "fallback": None}
        # What to do about a failed layout check is a feedback primitive's
        # decision (returned as data); carrying it out is this loop's job.
        if verification_feedback(rec, orientation_search=orientation_search)["action"] \
                == "retry_fixed_orientation":
            failed = list(r["verification"]["failed"])
            try:
                fr, flayout, fa = evaluate(circuit, library, pack, c, seconds=seconds,
                                           workers=workers, symmetry=sym, orient=False,
                                           held=held)
            except ValueError as e:
                rec["fallback"] = {"oriented_failed": failed, "error": str(e)}
            else:
                rec["fallback"] = {"oriented_failed": failed,
                                   "oriented_area_nm2": r["area_nm2"],
                                   "fixed_orientation_area_nm2": fr["area_nm2"],
                                   "fixed_orientation_verified": fr["verification"]["passed"]}
                if fr["verification"]["passed"]:
                    rec.update({"result": fr, "layout": flayout, "assembly": fa,
                                "area": fr["area_nm2"], "status": fr["solver"]["status"],
                                "verified": True})
        if previous is not None and previous.get("result") is not None:
            # Both bounds are sound; keep the stronger. Keep the better layout.
            rec["bound"] = max(rec["bound"], previous["bound"] or 0)
            if previous["verified"] and (not rec["verified"] or previous["area"] < rec["area"]):
                rec.update({k: previous.get(k) for k in ("result", "layout", "assembly", "area",
                                                         "status", "verified", "fallback")})
        return rec

    def objective(rec):
        """The priority vector (§2.1 of GLOBAL_COORDINATOR_DESIGN_zh.md):
        area, then the soft-relation cost, then the candidate's content as the
        canonical tie-break. Ties on area are decided by the declared levels,
        never by which run happened to finish first or by list position."""
        soft = rec["result"]["solver"].get("soft_symmetry_cost", -1) if rec.get("result") else -1
        return (rec["area"], max(soft, 0), candidate_key(rec["candidate"]))

    def incumbent():
        ok = [(objective(r), r) for r in records if r["kind"] == "solved" and r["verified"]]
        return min(ok, key=lambda t: t[0])[1] if ok else None

    def settled(rec, best):
        if rec["kind"] in ("infeasible", "rejected"):
            return True
        if rec.get("settled_by_area"):
            # Deferred candidate whose proven area is larger than the best
            # verified one: it cannot win, and the canonical stage (which only
            # picks among equal-area placements) does not change that.
            return True
        if rec["kind"] == "solved" and rec["verified"] and rec["status"] == "OPTIMAL" and not rec.get("fallback"):
            return True
        # Strict: a bound equal to the best area may still win on a later level.
        return (prune and best is not None and rec["bound"] is not None
                and rec["bound"] > best["area"])

    for c in candidates:
        rec = run(c, quick_seconds, defer=prune and defer_area)
        records.append(rec)
        if rec["kind"] == "solved":
            log(f"  quick {c}: area {rec['area'] / 1e6:.2f} um2 [{rec['status']}, bound "
                f"{rec['bound'] / 1e6:.2f}]"
                + ("" if rec.get("deferred") else f" verified={rec['verified']}"))
        else:
            log(f"  quick {c}: {rec['kind']} ({rec['error']})")
    trace = [{"round": 0, "seconds": quick_seconds, "solved": len(candidates)}]

    def resolve(i):
        """Place a deferred candidate for real, at its proven area.

        Plan A (design doc §6): the canonical stage, layout and checks run only
        once a candidate is known to be able to win. The area is pinned, so the
        canonical stage runs on exactly the model it would have run on, and the
        result equals a full (undeferred) run's.
        """
        rec = records[i]
        records[i] = run(rec["candidate"], quick_seconds, rec, pinned=rec["area"])
        r = records[i]
        log(f"  place {r['candidate']}: {r['kind']}"
            + (f" area {r['area'] / 1e6:.2f} um2 [{r['status']}, bound {r['bound'] / 1e6:.2f}]"
               f" verified={r['verified']}" if r["kind"] == "solved" else ""))
        return r

    if prune:
        # Every candidate's area is known and proven; only those that can still
        # win need a placement. Ties on area are decided on later levels, so a
        # candidate whose area equals the smallest must be placed for real;
        # the rest are settled by their proven area.
        waiting = [i for i, r in enumerate(records) if r.get("deferred")]
        if waiting:
            floor = min(records[i]["area"] for i in waiting)
            placed = 0
            for i in waiting:
                if settled(records[i], {"area": floor}):
                    records[i]["settled_by_area"] = True
                    continue
                resolve(i)
                placed += 1
            trace.append({"round": "area", "placed": placed,
                          "settled_by_area": len(waiting) - placed})
    # Anything still deferred could not be settled by area alone (only possible
    # if a deferred record's area was not proven, which the deferral excludes);
    # place it rather than leave it open.
    for i, r in enumerate(records):
        if r.get("deferred") and not r.get("settled_by_area"):
            resolve(i)
    for round_index in range(1, max_rounds + 1):
        best = incumbent()
        open_ = [i for i, r in enumerate(records) if not settled(r, best)]
        if not open_:
            break
        open_.sort(key=lambda i: (records[i]["bound"] if records[i]["bound"] is not None else 0,
                                  candidate_key(records[i]["candidate"])))
        for i in open_:
            best = incumbent()
            if settled(records[i], best):
                continue          # a better incumbent found this round settles it
            records[i] = run(records[i]["candidate"], final_seconds, records[i])
            r = records[i]
            log(f"  round {round_index} {r['candidate']}: {r['kind']}"
                + (f" area {r['area'] / 1e6:.2f} um2 [{r['status']}, bound {r['bound'] / 1e6:.2f}]"
                   if r["kind"] == "solved" else ""))
        trace.append({"round": round_index, "seconds": final_seconds, "solved": len(open_)})

    return {"records": records, "best": incumbent(), "folds": folds, "axes": axes,
            "trace": trace, "settled": settled, "symmetry": sym, "enforce_pairs": enforce_pairs,
            "orientation_search": orientation_search, "prune": prune, "held": held}


def finalize(circuit, library, pack, output, search, chosen, *, quick_seconds=15.0,
             final_seconds=90.0, workers=8):
    """Make ``chosen`` (a record of ``search``) the output: reproducible
    placement, certificate, GDS written and read back."""
    records, sym = search["records"], search["symmetry"]
    orientation_search, trace = search["orientation_search"], list(search["trace"])
    best = chosen
    if best is None:
        raise ValueError("no candidate construction passed verification: "
                         + str([r.get("error") or r["result"]["verification"]["failed"]
                                for r in records]))
    winner_path = best["result"]["solver"].get("determinism", "free")
    if "tiebreak_failed" in winner_path and best["status"] == "OPTIMAL":
        # The output layout must be reproducible. The winner's area is proven,
        # but its canonical stage ran out of budget, so its placement came from
        # the parallel stage. Re-run that stage alone with the longer budget;
        # keep the result only if it is the same area and verified.
        r, layout, a = evaluate(circuit, library, pack, best["candidate"], seconds=quick_seconds,
                                workers=workers, symmetry=sym, orient=orientation_search,
                                canonical_seconds=final_seconds, held=search.get("held"))
        if r["verification"]["passed"] and r["area_nm2"] == best["area"]:
            best.update({"result": r, "layout": layout, "assembly": a})
        trace.append({"round": "winner_canonical", "seconds": final_seconds,
                      "determinism": r["solver"].get("determinism")})
    certificate = _certificate(records, best, search["settled"], search["folds"], search["axes"],
                               orientation_search, trace,
                               symmetry=sym if search["enforce_pairs"] else None)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_gds(best["layout"], output)
    readback = db.Layout()
    readback.read(str(output))
    v = verify_circuit_layout(readback, circuit, pack)
    bbox = material_bbox(readback, pack)
    if not v["passed"]:
        raise ValueError(f"readback verification failed: {v['failed']}")
    if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) != best["area"]:
        raise ValueError("readback area differs from the solved area")
    r, a = best["result"], best["assembly"]
    return {"schema": SCHEMA, "cell": circuit.name, "pdk": pack.pdk_id,
            "symmetry": sym.as_dict() if sym else None, "best": _strip(r),
            "candidates": [_strip(x["result"]) if x["result"] is not None else
                           {"candidate": x["candidate"], "error": x["error"], "kind": x["kind"]}
                           for x in records],
            "finalists": [_strip(x["result"]) for x in records
                          if x["result"] is not None and x["seconds"] == final_seconds],
            "decider": certificate,
            "chains": [c.as_dict() for c in a["chains"]],
            "blocks": {n: b.as_dict() for n, b in a["blocks"].items()},
            "learned_used": {"contact_policy": library["contact_policies"].get(pack.pdk_id),
                             "relations": {k: v["decision"]
                                           for k, v in library["relations"].items()},
                             "resistor_style": library["resistor_style"].get(pack.pdk_id)},
            "reference_inputs": []}


def generate_circuit(circuit, library, pack, output, *, quick_seconds=15.0,
                     final_seconds=90.0, finalists=2, workers=8, axes=None,
                     fold_targets=None, log=print, orientation_search=True, max_rounds=2,
                     symmetry=_RULE, defer_area=True):
    """Decide a construction for ``circuit`` and write the best verified GDS.

    The symmetry mapping is fixed: by default the one ``netlist_symmetry``
    picks by its rule (recorded in the certificate's ``fixed_choices``). To
    search over mappings and choose by a declared objective, use the
    coordinator (:func:`clay.decision.core.coordinate`).

    Two levels of decision, both certified:

    * **inside a candidate** - orientation of every block (from the PDK's
      freedom and the learned relations) and its position, solved *jointly* by
      CP-SAT; the solver's own lower bound comes back with the area;
    * **between candidates** (fold target x axis) - best-first branch and
      bound (:func:`search_constructions`). ``finalists`` is accepted for
      compatibility and unused.
    """
    del finalists
    sym = netlist_symmetry(circuit) if symmetry is _RULE else symmetry
    search = search_constructions(circuit, library, pack, symmetry=sym,
                                  quick_seconds=quick_seconds, final_seconds=final_seconds,
                                  workers=workers, axes=axes, fold_targets=fold_targets, log=log,
                                  orientation_search=orientation_search, max_rounds=max_rounds,
                                  defer_area=defer_area)
    return finalize(circuit, library, pack, output, search, search["best"],
                    quick_seconds=quick_seconds, final_seconds=final_seconds, workers=workers)


def _certificate(records, best, settled, folds, axes, orientation_search, trace, *,
                 symmetry=None):
    """What was searched, how every candidate was settled, and what is still open.

    ``symmetry`` is the netlist symmetry the construction actually used (None
    when mirror pairs are not enforced). A netlist can have several legal
    symmetry mappings with different optimal areas (JKU: 16 mappings, four
    classes, 389.30-405.56 um2); ``netlist_symmetry`` picks one by a fixed rule,
    so the proof holds for that mapping only and the scope must say so
    (GLOBAL_COORDINATOR_DESIGN_zh.md §8.1, step S0).
    """
    rows, open_ = [], []
    for r in records:
        row = {"candidate": r["candidate"], "kind": r["kind"]}
        if r["kind"] == "solved":
            row.update({"area_nm2": r["area"], "status": r["status"], "lower_bound_nm2": r["bound"],
                        "verified": r["verified"],
                        "orientation": r["result"].get("orientation"),
                        "orientation_combinations": r["result"]["orientation_combinations"]})
        else:
            row["reason"] = r["error"]
        if r.get("fallback"):
            row["fallback"] = r["fallback"]
        if r is best:
            proven_winner = r["status"] == "OPTIMAL" and not r.get("fallback")
            row["settled"] = "winner" if proven_winner else "winner (not proven optimal)"
            if r.get("fallback"):
                open_.append(f"{r['candidate']}: winner is the fixed-orientation fallback")
        elif r["kind"] in ("infeasible", "rejected"):
            row["settled"] = r["kind"]
        elif r.get("settled_by_area"):
            row["settled"] = (f"area {r['area']} > best {best['area']}: canonical placement "
                              "not run (it only picks among equal-area placements)")
        elif r["kind"] == "solved" and r["verified"] and r["status"] == "OPTIMAL" and not r.get("fallback"):
            row["settled"] = "solved optimal, not better"
        elif settled(r, best):
            row["settled"] = f"bound {r['bound']} > best {best['area']}"
        else:
            row["settled"] = "open"
            open_.append(f"{r['candidate']}: {r['kind']}"
                         + (", oriented optimum failed verification" if r.get("fallback") else "")
                         + (f", bound {r['bound']} <= best {best['area']}" if r["bound"] else ""))
        if r.get("result") is not None:
            # A deferred candidate was never placed: no layout or check fed
            # this certificate, so its (absent) placement path says nothing
            # about reproducibility (plan A, design doc §6).
            if not r.get("deferred"):
                row["determinism"] = r["result"]["solver"].get("determinism", "free")
            row["soft_cost"] = r["result"]["solver"].get("soft_symmetry_cost", -1)
        rows.append(row)
    spaces = best["assembly"]["spaces"]
    blocks = {n: s.report() for n, s in spaces.items()}
    open_angles = {n: b["open_angles"] for n, b in blocks.items() if b["open_angles"]}
    if not orientation_search:
        # Only the identity orientation was searched: optimal in that subspace,
        # not in the action space the PDK and the library allow.
        open_.append("orientation search disabled: only the identity orientation was searched")
    proven = (best["status"] == "OPTIMAL" and not best.get("fallback") and not open_
              and not open_angles)
    paths = [row["determinism"] for row in rows if "determinism" in row]
    reproducible = bool(paths) and all(p in ("canonical", "single_thread") for p in paths)
    # Choices fixed before the search rather than searched. They bound what
    # "proven optimal" means; they are scope, not open items.
    fixed_choices = {}
    if symmetry is not None:
        fixed_choices["symmetry_mapping"] = {
            "rule": "netlist_symmetry: moved devices, swapped nets, pair classes, then names",
            "pairs": [list(p) for p in symmetry.pairs]}
    return {
        "scope": (f"fold targets {folds} x axes {axes}; per block "
                  + ("every executable orientation (PDK freedom x learned relations), decided "
                     "jointly with position by CP-SAT; " if orientation_search else
                     "the identity orientation only (MIM 0/90 as candidates); ")
                  + "area of the material bounding box. Not searched: other fold targets, "
                    "non-shared chains, diagonal constructions, routing"
                  + ("; other netlist symmetry mappings (one mapping chosen by a fixed rule)"
                     if symmetry is not None else "")
                  + "."),
        "fixed_choices": fixed_choices,
        "search": "per candidate: joint orientation+position CP-SAT; between candidates: "
                  "best-first branch and bound on the CP-SAT lower bound (strict: a bound "
                  "equal to the best area is not pruned)",
        # The priority order used to compare solutions and candidates
        # (GLOBAL_COORDINATOR_DESIGN_zh.md §2.1), and the winner's vector.
        "objective": ["hard_constraints", "area_nm2", "soft_relation_cost",
                      "canonical_tiebreak"],
        "winner_objective": [0, best["area"],
                             max(best["result"]["solver"].get("soft_symmetry_cost", -1), 0),
                             "candidate order, then smallest coordinates and variant indices"],
        # Reproducibility: every solve reports how its placement was fixed;
        # 'canonical' / 'single_thread' are deterministic, anything else is not.
        # The output layout only needs the winner's path; the certificate as a
        # whole (losing candidates' layouts feed their verification) needs all.
        "determinism": {
            "output_reproducible": best["result"]["solver"].get("determinism")
            in ("canonical", "single_thread"),
            "reproducible": reproducible,
            "paths": sorted(set(paths))},
        "orientation_search": orientation_search,
        "symmetry_breaking": ("uniform_gate_orientation: gate-bearing blocks keep gates as built "
                              "(a global quarter turn maps any other solution to an equal-area one "
                              "within the axis candidates)"),
        "trace": trace,
        "candidates": rows,
        "winner": best["candidate"],
        "best_area_nm2": best["area"],
        "blocks": blocks,
        "open_candidates": open_,
        "open_angles": open_angles,
        "proven_optimal_in_model": proven,
    }


def _strip(t):
    return {k: v for k, v in t.items() if k != "origins_nm"}
