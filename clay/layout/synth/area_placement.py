"""Small exact-area CP-SAT backend for reference-free placement experiments.

Unlike the production placement backend's W+H proxy, this backend minimizes
the product of the materialized outer width and height. It accepts generic
rectangles, pair clearances and optional symbolic relation preferences only.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from types import SimpleNamespace

from clay.layout.synth import placement_constraints
import time
from ortools.sat.python import cp_model


@dataclass(frozen=True)
class AreaPlacement:
    origins_nm: dict
    area_nm2: int
    status: str
    lower_bound_nm2: float
    secondary_status: str
    seconds: float
    preference_cost: int


def solve_area(rectangles, outer_boxes, clearances, preferences=(), *, grid=5, seconds=10, bands=None, core_boxes=None):
    """Rectangles keyed by target instance; offsets/bboxes are freshly generated.

    preferences: (a, b, relation) with a NMOS/b PMOS role mapping supplied by
    caller. Relations are soft and considered only AFTER the best found area.
    No fixed positions and no sample geometry enter this backend.
    """
    start = time.perf_counter()
    names = list(rectangles)
    if not names:
        raise ValueError("Cannot place an empty design")
    def units(n):
        if n % grid:
            raise ValueError("Off-grid geometry")
        return n // grid
    wh = {n: tuple(units(v) for v in rectangles[n]) for n in names}
    box = {n: tuple(units(v) for v in outer_boxes[n]) for n in names}
    gap = {(a, b): units(clearances[a, b]) for i, a in enumerate(names) for b in names[i+1:]}
    horizon = sum(max(b[2]-b[0], b[3]-b[1]) for b in box.values()) + sum(gap.values()) + 1
    m = cp_model.CpModel()
    xs = {n: m.NewIntVar(0, horizon, "x"+str(i)) for i, n in enumerate(names)}
    ys = {n: m.NewIntVar(0, horizon, "y"+str(i)) for i, n in enumerate(names)}
    width, height = m.NewIntVar(1, horizon, "width"), m.NewIntVar(1, horizon, "height")
    m.AddMaxEquality(width, [xs[n]+box[n][2] for n in names])
    m.AddMaxEquality(height, [ys[n]+box[n][3] for n in names])
    m.AddMinEquality(0, [xs[n]+box[n][0] for n in names])
    m.AddMinEquality(0, [ys[n]+box[n][1] for n in names])
    for n in names:
        m.Add(xs[n]+box[n][0] >= 0)
        m.Add(ys[n]+box[n][1] >= 0)
    for (a, b), g in gap.items():
        options = [m.NewBoolVar(f"sep{a}_{b}_{k}") for k in range(4)]
        m.Add(xs[a]+wh[a][0]+g <= xs[b]).OnlyEnforceIf(options[0])
        m.Add(xs[b]+wh[b][0]+g <= xs[a]).OnlyEnforceIf(options[1])
        m.Add(ys[a]+wh[a][1]+g <= ys[b]).OnlyEnforceIf(options[2])
        m.Add(ys[b]+wh[b][1]+g <= ys[a]).OnlyEnforceIf(options[3])
        m.AddBoolOr(options)
    if bands:
        # Declared two-band floorplan prior, common to B1/B2/B3. This limits the
        # search space explicitly; it is not learned from one sample.
        groups = {}
        for n in names:
            groups.setdefault(bands[n], []).append(n)
        if len(groups) != 2:
            raise ValueError("The experimental band contract requires two bands")
        for members in groups.values():
            for n in members[1:]:
                m.Add(ys[n] == ys[members[0]])
        first, second = list(groups.values())
        forward = m.NewBoolVar("band_direction")
        for a in first:
            for b in second:
                g = gap.get((a, b), gap.get((b, a)))
                m.Add(ys[a]+wh[a][1]+g <= ys[b]).OnlyEnforceIf(forward)
                m.Add(ys[b]+wh[b][1]+g <= ys[a]).OnlyEnforceIf(forward.Not())
    area = m.NewIntVar(1, horizon*horizon, "area")
    m.AddMultiplicationEquality(area, [width, height])
    m.Minimize(area)
    solver = cp_model.CpSolver()
    # Single worker, fixed seed, deterministic-time limit: the result depends
    # on the model alone, not on machine load. Wall time is a safety cap only.
    solver.parameters.max_deterministic_time = float(seconds)
    solver.parameters.max_time_in_seconds = max(60.0, 20.0 * float(seconds))
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ValueError(f"Area placement failed: {solver.StatusName(status)}")
    primary_status = solver.StatusName(status)
    best_area = solver.Value(area)
    bound = solver.BestObjectiveBound()*grid*grid
    primary = {n: (solver.Value(xs[n])*grid, solver.Value(ys[n])*grid) for n in names}
    # Keep the achieved area. Secondary preferences cannot buy imitation by
    # increasing it; this is not a weighted area/style objective.
    m.Add(area == best_area)
    penalties = []
    for i, (a, b, relation) in enumerate(preferences):
        v = m.NewIntVar(0, horizon*2, f"pref{i}")
        if relation == "align:right":
            m.AddAbsEquality(v, xs[a]+wh[a][0]-xs[b]-wh[b][0])
        elif relation == "align:left":
            m.AddAbsEquality(v, xs[a]-xs[b])
        elif relation == "align:center":
            m.AddAbsEquality(v, 2*xs[a]+wh[a][0]-2*xs[b]-wh[b][0])
        elif relation == "p_above_n":
            m.AddMaxEquality(v, [0, ys[a]+wh[a][1]-ys[b]])
        else:
            raise ValueError(f"Unexecutable preference: {relation}")
        penalties.append(v)
    # With no learned rule, no artificial preference/area penalty is added.
    # Deterministic compactness tie-break applies identically to every baseline.
    tie = sum(xs.values()) + sum(ys.values())
    pref = sum(penalties)
    if core_boxes is not None:
        if preferences:
            raise ValueError("Choose either core-footprint tie break or style preferences")
        # With identical material area prefer a genuinely compact device/access
        # envelope; absolute-origin sum alone can arbitrarily spread the active.
        core = {n: tuple(units(v) for v in core_boxes[n]) for n in names}
        lo_x,lo_y = m.NewIntVar(0,horizon,"core_x0"),m.NewIntVar(0,horizon,"core_y0")
        hi_x,hi_y = m.NewIntVar(0,horizon,"core_x1"),m.NewIntVar(0,horizon,"core_y1")
        cw,ch = m.NewIntVar(1,horizon,"core_w"),m.NewIntVar(1,horizon,"core_h")
        ca = m.NewIntVar(1,horizon*horizon,"core_area")
        m.AddMinEquality(lo_x,[xs[n]+core[n][0] for n in names])
        m.AddMinEquality(lo_y,[ys[n]+core[n][1] for n in names])
        m.AddMaxEquality(hi_x,[xs[n]+core[n][2] for n in names])
        m.AddMaxEquality(hi_y,[ys[n]+core[n][3] for n in names])
        m.Add(cw==hi_x-lo_x);m.Add(ch==hi_y-lo_y)
        m.AddMultiplicationEquality(ca,[cw,ch])
        m.Minimize(ca*(2*len(names)*horizon+1)+tie)
    else:
        m.Minimize(pref*(2*len(names)*horizon+1) + tie)
    for n in names:
        m.AddHint(xs[n], primary[n][0]//grid)
        m.AddHint(ys[n], primary[n][1]//grid)
    status2 = solver.Solve(m)
    secondary_status = solver.StatusName(status2)
    if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        primary = {n: (solver.Value(xs[n])*grid, solver.Value(ys[n])*grid) for n in names}
        cost = sum(solver.Value(v) for v in penalties)
    else:
        cost = -1
    return AreaPlacement(primary, best_area*grid*grid, primary_status, bound,
                         secondary_status, time.perf_counter()-start, cost)


@dataclass(frozen=True)
class BlockPlacement:
    origins_nm: dict
    area_nm2: int
    width_nm: int
    height_nm: int
    status: str
    lower_bound_nm2: float
    secondary_status: str
    seconds: float
    axis2_nm: Optional[int]
    wells_nm: dict
    soft_symmetry_cost: int
    #: Chosen variant index per block that offered more than one (orientation
    #: decided jointly with position); empty when every block had one variant.
    variant_index: dict = field(default_factory=dict)
    #: How the returned placement was chosen: ``free`` (whatever the parallel
    #: solver returned; not reproducible), ``canonical`` (proven-optimal area,
    #: placement fixed by the deterministic tie-break stage), ``single_thread``
    #: (area not proven by the parallel stage; deterministic re-solve), with
    #: ``+tiebreak_failed`` appended when the canonical stage found nothing.
    determinism: str = "free"


_BOX_KEYS = ("core", "outer", "diff", "well_box")


def solve_blocks(blocks, *, grid, well_to_opposite, well_space, axis=None, pairs=(),
                 centred=(), soft_centred=(), seconds=30.0, workers=8, seed=0,
                 hint=None, ties=(), variant_hint=None, deterministic=True,
                 canonical_seconds=None, constraints=None, defer_canonical=False,
                 pinned_area_nm2=None):
    """Exact-area CP-SAT placement of rigid blocks (circuit-general).

    ``blocks`` maps a name to a dict of block-local boxes (``core``, ``outer``,
    optional ``diff``/``well_box``), its ``halo`` (half the same-plane
    spacing it needs), its ``plane`` and its n-well domain ``well``.

    **Orientation as a decision variable.** A block may instead carry
    ``variants``: a list of such box dicts, one per legal orientation of the
    same construction (built by the caller through the freedom gate). The
    model then has one Boolean per variant, exactly one chosen, and every box
    of the block is ``origin + sum(chosen_k * box_k)``. Orientation and
    position are therefore optimised *together*, so an optimal solve is
    optimal over every combination of the offered orientations - which an
    outer enumeration (4**N combinations) could not afford. ``ties`` lists
    ``(a, b)`` whose variant indices must be equal (a mirror pair whose
    variants were generated as reflections of each other, index by index).
    ``variant_hint`` maps a block to a variant index to warm-start from.

    Constraints, each a stated rule rather than a tuned number:

    * blocks on one plane: cores at least ``halo_a + halo_b`` apart
      (``AddNoOverlap2D`` on halo-expanded cores); planes do not interact;
    * one rectangular n-well per domain, the bounding box of its members'
      ``well_box``; a diffusion outside a domain keeps ``well_to_opposite``
      from that rectangle; two domains keep ``well_space``;
    * ``axis`` (``'vertical'``/``'horizontal'``/``None``): every ``pairs``
      entry ``(a, b)`` is mirror-placed about one common axis (``b`` must be
      the reflection of ``a``), every ``centred`` block sits on the axis.

    The objective is the exact product of the outer width and height. A
    second solve keeps that area and minimises ``soft_centred`` blocks'
    distance to the axis. No reference geometry enters this function.

    **Constraint kinds** (step S3b): ``ties``, ``pairs``, ``centred`` and
    ``soft_centred`` are the built-in kinds of
    :mod:`clay.layout.synth.placement_constraints`; ``constraints`` passes data
    for any other registered kind (``{"max_separation": [(a, b, nm)]}``). The
    wells and spacing rules stay here: they are the process's rules, not
    requirements of a circuit.

    With one variant per block the model is exactly the one this function
    built before variants existed (same variables and constraints).

    **Determinism** (``deterministic=True``, the default): identical input
    gives an identical placement, independent of thread count, hash seed and
    machine load. ``seconds`` bounds the (parallel) area stage; the placement
    is then fixed by a single-threaded stage under a deterministic-time limit
    (``canonical_seconds``, default ``seconds``) that minimises the remaining
    priority levels: soft-centring cost, then a canonical tie-break (smallest
    coordinates, then lowest variant index). ``BlockPlacement.determinism``
    records which path produced the result.

    **Deferred canonicalisation** (design doc §6, plan A). The canonical stage
    is the expensive part and only matters for a candidate that can still win.
    ``defer_canonical=True``: when stage 1 proves the area optimal, return at
    once with ``determinism='deferred'`` - the *area* (unique) and bound are
    final, the placement is stage 1's and must not be used as output. When
    stage 1 does not prove optimality the call proceeds as usual (that area is
    itself run-dependent). ``pinned_area_nm2``: the proven optimum of an
    earlier deferred call on the same model; stage 1 is skipped and the
    canonical stage runs on exactly the model it would have run on (hints are
    cleared either way), so the result equals a full call's.
    """
    if pinned_area_nm2 is not None and not deterministic:
        raise ValueError("pinned_area_nm2 needs the deterministic mode")
    full_args = dict(grid=grid, well_to_opposite=well_to_opposite, well_space=well_space,
                     axis=axis, pairs=pairs, centred=centred, soft_centred=soft_centred,
                     seconds=seconds, workers=workers, seed=seed, hint=hint, ties=ties,
                     variant_hint=variant_hint, deterministic=deterministic,
                     canonical_seconds=canonical_seconds, constraints=constraints)
    start = time.perf_counter()
    names = list(blocks)
    if not names:
        raise ValueError("Cannot place an empty design")

    def u(v):
        return -(-int(v) // grid)

    def units(n, b, key):
        if any(int(v) % grid for v in b):
            raise ValueError(f"{n}.{key} is off the {grid} nm grid: {b}")
        return tuple(int(v) // grid for v in b)

    variants = {}
    for n in names:
        vs = blocks[n].get("variants") or [blocks[n]]
        conv = []
        for k, v in enumerate(vs):
            entry = {}
            for key in _BOX_KEYS:
                if v.get(key) is not None:
                    entry[key] = units(n, v[key], key if len(vs) == 1 else f"{key}[{k}]")
            conv.append(entry)
        for key in ("diff", "well_box"):
            if len({key in e for e in conv}) > 1:
                raise ValueError(f"{n}: variants disagree on whether {key!r} exists")
        variants[n] = conv
    halo = {n: u(blocks[n]["halo"]) for n in names}
    horizon = sum(max(max(e["outer"][2] - e["outer"][0], e["outer"][3] - e["outer"][1])
                      for e in variants[n]) + 2 * halo[n] for n in names)
    horizon += u(well_space) * 4 + u(well_to_opposite) * 4 + 1
    big = 4 * horizon
    m = cp_model.CpModel()
    xs = {n: m.NewIntVar(-horizon, horizon, f"x_{i}") for i, n in enumerate(names)}
    ys = {n: m.NewIntVar(-horizon, horizon, f"y_{i}") for i, n in enumerate(names)}
    sel = {}
    for i, n in enumerate(names):
        if len(variants[n]) > 1:
            sel[n] = [m.NewBoolVar(f"o_{i}_{k}") for k in range(len(variants[n]))]
            m.AddExactlyOne(sel[n])
    # Constraint kinds are plug-ins (placement_constraints); they run at the
    # points where the inline code used to, so the model is built in the
    # same order and the solver's search is unchanged.
    kinds = {"ties": list(ties), "mirror_pairs": list(pairs), "centred": list(centred),
             "soft_centred": list(soft_centred), **dict(constraints or {})}
    ctx = SimpleNamespace(m=m, names=names, xs=xs, ys=ys, sel=sel, variants=variants,
                          grid=grid, big=big, horizon=horizon, axis_var=None, along=None,
                          centre2=None, soft_terms=[])
    placement_constraints.run_phase("variants", ctx, kinds)

    def off(n, key, i):
        """Block-local coordinate ``i`` of box ``key``: a constant, or the
        selected variant's value as a linear expression."""
        if n not in sel:
            return variants[n][0][key][i]
        return sum(s * e[key][i] for s, e in zip(sel[n], variants[n]))

    def var_of(expr, tag, lo=None):
        """CP-SAT intervals and min/max need affine terms; name a sum."""
        if isinstance(expr, int):
            return expr
        v = m.NewIntVar(-big if lo is None else lo, big, tag)
        m.Add(v == expr)
        return v

    def coord(n, key, i):
        base = xs[n] if i in (0, 2) else ys[n]
        o = off(n, key, i)
        return base + o if isinstance(o, int) else var_of(base + o, f"{n}_{key}{i}")

    ctx.off, ctx.var_of, ctx.coord = off, var_of, coord

    for plane in sorted({blocks[n]["plane"] for n in names}):
        members = [n for n in names if blocks[n]["plane"] == plane]
        xi, yi = [], []
        for n in members:
            h = halo[n]
            if n not in sel:
                c = variants[n][0]["core"]
                xi.append(m.NewIntervalVar(xs[n] + (c[0] - h), c[2] - c[0] + 2 * h,
                                           xs[n] + (c[2] + h), ""))
                yi.append(m.NewIntervalVar(ys[n] + (c[1] - h), c[3] - c[1] + 2 * h,
                                           ys[n] + (c[3] + h), ""))
                continue
            x0, y0 = coord(n, "core", 0), coord(n, "core", 1)
            x1, y1 = coord(n, "core", 2), coord(n, "core", 3)
            wx = var_of(off(n, "core", 2) - off(n, "core", 0) + 2 * h, f"{n}_cw", lo=0)
            wy = var_of(off(n, "core", 3) - off(n, "core", 1) + 2 * h, f"{n}_ch", lo=0)
            xi.append(m.NewIntervalVar(x0 - h, wx, x1 + h, ""))
            yi.append(m.NewIntervalVar(y0 - h, wy, y1 + h, ""))
        m.AddNoOverlap2D(xi, yi)

    def sep(a, b, gap, tag):
        """a, b: (x0, y0, x1, y1) linear expressions; a gap in one direction."""
        opts = [m.NewBoolVar(f"{tag}_{k}") for k in range(4)]
        m.Add(a[2] + gap <= b[0]).OnlyEnforceIf(opts[0])
        m.Add(b[2] + gap <= a[0]).OnlyEnforceIf(opts[1])
        m.Add(a[3] + gap <= b[1]).OnlyEnforceIf(opts[2])
        m.Add(b[3] + gap <= a[1]).OnlyEnforceIf(opts[3])
        m.AddBoolOr(opts)

    def placed(n, key):
        return tuple(coord(n, key, i) for i in range(4))

    domains = {}
    for n in names:
        if blocks[n].get("well") is not None and "well_box" in variants[n][0]:
            domains.setdefault(blocks[n]["well"], []).append(n)
    wells = {}
    for i, (d, members) in enumerate(sorted(domains.items())):
        coords = []
        for k, fn in enumerate((m.AddMinEquality, m.AddMinEquality,
                                m.AddMaxEquality, m.AddMaxEquality)):
            v = m.NewIntVar(-big, big, f"well{i}_{k}")
            fn(v, [placed(n, "well_box")[k] for n in members])
            coords.append(v)
        wells[d] = tuple(coords)
    g_opp, g_well = u(well_to_opposite), u(well_space)
    for d, w in wells.items():
        for n in names:
            if blocks[n]["plane"] != "device" or blocks[n].get("well") is not None:
                continue                      # other domains are separated well-to-well
            key = "diff" if "diff" in variants[n][0] else "core"
            sep(placed(n, key), w, g_opp, f"wo_{n}")
    dl = sorted(wells)
    for i, a in enumerate(dl):
        for b in dl[i + 1:]:
            sep(wells[a], wells[b], g_well, "ww")

    xmax = m.NewIntVar(-big, big, "xmax")
    ymax = m.NewIntVar(-big, big, "ymax")
    m.AddMinEquality(0, [coord(n, "outer", 0) for n in names] + [w[0] for w in wells.values()])
    m.AddMinEquality(0, [coord(n, "outer", 1) for n in names] + [w[1] for w in wells.values()])
    m.AddMaxEquality(xmax, [coord(n, "outer", 2) for n in names] + [w[2] for w in wells.values()])
    m.AddMaxEquality(ymax, [coord(n, "outer", 3) for n in names] + [w[3] for w in wells.values()])
    width = m.NewIntVar(1, big, "width")
    height = m.NewIntVar(1, big, "height")
    m.Add(width == xmax)
    m.Add(height == ymax)

    axis_var = None
    if axis is not None:
        if axis not in ("vertical", "horizontal"):
            raise ValueError("axis must be 'vertical', 'horizontal' or None")
        axis_var = m.NewIntVar(-big, big, "axis2")
        along = 0 if axis == "vertical" else 1   # the coordinate the axis mirrors

        def centre2(n):
            pos = xs[n] if along == 0 else ys[n]
            return 2 * pos + (off(n, "outer", along) + off(n, "outer", along + 2))

        ctx.axis_var, ctx.along, ctx.centre2 = axis_var, along, centre2
    placement_constraints.run_phase("placement", ctx, kinds)
    soft_terms = ctx.soft_terms

    area = m.NewIntVar(1, big * big, "area")
    m.AddMultiplicationEquality(area, [width, height])
    m.Minimize(area)
    if hint:
        for n, (hx, hy) in hint.items():
            if n in xs:
                m.AddHint(xs[n], hx // grid)
                m.AddHint(ys[n], hy // grid)
    for n, s in sel.items():
        chosen = (variant_hint or {}).get(n, 0)
        for k, b in enumerate(s):
            m.AddHint(b, 1 if k == chosen else 0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    if pinned_area_nm2 is not None:
        # Stage 1 already ran in an earlier (deferred) call and proved this
        # area optimal; go straight to the canonical stage.
        if int(pinned_area_nm2) % (grid * grid):
            raise ValueError(f"pinned area {pinned_area_nm2} is not on the {grid} nm grid")
        status = cp_model.OPTIMAL
        primary_status = "OPTIMAL"
        best = int(pinned_area_nm2) // (grid * grid)
        bound = float(best * grid * grid)
        sol = chosen = None
        w_u = h_u = axis_v = None
        well_v = {}
    else:
        status = solver.Solve(m)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise ValueError(f"Block placement failed: {solver.StatusName(status)}")
        primary_status = solver.StatusName(status)
        best = solver.Value(area)
        bound = solver.BestObjectiveBound() * grid * grid
        sol = {n: (solver.Value(xs[n]), solver.Value(ys[n])) for n in names}
        chosen = {n: next(k for k, b in enumerate(s) if solver.Value(b)) for n, s in sel.items()}
        w_u, h_u = solver.Value(width), solver.Value(height)
        axis_v = solver.Value(axis_var) if axis_var is not None else None
        well_v = {d: tuple(solver.Value(v) for v in w) for d, w in wells.items()}
    secondary, soft_cost = "NONE", -1
    determinism = "free"

    if deterministic and defer_canonical and status == cp_model.OPTIMAL and pinned_area_nm2 is None:
        return BlockPlacement({n: (x * grid, y * grid) for n, (x, y) in sol.items()},
                              best * grid * grid, w_u * grid, h_u * grid, primary_status, bound,
                              "DEFERRED", time.perf_counter() - start,
                              axis_v * grid if axis_v is not None else None,
                              {d: tuple(v * grid for v in w) for d, w in well_v.items()}, -1,
                              chosen, "deferred")

    def single_thread(budget):
        """Deterministic solver: one worker, fixed seed, deterministic-time
        limit (independent of machine load). Wall time is a safety cap only."""
        s = cp_model.CpSolver()
        s.parameters.num_search_workers = 1
        s.parameters.random_seed = 0
        s.parameters.max_deterministic_time = float(budget)
        s.parameters.max_time_in_seconds = max(60.0, 20.0 * float(budget))
        return s

    if deterministic:
        # Stage 1 (above) may run multi-threaded: fast, and when it proves
        # OPTIMAL the optimal *area* is a unique number, whatever run found
        # it. The *placement* it returns is not: equal-area optima abound and a
        # parallel portfolio lands on a different one per run (measured: three
        # runs, three layouts). So the layout is chosen by a separate stage
        # that depends on the model alone: single thread, fixed seed,
        # deterministic time, no hints carried over from stage 1, area pinned
        # to the proven optimum, and the remaining priority levels as the
        # objective (soft relations, then a canonical tie-break).
        if status == cp_model.OPTIMAL:
            target = best
            determinism = "canonical"
        else:
            # Not proven: the stage-1 value itself is run-dependent. Re-solve
            # stage 1 deterministically from scratch instead (reproducible,
            # possibly worse than the discarded parallel incumbent).
            m.ClearHints()
            s1 = single_thread(seconds)
            st1 = s1.Solve(m)
            if st1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                raise ValueError(f"Block placement failed: {s1.StatusName(st1)}")
            target = s1.Value(area)
            primary_status = s1.StatusName(st1)
            bound = s1.BestObjectiveBound() * grid * grid
            sol = {n: (s1.Value(xs[n]), s1.Value(ys[n])) for n in names}
            chosen = {n: next(k for k, b in enumerate(s) if s1.Value(b)) for n, s in sel.items()}
            w_u, h_u = s1.Value(width), s1.Value(height)
            axis_v = s1.Value(axis_var) if axis_var is not None else None
            well_v = {d: tuple(s1.Value(v) for v in w) for d, w in wells.items()}
            determinism = "single_thread"
        m.ClearHints()
        m.Add(area == target)
        # Canonical tie-break (priority level 4): smallest coordinates, then
        # lowest variant indices, in a fixed linear form. Shifted to be
        # non-negative so the soft level can dominate it with one multiplier.
        tie = sum(xs[n] + horizon for n in names) + sum(ys[n] + horizon for n in names) \
            + sum(k * b for s in sel.values() for k, b in enumerate(s))
        tie_span = 4 * horizon * len(names) + sum(len(s) * len(s) for s in sel.values()) + 1
        m.Minimize((sum(soft_terms) * tie_span if soft_terms else 0) + tie)
        s2 = single_thread(canonical_seconds if canonical_seconds is not None else seconds)
        st2 = s2.Solve(m)
        secondary = s2.StatusName(st2)
        if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            sol = {n: (s2.Value(xs[n]), s2.Value(ys[n])) for n in names}
            chosen = {n: next(k for k, b in enumerate(s) if s2.Value(b)) for n, s in sel.items()}
            best = s2.Value(area)
            w_u, h_u = s2.Value(width), s2.Value(height)
            axis_v = s2.Value(axis_var) if axis_var is not None else None
            well_v = {d: tuple(s2.Value(v) for v in w) for d, w in wells.items()}
            if soft_terms:
                soft_cost = sum(s2.Value(t) for t in soft_terms) * grid
        else:
            if pinned_area_nm2 is not None:
                # No stage-1 placement to fall back on here: do what a full
                # call does (stage 1, then this same canonical stage).
                return solve_blocks(blocks, **full_args)
            # No placement at the pinned area within the deterministic budget:
            # keep the stage-1 placement and say that reproducibility was lost.
            determinism += "+tiebreak_failed"
    elif soft_terms:
        m.Add(area <= best)
        m.Minimize(sum(soft_terms))
        m.ClearHints()
        for n in names:
            m.AddHint(xs[n], sol[n][0])
            m.AddHint(ys[n], sol[n][1])
        for n, s in sel.items():
            for k, b in enumerate(s):
                m.AddHint(b, 1 if k == chosen[n] else 0)
        solver.parameters.max_time_in_seconds = max(1.0, seconds / 3)
        st2 = solver.Solve(m)
        secondary = solver.StatusName(st2)
        if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            sol = {n: (solver.Value(xs[n]), solver.Value(ys[n])) for n in names}
            chosen = {n: next(k for k, b in enumerate(s) if solver.Value(b))
                      for n, s in sel.items()}
            best = solver.Value(area)
            w_u, h_u = solver.Value(width), solver.Value(height)
            axis_v = solver.Value(axis_var)
            well_v = {d: tuple(solver.Value(v) for v in w) for d, w in wells.items()}
            soft_cost = sum(solver.Value(t) for t in soft_terms) * grid
    return BlockPlacement({n: (x * grid, y * grid) for n, (x, y) in sol.items()},
                          best * grid * grid, w_u * grid, h_u * grid, primary_status, bound,
                          secondary, time.perf_counter() - start,
                          axis_v * grid if axis_v is not None else None,
                          {d: tuple(v * grid for v in w) for d, w in well_v.items()}, soft_cost,
                          chosen, determinism)
