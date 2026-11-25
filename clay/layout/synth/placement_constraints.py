"""Placement constraint kinds of ``solve_blocks`` (design doc §5.3, step S3b).

Before S3b the solver built every constraint kind inline, so a new kind of
placement requirement (common centroid, a matching-distance limit, a keep-out)
meant editing the solver - which carries a frozen verification spec and the
determinism proof. Each kind is now a registered plug-in:

* ``name`` - the key a caller uses to pass its data;
* ``phase`` - where in the model it is added: ``variants`` (right after the
  orientation selectors exist) or ``placement`` (after the envelope and the
  symmetry axis exist);
* ``objective_level`` - 0: hard constraint; 2: soft cost, minimised after area
  by the solver's second stage (design doc §2.1);
* ``add(ctx, data)`` - adds its variables and constraints to the model.

Order is part of the contract: CP-SAT's search depends on the order in which a
model is built, so the built-in kinds run at the points and in the order the
inline code used (``BUILTIN_ORDER``); other kinds run after them, sorted by
name, never in registration order. A kind given no data adds nothing, so
registering a kind cannot change any existing model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping

PHASES = ("variants", "placement")
BUILTIN_ORDER = ("ties", "mirror_pairs", "centred", "soft_centred")


@dataclass(frozen=True)
class ConstraintKind:
    name: str
    phase: str
    objective_level: int
    add: Callable[[Any, Any], None]
    summary: str

    def __post_init__(self):
        if self.phase not in PHASES:
            raise ValueError(f"{self.name}: phase must be one of {PHASES}")
        if self.objective_level not in (0, 2):
            raise ValueError(f"{self.name}: objective_level is 0 (hard) or 2 (soft)")


REGISTRY: Dict[str, ConstraintKind] = {}


def register(kind: ConstraintKind) -> ConstraintKind:
    if kind.name in REGISTRY:
        raise ValueError(f"constraint kind {kind.name!r} is already registered")
    REGISTRY[kind.name] = kind
    return kind


def run_phase(phase: str, ctx, data: Mapping[str, Any]) -> None:
    """Add every kind of ``phase`` that has data: built-ins in their fixed
    order, then any other kind sorted by name."""
    unknown = sorted(set(data) - set(REGISTRY))
    if unknown:
        raise ValueError(f"unknown placement constraint kinds {unknown}; "
                         f"registered: {sorted(REGISTRY)}")
    order = [n for n in BUILTIN_ORDER if n in REGISTRY] + \
        sorted(n for n in REGISTRY if n not in BUILTIN_ORDER)
    for name in order:
        kind = REGISTRY[name]
        if kind.phase == phase and data.get(name):
            kind.add(ctx, data[name])


# ------------------------------------------------------------ built-in kinds
# The bodies below are the solver's pre-S3b inline code, moved unchanged.


def _ties(ctx, ties):
    m, sel, variants = ctx.m, ctx.sel, ctx.variants
    for a, b in ties:
        if a in sel or b in sel:
            if len(variants[a]) != len(variants[b]):
                raise ValueError(f"tied blocks {a!r}/{b!r} offer different variant counts")
            for sa, sb in zip(sel[a], sel[b]):
                m.Add(sa == sb)


def _mirror_pairs(ctx, pairs):
    if ctx.axis_var is None:
        return
    m, xs, ys, off, other = ctx.m, ctx.xs, ctx.ys, ctx.off, 1 - ctx.along
    for a, b in pairs:
        m.Add(ctx.centre2(a) + ctx.centre2(b) == 2 * ctx.axis_var)
        pa = ys[a] if other == 1 else xs[a]
        pb = ys[b] if other == 1 else xs[b]
        m.Add(pa + off(a, "outer", other) == pb + off(b, "outer", other))


def _centred(ctx, centred):
    if ctx.axis_var is None:
        return
    for n in centred:
        ctx.m.Add(ctx.centre2(n) == ctx.axis_var)


def _soft_centred(ctx, soft_centred):
    if ctx.axis_var is None:
        return
    m = ctx.m
    for n in soft_centred:
        dev = m.NewIntVar(0, 4 * ctx.big, "")
        m.AddAbsEquality(dev, ctx.centre2(n) - ctx.axis_var)
        ctx.soft_terms.append(dev)


def _max_separation(ctx, limits):
    """``(a, b, distance_nm)``: the outer-box centres of ``a`` and ``b`` are at
    most ``distance_nm`` apart in Manhattan distance. A matching-distance
    limit for devices that must see the same gradients; it needs no axis."""
    m, xs, ys, off = ctx.m, ctx.xs, ctx.ys, ctx.off
    for i, (a, b, dist) in enumerate(limits):
        cx = [2 * xs[n] + off(n, "outer", 0) + off(n, "outer", 2) for n in (a, b)]
        cy = [2 * ys[n] + off(n, "outer", 1) + off(n, "outer", 3) for n in (a, b)]
        dx = m.NewIntVar(0, 4 * ctx.big, f"msx_{i}")
        dy = m.NewIntVar(0, 4 * ctx.big, f"msy_{i}")
        m.AddAbsEquality(dx, cx[0] - cx[1])
        m.AddAbsEquality(dy, cy[0] - cy[1])
        # Centres are doubled coordinates; the limit is floored to the grid.
        m.Add(dx + dy <= 2 * (int(dist) // ctx.grid))


register(ConstraintKind("ties", "variants", 0, _ties,
                        "mirror pair: the two blocks choose the same variant index"))
register(ConstraintKind("mirror_pairs", "placement", 0, _mirror_pairs,
                        "mirror pair: centres symmetric about the axis, aligned across it"))
register(ConstraintKind("centred", "placement", 0, _centred,
                        "block centre on the symmetry axis"))
register(ConstraintKind("soft_centred", "placement", 2, _soft_centred,
                        "distance of the block centre to the axis, minimised after area"))
register(ConstraintKind("max_separation", "placement", 0, _max_separation,
                        "two blocks' centres within a Manhattan distance (matching)"))


__all__ = ["ConstraintKind", "REGISTRY", "BUILTIN_ORDER", "PHASES", "register", "run_phase"]
