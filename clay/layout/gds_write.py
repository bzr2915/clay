"""Reproducible GDS output.

A GDS file records the time it was written (BGNLIB/BGNSTR), so writing the
same layout twice gives two different files. "Same program, same layout" can
only be checked byte for byte if that timestamp is left out, which is all this
helper does. Measured: identical layouts written 1 s apart differ by SHA-256
with KLayout's default options and are identical with timestamps off.
"""
from __future__ import annotations

import klayout.db as db


def write_gds(layout: "db.Layout", path) -> None:
    options = db.SaveLayoutOptions()
    options.gds2_write_timestamps = False
    layout.write(str(path), options)


__all__ = ["write_gds"]
