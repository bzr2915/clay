"""Circuit-general layout checks: a local DRC subset plus device-level LVS-lite.

This is the generalisation of ``contact_primitives.verify`` (which assumes one
inverter: one gate net, two islands, ``2*gates+1`` metal pads). Here the
target is any :class:`clay.learning.netlist.FlatCircuit` and the layout is any
GDS written by ``materialize``. Two independent parts:

DRC subset
    Width/space/enclosure/separation checks for every rule key the
    constructors read, on the layout *as written to GDS and read back*.
    Optional checks run only where the pack states the rule (``pack.has``).
    It is a documented subset, not a foundry deck.

LVS-lite
    Channels are extracted as poly over diffusion; gate and S/D nets are read
    from the net labels on the metal pads that the contacts of each poly
    polygon / diffusion segment land on. Devices are compared after parallel
    reduction: the key ``(class, polarity, L, gate, {S, D}, select markers)``
    must carry the same total W in layout and netlist. Resistors compare
    ``(W, L, {a, b})``, MIM capacitors ``(W x L, {top, bot})``. Unrouted
    layouts are the scope: nets are *not* checked for connectivity between
    devices, only that each device terminal carries the right net label and
    that no metal shape carries two different labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional

import klayout.db as db

SCHEMA = "clay.circuit-verify.v1"


def _regions(layout, pack):
    top = layout.top_cell()
    out = {}
    for role, (layer, dt) in pack.layers.items():
        idx = layout.find_layer(layer, dt)
        out[role] = db.Region(top.begin_shapes_rec(idx)).merged() if idx is not None else db.Region()
    return out


def _texts(layout, pack):
    top = layout.top_cell()
    out = defaultdict(list)
    for role, (layer, dt) in pack.annotation_layers.items():
        idx = layout.find_layer(layer, dt)
        if idx is None:
            continue
        it = top.begin_shapes_rec(idx)
        while not it.at_end():
            sh = it.shape()
            if sh.is_text():
                t = sh.text.transformed(it.trans())
                out[role].append((db.Point(t.x, t.y), t.string))
            it.next()
    return out


def _labels_in(polygon, texts):
    return {s for pt, s in texts if polygon.inside(pt)}


def verify_circuit_layout(layout, circuit, pack, *, routed: bool = False) -> Dict[str, object]:
    p = pack
    R = _regions(layout, p)
    T = _texts(layout, p)
    checks: Dict[str, bool] = {}
    notes: Dict[str, object] = {}

    def empty(name, region):
        checks[name] = region.is_empty()
        if not checks[name]:
            notes[name] = [list((b.left, b.bottom, b.right, b.top))
                           for b in [q.bbox() for q in list(region.each())[:3]]]

    def has(*keys):
        return all(p.has(k) for k in keys)

    grid = p.nm("grid")
    checks["dbu_nm"] = abs(layout.dbu - 0.001) < 1e-12
    checks["grid"] = all(v.x % grid == 0 and v.y % grid == 0
                         for reg in R.values() for poly in reg.each()
                         for v in poly.each_point_hull())
    diff = R["active"] + (R["tap"] if "tap" in R else db.Region())
    poly, cut, m1, well = R["poly"], R["contact"], R["metal1"], R["nwell"]
    rpm = R.get("rpm", db.Region())
    res_body = R.get("polyres", db.Region())
    plain_cut = cut.outside(rpm)          # resistor head cuts are slots inside rpm
    c = p.nm("cut")
    checks["cut_size"] = all(q.bbox().width() == c and q.bbox().height() == c and q.is_box()
                             for q in plain_cut.each())
    empty("diff_space", diff.space_check(p.nm("active_space")))
    empty("poly_space", poly.space_check(p.nm("poly_space")))
    empty("poly_width", poly.width_check(p.nm("poly_width")))
    empty("cut_space", plain_cut.space_check(p.nm("cut_space")))
    empty("metal1_space", m1.space_check(p.nm("metal_space")))
    empty("metal1_width", m1.width_check(p.nm("metal_width")))
    checks["metal1_area"] = all(q.area() >= p.nm("metal_area") for q in m1.each())
    empty("cut_in_metal1", cut.sized(p.nm("metal_over_cut")) - m1 - rpm.sized(1))
    empty("cut_in_metal1_slot", (cut & rpm) - m1)
    sd_cut = cut.inside(diff)
    gate_cut = cut.inside(poly).outside(rpm)
    checks["cut_classified"] = sd_cut.count() + gate_cut.count() + (cut & rpm).count() == cut.count()
    empty("diff_cut_enclosure", sd_cut.inside(R["active"]).sized(p.nm("cut_to_active")) - R["active"])
    if "tap" in R and has("tap_cut_enclosure"):
        tap_cut = sd_cut.inside(R["tap"])
        # licon.7: tap encloses the cut by the rule on one of two opposite edges.
        bad = db.Region()
        for q in tap_cut.each():
            b = q.bbox()
            e = p.nm("tap_cut_enclosure")
            if not (db.Region(db.Box(b.left - e, b.bottom, b.right + e, b.top)) - R["tap"]).is_empty() \
                    and not (db.Region(db.Box(b.left, b.bottom - e, b.right, b.top + e)) - R["tap"]).is_empty():
                bad.insert(b)
        empty("tap_cut_enclosure", bad)
    empty("gate_cut_enclosure", gate_cut.sized(p.nm("cut_to_poly")) - poly)
    channels = (diff & poly).merged()
    empty("diff_cut_to_gate", sd_cut.inside(R["active"]).separation_check(
        channels & R["active"], p.nm("cut_to_gate")))
    if "tap" in R and has("var_cut_to_gate"):
        empty("tap_cut_to_varactor", sd_cut.inside(R["tap"]).separation_check(
            channels & R["tap"], p.nm("var_cut_to_gate")))
    empty("gate_cut_to_diff", gate_cut.separation_check(diff, p.nm("gate_cut_to_active")))
    if "npc" in R and has("npc_over_cut"):
        empty("npc_over_poly_cut", gate_cut.sized(p.nm("npc_over_cut")) - R["npc"])
        if has("npc_space"):
            empty("npc_space", R["npc"].space_check(p.nm("npc_space")))
            empty("npc_width", R["npc"].width_check(p.nm("npc_width")))
    # Wells and polarity.
    pdiff = R["active"] & R["pplus"] if "pplus" in R else db.Region()
    ndiff = R["active"] - pdiff
    taps_in_well = R["tap"].inside(well) if "tap" in R else db.Region()
    empty("pdiff_in_well", pdiff.sized(p.nm("well_over_active")) - well)
    empty("ndiff_to_well", ndiff.separation_check(well, p.nm("well_to_opposite_active")))
    empty("ndiff_outside_well", ndiff & well)
    empty("well_width", well.width_check(p.nm("well_width")))
    if has("well_space"):
        empty("well_space", well.space_check(p.nm("well_space")))
    if "tap" in R:
        empty("varactor_tap_in_well", R["tap"] - well)
        if has("var_well_over_poly"):
            empty("varactor_gate_in_well",
                  (poly & R["tap"]).sized(p.nm("var_well_over_poly")) - well)
    for imp, own, other in (("nplus", ndiff + R.get("tap", db.Region()), pdiff),
                            ("pplus", pdiff, ndiff + R.get("tap", db.Region()))):
        if imp not in R:
            continue
        empty(f"{imp}_encloses_diff", own.sized(p.nm("implant_over_active")) - R[imp])
        empty(f"{imp}_to_opposite_diff", R[imp].separation_check(other, p.nm("implant_to_opposite_active")))
        empty(f"{imp}_space", R[imp].space_check(p.nm("implant_space")))
        checks[f"{imp}_area"] = all(q.area() >= p.nm("implant_area") for q in R[imp].each())
    if "nplus" in R and "pplus" in R:
        empty("implant_overlap", (R["nplus"] & R["pplus"]) - rpm)
    # Threshold-select markers: every device whose model needs one is covered.
    over = p.nm("vt_marker_over_gate") if has("vt_marker_over_gate") else p.nm("implant_over_active")
    for role in sorted({r for ms in p.model_markers.values() for r in ms}):
        if role not in R or R[role].is_empty():
            continue
        if has("vt_marker_width", "vt_marker_space"):
            empty(f"{role}_width", R[role].width_check(p.nm("vt_marker_width")))
            empty(f"{role}_space", R[role].space_check(p.nm("vt_marker_space")))
    # Poly resistor and MIM.
    if not res_body.is_empty():
        empty("res_rpm_enclosure", res_body.sized(p.nm("rpm_over_res")) - rpm)
        empty("res_space_diff", res_body.separation_check(diff, p.nm("res_space")))
        empty("rpm_space", rpm.space_check(p.nm("rpm_space")))
    if "capm" in R and not R["capm"].is_empty():
        empty("capm_in_plate", R["capm"].sized(p.nm("plate_over_cap")) - R["metal3"])
        empty("via_in_capm", R["cut3"].sized(p.nm("cap_over_via")) - R["capm"])
        empty("capm_space", R["capm"].space_check(p.nm("cap_space")))
        empty("plate_space", R["metal3"].space_check(p.nm("plate_space")))
        empty("via_in_metal4", R["cut3"].sized(p.nm("metal4_over_via3")) - R["metal4"])

    # ------------------------------------------------------------ LVS-lite
    m1_labels = T.get("metal1", [])
    pad_net = {}
    multi = []
    for q in m1.each():
        labels = _labels_in(q, m1_labels)
        if len(labels) > 1 and not routed:
            multi.append(sorted(labels))
        pad_net[id(q)] = labels
    checks["no_shorted_labels"] = not multi
    if multi:
        notes["shorted_labels"] = multi[:5]
    m1_list = list(m1.each())

    def nets_of(region):
        """Labels on the metal1 pads that the cuts in ``region`` land on."""
        found = set()
        cuts = cut & region
        for q in m1_list:
            if not (db.Region(q) & cuts).is_empty():
                found |= _labels_in(q, m1_labels)
        return found

    marker_roles = sorted({r for ms in p.model_markers.values() for r in ms})
    extracted = Counter()
    unresolved = []
    poly_list = list(poly.each())
    segments = (diff - poly).merged()
    seg_list = list(segments.each())
    for ch in channels.each():
        b = ch.bbox()
        on_tap = "tap" in R and not (db.Region(ch) & R["tap"]).is_empty()
        pp = [q for q in poly_list if not (db.Region(q) & db.Region(ch)).is_empty()]
        gate = nets_of(db.Region(pp[0])) if len(pp) == 1 else set()
        # Gate runs across the diffusion: its length is the extent along the
        # diffusion's long direction, width the other.
        touching = [s for s in seg_list if s.bbox().overlaps(b.enlarged(1)) or s.bbox().touches(b)]
        vertical = any(s.bbox().right == b.left or s.bbox().left == b.right for s in touching)
        l_nm = b.width() if vertical else b.height()
        w_nm = b.height() if vertical else b.width()
        sd = [nets_of(db.Region(s)) for s in touching]
        kind = "pmos" if not (db.Region(ch) & R["pplus"]).is_empty() else "nmos"
        cls = "cap_var" if on_tap else "mos"
        marks = tuple(r for r in marker_roles
                      if r in R and (db.Region(ch.bbox().enlarged(over)) - R[r]).is_empty())
        if len(gate) != 1 or len(sd) != 2 or any(len(s) != 1 for s in sd):
            unresolved.append({"channel": [b.left, b.bottom, b.right, b.top],
                               "gate": sorted(gate), "sd": [sorted(s) for s in sd]})
            continue
        key = (cls, kind if cls == "mos" else "nmos", l_nm, next(iter(gate)),
               frozenset(next(iter(s)) for s in sd), marks)
        extracted[key] += w_nm
    expected = Counter()
    for d in circuit.devices:
        if d.cls == "mos":
            key = (d.cls, d.kind, d.l_nm, d.net("g"), frozenset({d.net("s"), d.net("d")}),
                   tuple(r for r in marker_roles if r in p.model_markers.get(d.model, ())))
            expected[key] += d.w_nm * d.m
        elif d.cls == "cap_var":
            key = (d.cls, "nmos", d.l_nm, d.net("top"), frozenset({d.net("bot")}),
                   tuple(r for r in marker_roles if r in p.model_markers.get(d.model, ())))
            expected[key] += d.w_nm * d.m
    checks["channels_resolved"] = not unresolved
    checks["devices_match"] = extracted == expected
    if unresolved:
        notes["unresolved_channels"] = unresolved[:5]
    if extracted != expected:
        notes["device_mismatch"] = {
            "missing": [list(map(str, k)) + [v] for k, v in (expected - extracted).items()][:8],
            "extra": [list(map(str, k)) + [v] for k, v in (extracted - expected).items()][:8]}
    # Passives.
    res_expected = Counter((d.w_nm, d.l_nm, frozenset({d.net("a"), d.net("b")}))
                           for d in circuit.devices if d.cls == "res_poly" for _ in range(d.m))
    res_found = Counter()
    for body in res_body.each():
        bb = body.bbox()
        pp = [q for q in poly_list if not (db.Region(q) & db.Region(body)).is_empty()]
        nets = nets_of(db.Region(pp[0])) if len(pp) == 1 else set()
        horizontal = bb.width() >= bb.height()
        res_found[(bb.height() if horizontal else bb.width(),
                   bb.width() if horizontal else bb.height(), frozenset(nets))] += 1
    checks["resistors_match"] = res_found == res_expected
    cap_expected = Counter((min(d.w_nm, d.l_nm), max(d.w_nm, d.l_nm), d.net("top"), d.net("bot"))
                           for d in circuit.devices if d.cls == "cap_mim" for _ in range(d.m))
    cap_found = Counter()
    if "capm" in R:
        for q in R["capm"].each():
            bb = q.bbox()
            tops = {s for m4 in R["metal4"].each() if m4.bbox().overlaps(bb)
                    for s in _labels_in(m4, T.get("metal4", []))}
            bots = {s for m3 in R["metal3"].each() if m3.bbox().overlaps(bb)
                    for s in _labels_in(m3, T.get("metal3", []))}
            cap_found[(min(bb.width(), bb.height()), max(bb.width(), bb.height()),
                       next(iter(tops)) if len(tops) == 1 else str(sorted(tops)),
                       next(iter(bots)) if len(bots) == 1 else str(sorted(bots)))] += 1
    checks["capacitors_match"] = cap_found == cap_expected
    failed = [k for k, v in checks.items() if not v]
    return {"schema": SCHEMA, "pdk": p.pdk_id, "passed": not failed, "check_count": len(checks),
            "checks": checks, "failed": failed, "notes": notes,
            "devices": {"keys": len(expected), "extracted_channels": sum(1 for _ in channels.each()),
                        "resistors": sum(res_expected.values()),
                        "capacitors": sum(cap_expected.values())},
            "scope": "local DRC subset of the pack rules + label-based device LVS after parallel "
                     "reduction; unrouted placement: inter-device connectivity, full DRC, "
                     "density, antenna and PEX are out of scope"}
