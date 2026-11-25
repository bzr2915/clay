"""Training-only flat-GDS observer and finite symbolic hypothesis selection.

Coordinates/regions never cross into primitive_rules.json. This is single-
sample relation induction, NOT discovery of arbitrary new topology predicates.
"""
from dataclasses import asdict
from types import SimpleNamespace
import hashlib
import klayout.db as db

from clay.frontend.reference_device_shapes import observe_reference_pcell_geometry, _shapes
from clay.layout.extract.discover import detect_relations
from clay.learning.circuit import match_inverters
from clay.learning.primitive_rules import PrimitiveRule, library_dict
from clay.learning.generate import generate_placement, box_tuple
from clay.pdk.semantic import default_pack


def observe_training(gds_path, circuit, contract, *, pack=None):
    pairs = match_inverters(circuit, contract)
    if len(pairs) != 1:
        raise ValueError("The S1 observer requires one unambiguous complementary pair")
    layout = db.Layout()
    layout.read(str(gds_path))
    if len(layout.top_cells()) != 1 or layout.dbu != .001:
        raise ValueError("Training adapter requires one top and 1 nm DBU")
    top = layout.top_cell()
    if any(True for _ in top.each_inst()):
        raise ValueError("This training adapter accepts flat GDS only")
    # Layer numbers come from the pack, never from this observer. The old table
    # was GF180's numbers under process-neutral names, so the "semantic_layers"
    # it handed downstream were semantic in name only.
    p = default_pack() if pack is None else pack
    keys = {role: p.layer(role) for role in
            ("active", "poly", "contact", "nwell", "nplus", "pplus", "dualgate")
            if role in p.layers}
    keys["diff"] = keys.pop("active")
    # ``od2`` was GF180's 5 V oxide role; a pack without it falls back to the
    # diffusion layer so the artwork map still has an entry.
    keys["od2"] = keys.get("dualgate", keys["diff"])
    regs = {name: db.Region(top.begin_shapes_rec(layout.layer(*key))).merged() for name, key in keys.items()}
    metal = db.Region(top.begin_shapes_rec(layout.layer(*p.layer("metal1")))).merged()
    # Trace the routed training cell's labelled G/S/D conductors. Bulk has no
    # local tap in this library cell; its net is an explicit contract obligation.
    nodes = []
    for key, reg in (("sd", regs["diff"]-regs["poly"]), ("poly", regs["poly"]),
                     ("metal", metal), ("cut", regs["contact"])):
        nodes.extend((key, db.Region(p)) for p in reg.each())
    parent = list(range(len(nodes)))
    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for i, (kind, reg) in enumerate(nodes):
        if kind != "cut":
            continue
        for j, (other, r) in enumerate(nodes):
            if other != "cut" and not (reg & r).is_empty():
                parent[root(i)] = root(j)
    labels = {}
    # Label layer comes from the pack: GF180 labels metal1 at 34/10, sky130 at
    # 67/5. Reading GF180's number here silently produced zero labels on any
    # other process, which then failed as "connectivity mismatch".
    label_layer = p.annotation_layers.get("metal1")
    if label_layer is None:
        raise ValueError(f"pack {p.pdk_id!r} declares no label layer for metal1")
    for shape in top.shapes(layout.layer(*label_layer)).each():
        if shape.is_text():
            text = shape.text
            point = db.Point(text.x, text.y)
            for i, (kind, reg) in enumerate(nodes):
                if kind == "metal" and any(p.inside(point) for p in reg.each()):
                    labels.setdefault(root(i), set()).add(text.string)
    def nets_for(region, kind):
        return set().union(*(labels.get(root(i), set()) for i, (k, r) in enumerate(nodes)
                             if k == kind and not (r & region).is_empty()))
    bindings, bboxes = {}, {}
    for polygon in regs["diff"].each():
        active = db.Region(polygon)
        channel = (active & regs["poly"]).merged()
        if channel.is_empty():
            continue
        if channel.count() != 1:
            raise ValueError("Multifinger training islands not supported")
        kind = "pmos" if (active-regs["nwell"]).is_empty() else "nmos"
        ab, cb = active.bbox(), channel.bbox()
        local_poly = regs["poly"] & db.Region(db.Box(cb.left, ab.bottom-220, cb.right, ab.top+220))
        artwork = {keys["diff"]: active, keys["poly"]: local_poly,
                   keys["contact"]: regs["contact"].inside(active), keys["od2"]: regs["od2"],
                   keys["pplus" if kind == "pmos" else "nplus"]: regs["pplus" if kind == "pmos" else "nplus"]}
        if kind == "pmos":
            artwork[keys["nwell"]] = regs["nwell"]
        observation = observe_reference_pcell_geometry(
            geometry_by_layer_nm=tuple((k, _shapes(v)) for k, v in sorted(artwork.items()) if not v.is_empty()),
            family=kind, semantic_layers=tuple(sorted(keys.items())),
            raw_to_global=(1, 0, 0, 1, 0, 0), checkpoint=lambda: None)
        if len(observation.primitives) != 1:
            raise ValueError("Exact recognizer did not recover one transistor")
        primitive = observation.primitives[0]
        matches = [d for d in circuit.devices if d.kind == kind and d.w_nm == primitive.width_nm and d.l_nm == primitive.length_nm]
        if len(matches) != 1 or matches[0].name in bindings:
            raise ValueError("Ambiguous/missing geometric device binding")
        d = matches[0]
        gate_nets = nets_for(channel, "poly")
        sd_nets = nets_for(active-channel, "sd")
        if gate_nets != {d.gate} or sd_nets != {d.source, d.drain}:
            raise ValueError(f"Training connectivity mismatch for {d.name}: {gate_nets}, {sd_nets}")
        bboxes[d.name] = tuple(v/1000 for v in box_tuple(ab))
        bindings[d.name] = {"active_bbox_nm": box_tuple(ab), "channel_bbox_nm": box_tuple(cb),
                            "w_nm": primitive.width_nm, "l_nm": primitive.length_nm,
                            "gate_nets": sorted(gate_nets), "sd_nets": sorted(sd_nets),
                            "body": d.body, "body_evidence": "CDL plus external-tap contract; no local body contact",
                            "binding": "unique polarity/exact W,L plus traced G/S/D labels"}
    if set(bindings) != {d.name for d in circuit.devices}:
        raise ValueError("Training geometry does not cover the target circuit")
    relations = detect_relations(SimpleNamespace(device_bboxes=bboxes), touch_tol_um=.001, align_tol_um=.001)
    return {"bindings": bindings, "detected_relations": [asdict(r) for r in relations],
            "sha256": hashlib.sha256(gds_path.read_bytes()).hexdigest(),
            "source": str(gds_path), "coordinate_scope": "training/evaluation ONLY"}


def train_primitives(circuit, contract, observation):
    n, p = match_inverters(circuit, contract)[0]
    nb = observation["bindings"][n.name]["active_bbox_nm"]
    pb = observation["bindings"][p.name]["active_bbox_nm"]
    exact = {r["kind"] for r in observation["detected_relations"]}
    # Finite, predeclared executable alphabet. Center is represented by align:x
    # in the existing extractor and align:center in the runtime schema.
    supported = {"align:left": "align:left" in exact,
                 "align:right": "align:right" in exact,
                 "align:center": "align:x" in exact}
    order_observed = pb[1] >= nb[3]
    baseline = generate_placement(circuit, library_dict([]), contract)
    baseline_area = baseline["measurements"]["material_bbox"]["area_um2"]
    candidates = []
    for alignment in ("align:left", "align:center", "align:right"):
        rule = PrimitiveRule(alignment=alignment)
        try:
            generated = generate_placement(circuit, library_dict([rule]), contract)
            area = generated["measurements"]["material_bbox"]["area_um2"]
            valid = generated["verification"]["passed"]
            violations = int(not supported[alignment]) + int(not order_observed)
            accepted = valid and area <= baseline_area+1e-9 and violations == 0
            candidates.append({"rule": asdict(rule), "local_checks_passed": valid,
                               "area_um2": area, "observed_relation_violations": violations,
                               "symbol_code_bits": 3, "accepted": accepted,
                               "reason": "exact observed role relation; executable; no area regression" if accepted else "not supported by exact training observation"})
        except ValueError as error:
            candidates.append({"rule": asdict(rule), "accepted": False, "reason": str(error)})
    winners = [c for c in candidates if c["accepted"]]
    if len(winners) != 1:
        raise ValueError("Training sample does not identify one supported preference")
    frozen = library_dict([PrimitiveRule(**winners[0]["rule"])])
    return frozen, {"before": library_dict([]), "after": frozen, "candidates": candidates,
                    "baseline_area_um2": baseline_area, "training_samples": 1,
                    "objective": "hard local validation + no area regression; exact symbolic observation consistency; fixed 3-bit relation code tie break",
                    "limitations": "S1 finite-hypothesis style induction, not a fitted topology predicate or full MDL dictionary search; area benefit needs independent ablation"}
