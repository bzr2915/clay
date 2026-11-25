"""Relation registry: what each learned relation does to a construction.

Design doc §5.3, step S3. A learned library states relations as
``{"<key>": {"decision": <value>, ...}}``. Before this module the flow read
them by key wherever it needed one (``relations["mirror_pairs"]`` in two
places, ``relations.get("uniform_gate_orientation")`` in a third), and one
relation (``centre_fixed_devices``) was learned but never read: unpaired
blocks were soft-centred unconditionally.

Here every relation the flow understands is registered once, with the
decisions it accepts and the effect each decision has. A library relation that
is not registered is an error rather than silently ignored: a newly learned
kind of relation has to say what it does before it can be used. Adding one
means adding an entry here (and, if it needs a new kind of placement
constraint, a constraint kind; design doc §5.3) - not editing the flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Tuple


@dataclass(frozen=True)
class RelationEffects:
    """What the learned relations ask of one construction."""

    #: Netlist-symmetric devices are built as mirror images (mirror_space) and
    #: their blocks are constrained as mirror pairs about one axis.
    enforce_mirror_pairs: bool
    #: Gate-bearing blocks keep their gates as built (0/180 degrees, +/- mirror).
    keep_gates: bool
    #: Which symmetry axes the construction level tries, relative to the gates.
    axis_rule: str
    #: Blocks without a mirror mate are softly centred on the axis.
    soft_centre_unpaired: bool

    def axes(self) -> Tuple[str, ...]:
        """Axis candidates (absolute directions) implied by ``axis_rule``."""
        if self.axis_rule == "free":
            return ("vertical", "horizontal")
        return ("vertical",) if self.axis_rule == "parallel" else ("horizontal",)


@dataclass(frozen=True)
class Relation:
    decisions: Tuple[str, ...]
    #: decision -> (field of RelationEffects, value)
    effect: Callable[[str], Tuple[str, object]]
    #: Used when the library does not state the relation at all. ``None``
    #: means the relation is required.
    default: object = None
    summary: str = ""


RELATIONS: Dict[str, Relation] = {
    "mirror_pairs": Relation(
        decisions=("enforce", "off"),
        effect=lambda d: ("enforce_mirror_pairs", d == "enforce"),
        summary="mirror-pair blocks built by reflection and constrained about one axis"),
    "mirror_axis": Relation(
        decisions=("parallel", "perpendicular", "free"),
        effect=lambda d: ("axis_rule", d),
        summary="symmetry-axis candidates relative to the gate lines"),
    "centre_fixed_devices": Relation(
        decisions=("soft",),
        effect=lambda d: ("soft_centre_unpaired", True),
        # Libraries older than this registry may omit it; the flow then did
        # soft-centre, so the default keeps that behaviour.
        default=True,
        summary="blocks without a mirror mate are softly centred on the axis"),
    "uniform_gate_orientation": Relation(
        decisions=("enforce", "off"),
        effect=lambda d: ("keep_gates", d == "enforce"),
        default=False,
        summary="gate-bearing blocks keep their gate direction"),
}

_FIELDS = {"enforce_mirror_pairs": "mirror_pairs", "axis_rule": "mirror_axis",
           "soft_centre_unpaired": "centre_fixed_devices", "keep_gates": "uniform_gate_orientation"}


def relation_effects(library: Mapping[str, object]) -> RelationEffects:
    """Translate a library's relations into their effects, by the registry."""
    stated = library["relations"]
    unknown = sorted(set(stated) - set(RELATIONS))
    if unknown:
        raise ValueError(f"library relations {unknown} have no entry in the relation "
                         "registry (clay/learning/relations.py); register what they do first")
    values = {}
    for field_name, key in _FIELDS.items():
        relation = RELATIONS[key]
        if key not in stated:
            if relation.default is None:
                raise ValueError(f"library does not state required relation {key!r}")
            values[field_name] = relation.default
            continue
        decision = stated[key]["decision"]
        if decision not in relation.decisions:
            raise ValueError(f"relation {key!r}: decision {decision!r} not in "
                             f"{relation.decisions}")
        name, value = relation.effect(decision)
        values[name] = value
    return RelationEffects(**values)


__all__ = ["RELATIONS", "Relation", "RelationEffects", "relation_effects"]
