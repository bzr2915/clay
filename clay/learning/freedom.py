"""Per-part, per-layer degrees of freedom and the action gate.

Why this module exists
----------------------
A layout's parts do not all move the same way. In SKY130A the diffusion, poly,
licon and mcon may only be oriented at multiples of 90 degrees, while the well,
the implant selects and the metal layers may also sit at 45 degrees; GF180
permits 45 degrees nearly everywhere. A row is therefore *not* the unit of
freedom: it spans several layers, and the freedom of a part is the strictest
freedom among the layers it covers.

The second concern is that an action planner may not exceed what the design
allows. :func:`enforce` is a pure function that refuses any action outside a
part's declared freedom, before any geometry is touched. It is deliberately not
a heuristic: a planner that proposes something illegal gets an exception, not a
silently clamped action, because a clamp would hide exactly the local-optimum
mistake this work is meant to prevent.

What is asserted, and what is not
---------------------------------
This module decides *permission*. It does not perform rotations, and it does not
claim the verifier can check them. A freedom the engine cannot execute (45
degrees today: see ``Pack.capability``) is reported as unusable rather than
being quietly rounded down to 90.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import gcd
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from clay.pdk.semantic import LayerFreedom, Pack


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

ROTATE = "rotate"
MIRROR = "mirror"
TRANSLATE = "translate"


@dataclass(frozen=True)
class Action:
    """One geometric action on one part.

    ``op`` is one of :data:`ROTATE`, :data:`MIRROR`, :data:`TRANSLATE`.
    For ``rotate`` the angle is ``degrees`` (counter-clockwise, multiples of the
    part's permitted step). For ``mirror`` ``axis`` is ``"x"``, ``"y"`` or
    ``"xy"``. For ``translate`` ``degrees``/``axis`` are unused and the offset is
    ``dx_nm``/``dy_nm``.
    """

    op: str
    degrees: int = 0
    axis: str = ""
    dx_nm: int = 0
    dy_nm: int = 0

    def __post_init__(self):
        if self.op not in (ROTATE, MIRROR, TRANSLATE):
            raise ValueError(f"unknown action op {self.op!r}")
        if self.op == ROTATE:
            # Any whole degree is expressible. Whether a *particular* angle is
            # permitted is a property of the part's freedom, checked by
            # enforce(); refusing angles here would encode one process's step
            # into the action vocabulary itself.
            if type(self.degrees) is not int:
                raise ValueError(f"rotation must be an integer number of degrees, got {self.degrees!r}")
            if not 0 <= self.degrees < 360:
                raise ValueError(f"rotation must be in [0, 360), got {self.degrees!r}")
        if self.op == MIRROR and self.axis not in ("x", "y", "xy"):
            raise ValueError(f"mirror axis must be x, y or xy, got {self.axis!r}")
        if self.op == TRANSLATE and (type(self.dx_nm) is not int or type(self.dy_nm) is not int):
            raise ValueError("translation offsets must be integers in nm")

    @property
    def quarter_turns(self) -> int:
        if self.op != ROTATE:
            raise ValueError("only rotations map onto quarter turns")
        return (self.degrees // 90) % 4


# ---------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """A movable piece of the layout, and the layers it is made of.

    ``layer_roles`` are semantic roles (``active``, ``poly``, ...). The part's
    freedom is the intersection of those roles' freedoms, so a part containing
    poly can never rotate to 45 degrees on a process that forbids it - even if
    its well or its metal could.

    ``coupling`` names parts that must receive the *same* action; a plan that
    moves one and not the other is rejected. ``constraints`` are named predicates
    the caller supplies implementations for (see :func:`enforce`).
    """

    name: str
    layer_roles: Tuple[str, ...]
    coupling: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()

    def __post_init__(self):
        if not self.name:
            raise ValueError("part needs a name")
        if not self.layer_roles:
            raise ValueError(f"part {self.name!r} covers no layers")
        if self.name in self.coupling:
            raise ValueError(f"part {self.name!r} couples to itself")


def _intersect(freedoms: Sequence[LayerFreedom]) -> LayerFreedom:
    """Strictest freedom across layers, as the intersection of rotation sets.

    A layer permitting step ``s`` admits rotations ``{0, s, 2s, ...}``. The set
    a part may use is the intersection of its layers' sets, which is the set of
    multiples of their **least common multiple** - not their maximum. The two
    agree for 45 and 90 (where max happened to be used before), and diverge for,
    say, 30 and 45: their common rotations are the multiples of 90, while the
    maximum (45) is not even in the 30-degree set. Using max would therefore
    grant a rotation one of the layers forbids.

    Mirrors are intersected element-wise: a part may mirror only if every layer
    it covers may.
    """
    if not freedoms:
        raise ValueError("no layer freedom to intersect")
    step = freedoms[0].step_deg
    for f in freedoms[1:]:
        step = step * f.step_deg // gcd(step, f.step_deg)
    mirrors = set(freedoms[0].mirror)
    for f in freedoms[1:]:
        mirrors &= set(f.mirror)
    return LayerFreedom(step, freedoms[0].provenance,
                        " ∩ ".join(sorted({f.source for f in freedoms})),
                        tuple(sorted(mirrors)))


@dataclass(frozen=True)
class PartFreedom:
    """What a part may do, resolved from the pack's per-layer freedoms.

    Two different sets are kept apart on purpose:

    ``rotations_deg``  what the *process* permits, from the deck's angle rules.
    ``executable_deg`` what this *engine* can perform and verify.

    ``executable_deg`` is always a subset of ``rotations_deg``. The gap is the
    honest statement "the process allows this, our verifier does not yet check
    it", and :func:`enforce` refuses actions from that gap instead of clamping
    them into the executable set.

    ``verdicts`` closes every candidate angle (see :func:`angle_verdicts`): each
    one is executable, forbidden by a deck angle rule, proven to have no legal
    realisation on the grid, or left open (unknown / not executable). Only the
    open ones are gaps a search must report.
    """

    part: Part
    layer_freedom: LayerFreedom
    executable_deg: Tuple[int, ...]
    #: Why the executable set is narrower than the permitted one (empty when equal).
    narrowed_reason: str = ""
    #: Set when no freedom could be established at all.
    unknown_reason: str = ""
    verdicts: Tuple["AngleVerdict", ...] = ()

    @property
    def usable(self) -> bool:
        return not self.unknown_reason and bool(self.executable_deg)

    @property
    def rotations_deg(self) -> Tuple[int, ...]:
        return self.layer_freedom.rotations_deg

    @property
    def mirror_axes(self) -> Tuple[str, ...]:
        return self.layer_freedom.mirror

    def verdict(self, degrees: int) -> Optional["AngleVerdict"]:
        for v in self.verdicts:
            if v.degrees == degrees:
                return v
        return None


# ---------------------------------------------------------------------------
# per-angle verdicts: every candidate angle is closed or reported open
# ---------------------------------------------------------------------------

EXECUTABLE = "executable"          # in the search space
DECK_FORBIDDEN = "deck_forbidden"  # a layer's deck angle rule forbids it (proof)
DECK_INFEASIBLE = "deck_infeasible"  # no legal on-grid realisation exists (proof)
GRID_UNREALIZABLE = "grid_unrealizable"  # no on-grid edge has this direction at all (proof)
UNKNOWN = "unknown"                # a layer has no angle rule: neither allowed nor refuted
NOT_EXECUTABLE = "not_executable"  # permitted and not refuted, but the engine cannot build/verify it

#: Verdicts that settle an angle: it is either searched or provably excluded.
CLOSED = (EXECUTABLE, DECK_FORBIDDEN, GRID_UNREALIZABLE, DECK_INFEASIBLE)


@dataclass(frozen=True)
class AngleVerdict:
    degrees: int
    status: str
    reason: str

    @property
    def closed(self) -> bool:
        return self.status in CLOSED

    def as_dict(self) -> Dict[str, object]:
        return {"degrees": self.degrees, "status": self.status, "reason": self.reason}


def grid_edge(length_nm: int, grid_nm: int, degrees: int) -> Optional[Tuple[int, int]]:
    """An on-grid edge of exactly ``length_nm`` at ``degrees``, or ``None``.

    An edge whose two end points lie on the grid spans ``(a*g, b*g)`` for
    integers ``a, b``, so its length is ``g*sqrt(a^2 + b^2)`` and its direction
    is ``atan2(b, a)``. The edge exists iff some integer pair meets both. For
    45 degrees ``a == b`` and the length is ``a*g*sqrt(2)``, never an integer
    multiple of ``g``: a cut with an exact edge length cannot have a diagonal
    edge. For angles whose tangent is irrational (30, 60, ...) no lattice
    direction exists at all (Niven). Pythagorean directions (e.g. 3-4-5) can
    succeed and are reported as such rather than refused by assumption.
    """
    if length_nm <= 0 or grid_nm <= 0 or length_nm % grid_nm:
        return None
    n = length_nm // grid_nm
    theta = degrees % 90
    if theta == 0:
        return (n, 0)
    from math import atan2, degrees as to_deg, isqrt
    for a in range(1, n):
        b2 = n * n - a * a
        b = isqrt(b2)
        if b > 0 and b * b == b2 and abs(to_deg(atan2(b, a)) - theta) < 1e-9:
            return (a, b)
    return None


def grid_direction(degrees: int) -> Optional[Tuple[int, int]]:
    """A primitive lattice vector ``(a, b)`` pointing at ``degrees``, or ``None``.

    Angles here are whole degrees (``Action`` carries an ``int``). By Niven's
    theorem the only whole-degree angles with a rational tangent are the
    multiples of 45, so those are the only directions an on-grid edge can take:
    0/90/180/270 (axis) and 45/135/225/315 (diagonal).
    """
    d = degrees % 360
    if d % 45:
        return None
    return {0: (1, 0), 45: (1, 1), 90: (0, 1), 135: (-1, 1), 180: (-1, 0),
            225: (-1, -1), 270: (0, -1), 315: (1, -1)}[d]


def executable_directions(pack: Pack, ability: str) -> Tuple[int, ...]:
    """Directions the engine can ``construct``/``place``/``verify`` on this pack.

    Declared per ability in ``Pack.direction_capability``, because a direction
    is only executable when all three exist: a constructor that draws it on the
    grid, a placement model that keeps such shapes apart, and a verifier that
    checks them. The legacy flags are honoured: ``rotate_orthogonal`` grants the
    quarter turns, ``rotate_non_orthogonal`` every grid-realisable direction.
    """
    declared = getattr(pack, "direction_capability", None) or {}
    if ability in declared:
        return tuple(declared[ability])
    out = []
    if "rotate_orthogonal" in pack.capability:
        out += [0, 90, 180, 270]
    if "rotate_non_orthogonal" in pack.capability:
        out += [45, 135, 225, 315]
    return tuple(sorted(out))


def angle_verdicts(pack: Pack, layer_roles: Iterable[str]) -> Tuple[AngleVerdict, ...]:
    """Close every candidate angle for a rigid part covering ``layer_roles``.

    Candidates are the multiples of ``gcd(90, every known layer step)``, so a
    part touching any 45-degree layer lists all eight angles; nothing is
    dropped before it is judged. Quarter turns and the deck-permitted
    non-orthogonal angles are then decided as follows:

    * quarter turn: legal when the pack declares that quarter turns and
      reflections preserve legality (``Pack.orthogonal_isometries``), or when
      every layer has an angle rule (a step dividing 90 admits it);
    * other angles, in order of strength:
      1. ``deck_forbidden`` - some layer's angle rule excludes it;
      2. ``grid_unrealizable`` - the direction itself has no on-grid edge
         (:func:`grid_direction`): every part here is drawn with edges at
         multiples of 45 degrees, so a rigid turn by such an angle produces an
         edge no grid can carry, whatever the layers or sizes;
      3. ``deck_infeasible`` - a layer pinned to an exact edge length (a cut)
         has no on-grid edge of that length at this angle (:func:`grid_edge`),
         so no realisation of the part exists at all;
      4. ``unknown`` - a layer has no angle rule to permit it;
      5. ``not_executable`` - permitted and not refuted, but the engine cannot
         construct, place or verify that direction (``Pack.direction_capability``);
      6. otherwise ``executable``.

    Only 4 and 5 are open: a search that leaves one of them unevaluated must say
    so, and cannot claim optimality over the full action space.
    """
    roles = tuple(dict.fromkeys(layer_roles))
    missing = [r for r in roles if r not in pack.layers]
    known = {r: pack.freedom[r] for r in roles if r in pack.freedom}
    silent = [r for r in roles if r in pack.layers and r not in pack.freedom]
    step = 90
    # Candidates come from every angle step the *process* states anywhere, not
    # only from this part's layers: a part whose layers are all 90-only still
    # lists 45/135/... so the certificate shows them closed as deck_forbidden
    # rather than silently absent.
    for f in list(known.values()) + list(pack.freedom.values()):
        step = gcd(step, f.step_deg)
    def engine_gap(d):
        """Which of construct/place/verify the engine cannot do at ``d``."""
        return [k for k in ("construct", "place", "verify") if d not in executable_directions(pack, k)]

    out = []
    for d in range(0, 360, step):
        if missing:
            out.append(AngleVerdict(d, UNKNOWN, f"layer role(s) {missing} are not defined by "
                                                f"pack {pack.pdk_id}; no deck angle rule"))
            continue
        if d % 90 == 0:
            if pack.orthogonal_isometries:
                basis = "quarter turn about a grid point; " + pack.orthogonal_isometries
            elif silent:
                out.append(AngleVerdict(d, UNKNOWN, f"no deck angle rule for {silent} and no "
                                                    "orthogonal-isometry declaration"))
                continue
            else:
                basis = "every layer's deck angle step divides 90"
            gap = engine_gap(d)
            out.append(AngleVerdict(d, NOT_EXECUTABLE if gap else EXECUTABLE,
                                    f"engine cannot {'/'.join(gap)} {d} degrees" if gap else basis))
            continue
        forbidding = sorted(f"{r} ({f.source}, step {f.step_deg})"
                            for r, f in known.items() if not f.allows(d))
        if forbidding:
            out.append(AngleVerdict(d, DECK_FORBIDDEN,
                                    f"deck angle rule forbids {d} degrees on " + "; ".join(forbidding)))
            continue
        if grid_direction(d) is None:
            out.append(AngleVerdict(
                d, GRID_UNREALIZABLE,
                f"tan({d % 180}) is irrational (Niven: among whole degrees only multiples of 45 "
                f"have a rational tangent), so no edge whose end points lie on the "
                f"{pack.grid_nm} nm grid points in that direction. The part's edges are at "
                f"multiples of 45 degrees; turned by {d} degrees they would point in directions "
                f"no grid carries, so no legal realisation exists for any layer or size."))
            continue
        proof = None
        for r in roles:
            if r in pack.exact_edges:
                rv = pack.exact_edges[r]
                if grid_edge(rv.value, pack.grid_nm, d) is None:
                    proof = (f"{r}: every edge must be exactly {rv.value} nm ({rv.source}) and "
                             f"every vertex on the {pack.grid_nm} nm grid; an on-grid edge at "
                             f"{d % 90 or 90} degrees spanning (a, b) grid steps has length "
                             f"{pack.grid_nm}*sqrt(a^2+b^2), and no integers give {rv.value} nm "
                             f"in that direction. No legal {r} can be drawn at {d} degrees, so "
                             f"no part containing it can be placed there.")
                    break
        if proof:
            out.append(AngleVerdict(d, DECK_INFEASIBLE, proof))
        elif silent:
            out.append(AngleVerdict(d, UNKNOWN, f"no deck angle rule for {silent}: {d} degrees "
                                                "is neither permitted nor refuted"))
        elif engine_gap(d):
            out.append(AngleVerdict(d, NOT_EXECUTABLE,
                                    f"deck permits {d} degrees and no rule refutes it, but this "
                                    f"engine cannot {'/'.join(engine_gap(d))} it (no "
                                    "'rotate_non_orthogonal' capability for this direction)"))
        else:
            out.append(AngleVerdict(d, EXECUTABLE, "deck permits it; engine can build and verify it"))
    return tuple(out)


def part_freedom(pack: Pack, part: Part) -> PartFreedom:
    """Resolve a part's freedom from the pack, refusing to invent one.

    A layer role the pack does not define has *unknown* freedom, and so does a
    role with no deck angle rule unless the pack declares that quarter turns
    and reflections preserve legality (``Pack.orthogonal_isometries``). In the
    latter case only the quarter turns become legal: a missing angle rule can
    never grant a non-orthogonal angle ("the deck says nothing" must not
    become "anything goes").

    Note what the deck's angle rule actually constrains: the *edges* of the
    geometry, not the placement. A rectangle placed at 0 degrees already has all
    its edges at multiples of 90, so it satisfies a 45-degree rule. What the
    rule permits is a shape drawn *diagonally*; what a 90-only rule forbids is
    exactly that. So the permitted rotation set follows the strictest layer:
    one 90-only layer in the part means the part may not be placed diagonally.
    """
    verdicts = angle_verdicts(pack, part.layer_roles)
    missing = [r for r in part.layer_roles if r not in pack.layers]
    silent = [r for r in part.layer_roles if r in pack.layers and r not in pack.freedom]
    if missing or (silent and not pack.orthogonal_isometries):
        gap = missing + silent
        return PartFreedom(
            part, LayerFreedom(90, "unknown", f"no angle rule for {', '.join(gap)}"),
            (), unknown_reason=f"no deck angle rule for layer(s) {', '.join(gap)}; "
                               "freedom unknown, treat as not free",
            verdicts=verdicts)
    known = [pack.freedom[r] for r in part.layer_roles if r in pack.freedom]
    mirrors = ("x", "xy", "y") if pack.orthogonal_isometries else ()
    if known:
        resolved = _intersect(known)
        if silent:
            # A silent layer admits only what the isometry declaration grants.
            resolved = LayerFreedom(resolved.step_deg * 90 // gcd(resolved.step_deg, 90),
                                    "derived", resolved.source + " ∩ quarter turns "
                                    f"(no angle rule for {', '.join(silent)})", mirrors)
        else:
            resolved = LayerFreedom(resolved.step_deg, resolved.provenance, resolved.source,
                                    tuple(sorted(set(resolved.mirror) | set(mirrors))))
    else:
        resolved = LayerFreedom(90, "derived", "orthogonal-isometry declaration only", mirrors)
    executable = tuple(v.degrees for v in verdicts
                       if v.status == EXECUTABLE and resolved.allows(v.degrees))
    if len(executable) < len(resolved.rotations_deg):
        withheld = [d for d in resolved.rotations_deg if d not in executable]
        infeasible = [v for v in verdicts if v.degrees in withheld
                      and v.status in (DECK_INFEASIBLE, GRID_UNREALIZABLE)]
        open_ = [d for d in withheld if d not in {v.degrees for v in infeasible}]
        parts = []
        if open_:
            parts.append(f"deck permits steps of {resolved.step_deg} degrees (angles {open_} "
                         f"withheld), but this engine declares no 'rotate_non_orthogonal' "
                         f"capability")
        if infeasible:
            parts.append(f"angles {[v.degrees for v in infeasible]} proven infeasible: "
                         f"{infeasible[0].reason}")
        return PartFreedom(part, resolved, executable,
                           narrowed_reason="; ".join(parts) + f"; issuing {list(executable)} only",
                           verdicts=verdicts)
    return PartFreedom(part, resolved, executable, verdicts=verdicts)


@dataclass(frozen=True)
class Freedom:
    """The full freedom model of one design under one rule pack."""

    pack: Pack
    parts: Mapping[str, Part]
    resolved: Mapping[str, PartFreedom]

    def part(self, name: str) -> PartFreedom:
        try:
            return self.resolved[name]
        except KeyError:
            raise KeyError(f"unknown part {name!r}; known: {sorted(self.resolved)}") from None

    def actions(self, name: str) -> Tuple[Action, ...]:
        """Every rotation this part may legally take, right now.

        This is the *executable* set, not the process-permitted one: an action
        the verifier cannot check is not offered, so a planner given this list
        cannot propose one by accident.
        """
        pf = self.part(name)
        return tuple(Action(ROTATE, degrees=d) for d in pf.executable_deg)

    def report(self) -> Dict[str, object]:
        return {
            "pdk": self.pack.pdk_id,
            "capability": sorted(self.pack.capability),
            "parts": {
                name: {
                    "layers": list(pf.part.layer_roles),
                    "step_deg": pf.layer_freedom.step_deg,
                    "permitted_deg": list(pf.rotations_deg),
                    "executable_deg": list(pf.executable_deg),
                    "mirror": list(pf.mirror_axes),
                    "usable": pf.usable,
                    "narrowed": pf.narrowed_reason,
                    "unknown": pf.unknown_reason,
                    "freedom_source": pf.layer_freedom.source,
                    "angle_verdicts": [v.as_dict() for v in pf.verdicts],
                    "open_angles": [v.degrees for v in pf.verdicts if not v.closed],
                }
                for name, pf in sorted(self.resolved.items())
            },
        }


def executable_rotations(pack: Pack, layer_roles: Iterable[str]) -> Tuple[int, ...]:
    """The rotation angles a part may be *built* at, straight from the pack.

    This is the one place a construction should ask "which orientations am I
    allowed to enumerate?". Writing ``range(4)`` inside a constructor hard-codes
    both the process's step and the engine's capability, and keeps hard-coding
    them after either changes. Routing needs the same answer, so it asks here
    instead of growing a second copy of the rule.

    Returns the empty tuple when the freedom is unknown (see
    :func:`part_freedom`); a caller must treat that as "do not rotate", never
    as "any angle".
    """
    resolved = part_freedom(pack, Part("_orientation_probe", tuple(layer_roles)))
    return resolved.executable_deg


def orientations(freedom_of_part: PartFreedom) -> Tuple[Tuple[int, bool], ...]:
    """Executable orthogonal orientations of a part as ``(degrees, mirrored)``.

    The eight symmetries of a rectangle are the four quarter turns, each with
    or without a reflection about the vertical axis (``'y'``); a reflection
    about ``'x'`` equals ``'y'`` followed by a half turn, so it adds nothing.
    Only orientations both legal and executable are returned; the order is
    fixed (identity first) so callers can index variants deterministically.

    **A non-orthogonal executable direction is a hard error here, not a filter.**
    This function enumerates the orientations of a *rigid* part, and the only
    angles that map the nanometre lattice onto itself are the quarter turns:
    rotating ``(x, y)`` by 45 degrees gives ``((x-y)/sqrt(2), (x+y)/sqrt(2))``,
    which is on the lattice only for a degenerate (collinear) shape - the square
    lattice's rotation group is Z4 plus mirrors, eight elements, whatever the
    process. So if a pack declares ``rotate_non_orthogonal`` and the freedom
    model therefore marks 45 degrees ``executable``, that angle cannot be
    realised by turning a built part; silently dropping it would leave the
    certificate claiming a searched-and-closed angle it never searched. The
    executable way to place geometry at 45 degrees is to *draw* it at 45
    degrees (a constructor primitive), not to rotate it.
    """
    non_orthogonal = [d for d in freedom_of_part.executable_deg if d % 90]
    if non_orthogonal:
        raise ValueError(
            f"part {freedom_of_part.part.name!r}: pack {freedom_of_part.part.layer_roles!r} "
            f"declares directions {non_orthogonal} executable, but a rigid part cannot be "
            "turned to a non-orthogonal angle on the nanometre lattice (the rotation group "
            "of a square lattice is the quarter turns and mirrors). Draw the geometry at "
            "that angle with a constructor instead of rotating this part to it.")
    out = []
    for mirrored in (False, True):
        if mirrored and "y" not in freedom_of_part.mirror_axes:
            continue
        for d in freedom_of_part.executable_deg:
            out.append((d, mirrored))
    return tuple(out)


def build_freedom(pack: Pack, parts: Iterable[Part]) -> Freedom:
    """Resolve every part against the pack."""
    by_name = {}
    for part in parts:
        if part.name in by_name:
            raise ValueError(f"duplicate part {part.name!r}")
        by_name[part.name] = part
    for part in by_name.values():
        for other in part.coupling:
            if other not in by_name:
                raise ValueError(f"part {part.name!r} couples to unknown part {other!r}")
    return Freedom(pack, by_name, {n: part_freedom(pack, p) for n, p in by_name.items()})


# ---------------------------------------------------------------------------
# enforcement
# ---------------------------------------------------------------------------


class FreedomViolation(ValueError):
    """An action plan asked for something the design does not allow."""


def enforce(freedom: Freedom, plan: Mapping[str, Action],
            predicates: Optional[Mapping[str, object]] = None) -> None:
    """Refuse an illegal action plan. Returns ``None`` when the plan is legal.

    Checks, in order:

    1. every named part exists and is usable;
    2. a rotation's angle is in the part's permitted set;
    3. a mirror's axis is permitted;
    4. a translation lands on the manufacturing grid;
    5. coupled parts received the same action;
    6. every named constraint predicate holds.

    Nothing is clamped or repaired: a violation raises. That is the point - a
    planner that goes outside the freedom model must be visible, because the
    failure it would otherwise cause (a locally optimal but globally wrong
    placement) is exactly what this work exists to prevent.
    """
    predicates = dict(predicates or {})
    for name, action in plan.items():
        pf = freedom.part(name)
        if not pf.usable:
            raise FreedomViolation(f"part {name!r} is not free to act: {pf.unknown_reason}")
        if action.op == ROTATE:
            # Checked against the *executable* set, which is the permitted set
            # narrowed by engine capability. The message distinguishes the two
            # cases so a 45-degree refusal says why, not merely "illegal".
            if action.degrees not in pf.executable_deg:
                verdict = pf.verdict(action.degrees)
                if verdict is not None and verdict.status == DECK_INFEASIBLE:
                    raise FreedomViolation(
                        f"part {name!r}: {action.degrees} degrees is permitted by the angle "
                        f"rules but proven infeasible ({verdict.reason})")
                if action.degrees in pf.rotations_deg:
                    raise FreedomViolation(
                        f"part {name!r}: {action.degrees} degrees is permitted by the rule "
                        f"pack but not executable by this engine ({pf.narrowed_reason})")
                raise FreedomViolation(
                    f"part {name!r} may rotate to {pf.executable_deg} degrees, "
                    f"not {action.degrees} (layers: {', '.join(pf.part.layer_roles)}; "
                    f"source: {pf.layer_freedom.source})")
        elif action.op == MIRROR:
            if action.axis not in pf.mirror_axes:
                raise FreedomViolation(
                    f"part {name!r} may mirror on {pf.mirror_axes or 'nothing'}, "
                    f"not {action.axis!r}")
        elif action.op == TRANSLATE:
            grid = freedom.pack.grid_nm
            if action.dx_nm % grid or action.dy_nm % grid:
                raise FreedomViolation(
                    f"part {name!r} translation ({action.dx_nm}, {action.dy_nm}) nm "
                    f"is off the {grid} nm grid")
    # Coupled parts move together.
    for name, pf in freedom.resolved.items():
        for other in pf.part.coupling:
            if name in plan and other in plan and plan[name] != plan[other]:
                raise FreedomViolation(
                    f"parts {name!r} and {other!r} are coupled and must receive the "
                    f"same action; got {plan[name]} and {plan[other]}")
            if name in plan and other not in plan:
                raise FreedomViolation(
                    f"part {name!r} is coupled to {other!r}, which received no action")
    # Named design predicates.
    needed = {c for pf in freedom.resolved.values() for c in pf.part.constraints
              if pf.part.name in plan}
    for constraint in sorted(needed):
        check = predicates.get(constraint)
        if check is None:
            raise FreedomViolation(
                f"constraint {constraint!r} was not supplied; a plan touching it "
                "cannot be certified")
        if not check(plan):
            raise FreedomViolation(f"constraint {constraint!r} does not hold for this plan")


__all__ = [
    "Action", "Part", "PartFreedom", "Freedom", "FreedomViolation", "AngleVerdict",
    "build_freedom", "part_freedom", "enforce", "executable_rotations", "orientations",
    "angle_verdicts", "grid_edge",
    "ROTATE", "MIRROR", "TRANSLATE",
    "EXECUTABLE", "DECK_FORBIDDEN", "DECK_INFEASIBLE", "UNKNOWN", "NOT_EXECUTABLE", "CLOSED",
    "GRID_UNREALIZABLE", "grid_direction", "executable_directions",
]
