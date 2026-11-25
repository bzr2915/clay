"""Primitives split out of ``circuit_generate.assemble`` (design doc §5, step S3).

``assemble`` used to hold circuit knowledge in one function: which constructor
serves which device class, how a diffusion chain becomes a contacted row (with
a varactor special case), which blocks are mirror pairs or centred, and the
call into the placement solver. Each of those is now a classified primitive:

* :func:`build_chain_row` - one diffusion chain -> contacted row. How a device
  class other than plain MOS is drawn (the SKY130 varactor: n+ tap in an
  n-well, varactor cut rules) is read from ``Pack.chain_construction``.
* :func:`placement_units` - chains and the remaining devices -> the units to
  place, in build order, with their mirror relations.
* :func:`placement_intents` - units -> mirror pairs, hard and soft centring.
* :func:`place_blocks` - block spaces + intents -> placement (calls
  ``solve_blocks`` with the pack's well rules).

Which constructor builds which unit is not named here or in ``assemble``: it is
the classification's ``dispatch`` entry for the unit's device class.
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from clay.layout.synth.area_placement import solve_blocks
from clay.learning.action_space import solver_blocks, ties
from clay.learning.contact_primitives import build_row, contact_plan_from_policy

_Dev = namedtuple("_Dev", "name model w_nm l_nm")


def build_chain_row(chain, policy, *, pack):
    """Construct one diffusion chain with the learned contact policy.

    A device class listed in ``pack.chain_construction`` is drawn on that
    diffusion role with those rule replacements (each applied only where the
    pack defines the replacement key); any other class is a plain MOS chain.
    """
    cls, kind, model, l_nm, w_nm, well = chain.group
    profile = pack.chain_construction.get(cls, {})
    over = {k: v for k, v in profile.get("rule_overrides", {}).items() if pack.has(v)} \
        if "rule_overrides" in profile else None
    plan = contact_plan_from_policy(policy, w_nm, pack=pack, rule_overrides=over)
    devs = [_Dev(f"{f.device}#{f.index}", model, w_nm, l_nm) for f in chain.fingers]
    row = build_row(devs, None, plan, None, None, None, pack=pack,
                    gate_nets=list(chain.gate_nets), segment_nets=list(chain.segment_nets),
                    kind=kind, diffusion_role=profile.get("diffusion_role", "active"),
                    well=well is not None, rule_overrides=over,
                    finger_names=[f.device for f in chain.fingers])
    row["well_domain"] = well
    return row


def build_learned_pattern_row(chain, library, *, pack):
    """Alternative MOS chain constructor: the inverter-trained construction.

    Step S5 (design doc §6.3): the inverter flow's learned construction - a
    diffusion pattern of ``rail``/``output`` segments per gate count, and a
    contact policy per polarity, both fitted on inverter layouts - offered to
    the coordinator as an *alternative* to ``build_chain_row`` for ``mos``
    chains. It applies to any chain whose channels share one gate net and
    whose segment nets bind consistently to the learned pattern's symbols;
    any other chain is refused (the candidate using it is then rejected).

    ``library`` is an inverter contact library (``clay.contact-primitive``
    v1/v2), the same one the inverter flow uses.
    """
    from clay.learning.contact_primitives import contact_plan
    cls, kind, model, l_nm, w_nm, well = chain.group
    if cls != "mos":
        raise ValueError(f"learned pattern construction is for MOS chains, not {cls!r}")
    gates = set(chain.gate_nets)
    if len(gates) != 1:
        raise ValueError(f"chain {chain.name}: learned pattern needs one gate net, "
                         f"has {sorted(gates)}")
    k = len(chain.fingers)
    patterns = [pt for pt in library["patterns"].get(kind, []) if pt["gate_count"] == k]
    if not patterns:
        raise ValueError(f"chain {chain.name}: no learned {kind} pattern for {k} gates")
    segments = list(patterns[0]["segments"])
    binding = {}
    for symbol, net in zip(segments, chain.segment_nets):
        if binding.setdefault(symbol, net) != net:
            raise ValueError(f"chain {chain.name}: segment nets do not follow the learned "
                             f"pattern {segments}")
    if set(binding) != {"rail", "output"} or binding["rail"] == binding["output"]:
        raise ValueError(f"chain {chain.name}: segment nets do not follow the learned "
                         f"pattern {segments}")
    plan = contact_plan(library, kind, w_nm, pack=pack)
    devs = [_Dev(f"{f.device}#{f.index}", model, w_nm, l_nm) for f in chain.fingers]
    row = build_row(devs, segments, plan, chain.gate_nets[0], binding["output"],
                    binding["rail"], None, pack=pack, gate_nets=list(chain.gate_nets),
                    segment_nets=list(chain.segment_nets), kind=kind,
                    well=well is not None, finger_names=[f.device for f in chain.fingers])
    row["well_domain"] = well
    return row


@dataclass(frozen=True)
class Unit:
    """One thing to place: a diffusion chain or a device built on its own.

    ``kind`` is the classification type of ``payload`` (``Chain`` or
    ``Device``), which is how a constructor is matched to it. A unit with
    ``mirror_of`` is not constructed: it is the reflection of that unit.
    ``order`` fixes the order in which placement intents are stated.
    """
    name: str
    cls: str
    kind: str
    payload: Any
    mirror_of: Optional[str]
    self_symmetric: bool
    paired: bool
    well: Optional[str]
    sources: Tuple[str, ...]
    order: float


def placement_units(chains, circuit, symmetry=None, *, chain_classes) -> Tuple[Unit, ...]:
    """Units in build order: chains, then their mirror chains, then every
    device whose class is not built as a chain, each followed by its mirror
    mate. ``symmetry`` is the netlist symmetry the construction enforces
    (``None`` when mirror pairs are off)."""
    units: List[Unit] = []
    for i, ch in enumerate(chains):
        if ch.mirror_of:
            continue
        devices = {f.device for f in ch.fingers}
        paired = symmetry is not None and any(symmetry.mate(d) != d for d in devices)
        units.append(Unit(ch.name, ch.group[0], "Chain", ch, None, ch.self_symmetric, paired,
                          ch.group[5], tuple(sorted(devices)), float(i)))
    for i, ch in enumerate(chains):
        if ch.mirror_of:
            units.append(Unit(ch.name, ch.group[0], "Chain", ch, ch.mirror_of, ch.self_symmetric,
                              True, ch.group[5],
                              tuple(sorted({f.device for f in ch.fingers})), float(i)))
    built = {u.name for u in units}
    k = len(chains)
    for d in circuit.devices:
        if d.cls in chain_classes:
            continue
        mate = symmetry.mate(d.name) if symmetry is not None else d.name
        if mate != d.name and mate in built:
            continue                       # built as the mirror half of its mate
        units.append(Unit(d.name, d.cls, "Device", d, None, False, mate != d.name, None,
                          (d.name,), float(k)))
        built.add(d.name)
        if mate != d.name:
            units.append(Unit(mate, d.cls, "Device", None, d.name, False, True, None,
                              (mate,), k + 0.5))
            built.add(mate)
        k += 1
    return tuple(units)


@dataclass(frozen=True)
class PlacementIntents:
    """What the placement must honour about the symmetry axis, plus data for
    any other registered constraint kind (``extra``: kind -> data; see
    :mod:`clay.layout.synth.placement_constraints`, step S3b)."""
    pairs: List[Tuple[str, str]]
    centred: List[str]
    soft: List[str]
    extra: Mapping[str, Any] = field(default_factory=dict)


def placement_intents(units: Sequence[Unit], symmetry=None, *, axis=None,
                      soft_centre=True) -> PlacementIntents:
    """Mirror pairs, hard-centred and soft-centred units about ``axis``.

    With no enforced symmetry or no axis there is nothing to honour. Otherwise,
    in ``order``: a mirror unit pairs with its source; a self-symmetric unit
    whose devices have mates is centred on the axis; a unit without mates is
    softly centred (``soft_centre``, the learned ``centre_fixed_devices``).
    """
    pairs: List[Tuple[str, str]] = []
    centred: List[str] = []
    soft: List[str] = []
    if symmetry is not None and axis is not None:
        for u in sorted(units, key=lambda u: u.order):
            if u.mirror_of:
                pairs.append((u.mirror_of, u.name))
            elif u.self_symmetric and u.paired:
                centred.append(u.name)
            elif not u.paired and soft_centre:
                soft.append(u.name)
    return PlacementIntents(pairs, centred, soft)


def place_blocks(spaces, intents, *, pack, axis=None, seconds, workers=8, hint=None,
                 variant_hint=None, canonical_seconds=None, defer_canonical=False,
                 pinned_area_nm2=None):
    """Orientation and position of every block, jointly, by ``solve_blocks``.

    ``defer_canonical`` / ``pinned_area_nm2``: two-stage placement for the
    construction search (design doc §6, plan A) - see ``solve_blocks``.
    """
    return solve_blocks(solver_blocks(spaces), grid=pack.nm("grid"),
                        well_to_opposite=pack.nm("well_to_opposite_active"),
                        well_space=pack.nm("well_space"), axis=axis,
                        pairs=intents.pairs, centred=intents.centred,
                        soft_centred=intents.soft, seconds=seconds, workers=workers,
                        hint=hint, ties=ties(spaces), variant_hint=variant_hint,
                        canonical_seconds=canonical_seconds,
                        constraints=dict(intents.extra) or None,
                        defer_canonical=defer_canonical, pinned_area_nm2=pinned_area_nm2)


def verification_feedback(record, *, orientation_search=True):
    """What to do when a solved construction candidate fails a layout check.

    A ``decide`` primitive with ``decision_role: feedback`` (design doc §6.5):
    it returns the decision as data and does nothing itself; the search loop
    carries it out. Today one rule: the placement model does not see
    marker-layer interactions (materialize's implant/npc closing can leave
    slivers once neighbours are turned), so an oriented optimum that fails a
    check is retried with the fixed orientation the flow used before. The
    retry yields a valid layout, not a proof: the candidate stays open unless
    its (oriented, still sound) bound settles it.
    """
    if record.get("kind") == "solved" and not record.get("verified") and orientation_search:
        return {"action": "retry_fixed_orientation",
                "reason": "oriented optimum failed a layout check: "
                          + ", ".join(record["result"]["verification"]["failed"])}
    return {"action": "none", "reason": ""}


__all__ = ["build_chain_row", "build_learned_pattern_row", "Unit", "placement_units",
           "PlacementIntents", "placement_intents", "place_blocks", "verification_feedback"]
