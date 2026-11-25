"""The single rotation executor shared by every construction.

Why one module
--------------
Rotation used to exist twice: ``contact_primitives.turn`` rotated an already
built contact row, and ``device_placement.rotate_device`` rotated a
``GeneratedDevice``. The two implementations agreed by luck, not by
construction, and the second one validated its input with the literal
``if quarter_turns not in (0, 1, 2, 3)`` - a permission check written as a
constant in an executor. A permission belongs to the freedom model
(:mod:`clay.learning.freedom`); an executor should only know how to perform the
transform it is handed.

The two callers remain, because they transform different data shapes (a row
dict of regions versus a device dataclass), but both now go through
:func:`turn_transform` and :func:`normalize_to_origin`. Adding an angle means
changing this file, so the two cannot drift apart again.

Scope
-----
Only rotations that map the integer nanometre grid onto itself are executable
here: multiples of 90 degrees. A 45 degree placement needs a complex transform
and rotated polygons whose vertices no longer sit on the grid; that is a
*different* executor, and the freedom model already reports those angles as
permitted-but-not-executable rather than silently rounding them.
"""
from __future__ import annotations

import klayout.db as db


def turn_transform(degrees: int) -> db.Trans:
    """The rotation itself, about the origin, for any multiple of 90 degrees.

    Negative and full-turn angles are accepted and normalised, so a caller that
    accumulates rotations does not have to reduce them first.
    """
    if degrees % 90:
        raise ValueError(
            f"{degrees} degrees is not executable: this engine only performs "
            "rotations that preserve the nanometre grid (multiples of 90)")
    return db.Trans((degrees // 90) % 4, False, 0, 0)


def normalize_to_origin(transform: db.Trans, anchor) -> db.Trans:
    """Extend ``transform`` with a translation that puts ``anchor`` at (0, 0).

    Normalising on the *active* box rather than on the whole envelope is what
    keeps a rotated construction placeable: the solver separates active
    rectangles, so the active origin is the only origin whose translation it can
    reason about. Passing the envelope here would make the same rotation produce
    a different origin for each row extension.
    """
    moved = anchor.transformed(transform)
    return db.Trans(-moved.left, -moved.bottom) * transform


__all__ = ["turn_transform", "normalize_to_origin"]
