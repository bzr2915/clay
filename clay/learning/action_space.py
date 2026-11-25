"""Circuit-general action space: blocks x legal orientations x learned couplings.

Why this module exists
----------------------
The inverter decider (:mod:`clay.learning.actions`) enumerates plans in an
outer loop: 4 orientations per row, 16 plans for two rows. That cannot serve a
comparator: 7 to 18 blocks would mean up to 4**18 plans. Here orientation
becomes a *variable of the placement model* instead. For each block this
module builds one variant per legal orientation (every variant passes the
freedom gate, :func:`clay.learning.freedom.enforce`, before any geometry is
transformed), and translates the learned layout relations into constraints on
those variants. :func:`clay.layout.synth.area_placement.solve_blocks` then
chooses orientations and positions together, so an OPTIMAL solve is optimal
over every combination at once.

Where the orientation set comes from (nothing here is hard-coded)
-----------------------------------------------------------------
* the PDK: :func:`clay.learning.freedom.part_freedom` on the layers the block
  actually draws. Every candidate angle of the process (all eight on a deck
  with a 45-degree step) gets a verdict; quarter turns and reflections are
  executable through the pack's orthogonal-isometry declaration, the others are
  closed by a deck rule or proof, or reported open;
* the learned library: ``uniform_gate_orientation`` and ``mirror_pairs``;
* the netlist symmetry: centred self-symmetric blocks keep their symmetry.

Symmetry breaking (stated, because it removes variants)
-------------------------------------------------------
When the library enforces one gate orientation, every gate-bearing block is
restricted to the orientations that keep its gates as built (0/180 degrees,
with or without a reflection). This loses nothing: rotating an entire layout
by 90 degrees preserves legality (isometry declaration), area, mirror pairing
and the axis' relation to the gates, and the axis candidates are expressed
relative to the gates. Any solution with all gates turned therefore has an
equal-area twin with the gates as built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from clay.learning.contact_primitives import reflect, turn
from clay.learning.freedom import (
    MIRROR, ROTATE, Action, Part, PartFreedom, build_freedom, enforce, orientations)
from clay.learning.passive_primitives import block_from_row

#: Device classes whose rows carry gates (poly over diffusion or tap).
GATED = ("mos", "cap_var")


@dataclass(frozen=True)
class Variant:
    degrees: int
    mirrored: bool
    row: dict
    block: object        # passive_primitives.Block

    @property
    def name(self) -> str:
        return f"r{self.degrees}" + ("m" if self.mirrored else "")

    def boxes(self) -> Dict[str, object]:
        b = self.block
        return {"core": b.core, "outer": b.outer, "diff": b.diff, "well_box": b.well_box}


@dataclass
class BlockSpace:
    name: str
    cls: str
    freedom: PartFreedom
    variants: List[Variant]
    constraints: List[str] = field(default_factory=list)
    mirror_of: Optional[str] = None

    def report(self) -> Dict[str, object]:
        return {
            "cls": self.cls,
            "layers": list(self.freedom.part.layer_roles),
            "variants": [v.name for v in self.variants],
            "constraints": list(self.constraints),
            "mirror_of": self.mirror_of,
            "angle_verdicts": [v.as_dict() for v in self.freedom.verdicts],
            "open_angles": [v.degrees for v in self.freedom.verdicts if not v.closed],
        }


def transform_row(row: dict, degrees: int, mirrored: bool) -> dict:
    """Reflect about the vertical axis first (if asked), then quarter-turn.

    Only quarter turns can be applied to a *built* row: a rigid turn by any
    other angle leaves the nanometre lattice (the rotation group of a square
    lattice is the quarter turns plus mirrors), so ``degrees // 90`` would
    silently round 45 to 0 and hand back the original geometry. Refuse instead
    of rounding - geometry that belongs at 45 degrees has to be drawn there by
    a constructor (see :func:`clay.learning.freedom.orientations`).
    """
    if degrees % 90:
        raise ValueError(
            f"a built row cannot be turned to {degrees} degrees: rigid turns keep the "
            "nanometre lattice only for multiples of 90. Construct the geometry at that "
            "angle instead of rotating it.")
    out = reflect(row, "y") if mirrored else row
    if degrees:
        out = turn(out, degrees // 90)
    if "well_domain" in row:
        out = {**out, "well_domain": row["well_domain"]}
    return out


def drawn_roles(row: dict, pack) -> Tuple[str, ...]:
    """The layer roles a built row actually draws (what its freedom depends on)."""
    return tuple(sorted(k for k, reg in row["regions"].items()
                        if k in pack.layers and not reg.is_empty()))


def _box_key(block) -> tuple:
    return (block.core, block.outer, block.diff, block.well_box)


def block_space(name: str, cls: str, row: dict, *, pack, well=None, self_symmetric=False,
                sources=(), keep_gates=False, keep_axis_symmetry=False,
                orient=True) -> BlockSpace:
    """Every legal orientation of one constructed block, deduplicated.

    ``keep_gates``: restrict to orientations that keep gate lines as built
    (learned ``uniform_gate_orientation``; see the module docstring for why
    this is without loss). ``keep_axis_symmetry``: a centred self-symmetric
    chain keeps its own mirror symmetry about the axis only at 0/180 degrees.
    ``orient=False`` offers the identity alone (the previous behaviour).
    """
    part = Part(name, drawn_roles(row, pack))
    pf = build_freedom(pack, [part]).part(name)
    constraints = []
    allowed = []
    for degrees, mirrored in orientations(pf) if orient else ((0, False),):
        if (keep_gates or keep_axis_symmetry) and degrees % 180:
            continue
        allowed.append((degrees, mirrored))
    if keep_gates:
        constraints.append("uniform_gate_orientation: gates kept as built (0/180, +/- mirror)")
    if keep_axis_symmetry:
        constraints.append("centred self-symmetric chain: 0/180 keep its symmetry about the axis")
    if not orient:
        constraints.append("orientation search disabled: identity only")
    freedom = build_freedom(pack, [part])
    variants: List[Variant] = []
    seen = set()
    for degrees, mirrored in allowed:
        # The gate runs before any geometry is transformed.
        enforce(freedom, {name: Action(ROTATE, degrees=degrees)})
        if mirrored:
            enforce(freedom, {name: Action(MIRROR, axis="y")})
        r = transform_row(row, degrees, mirrored)
        blk = block_from_row(name, cls, r, pack=pack, well=well,
                             self_symmetric=self_symmetric, sources=sources)
        key = _box_key(blk)
        if key in seen:
            continue          # same boxes as an earlier variant: same placement problem
        seen.add(key)
        variants.append(Variant(degrees, mirrored, r, blk))
    if not variants:
        raise ValueError(f"block {name!r}: no executable orientation "
                         f"({pf.unknown_reason or pf.narrowed_reason})")
    return BlockSpace(name, cls, pf, variants, constraints)


def mirror_space(name: str, source: BlockSpace, *, pack, axis: str, net_map, well=None,
                 sources=()) -> BlockSpace:
    """The mirror half of a matched pair: variant ``k`` is the reflection of the
    source's variant ``k``, so tying the two indices keeps the pair a mirror
    image under every orientation the source may take."""
    variants = []
    part = Part(name, source.freedom.part.layer_roles)
    freedom = build_freedom(pack, [part])
    enforce(freedom, {name: Action(MIRROR, axis=axis)})
    for v in source.variants:
        r = reflect(v.row, axis, net_map)
        if "well_domain" in v.row:
            r["well_domain"] = well
        blk = block_from_row(name, source.cls, r, pack=pack, well=well,
                             mirror_of=source.name, sources=sources)
        variants.append(Variant(v.degrees, not v.mirrored, r, blk))
    return BlockSpace(name, source.cls, freedom.part(name), variants,
                      [f"mirror of {source.name} about the {axis} axis, variant-tied"],
                      mirror_of=source.name)


def solver_blocks(spaces: Dict[str, BlockSpace]) -> Dict[str, dict]:
    """The ``solve_blocks`` input: one entry per block, variants listed."""
    out = {}
    for n, s in spaces.items():
        first = s.variants[0].block
        entry = {"halo": first.halo, "plane": first.plane, "well": first.well,
                 **{k: v for k, v in s.variants[0].boxes().items()}}
        if len(s.variants) > 1:
            entry["variants"] = [v.boxes() for v in s.variants]
        out[n] = entry
    return out


def ties(spaces: Dict[str, BlockSpace]) -> List[Tuple[str, str]]:
    return [(s.mirror_of, n) for n, s in spaces.items() if s.mirror_of]


def chosen(spaces: Dict[str, BlockSpace], index: Dict[str, int]) -> Dict[str, Variant]:
    return {n: s.variants[index.get(n, 0)] for n, s in spaces.items()}


def count_combinations(spaces: Dict[str, BlockSpace]) -> int:
    """Orientation combinations the joint model covers (ties counted once)."""
    total = 1
    for n, s in spaces.items():
        if not s.mirror_of:
            total *= len(s.variants)
    return total


__all__ = ["Variant", "BlockSpace", "block_space", "mirror_space", "solver_blocks", "ties",
           "chosen", "transform_row", "drawn_roles", "count_combinations", "GATED"]
