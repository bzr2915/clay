"""Call a classified primitive by type (design doc §6, step S3).

The flow no longer names constructors. It asks the index which primitive
produces a type for a device class (:func:`dispatch`) and calls it with the
values it holds, keyed by type (:func:`call`): each declared input is bound to
the value of its type, optional inputs when such a value is held, parameters
by name. This is the mechanism the coordinator core will use for every step
(S4); today ``assemble`` uses it for the per-device-class constructors.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from clay.decision import index as ix
from clay.learning import primitive_classification as pc


def dispatch(index: ix.Index, output: str, device_class: str) -> pc.Classification:
    """The one ``dispatch`` primitive producing ``output`` for ``device_class``."""
    found = [index.table[n] for n in index.by_output.get(output, ())
             if index.table[n].composition == "dispatch"
             and device_class in index.table[n].applies_to]
    if len(found) != 1:
        raise LookupError(f"{len(found)} dispatch primitives produce {output!r} for device "
                          f"class {device_class!r}: {[i.name for i in found]}; exactly one "
                          "must be classified")
    return found[0]


def call(item: pc.Classification, values: Mapping[str, Any],
         params: Optional[Mapping[str, Any]] = None) -> Any:
    """Call ``item`` with ``values`` (type -> value) and ``params`` (name -> value)."""
    sig = item.signature
    kwargs = {}
    for name, type_name in sig.inputs.items():
        if type_name not in values:
            raise ValueError(f"{item.name} needs a {type_name} ({name!r}), which is not "
                             f"available here (held: {sorted(values)})")
        kwargs[name] = values[type_name]
    for name, type_name in sig.optional_inputs.items():
        if type_name in values:
            kwargs[name] = values[type_name]
    for name, value in (params or {}).items():
        if name not in sig.params:
            raise ValueError(f"{item.name} takes no parameter {name!r}; "
                             f"declared: {sig.params}")
        kwargs[name] = value
    return pc.resolve(item)(**kwargs)


__all__ = ["dispatch", "call"]
