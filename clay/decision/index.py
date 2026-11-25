"""Retrieval over the primitive classification (design doc §4.7, step S2).

Everything here is a query over declared data: which primitives produce or
consume a type, which serve a device class, which are options for each other,
and which types the declared primitives cannot yet produce. Nothing imports
primitive code; :func:`clay.learning.primitive_classification.check_signature`
is what ties the declarations to the code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from clay.learning import primitive_classification as pc


@dataclass(frozen=True)
class Index:
    table: Mapping[str, pc.Classification]
    by_output: Mapping[str, Tuple[str, ...]]
    by_input: Mapping[str, Tuple[str, ...]]
    by_device_class: Mapping[str, Tuple[str, ...]]
    alternatives: Mapping[str, Tuple[str, ...]]

    def producers(self, type_name: str, device_class: Optional[str] = None) -> Tuple[str, ...]:
        """Who can produce ``type_name`` (for ``device_class``, when given)."""
        names = self.by_output.get(type_name, ())
        if device_class is None:
            return names
        return tuple(n for n in names
                     if not self.table[n].applies_to or device_class in self.table[n].applies_to)


def _group(pairs: Iterable[Tuple[str, str]]) -> Dict[str, Tuple[str, ...]]:
    out: Dict[str, List[str]] = {}
    for key, name in pairs:
        out.setdefault(key, []).append(name)
    return {k: tuple(sorted(v)) for k, v in sorted(out.items())}


def build(table: Optional[Mapping[str, pc.Classification]] = None) -> Index:
    table = pc.load() if table is None else table
    items = list(table.values())
    return Index(
        table=table,
        by_output=_group((i.signature.output, i.name) for i in items),
        by_input=_group((t, i.name) for i in items
                        for t in set(i.signature.inputs.values())
                        | set(i.signature.optional_inputs.values())),
        by_device_class=_group((c, i.name) for i in items for c in i.applies_to),
        alternatives=_group((i.alternative_group, i.name) for i in items
                            if i.composition == "alternative"),
    )


def dispatch_conflicts(index: Index) -> List[Tuple[str, str, Tuple[str, ...]]]:
    """``(type, device class, producers)`` where more than one ``dispatch``
    primitive claims the same class. Dispatch must be unambiguous; two genuine
    options belong in one ``alternative_group`` instead."""
    out = []
    for type_name, names in index.by_output.items():
        dispatch = [n for n in names if index.table[n].composition == "dispatch"]
        for cls in pc.DEVICE_CLASSES:
            claim = tuple(n for n in dispatch if cls in index.table[n].applies_to)
            if len(claim) > 1:
                out.append((type_name, cls, claim))
    return out


def producible(index: Index, given: Iterable[str] = pc.EXTERNAL_TYPES) -> Set[str]:
    """Types obtainable from ``given`` by running declared primitives.

    A primitive fires once all its required input types are available
    (optional inputs never block it); a collection is available once its
    element is, and a part once its whole is (the core gathers and reads
    fields; that is not a primitive's job).
    """
    have = set(given)
    changed = True
    while changed:
        changed = False
        for t, element in pc.COLLECTIONS.items():
            if element in have and t not in have:
                have.add(t)
                changed = True
        for t, whole in pc.PARTS.items():
            if whole in have and t not in have:
                have.add(t)
                changed = True
        for item in index.table.values():
            out = item.signature.output
            if out not in have and set(item.signature.inputs.values()) <= have:
                have.add(out)
                changed = True
    return have


def gaps(index: Index,
         given: Iterable[str] = pc.EXTERNAL_TYPES + pc.TRAINING_TYPES + pc.CORE_TYPES
         ) -> Dict[str, Tuple[str, ...]]:
    """Input types no declared primitive can produce, with their consumers.

    ``given`` defaults to every legitimate outside input, training ones
    included, and to what the coordinator core makes itself: none of those is
    a gap. Each type left is a place where hand-written glue still sits
    between primitives (design doc §5).
    """
    have = producible(index, given)
    return {t: names for t, names in index.by_input.items() if t not in have}


@dataclass(frozen=True)
class Derivation:
    """The primitives a target needs, found backwards from the target type.

    ``steps``: primitives the derivation settled on. ``choices``: types with
    more than one feasible producer that are not resolved by device class; each
    is a decision the coordinator has to make (or an ambiguity to remove).
    """
    target: str
    steps: Tuple[str, ...]
    choices: Mapping[str, Tuple[str, ...]]


def derive(index: Index, target: str, given: Iterable[str] = pc.EXTERNAL_TYPES,
           device_classes: Iterable[str] = pc.DEVICE_CLASSES) -> Derivation:
    """Backward-chain from ``target`` to the ``given`` types.

    A producer is feasible when its required inputs are producible from
    ``given``. Not offered: ``engine`` primitives (only called inside another
    primitive) and producers whose inputs include their own output (transforms
    such as ``turn``: applying them is a decision of the action space, not a
    way to *reach* a type). When ``dispatch`` producers serve the requested
    ``device_classes``, they settle the type; other producers are used only
    when no dispatch producer applies. Optional inputs are followed when they
    can be produced (the flow uses what it can obtain).
    """
    given = set(given)
    classes = set(device_classes)
    have = producible(index, given)
    if target not in have:
        raise ValueError(f"{target!r} cannot be produced from {sorted(given)}")
    steps: Set[str] = set()
    choices: Dict[str, Tuple[str, ...]] = {}
    seen: Set[str] = set()

    def need(t: str) -> None:
        if t in seen or t in given:
            return
        seen.add(t)
        if t in pc.COLLECTIONS:
            need(pc.COLLECTIONS[t])
            return
        if t in pc.PARTS and pc.PARTS[t] in have:
            need(pc.PARTS[t])
            return
        feasible = [n for n in index.by_output.get(t, ())
                    if index.table[n].composition != "engine"
                    and set(index.table[n].signature.inputs.values()) <= have
                    and t not in index.table[n].signature.inputs.values()]
        dispatch = [n for n in feasible if index.table[n].composition == "dispatch"
                    and classes & set(index.table[n].applies_to)]
        # An alternative competes with the dispatch default for its classes
        # (S5): it is a decision of the coordinator, so it is a choice here.
        alternatives = [n for n in feasible if index.table[n].composition == "alternative"
                        and classes & set(index.table[n].applies_to)]
        other = [] if dispatch else [n for n in feasible
                                     if index.table[n].composition not in
                                     ("dispatch", "alternative")]
        chosen = dispatch + (other if len(other) == 1 else [])
        if len(other) > 1:
            choices[t] = tuple(sorted(other))
            chosen += other
        if alternatives:
            served = set().union(*(index.table[n].applies_to for n in alternatives))
            rivals = [n for n in dispatch if served & set(index.table[n].applies_to)]
            choices[t] = tuple(sorted(set(choices.get(t, ())) | set(rivals)
                                      | set(other) | set(alternatives)))
            chosen += alternatives
        for n in chosen:
            steps.add(n)
            sig = index.table[n].signature
            for u in sig.inputs.values():
                need(u)
            for u in sig.optional_inputs.values():
                if u in have:
                    need(u)

    need(target)
    return Derivation(target, tuple(sorted(steps)), dict(sorted(choices.items())))


__all__ = ["Index", "Derivation", "build", "dispatch_conflicts", "producible", "gaps",
           "derive"]
