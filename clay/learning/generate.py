"""Target netlist + frozen symbolic rules -> independent placement GDS.

This module never imports the training reader or accepts a reference path.
"""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import klayout.db as db

from clay.learning.circuit import match_inverters
from clay.learning.primitive_rules import load_library
from clay.pdk.semantic import default_pack
from clay.pdk.device_placement import build_contacted_device
from clay.layout.synth.area_placement import solve_area


def _pack(pack):
    return default_pack() if pack is None else pack


def box_tuple(box):
    return (box.left, box.bottom, box.right, box.top)


def materialize(devices, origins, circuit, contract, *, pack=None):
    """Regenerate shared context, retaining every target terminal's net identity."""
    p = _pack(pack)
    layout = db.Layout()
    layout.dbu = .001
    top = layout.create_cell("CLAY_GENERATED")
    regs = {key: db.Region() for key in p.layers}
    bindings = []
    for d in devices:
        x, y = origins[d.device.name]
        trans = db.Trans(x, y)
        for key, reg in d.regions.items():
            regs[key] += reg.transformed(trans)
        terminals = {role: {"net": getattr(d.device, role), "bbox_nm": box_tuple(b.transformed(trans))}
                     for role, b in d.terminals.items()}
        terminals["body"] = {"net": d.device.body, "access": "external_shared_tap_required",
                              "domain": "nwell" if d.device.kind == "pmos" else "substrate"}
        bindings.append({**asdict(d.device), "origin_nm": (x, y), "rotation": d.rotation,
                         "sd_contact_rows": d.sd_contact_rows, "terminals": terminals})
    # All bodies in a polarity have the same declared domain. Their common row
    # permits gap filling without covering the opposite row. External tap scope
    # is unchanged. No source/body nets are shorted or implicit wires inserted.
    for key in ("nwell", "nplus", "pplus", "dualgate", "v5_xtor"):
        if key in regs and not regs[key].is_empty():
            regs[key] = db.Region(regs[key].bbox())
    for key, reg in regs.items():
        top.shapes(layout.layer(*p.layer(key))).insert(reg.merged())
    material = db.Region()
    for reg in regs.values():
        material += reg
    boundary = material.bbox()
    # Boundary and label layers are declared data, not constants: GF180 draws
    # the cell boundary on 0/0 and labels metal1 at 34/10, while sky130 uses
    # 236/0 and 67/5. Hard-coding GF180's numbers on a sky130 layout produces a
    # frame and net names no reader of that process can find.
    top.shapes(layout.layer(*p.boundary_layer)).insert(boundary)
    label_layer = p.annotation_layers.get("metal1")
    if label_layer is None:
        raise ValueError(f"pack {p.pdk_id!r} declares no label layer for metal1; "
                         "net names would be unreadable on the output GDS")
    for binding in bindings:
        for role in ("source", "drain", "gate"):
            term = binding["terminals"][role]
            b = db.Box(*term["bbox_nm"])
            top.shapes(layout.layer(*label_layer)).insert(db.Text(term["net"], db.Trans(b.center())))
    return layout, top, bindings


def validate_layout(layout, top, circuit, contract, bindings, *, pack=None):
    """Read actual GDS geometry; local checks, not full DRC/LVS or routability."""
    p = _pack(pack)
    regs = {key: db.Region(top.begin_shapes_rec(layout.layer(*layer))).merged() for key, layer in p.layers.items()}
    checks = {}
    def empty(label, region):
        checks[label] = region.is_empty()
    def test(label, condition):
        checks[label] = bool(condition)
    active, poly, cuts, metal, well = (regs[k] for k in ("active", "poly", "contact", "metal1", "nwell"))
    test("nm_dbu", layout.dbu == .001)
    grid = p.nm("grid")
    test("manufacturing_grid", all(v.x % grid == 0 and v.y % grid == 0 for reg in regs.values() for poly in reg.each() for v in poly.each_point_hull()))
    test("binding_coverage", len(bindings) == len(circuit.devices) and {b["name"] for b in bindings} == {d.name for d in circuit.devices})
    test("active_islands", active.count() == len(circuit.devices))
    # Rows that fit within the active height, derived from the rule pack only.
    pitch = p.nm("cut") + p.nm("cut_space")
    edge = 2 * p.nm("cut_to_active")
    expected_rows = {d.name: (max(1, (d.w_nm - edge + p.nm("cut_space")) // pitch)
                              if contract.device_recipe.endswith("contact_rows_v2") else 1)
                     for d in circuit.devices}
    total_sd = 2*sum(expected_rows.values())
    test("three_contacted_accesses_per_device", cuts.count() == total_sd+len(circuit.devices) and metal.count() == 3*len(circuit.devices))
    empty("DF.3a_5V", active.space_check(p.nm("active_space")))
    empty("PL.3a_5V", poly.space_check(p.nm("poly_space")))
    empty("PL.1_5V", poly.width_check(p.nm("poly_width")))
    empty("CO.2a", cuts.space_check(p.nm("cut_space")))
    test("CO.1", all(q.bbox().width() == p.nm("cut") and q.bbox().height() == p.nm("cut") and q.area() == p.nm("cut")**2 for q in cuts.each()))
    empty("CO.6_conservative", cuts.sized(p.nm("metal_over_cut")) - metal)
    empty("M1.1", metal.width_check(p.nm("metal_width")))
    empty("M1.2a", metal.space_check(p.nm("metal_space")))
    test("M1.3", all(q.area() >= p.nm("metal_area") for q in metal.each()))
    sd_cuts, gate_cuts = cuts.inside(active), cuts.inside(poly)
    test("contact_classification", sd_cuts.count() == total_sd and gate_cuts.count() == len(circuit.devices))
    empty("CO.4", sd_cuts.sized(p.nm("cut_to_active")) - active)
    empty("CO.3", gate_cuts.sized(p.nm("cut_to_poly")) - poly)
    empty("CO.7", sd_cuts.separation_check(active & poly, p.nm("cut_to_gate")))
    empty("CO.8", gate_cuts.separation_check(active, p.nm("implant_to_opposite_active")))
    pactive, nactive = active.inside(well), active.outside(well)
    test("well_classification", pactive.count()+nactive.count() == active.count())
    empty("DF.4c_5V", pactive.sized(p.nm("well_over_active")) - well)
    empty("DF.16_5V", nactive.separation_check(well, p.nm("well_to_opposite_active")))
    empty("nactive_outside_well", nactive & well)
    empty("NW.1a_5V", well.width_check(p.nm("well_width")))
    empty("NW.2a_5V", well.space_check(p.nm("well_space")))
    for key, act, other in (("nplus", nactive, pactive), ("pplus", pactive, nactive)):
        empty(key+"_enclosure", act.sized(p.nm("implant_over_active")) - regs[key])
        empty(key+"_spacing", regs[key].space_check(p.nm("implant_space")))
        empty(key+"_opposite_active", regs[key].separation_check(other, p.nm("implant_to_opposite_active")))
        test(key+"_area", all(q.area() >= p.nm("implant_area") for q in regs[key].each()))
    empty("DV.6", active.sized(p.nm("oxide_over_active")) - regs["dualgate"])
    empty("DV.8", poly.sized(p.nm("oxide_over_poly")) - regs["dualgate"])
    empty("DV.5", regs["dualgate"].width_check(p.nm("oxide_width")))
    empty("PL.11_12_5V_marker", active + poly - regs["v5_xtor"])
    channels = (active & poly).merged()
    test("channel_count", channels.count() == len(circuit.devices))
    measured = []
    for device in circuit.devices:
        b = next((b for b in bindings if b["name"] == device.name), None)
        if b is None:
            continue
        test(device.name+"_input_fidelity", all(b.get(k) == v for k, v in asdict(device).items()))
        source = db.Box(*b["terminals"]["source"]["bbox_nm"])
        owners = [q for q in active.each() if q.bbox().contains(source.center())]
        if len(owners) != 1:
            test(device.name+"_active_owner", False)
            continue
        own = db.Region(owners[0])
        ch = (own & poly).merged()
        cb, ab = ch.bbox(), own.bbox()
        horizontal_gate = b.get("rotation", 0) % 2 == 1
        length, width = (cb.height(), cb.width()) if horizontal_gate else (cb.width(), cb.height())
        test(device.name+"_exact_WL", ch.count() == 1 and length == device.l_nm and width == device.w_nm and ch.area() == device.l_nm*device.w_nm)
        test(device.name+"_polarity", (own-well).is_empty() if device.kind == "pmos" else (own&well).is_empty())
        cap = (db.Box(ab.left-p.nm("poly_endcap"), cb.bottom, ab.right+p.nm("poly_endcap"), cb.top) if horizontal_gate
               else db.Box(cb.left, ab.bottom-p.nm("poly_endcap"), cb.right, ab.top+p.nm("poly_endcap")))
        test(device.name+"_PL.4_5V", (db.Region(cap)-poly).is_empty())
        overhang = (cb.bottom-ab.bottom, ab.top-cb.top) if horizontal_gate else (cb.left-ab.left, ab.right-cb.right)
        test(device.name+"_DF.6_5V", min(overhang) >= p.nm("sd_overhang"))
        empty(device.name+"_PL.5a_5V", poly.interacting(own).separation_check(active-own, p.nm("field_poly_active") if p.has("field_poly_active") else p.nm("sd_overhang")))
        for role in ("source", "drain", "gate"):
            term = b["terminals"][role]
            landing = db.Region(db.Box(*term["bbox_nm"]))
            expected = 1 if role == "gate" else expected_rows[device.name]
            test(device.name+"_"+role+"_access", term["net"] == getattr(device, role) and (landing-metal).is_empty() and (landing&cuts).count() == expected)
        test(device.name+"_external_body", b["terminals"]["body"]["net"] == device.body)
        # Independently check that drain/source contacts land on opposite
        # connected diffusion regions. Metadata preservation alone is insufficient.
        sd_islands = list((own-poly).merged().each())
        landing_owners = []
        for role in ("source", "drain"):
            point = db.Box(*b["terminals"][role]["bbox_nm"]).center()
            landing_owners.append([i for i, island in enumerate(sd_islands) if island.inside(point)])
        test(device.name+"_distinct_SD", len(sd_islands) == 2 and all(len(v) == 1 for v in landing_owners)
             and landing_owners[0] != landing_owners[1])
        measured.append({"name": device.name, "w_nm": width, "l_nm": length, "model": device.model})
    failed = [key for key, ok in checks.items() if not ok]
    return {"passed": not failed, "check_count": len(checks), "checks": checks, "failed": failed,
            "measured_channels": measured,
            "scope": "local placement geometry and terminal inventory; external taps; no routing/full DRC/LVS/PEX"}


def measure_layout(layout, top, *, pack=None):
    p = _pack(pack)
    material, core = db.Region(), db.Region()
    for key, layer in p.layers.items():
        reg = db.Region(top.begin_shapes_rec(layout.layer(*layer)))
        material += reg
        if key in {"active", "poly", "contact", "metal1"}:
            core += reg
    def dimensions(reg):
        box = reg.bbox()
        return {"bbox_nm": box_tuple(box), "width_um": box.width()/1000,
                "height_um": box.height()/1000, "area_um2": box.area()/1e6}
    return {"material_bbox": dimensions(material), "device_access_bbox": dimensions(core)}


def generate_placement(circuit, library, contract, output=None, *, pack=None):
    p = _pack(pack)
    if contract.floorplan != "two_polarity_bands":
        raise ValueError("Use construction_search for the free single-pair contract")
    # Every device model must be explicitly bound by the rule pack. Parsing is
    # process-neutral, so this is where an unbound model (e.g. a 3.3 V nfet in a
    # 5 V pack) is rejected rather than silently treated as the pack's device.
    for d in circuit.devices:
        try:
            p.polarity(d.model)
        except KeyError:
            raise ValueError(
                f"device model {d.model!r} is not bound by rule pack {p.pdk_id!r}; "
                f"known models: {sorted(p.model_polarity)}") from None
    pairs = match_inverters(circuit, contract)
    rules = load_library(library)
    devices = [build_contacted_device(d, contact_rows=contract.device_recipe == "gf180_5v_contact_rows_v2") for pair in pairs for d in pair]
    names = [d.device.name for d in devices]
    rectangles = {d.device.name: (d.active.width(), d.active.height()) for d in devices}
    outer = {d.device.name: box_tuple(d.bbox()) for d in devices}
    types = {d.device.name: d.device.kind for d in devices}
    clearances = {(a, b): (p.nm("opposite_row_clearance") if types[a] != types[b] else p.nm("active_space"))
                  for i, a in enumerate(names) for b in names[i+1:]}
    preferences = [(n.name, p.name, relation) for n, p in pairs for rule in rules for relation in (rule.alignment, rule.order)]
    placement = solve_area(rectangles, outer, clearances, preferences, grid=contract.grid_nm,
                           seconds=contract.solver_seconds, bands=types)
    layout, top, bindings = materialize(devices, placement.origins_nm, circuit, contract, pack=p)
    verification = validate_layout(layout, top, circuit, contract, bindings, pack=p)
    if not verification["passed"]:
        raise ValueError(f"Generated placement failed local verification: {verification['failed']}")
    measurement = measure_layout(layout, top, pack=p)
    if round(measurement["material_bbox"]["area_um2"]*1e6) != placement.area_nm2:
        raise ValueError("Solver/materialized GDS area mismatch")
    if output is not None:
        layout.write(str(output))
        # Verify the serialized GDS, not just in-memory construction.
        reloaded = db.Layout()
        reloaded.read(str(output))
        verification = validate_layout(reloaded, reloaded.top_cell(), circuit, contract, bindings, pack=p)
        if not verification["passed"]:
            raise ValueError("GDS roundtrip changed validated geometry")
    return {"pdk": p.pdk_id, "circuit": asdict(circuit), "contract": asdict(contract), "bindings": bindings,
            "measurements": measurement, "verification": verification, "solver": asdict(placement),
            "applied_preferences": preferences, "reference_inputs": [], "residual_pins": 0,
            "search_scope": "two polarity bands; free x order/position; both band orders; R0/MX gate-access recipe; M=nf=1; no folding"}
