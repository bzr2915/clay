"""Learn a circuit-level primitive library from reference layouts.

Training input is a set of *(netlist, layout, rule pack)* samples. Nothing in
this module looks at a test sample; the generator (``circuit_generate``)
reads only the frozen library JSON written by :func:`fit_library`.

What is observed (``observe_layout``), per sample, from the GDS alone:

* contact rows per diffusion segment versus segment width (contact policy);
* the best mirror axis of the gate shapes and the fraction of gates that have
  a distinct mirror partner about it (exact and within a tolerance);
* gate orientation uniformity;
* diffusion sharing (gates per diffusion island) and n-well islands per
  PMOS-body domain;
* poly-resistor head geometry and the resistor's length direction relative to
  the channel-length direction.

What is fitted (``fit_library``):

``contact_policies[pdk]``   ``constant`` vs ``fraction``-of-capacity count,
                            chosen by squared error then simplicity; mean
                            normalised first-contact offset.
``relations.mirror_pairs``  enforce when every sample whose netlist has a
                            symmetry also shows a mirrored layout.
``relations.mirror_axis``   the axis direction relative to the gate lines, or
                            ``free`` when samples disagree.
``relations.uniform_gate_orientation``  enforce when every sample has >= 95 %.
``resistor_style[pdk]``     head-cut size and margins (clipped up to rules at
                            construction time).

What is *recorded but not imitated*: per-device diffusion islands and per-
device wells. Those are construction choices, and copying them would reproduce
the training area instead of generalising; the generator decides them with the
area objective. The library says so explicitly (``not_imitated``).
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Sequence

import klayout.db as db

from clay.learning.contact_primitives import contact_capacity
from clay.learning.symmetry import netlist_symmetry

SCHEMA = "clay.circuit-primitives.v1"


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_gds(path) -> db.Layout:
    layout = db.Layout()
    layout.read(str(path))           # KLayout reads .gds.gz transparently
    return layout


def _region(layout, layer):
    idx = layout.find_layer(*layer)
    if idx is None:
        return db.Region()
    return db.Region(layout.top_cell().begin_shapes_rec(idx)).merged()


def _scaled(region, dbu_nm):
    if dbu_nm == 1:
        return region
    return region.transformed(db.ICplxTrans(dbu_nm))


def layout_regions(layout, pack) -> Dict[str, db.Region]:
    """Every pack role of ``layout`` in integer nm, whatever the file's dbu."""
    dbu_nm = round(layout.dbu * 1000)
    return {role: _scaled(_region(layout, layer), dbu_nm) for role, layer in pack.layers.items()}


def material_bbox(layout, pack, roles: Optional[Sequence[str]] = None):
    """Bounding box (nm) of drawn material, frames excluded.

    ``roles=None`` uses every polygon layer in the file except the pack's
    frame layers; a role list restricts it to those pack roles.
    """
    dbu_nm = round(layout.dbu * 1000)
    top = layout.top_cell()
    total = db.Box()
    frames = set(tuple(f) for f in pack.frame_layers) | {tuple(pack.boundary_layer)}
    if roles is None:
        indices = [i for i in layout.layer_indexes()
                   if (layout.get_info(i).layer, layout.get_info(i).datatype) not in frames]
    else:
        indices = [layout.find_layer(*pack.layers[r]) for r in roles if r in pack.layers]
        indices = [i for i in indices if i is not None]
    for i in indices:
        reg = db.Region(top.begin_shapes_rec(i))
        if not reg.is_empty():
            total += reg.bbox()
    if total.empty():
        return None
    b = total.transformed(db.ICplxTrans(dbu_nm)) if dbu_nm != 1 else total
    return (b.left, b.bottom, b.right, b.top)


def frame_bbox(layout, pack):
    dbu_nm = round(layout.dbu * 1000)
    total = db.Box()
    for layer in pack.frame_layers:
        reg = _region(layout, layer)
        if not reg.is_empty():
            total += reg.bbox()
    if total.empty():
        return None
    b = total.transformed(db.ICplxTrans(dbu_nm)) if dbu_nm != 1 else total
    return (b.left, b.bottom, b.right, b.top)


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1]) if b else None


DEVICE_PLANE_ROLES = ("active", "tap", "poly", "contact", "metal1", "nwell", "nplus", "pplus",
                      "hvtp", "lvtn", "npc", "polyres", "rpm", "dualgate", "v5_xtor")


def _mirror_stats(gates: List[db.Box], tol: int):
    """Best mirror axis per direction and the fraction of gates it pairs."""
    out = {}
    keys = [(b.left, b.bottom, b.right, b.top) for b in gates]
    for axis in ("vertical", "horizontal"):
        best = (0, 0, None)
        cands = set()
        for a in keys:
            for b in keys:
                cands.add(a[0] + b[2] if axis == "vertical" else a[1] + b[3])
        for s2 in cands:
            paired = centred = 0
            for k in keys:
                m = (s2 - k[2], k[1], s2 - k[0], k[3]) if axis == "vertical" else \
                    (k[0], s2 - k[3], k[2], s2 - k[1])
                if max(abs(x - y) for x, y in zip(m, k)) <= tol:
                    centred += 1
                elif any(max(abs(x - y) for x, y in zip(m, o)) <= tol for o in keys):
                    paired += 1
            if (paired, centred) > best[:2]:
                best = (paired, centred, s2)
        out[axis] = {"paired_fraction": best[0] / len(keys) if keys else 0.0,
                     "centred_fraction": best[1] / len(keys) if keys else 0.0,
                     "axis2_nm": best[2]}
    return out


def observe_layout(sample_id, gds_path, circuit, pack) -> Dict[str, object]:
    """Measurements of one training layout (see module docstring)."""
    layout = read_gds(gds_path)
    R = layout_regions(layout, pack)
    diff = R["active"]
    gates = (diff & R["poly"]).merged()
    gate_boxes = [q.bbox() for q in gates.each()]
    segs = (diff - R["poly"]).merged()
    cuts = R["contact"]
    vertical = sum(1 for b in gate_boxes if b.height() >= b.width())
    orient = max(vertical, len(gate_boxes) - vertical) / max(1, len(gate_boxes))
    gates_vertical = vertical >= len(gate_boxes) - vertical
    contacts = []
    for s in segs.each():
        b = s.bbox()
        inside = [q.bbox() for q in (cuts & db.Region(s)).each()]
        if not inside:
            continue
        width = b.height() if gates_vertical else b.width()
        lo = (b.bottom if gates_vertical else b.left)
        first = min((q.bottom if gates_vertical else q.left) for q in inside)
        columns = len({(q.left if gates_vertical else q.bottom) for q in inside})
        rows = len(inside) // max(1, columns)
        contacts.append({"width_nm": width, "count": rows, "columns": columns,
                         "first_offset_nm": first - lo})
    islands = []
    for isl in diff.each():
        n = (gates & db.Region(isl)).count()
        if n:
            islands.append(n)
    pmos_bodies = {d.net("b") for d in circuit.devices if d.cls == "mos" and d.kind == "pmos"}
    sym = netlist_symmetry(circuit)
    tol = 2 * pack.nm("active_space")
    mirror_exact = _mirror_stats(gate_boxes, 0)
    mirror_tol = _mirror_stats(gate_boxes, tol)
    best_axis = max(mirror_tol, key=lambda a: (mirror_tol[a]["paired_fraction"],
                                               mirror_tol[a]["centred_fraction"]))
    # The axis relative to the gate lines: gates vertical + vertical axis = parallel.
    rel = "parallel" if (best_axis == "vertical") == gates_vertical else "perpendicular"
    resistors = []
    if "polyres" in R and not R["polyres"].is_empty():
        for body in R["polyres"].each():
            bb = body.bbox()
            poly = R["poly"].interacting(db.Region(body)).merged()
            pb = poly.bbox()
            head_cuts = [q.bbox() for q in (cuts & poly).each()]
            horizontal = bb.width() >= bb.height()
            if not head_cuts:
                continue
            hc = head_cuts[0]
            along = hc.width() if horizontal else hc.height()
            across = hc.height() if horizontal else hc.width()
            heads = [q.bbox() for q in (poly - db.Region(body)).merged().each()
                     if not (db.Region(q) & cuts).is_empty()]
            head = heads[0] if heads else pb
            margin = min(hc.left - head.left, head.right - hc.right,
                         hc.bottom - head.bottom, head.top - hc.top)
            pads = [q.bbox() for q in R["metal1"].each() if q.bbox().overlaps(hc)]
            mmargin = min(hc.left - pads[0].left, pads[0].right - hc.right,
                          hc.bottom - pads[0].bottom, pads[0].top - hc.top) if pads else None
            rpm = R["rpm"].interacting(poly).bbox() if "rpm" in R else None
            resistors.append({
                "body_nm": [bb.width(), bb.height()],
                "length_axis": "x" if horizontal else "y",
                "relation_to_channel_length": "parallel" if horizontal == gates_vertical
                else "perpendicular",
                "head_cut_nm": [along, across], "poly_over_head_cut_nm": margin,
                "metal_over_head_cut_nm": mmargin,
                "marker_over_poly_nm": min(pb.left - rpm.left, rpm.right - pb.right,
                                           pb.bottom - rpm.bottom, rpm.top - pb.top) if rpm else None})
    mat = material_bbox(layout, pack)
    dev = material_bbox(layout, pack, DEVICE_PLANE_ROLES)
    return {
        "sample_id": sample_id, "pdk": pack.pdk_id, "gds_sha256": sha256(gds_path),
        "gates": len(gate_boxes), "gate_orientation_uniformity": orient,
        "gates_vertical": gates_vertical,
        "contacts": contacts, "gates_per_island": islands,
        "nwell_islands": R["nwell"].count(), "pmos_body_domains": len(pmos_bodies),
        "pmos_devices": sum(1 for d in circuit.devices if d.cls == "mos" and d.kind == "pmos"),
        "netlist_symmetry": sym.as_dict() if sym else None,
        "mirror_exact": mirror_exact, "mirror_tolerant": mirror_tol, "mirror_tolerance_nm": tol,
        "best_axis": best_axis, "axis_relative_to_gates": rel,
        "resistors": resistors,
        "material_bbox_nm": mat, "material_area_nm2": _area(mat),
        "device_plane_bbox_nm": dev, "device_plane_area_nm2": _area(dev),
    }


def _fit_contacts(examples, pack):
    """Choose ``constant`` or ``fraction``; ties go to the simpler constant."""
    caps = [contact_capacity(e["width_nm"], pack=pack) for e in examples]
    counts = [e["count"] for e in examples]
    cands = []
    for n in sorted(set(counts)):
        err = sum((min(n, c) - k) ** 2 for k, c in zip(counts, caps))
        cands.append((err, 0, {"kind": "constant", "count": n}))
    f = mean(k / c for k, c in zip(counts, caps) if c)
    err = sum((max(1, min(c, round(f * c))) - k) ** 2 for k, c in zip(counts, caps))
    cands.append((err, 1, {"kind": "fraction", "f": round(f, 4)}))
    cands.sort(key=lambda t: (t[0], t[1]))
    best = cands[0]
    pitch = pack.nm("cut") + pack.nm("cut_space")
    lo = pack.nm("cut_to_active")
    alphas = []
    for e in examples:
        n = e["count"]
        free = e["width_nm"] - 2 * lo - pack.nm("cut") - (n - 1) * pitch
        if free > 0:
            alphas.append(min(1.0, max(0.0, (e["first_offset_nm"] - lo) / free)))
    return {"rows": best[2], "first_contact_free_margin_fraction": round(mean(alphas), 4)
            if alphas else 0.5,
            "evidence": {"examples": len(examples), "squared_error": best[0],
                         "alternatives": [{"policy": c[2], "squared_error": c[0]} for c in cands],
                         "widths_nm": sorted({e["width_nm"] for e in examples})}}


def fit_library(observations: Sequence[Dict[str, object]], packs) -> Dict[str, object]:
    """Freeze the learned circuit primitives (see module docstring)."""
    if len({o["gds_sha256"] for o in observations}) != len(observations):
        raise ValueError("duplicate training layout")
    by_pdk: Dict[str, List[dict]] = {}
    for o in observations:
        by_pdk.setdefault(o["pdk"], []).append(o)
    contact_policies = {}
    for pdk, obs in by_pdk.items():
        ex = [e for o in obs for e in o["contacts"]]
        if ex:
            contact_policies[pdk] = _fit_contacts(ex, packs[pdk])
    with_sym = [o for o in observations if o["netlist_symmetry"]]
    support = [{"sample": o["sample_id"],
                "netlist_pairs": len(o["netlist_symmetry"]["pairs"]),
                "layout_paired_fraction_exact": o["mirror_exact"][o["best_axis"]]["paired_fraction"],
                "layout_paired_fraction_tolerant": o["mirror_tolerant"][o["best_axis"]]["paired_fraction"],
                "layout_centred_fraction_tolerant": o["mirror_tolerant"][o["best_axis"]]["centred_fraction"],
                "axis_relative_to_gates": o["axis_relative_to_gates"]} for o in with_sym]
    enforce = bool(support) and all(s["layout_paired_fraction_tolerant"] >= .25 for s in support)
    rels = {s["axis_relative_to_gates"] for s in support}
    orient = [o["gate_orientation_uniformity"] for o in observations]
    resistor_style = {}
    for pdk, obs in by_pdk.items():
        rs = [r for o in obs for r in o["resistors"]]
        if rs:
            resistor_style[pdk] = {
                "head_cut_nm": max((r["head_cut_nm"] for r in rs), key=lambda v: v[0] * v[1]),
                "poly_over_head_cut_nm": max(r["poly_over_head_cut_nm"] for r in rs),
                "metal_over_head_cut_nm": max((r["metal_over_head_cut_nm"] or 0) for r in rs),
                "marker_over_poly_nm": max((r["marker_over_poly_nm"] or 0) for r in rs),
                "length_relation_to_channel_length": sorted({r["relation_to_channel_length"]
                                                              for r in rs}),
                "examples": len(rs)}
    sharing = [n for o in observations for n in o["gates_per_island"]]
    lib = {
        "schema": SCHEMA,
        "sources": [{"sample": o["sample_id"], "pdk": o["pdk"], "gds_sha256": o["gds_sha256"]}
                    for o in observations],
        "rule_packs": {pdk: {"deck_sha256": packs[pdk].deck_sha256, "grid_nm": packs[pdk].nm("grid"),
                             "cut_pitch_nm": packs[pdk].nm("cut_pitch")} for pdk in by_pdk},
        "contact_policies": contact_policies,
        "relations": {
            "mirror_pairs": {"decision": "enforce" if enforce else "off", "support": support,
                             "rule": "netlist-symmetric devices are placed as mirror images "
                                     "about one common axis"},
            "mirror_axis": {"decision": rels.pop() if len(rels) == 1 else "free",
                            "observed": sorted({s["axis_relative_to_gates"] for s in support})},
            "centre_fixed_devices": {"decision": "soft",
                                     "observed_centred_fraction": [s["layout_centred_fraction_tolerant"]
                                                                   for s in support]},
            "uniform_gate_orientation": {"decision": "enforce" if orient and min(orient) >= .95
                                         else "off", "observed": orient},
        },
        "resistor_style": resistor_style,
        "not_imitated": {
            "gates_per_diffusion_island": {"observed_mean": mean(sharing) if sharing else None,
                                           "observed_max": max(sharing) if sharing else None},
            "nwell_islands_per_pmos_domain": [o["nwell_islands"] / max(1, o["pmos_body_domains"])
                                              for o in observations],
            "reason": "construction choices (diffusion sharing, well merging, finger folding) "
                      "are decided per target by the area objective; copying the training "
                      "choice would reproduce the training area rather than generalise",
        },
    }
    validate_library(lib)
    return lib


def validate_library(lib) -> None:
    if lib.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} library")
    for key in ("sources", "contact_policies", "relations", "resistor_style", "not_imitated"):
        if key not in lib:
            raise ValueError(f"library misses {key!r}")
    for pdk, pol in lib["contact_policies"].items():
        rows = pol["rows"]
        if rows["kind"] not in ("constant", "fraction", "width_stump"):
            raise ValueError(f"{pdk}: unknown contact policy {rows['kind']!r}")
        if not 0 <= pol["first_contact_free_margin_fraction"] <= 1:
            raise ValueError(f"{pdk}: offset fraction out of range")
    if lib["relations"]["mirror_pairs"]["decision"] not in ("enforce", "off"):
        raise ValueError("mirror_pairs decision must be enforce/off")


def save_library(lib, path) -> None:
    validate_library(lib)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(lib, indent=2, ensure_ascii=False), encoding="utf-8")


def load_library(path) -> Dict[str, object]:
    lib = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_library(lib)
    return lib
