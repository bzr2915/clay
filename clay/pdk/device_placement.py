"""Single-finger, contacted device geometry (integer nm).

Every number and layer comes from a :class:`clay.pdk.semantic.Pack`; this module
owns only the *shape* of the recipe, which is why neither the module nor its
functions carry a process name. The file was once called
``gf180_5v_placement.py`` and carried its own ``LAYERS`` and ``RULES`` tables -
GF180's numbers under process-neutral names, the same defect the SKY130
reproduction exposed elsewhere. The old literal names now map to pack keys like
this:

============================  ======================================
old literal                   pack source
============================  ======================================
``contact``                   ``cut``
``contact_space``             ``cut_space``
``contact_gate``              ``cut_to_gate``
``contact_comp``              ``cut_to_active``
``contact_poly``              ``cut_to_poly``
``gate_contact_comp``         ``gate_cut_to_active``
``metal_contact``             ``metal_over_cut``
``well_pactive``              ``well_over_active``
``well_nactive_space``        ``extra['well_nactive_space']`` (placer, not drawn)
``implant_enclose``           ``implant_over_active``
``implant_opp_active``        ``implant_to_opposite_active``
``oxide_active``              ``oxide_over_active``
``oxide_poly``                ``oxide_over_poly``
``n_length`` / ``p_length``   ``length_rule(kind)``
110 / 190 / 170 / 215         ``cut_half`` / ``gate_pad_half`` /
                              ``pad_half_x`` / ``pad_half_y``
============================  ======================================

What is *not* in the pack, and why
----------------------------------
The PMOS vertical flip (``db.Trans(0, True, 0, height)``) is a construction
convention - "the gate access faces the complementary row" - not a foundry
rule. It is a reflection about the row's own horizontal axis, which is a
symmetry of the device: it exchanges the left and right S/D pads, which no
netlist fact constrains, and leaves W/L untouched. It therefore stays here,
commented as a convention, rather than being smuggled into a pack as if the
process had said it.

This compact recipe omits routing straps and external shared taps.
"""
from dataclasses import dataclass
import klayout.db as db

from clay.pdk.semantic import default_pack
from clay.rotations import normalize_to_origin, turn_transform


@dataclass
class GeneratedDevice:
    device: object
    regions: dict
    active: object
    terminals: dict
    rotation: int = 0
    sd_contact_rows: int = 1

    def bbox(self):
        result = db.Region()
        for reg in self.regions.values():
            result += reg
        return result.bbox()


def build_contacted_device(device, *, contact_rows=False, pack=None):
    """Generate a contacted single-finger device from W/L, model and the pack.

    Named for what it builds rather than for a process: the same call produces a
    GF180MCU 5 V device or a SKY130A 1.8 V device depending only on ``pack``.
    """
    p = default_pack() if pack is None else pack
    r = p.nm
    if device.m != 1 or device.nf != 1:
        raise ValueError("M and nf are not interchangeable; only M=nf=1 supported")
    if device.l_nm < p.length_rule(device.kind):
        raise ValueError(f"minimum channel length violated for {device.kind} "
                         "(no size clamping)")
    if device.w_nm < max(r("width_min"), r("cut") + 2*r("cut_to_active")):
        raise ValueError("Device too narrow for contacted geometry (no size clamping)")
    if device.w_nm % r("grid") or device.l_nm % r("grid"):
        raise ValueError("Off-grid target")
    cut_half, gate_half = r("cut_half"), r("gate_pad_half")
    pad_hx, pad_hy = r("pad_half_x"), r("pad_half_y")
    side = max(r("sd_overhang"), r("cut_to_active") + r("cut") + r("cut_to_gate"))
    width, height = device.l_nm + 2*side, device.w_nm
    active = db.Box(0, 0, width, height)
    # Gate access faces the complementary row: NMOS up, PMOS down. A generic
    # physical recipe, not inferred source artwork. PMOS reflection changes no S/D.
    tail = r("gate_cut_to_active") + r("cut") + r("cut_to_poly")
    poly = db.Box(side, -r("poly_endcap"), side+device.l_nm, height+tail)
    reg = {"active": db.Region(active), "poly": db.Region(poly),
           "contact": db.Region(), "metal1": db.Region()}
    terminals = {}
    def access(role, cx, cy):
        box = db.Box(cx-cut_half, cy-cut_half, cx+cut_half, cy+cut_half)
        # Both >= M1.3 (metal_area) and CO.6a/b. Narrow S/D pads stay inside COMP
        # in x; square gate pads leave the complementary-row channel accessible.
        hx, hy = (gate_half, gate_half) if role == "gate" else (pad_hx, pad_hy)
        landing = db.Box(cx-hx, cy-hy, cx+hx, cy+hy)
        reg["contact"].insert(box)
        reg["metal1"].insert(landing)
        terminals[role] = landing
    grid = r("grid")
    rows = max(1, (height-2*r("cut_to_active")+r("cut_space"))
               // (r("cut")+r("cut_space"))) if contact_rows else 1
    pitch = r("cut")+r("cut_space")
    first = ((height-(rows-1)*pitch)//2//grid)*grid
    for role, cx in (("source", r("cut_to_active")+cut_half),
                     ("drain", width-r("cut_to_active")-cut_half)):
        boxes = []
        for row in range(rows):
            access(role, cx, first+row*pitch)
            boxes.append(terminals[role])
        # Internal device access landing joins contacts of ONE terminal only.
        landing = db.Box(boxes[0].left, boxes[0].bottom, boxes[-1].right, boxes[-1].top)
        reg["metal1"].insert(landing)
        terminals[role] = landing
    access("gate", ((side+device.l_nm//2)//grid)*grid,
           height+r("gate_cut_to_active")+cut_half)
    if device.kind == "pmos":
        transform = db.Trans(0, True, 0, height)
        reg = {key: value.transformed(transform) for key, value in reg.items()}
        terminals = {key: value.transformed(transform) for key, value in terminals.items()}
        reg["nwell"] = db.Region(active.enlarged(r("well_over_active")))
    implant = "pplus" if device.kind == "pmos" else "nplus"
    reg[implant] = db.Region(active.enlarged(r("implant_over_active")))
    # DV.6 and DV.8. A bounding rectangle avoids notches/nearby marker islands.
    # Both the spacing rules and the layers themselves are optional: SKY130 has
    # neither the dual-gate nor the 5 V selector layer, and states no
    # oxide-over-poly rule. Asking for them unconditionally would raise on a
    # perfectly good pack, so the artwork is built from whatever the pack
    # declares and skipped otherwise.
    oxide = db.Region()
    for key, region in (("oxide_over_active", reg["active"]),
                        ("oxide_over_poly", reg["poly"])):
        if p.has(key):
            oxide += region.sized(r(key))
    if not oxide.is_empty():
        # Marker layers are drawn only where the pack declares them, so a process
        # without a dual-gate or 5 V selector layer does not get a phantom shape.
        for role in ("dualgate", "v5_xtor"):
            if role in p.layers:
                reg[role] = db.Region(oxide.bbox())
    return GeneratedDevice(device, reg, active, terminals, sd_contact_rows=rows)


def rotate_device(generated, quarter_turns):
    """Rotate all polygons and terminals together; W/L and net roles survive.

    Normalize the active origin, not the outer boundary. No reference geometry
    is accepted here. Rotation is a physical implementation choice, not resizing.

    The permission check that used to live here (``not in (0, 1, 2, 3)``) is
    gone: an executor performs the transform it is handed, and legality is the
    freedom model's question. The transform itself is shared with the contact-row
    builder through :mod:`clay.rotations`, so the two cannot drift apart.
    """
    transform = normalize_to_origin(turn_transform(quarter_turns * 90),
                                    generated.active.bbox())
    return GeneratedDevice(generated.device,
                           {k: r.transformed(transform) for k, r in generated.regions.items()},
                           generated.active.transformed(transform),
                           {k: b.transformed(transform) for k, b in generated.terminals.items()},
                           (generated.rotation + quarter_turns) % 4,
                           generated.sd_contact_rows)


__all__ = ["GeneratedDevice", "build_contacted_device", "rotate_device"]
