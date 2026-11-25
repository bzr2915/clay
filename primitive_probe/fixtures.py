"""Input fixtures. The ONLY place a probe creates shapes, and only as INPUTS.

Shapes made here are handed to production code (e.g. two rectangles given to a
placement relation) or shown for reference on probe layer 200 (display fixtures).
They are never presented as primitive output.
"""
import klayout.db as db


def box(b):
    x0, y0, x1, y1 = (int(v) for v in b)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f'Fixture box must be [x0,y0,x1,y1] with x1>x0, y1>y0: {b}')
    return db.Box(x0, y0, x1, y1)


def region(*boxes):
    r = db.Region()
    for b in boxes:
        r.insert(box(b))
    return r


def row(layers, terminals=(), kind=None):
    """A row-shaped input in the format the project's turn()/materialize() consume.

    layers: {'active': [[x0,y0,x1,y1], ...], 'poly': [...], ...}
    terminals: [[net, [x0,y0,x1,y1]], ...]
    """
    regions = {name: region(*bs) for name, bs in layers.items()}
    active = regions['active'].bbox() if 'active' in regions and not regions['active'].is_empty() else None
    return {'regions': regions, 'active': active,
            'terminals': [(net, box(b)) for net, b in terminals], 'kind': kind}


def boxes_to_region(boxes):
    """Collect production-returned Box objects into one Region (no new geometry)."""
    r = db.Region()
    for b in boxes:
        r.insert(b)
    return r


def point(p):
    return (int(p[0]), int(p[1]))


class Extent:
    """Plain rectangle object exposing .extent(), the interface the MILP placer reads."""
    def __init__(self, w, h):
        self.w, self.h = int(w), int(h)

    def extent(self):
        return (0, 0, self.w, self.h)


def display(ctx, bbox, label):
    """Register a display-only input shape (drawn on probe layer 200/0)."""
    ctx.fixtures.append({'bbox_nm': [int(v) for v in bbox], 'label': label})


def load_gds(relative_path):
    """Read an existing GDS (e.g. produced by the project's full flow) as an INPUT."""
    from primitive_probe.core import PROJECT_ROOT
    layout = db.Layout()
    layout.read(str(PROJECT_ROOT / relative_path))
    return layout
