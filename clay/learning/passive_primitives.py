"""Constructors for passive devices and the placement *block* abstraction.

Passive constructors
--------------------
``build_poly_resistor``  a straight poly resistor with contacted heads. The
    head geometry (slot-cut size, poly and metal margins around it, the
    implant/marker margin) is a *learned style* read from the circuit library;
    every value is clipped up to the pack's rule, never below it.
``build_mim_cap``        a metal-insulator-metal capacitor: top-plate cut
    (``capm``) of the netlist W x L, bottom plate on ``metal3``, a ``cut3``
    array onto a ``metal4`` top terminal. All margins are pack rules.

A varactor is not here: it *is* a MOS construction (poly over n+ tap in an
n-well) and is built by ``contact_primitives.build_row``.

Blocks
------
A block is one rigid construction the placer moves as a unit (paper section
3.1: "Moving a constructed device group as a block retains its internal
matching geometry"). Besides its regions it carries the boxes the placer
reasons about, all in block-local coordinates:

``core``     every drawn shape on the device plane (active/tap, poly, cuts,
             metal1, npc, select markers)
``diff``     the diffusion box (for well-to-opposite-active spacing)
``well``     the box its n-well must cover, if it sits in one
``outer``    everything the block draws (for the cell bounding box)
``plane``    ``device`` or ``mim`` -- blocks on different planes share no
             layer and no spacing rule, so they may overlap
``halo``     half of the largest same-plane spacing this block needs
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

import klayout.db as db

SCHEMA = "clay.blocks.v1"

DEVICE_ROLES_CORE = ("active", "tap", "poly", "contact", "metal1", "npc", "hvtp", "lvtn",
                     "polyres", "rpm")


def _grid_up(v, g):
    return -(-int(v) // int(g)) * int(g)


def build_poly_resistor(device, style, *, pack):
    """Straight poly resistor between two contacted heads.

    ``style`` (learned, see ``circuit_library``) supplies
    ``head_cut_nm`` [along, across], ``poly_over_head_cut_nm`` and
    ``marker_over_poly_nm``. The body is ``device.l_nm`` long and
    ``device.w_nm`` wide; heads are as wide as the slot cut plus margin.
    """
    p = pack
    grid = p.nm("grid")
    if p.has("res_width_min") and device.w_nm < p.nm("res_width_min"):
        raise ValueError(f"{device.name}: resistor width below the pack minimum")
    for role in ("poly", "contact", "metal1", "polyres", "rpm", "pplus", "npc"):
        if role not in p.layers:
            raise ValueError(f"pack {p.pdk_id!r} lacks layer role {role!r} for a poly resistor")
    along, across = style["head_cut_nm"]
    margin = max(style["poly_over_head_cut_nm"], p.nm("cut_to_poly"))
    head_len = _grid_up(along + 2 * margin, grid)
    head_w = _grid_up(max(across + 2 * margin, device.w_nm), grid)
    body_l = device.l_nm
    total = 2 * head_len + body_l
    y0 = (head_w - device.w_nm) // 2 // grid * grid
    regs = {k: db.Region() for k in p.layers}
    heads = [db.Box(0, 0, head_len, head_w), db.Box(head_len + body_l, 0, total, head_w)]
    body = db.Box(head_len, y0, head_len + body_l, y0 + device.w_nm)
    for h in heads:
        regs["poly"].insert(h)
    regs["poly"].insert(body)
    regs["polyres"].insert(body)
    terminals = []
    metal_margin = max(style.get("metal_over_head_cut_nm", 0), p.nm("metal_over_cut"))
    for h, term in zip(heads, ("a", "b")):
        c = h.center()
        cut = db.Box(c.x - along // 2, c.y - across // 2, c.x + along // 2, c.y + across // 2)
        regs["contact"].insert(cut)
        pad = cut.enlarged(metal_margin)
        regs["metal1"].insert(pad)
        terminals.append((device.net(term), pad))
    poly_box = db.Box(0, 0, total, head_w)
    over = max(style["marker_over_poly_nm"], p.nm("rpm_over_res"),
               p.nm("res_implant_over_res"), p.nm("npc_over_res"))
    marker = poly_box.enlarged(over)
    if marker.width() < p.nm("rpm_width") or marker.height() < p.nm("rpm_width"):
        grow_x = max(0, -(-(p.nm("rpm_width") - marker.width()) // 2))
        grow_y = max(0, -(-(p.nm("rpm_width") - marker.height()) // 2))
        marker = marker.enlarged(_grid_up(grow_x, grid), _grid_up(grow_y, grid))
    for role in ("rpm", "pplus", "npc"):
        regs[role].insert(marker)
    return {"regions": regs, "active": poly_box, "terminals": terminals, "kind": None,
            "channels": [], "resistor": {"device": device.name, "body": body,
                                          "nets": (device.net("a"), device.net("b"))}}


def build_mim_cap(device, *, pack):
    """MIM capacitor: ``capm`` W x L over a ``metal3`` plate, ``cut3`` onto ``metal4``."""
    p = pack
    for role in ("metal3", "cut3", "metal4", "capm"):
        if role not in p.layers:
            raise ValueError(f"pack {p.pdk_id!r} lacks layer role {role!r} for a MIM capacitor")
    w, l = device.w_nm, device.l_nm
    if min(w, l) < p.nm("cap_width_min"):
        raise ValueError(f"{device.name}: MIM side below capm minimum")
    regs = {k: db.Region() for k in p.layers}
    cap = db.Box(0, 0, w, l)
    regs["capm"].insert(cap)
    plate = cap.enlarged(p.nm("plate_over_cap"))
    regs["metal3"].insert(plate)
    via, space, margin = p.nm("via3"), p.nm("via3_space"), p.nm("cap_over_via")
    pitch = via + space
    nx = (w - 2 * margin - via) // pitch + 1
    ny = (l - 2 * margin - via) // pitch + 1
    if nx < 1 or ny < 1:
        raise ValueError(f"{device.name}: no room for a top-plate via")
    ox = (w - ((nx - 1) * pitch + via)) // 2
    oy = (l - ((ny - 1) * pitch + via)) // 2
    grid = p.nm("grid")
    ox, oy = ox // grid * grid, oy // grid * grid
    for i in range(nx):
        for j in range(ny):
            x, y = ox + i * pitch, oy + j * pitch
            regs["cut3"].insert(db.Box(x, y, x + via, y + via))
    top = regs["cut3"].bbox().enlarged(p.nm("metal4_over_via3"))
    regs["metal4"].insert(top)
    terminals = [(device.net("top"), top, "metal4"),
                 (device.net("bot"), db.Box(plate.left, plate.bottom, plate.right,
                                            plate.bottom + p.nm("plate_over_cap")), "metal3")]
    return {"regions": regs, "active": plate, "terminals": terminals, "kind": None,
            "channels": [], "capacitor": {"device": device.name, "cap": cap,
                                           "nets": (device.net("top"), device.net("bot"))}}


@dataclass
class Block:
    name: str
    cls: str                       # mos | cap_var | res_poly | cap_mim
    row: dict
    kind: Optional[str]            # diffusion polarity, or None
    well: Optional[str]            # n-well domain net, or None
    plane: str                     # device | mim
    core: Tuple[int, int, int, int]
    diff: Optional[Tuple[int, int, int, int]]
    well_box: Optional[Tuple[int, int, int, int]]
    outer: Tuple[int, int, int, int]
    halo: int
    mirror_of: Optional[str] = None
    self_symmetric: bool = False
    sources: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self):
        return {"name": self.name, "cls": self.cls, "kind": self.kind, "well": self.well,
                "plane": self.plane, "core": list(self.core),
                "diff": list(self.diff) if self.diff else None,
                "well_box": list(self.well_box) if self.well_box else None,
                "outer": list(self.outer), "halo": self.halo, "mirror_of": self.mirror_of,
                "self_symmetric": self.self_symmetric, "sources": list(self.sources)}


def _bbox(region):
    b = region.bbox()
    return (b.left, b.bottom, b.right, b.top)


def block_from_row(name, cls, row, *, pack, well=None, mirror_of=None, self_symmetric=False,
                   sources=()):
    """Wrap a built construction as a placement block (see module docstring)."""
    p = pack
    regs = row["regions"]
    plane = "mim" if cls == "cap_mim" else "device"
    if plane == "mim":
        core = db.Region()
        for role in ("metal3", "capm", "cut3", "metal4"):
            core += regs[role]
        halo = (max(p.nm("plate_space"), p.nm("cap_space")) + 1) // 2
    else:
        core = db.Region()
        for role in DEVICE_ROLES_CORE:
            if role in regs:
                core += regs[role]
        base = max(p.nm("active_space"), p.nm("poly_space"), p.nm("metal_space"),
                   p.nm("field_poly_active") if p.has("field_poly_active") else 0)
        if cls == "res_poly":
            # A poly resistor keeps poly.9 from every poly/diff and its rpm
            # keeps rpm.2 from another rpm; its core box is the rpm box.
            base = max(base, p.nm("rpm_space"), p.nm("res_space") - p.nm("rpm_over_res"))
        halo = (base + 1) // 2
    allr = db.Region()
    for reg in regs.values():
        allr += reg
    diff = None
    for role in ("active", "tap"):
        if role in regs and not regs[role].is_empty() and cls in ("mos", "cap_var"):
            diff = _bbox(regs[role])
    well_box = _bbox(regs["nwell"]) if well is not None and "nwell" in regs and \
        not regs["nwell"].is_empty() else None
    return Block(name, cls, row, row.get("kind"), well, plane, _bbox(core), diff, well_box,
                 _bbox(allr), halo, mirror_of, self_symmetric, tuple(sources))
