"""PDK-neutral semantic rule pack: the only thing a layout primitive may read.

Why this module exists
----------------------
The layout primitives (``build_row``, ``contact_plan``, ``geometry_plan``,
``materialize``, ``verify``, ``turn``) encode *behaviour*: "a contacted
diffusion chain with one gate per channel, a landing pad per terminal, a well
around PMOS, an implant over every active island".  None of that behaviour is
specific to GF180.  What *is* specific is a set of numbers (contact is 220 nm
square, minimum N-channel length is 600 nm, gate pitch is 470 nm ...) and a set
of layer numbers.  Those numbers used to live in ``RULES``/``LAYERS`` inside
``clay.pdk.device_placement`` and were imported directly by the primitives,
which is exactly what made a primitive unable to run on another PDK.

This module separates the two.  A :class:`Pack` carries

  * ``layers``  semantic layer role -> GDS (layer, datatype)
  * ``r``       semantic rule key -> :class:`RuleValue` (value + provenance)
  * ``derived`` quantities computed from the rules (pad half-widths, gate
                access offsets ...) each with a recorded formula
  * ``markers`` which of the optional marker layers this PDK needs
  * ``model_polarity`` device model name -> polarity, and the minimum
                channel-length rule key per polarity

so a primitive can be written once and run on any PDK that supplies a pack.

Provenance is part of the data, not a comment
---------------------------------------------
Every value records where it came from:

  ``deck``        parsed from the foundry KLayout DRC deck (rule id recorded)
  ``derived``     computed from deck values by a named formula
  ``convention``  our own modelling choice where the deck is silent; the
                  value is a defensible default, labelled as such, and never
                  presented as a foundry requirement

``report()`` prints the breakdown so a reviewer can see exactly which numbers
are foundry facts and which are ours.

Adding a PDK is a data operation
--------------------------------
:func:`build_sky130_1v8` and :func:`build_gf180_5v` are both written against
the same semantic vocabulary.  A new PDK means a new builder returning a
:class:`Pack`; no primitive changes.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from math import ceil, gcd, sqrt
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


class Provenance:
    DECK = "deck"
    DERIVED = "derived"
    CONVENTION = "convention"


@dataclass(frozen=True)
class RuleValue:
    """One semantic quantity in nanometres (lengths) or nm^2 (areas)."""

    value: int
    provenance: str
    source: str          # rule id ("CO.1") or formula, for derived/convention

    def __post_init__(self):
        if type(self.value) is not int:
            raise TypeError(f"rule value must be an int in nm/nm^2, got {self.value!r}")
        if self.value < 0:
            raise ValueError(f"rule value must be non-negative: {self.value!r}")
        if self.provenance not in (Provenance.DECK, Provenance.DERIVED, Provenance.CONVENTION):
            raise ValueError(f"unknown provenance {self.provenance!r}")
        if not self.source:
            raise ValueError("every rule value needs a source (rule id or formula)")


# ---------------------------------------------------------------------------
# deck parsing (format-shared between gf180mcu and sky130)
# ---------------------------------------------------------------------------

# Both decks describe a rule with a KLayout ``.output`` whose description ends
# in the scalar::
#
#     m11_l1.output("M1.1", "M1.1 : min. metal1 width : 0.23um")     # gf180
#     m1.width(0.14).output("m1.1", "m1.1 : min. m1 width : 0.14um") # sky130
#     df9.output("DF.9_5V", "DF.9_5V : Min. COMP area (um2). : 0.2025um2")
#
# The unit may be written as ``um``, ``µm`` (micro sign) or ``μm`` (greek mu);
# an area rule appends ``2``/``²``.
_OUTPUT_RE = re.compile(
    r"""\.output\(\s*["'](?P<id>[^"']+)["']\s*,\s*["']
        [^"']*?:\s*
        (?:nil\s*,\s*)?
        (?P<val>\d+(?:\.\d+)?)\s*
        [\xb5μ u]?m
        (?P<area>2|\xb2|\^2)?
        \s*["']""",
    re.VERBOSE,
)


def parse_deck(path: str) -> Dict[str, Tuple[int, bool]]:
    """Parse one KLayout DRC deck into ``{rule_id: (value_nm, is_area)}``.

    ``value_nm`` is nm for length rules and nm^2 for area rules.  Rules whose
    description carries no trailing scalar (pure topology checks) are skipped:
    they have no number for us to bind.  The first occurrence of an id wins so
    ``_3.3V``/``_5V`` siblings stay distinct under their own ids.
    """
    text = _read(path)
    out: Dict[str, Tuple[int, bool]] = {}
    for m in _OUTPUT_RE.finditer(text):
        rid = m.group("id")
        if rid in out:
            continue
        val = float(m.group("val"))
        is_area = m.group("area") is not None
        out[rid] = (int(round(val * (1_000_000 if is_area else 1_000))), is_area)
    return out


def parse_deck_dir(directory: str) -> Dict[str, Tuple[int, bool]]:
    """Parse every ``*.drc`` under ``directory`` (recursively) into one table."""
    out: Dict[str, Tuple[int, bool]] = {}
    if not directory or not os.path.isdir(directory):
        return out
    for path in _deck_files(directory):
        for rid, value in parse_deck(path).items():
            out.setdefault(rid, value)
    return out


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


# Both decks state an angle rule as a ``with_angle`` check whose description
# names the *permitted* step::
#
#     comp.with_angle(0 .. 45).output("comp_angle", "ACUTE : non 45 degree angle comp")
#     diff.with_angle(0 ..  90).output("diff_angle", "x.2 : non 90 degree angle diff")
#
# "non 45 degree angle" means every edge must be a multiple of 45 degrees; a
# 90-only layer ("non 90 degree") permits rectangles only. The description is
# the unambiguous statement, so it is what we read; the ``with_angle`` literal
# is captured too and cross-checked against it.
_ANGLE_RE = re.compile(
    r"""\.with_angle\(\s*(?P<lo>\d+)\s*\.\.\s*(?P<hi>\d+)\s*\)
        \s*\.output\(\s*["'](?P<id>[^"']+)["']\s*,\s*["']
        [^"']*?:\s*
        (?:non\s+)?(?P<step>\d+)\s*degree\s+angle""",
    re.VERBOSE,
)

#: Token used in the decks for the angle check -> nothing semantic yet; the
#: pack builder maps each token to a layer role.
def parse_deck_angles(path: str) -> Dict[str, Tuple[int, int]]:
    """Parse angle rules from one deck into ``{token: (step_deg, hi_deg)}``.

    ``token`` is the ``*_angle`` id with the suffix stripped, i.e. the deck's own
    name for the layer. ``step_deg`` is the permitted edge-angle step: 45 means
    edges may sit at any multiple of 45 degrees, 90 means rectangles only.

    A token may appear more than once. sky130 states ``diff`` (and ``tap``)
    twice: ``with_angle(0..90)`` for ordinary geometry and ``with_angle(0..45)``
    inside ``uhvi``. Both rules must hold, so the permitted rotations are the
    *intersection* of the two sets: multiples of the **least common multiple**
    of the steps (90 for 45 and 90). Taking the first or the loosest match
    would let a region-specific relaxation leak into general geometry freedom.
    """
    out: Dict[str, Tuple[int, int]] = {}
    for m in _ANGLE_RE.finditer(_read(path)):
        token = m.group("id")
        if token.endswith("_angle"):
            token = token[: -len("_angle")]
        step, hi = int(m.group("step")), int(m.group("hi"))
        out[token] = _strictest(out.get(token), (step, hi))
    return out


def _strictest(a: Optional[Tuple[int, int]], b: Tuple[int, int]) -> Tuple[int, int]:
    """Combine two angle rules on one token: both hold, so take the lcm step."""
    if a is None:
        return b
    step = a[0] * b[0] // gcd(a[0], b[0])
    # ``hi`` is only used to cross-check one statement against its own
    # description; for a combined rule it is at least the combined step.
    return (step, max(a[1], b[1], step))


def _deck_files(directory: str):
    """Every deck file under ``directory``, in a fully deterministic order."""
    for root, dirs, names in os.walk(directory):
        dirs.sort()
        for name in sorted(names):
            if name.endswith((".drc", ".lydrc")):
                yield os.path.join(root, name)


def parse_deck_angles_dir(directory: str) -> Dict[str, Tuple[int, int]]:
    """Parse angle rules from every deck under ``directory``.

    A token stated in several files is combined exactly as within one file
    (:func:`_strictest`): every statement must hold, so the order in which the
    files are read cannot decide the answer.
    """
    out: Dict[str, Tuple[int, int]] = {}
    if not directory or not os.path.isdir(directory):
        return out
    for path in _deck_files(directory):
        for token, value in parse_deck_angles(path).items():
            out[token] = _strictest(out.get(token), value)
    return out


# A rule that fixes an edge length *exactly* is written as a check that flags
# every edge whose length differs from the value::
#
#     co1_l1 = contact.edges.without_length(0.22.um).extended(...)     # gf180
#     co1_l1.output("CO.1", "CO.1 : Min/max contact size. : 0.22µm")
#
#     rectLICON.not(rpm.or(urpm)).edges.without_length(0.17).output("licon.1_a/b", ...)  # sky130
#
# ``without_length(x)`` selects the edges that are NOT x long, so the rule says
# "every edge is exactly x". This is read from the *check expression*, not from
# the description, so it is the threshold the deck actually enforces.
#: ``<expr>.edges.without_length(<number>[.um])`` as the *outermost* check of a
#: statement: only an optional ``.extended(...)`` may follow before the
#: ``.output`` or the end of the line. A single value means an exact length;
#: ``without_length(nil, x)`` (a maximum only) does not match, and neither does
#: a ``without_length`` buried inside an enclosure check (sky130 via.4a).
_EXACT_CHECK_RE = re.compile(
    r"\.edges\.without_length\(\s*(?P<val>\d+(?:\.\d+)?)\s*(?:\.um)?\s*\)"
    r"\s*(?:\.extended\([^()]*\))?\s*(?=\.output\(|$)")
_ASSIGN_RE = re.compile(r"^[ \t]*(?P<var>\w+)[ \t]*=(?!=)")
_OUTPUT_ID_RE = re.compile(r"\.output\(\s*[\"'](?P<id>[^\"']+)[\"']")
_OUTPUT_OF_RE = re.compile(r"^[ \t]*(?P<var>\w+)\.output\(\s*[\"'](?P<id>[^\"']+)[\"']")


def parse_deck_exact_edges(path: str) -> Dict[str, int]:
    """``{rule_id: exact_edge_nm}`` for every rule that pins an edge length.

    Read line by line from the *check expressions*; comment lines are ignored
    (sky130 keeps a commented-out ``ct.1_a/b`` exact check, which is not a rule).
    Two forms are recognised: the check and its ``.output`` on one line, or the
    check assigned to a variable whose ``.output`` follows on a later line.
    """
    out: Dict[str, int] = {}
    assigned: Dict[str, int] = {}
    for line in _read(path).splitlines():
        code = line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        check = _EXACT_CHECK_RE.search(code)
        if check:
            value = int(round(float(check.group("val")) * 1000))
            output = _OUTPUT_ID_RE.search(code, check.end())
            if output:
                out.setdefault(output.group("id"), value)
                continue
            assign = _ASSIGN_RE.match(code)
            if assign:
                assigned[assign.group("var")] = value
            continue
        assign = _ASSIGN_RE.match(code)
        if assign:
            # A variable re-assigned to anything else no longer holds the check.
            assigned.pop(assign.group("var"), None)
        emitted = _OUTPUT_OF_RE.match(code)
        if emitted and emitted.group("var") in assigned:
            out.setdefault(emitted.group("id"), assigned[emitted.group("var")])
    return out


def parse_deck_exact_edges_dir(directory: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not directory or not os.path.isdir(directory):
        return out
    for path in _deck_files(directory):
        for rid, value in parse_deck_exact_edges(path).items():
            if rid in out and out[rid] != value:
                raise ValueError(f"exact-edge rule {rid!r} stated with two values: "
                                 f"{out[rid]} and {value} nm")
            out.setdefault(rid, value)
    return out


@dataclass(frozen=True)
class LayerFreedom:
    """What a layout engine is allowed to do to one layer role.

    Freedom is expressed as a **step in degrees**, not as a closed vocabulary of
    {45, 90}. A deck may state any permitted edge-angle increment
    (``with_angle(0 .. 45)`` / ``0 .. 90`` are the common ones, but nothing in
    the format restricts it to those), and a process that permits, say, 30 or 15
    degrees must be representable without a code change. The whole-shape
    rotations this role may take are therefore ``0, step, 2*step, ...``.

    The step must tile a full turn, otherwise the rotation set would not be
    closed: a caller could rotate past the last value and leave the set. That is
    a real constraint of the model, not a preference, and it is checked.
    """

    step_deg: int
    provenance: str
    source: str
    mirror: Tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.step_deg) is not int or self.step_deg < 1:
            raise ValueError(f"angle step must be a positive integer, got {self.step_deg!r}")
        if 360 % self.step_deg:
            raise ValueError(
                f"angle step {self.step_deg} does not tile a full turn "
                f"(360 % {self.step_deg} != 0); the rotation set would not be closed")

    @property
    def rotations_deg(self) -> Tuple[int, ...]:
        return tuple(range(0, 360, self.step_deg))

    def allows(self, degrees: int) -> bool:
        """Is a whole-shape rotation of ``degrees`` permitted by this rule?"""
        return type(degrees) is int and 0 <= degrees < 360 and degrees % self.step_deg == 0

    @property
    def allows_non_orthogonal(self) -> bool:
        """True when the deck permits any orientation beyond the quarter turns."""
        return any(d % 90 for d in self.rotations_deg)

    @property
    def allows_45(self) -> bool:
        return self.allows(45)



def _deck_sha(directory: str) -> str:
    """Stable hash over the deck files a pack was parsed from."""
    if not directory or not os.path.isdir(directory):
        return ""
    digest = hashlib.sha256()
    for root, _dirs, names in sorted(os.walk(directory)):
        for name in sorted(names):
            if not name.endswith((".drc", ".lydrc")):
                continue
            digest.update(os.path.relpath(os.path.join(root, name), directory).encode())
            digest.update(_read(os.path.join(root, name)).encode("utf-8", "replace"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# the pack
# ---------------------------------------------------------------------------

#: Every semantic layer role a layout primitive may name.  A pack must define
#: all of these except the optional marker roles listed in ``OPTIONAL_ROLES``.
REQUIRED_ROLES = (
    "active",     # diffusion / COMP / difftap
    "poly",       # gate / Poly2
    "contact",    # cut between active-or-poly and the first metal
    "metal1",     # first routing layer over the cut (M1 / li)
    "nwell",
)
OPTIONAL_ROLES = (
    "nplus", "pplus",  # implant / diffusion-select markers
    "dualgate",        # oxide marker (GF180); absent on many PDKs
    "v5_xtor",         # voltage marker (GF180 5 V); absent on most PDKs
    # The conductor above the first one. Present where the contact stack has
    # more than one metal level (sky130 licon/li then mcon/met1); absent on a
    # single-metal chain such as the GF180 contact/M1 stack this operator models.
    "metal2",
    # A second cut level, where the contact stack has more than one (sky130's
    # mcon between li and met1). Absent on single-cut stacks such as GF180.
    "cut2",
    # Threshold/well select layers that distinguish device flavours of one
    # polarity (sky130's hvtp marks an HVT PMOS). Declared per model below.
    "hvtp",
    # Low-Vt select (sky130 lvtn marks LVT NMOS and the LVT varactor).
    "lvtn",
    # Tap diffusion, where a process draws it on its own layer (sky130 65/44).
    # The MOS varactor channel is poly over n+ tap in an n-well.
    "tap",
    # Nitride poly cut: must enclose every poly contact (sky130 licon.15).
    "npc",
    # Poly-resistor body marker and resistor implant marker (sky130 high_po).
    "polyres", "rpm",
    # Upper metal stack used by a metal-insulator-metal capacitor.
    "metal3", "cut3", "metal4", "capm",
)

#: Device classes a netlist device may belong to, and the order in which a
#: SPICE instance lists the terminals of each class. The class of a *model* is
#: pack data (``Pack.device_classes``); the terminal order is a SPICE
#: convention shared by the supported PDKs, so it lives here once.
DEVICE_CLASS_TERMINALS = {
    "mos": ("d", "g", "s", "b"),
    "res_poly": ("a", "b", "sub"),
    "cap_mim": ("top", "bot"),
    "cap_var": ("top", "bot", "sub"),
}

#: Terminals of one device class that are physically interchangeable, mapped to
#: the shared name they are compared under. A netlist may list either end first
#: (a MOS's source and drain, a resistor's two ends), so anything that reasons
#: about a net's role must not distinguish them. Same rule as
#: clay.learning.symmetry, stated once as data.
INTERCHANGEABLE_TERMINALS = {
    "mos": {"d": "sd", "s": "sd"},
    "res_poly": {"a": "ends", "b": "ends"},
}

#: Semantic rule keys a primitive may read.  Grouped by what the primitive is
#: asking about, not by which layer it happens to land on.
REQUIRED_KEYS = (
    # grid / discretisation
    "grid",                    # layout grid in nm
    # cut
    "cut",                     # cut square size
    "cut_space",               # cut-to-cut spacing (pitch = cut + cut_space)
    "cut_to_active",           # active enclosure of the cut
    "cut_to_poly",             # poly enclosure of the cut
    "cut_to_gate",             # cut-to-gate(channel) spacing on active
    "gate_cut_to_active",      # gate cut to active (a gate cut sits off-channel)
    # first metal
    "metal_width",
    "metal_space",
    "metal_area",              # minimum metal area
    "metal_over_cut",          # metal enclosure of the cut
    # active / device
    "active_space",
    "sd_overhang",             # active extension beyond the gate
    "width_min",
    # poly
    "poly_width",
    "poly_space",
    "poly_endcap",             # poly extension beyond the active
    # well
    "well_over_active",        # well encloses its own active island
    "well_to_opposite_active", # well to active of the opposite polarity
    "well_space",              # well to well (same or different potential)
    "well_width",
    # implant
    "implant_over_active",
    "implant_space",
    "implant_area",
    "implant_to_opposite_active",
)


@dataclass(frozen=True)
class Pack:
    """A PDK reduced to the semantic quantities layout primitives consume."""

    pdk_id: str
    family: str                       # human label, e.g. "GF180MCU 5V"
    grid_nm: int
    layers: Mapping[str, Tuple[int, int]]
    r: Mapping[str, RuleValue]
    derived: Mapping[str, RuleValue]
    markers: Tuple[str, ...] = ()     # optional marker roles this PDK inserts
    model_polarity: Mapping[str, str] = field(default_factory=dict)
    min_length: Mapping[str, RuleValue] = field(default_factory=dict)
    #: Extra quantities only some PDKs need, keyed by free-form name.
    extra: Mapping[str, int] = field(default_factory=dict)
    deck_sha256: str = ""
    source: str = ""
    #: Prefix used in contract recipe names. Kept distinct from ``pdk_id`` so
    #: existing experiment artefacts keep their recorded recipe string.
    recipe_prefix: str = ""
    #: Net-name text layers, mapped to the conductor role each one labels.
    #: The text layer is NOT derivable from the drawing layer (GF180 labels
    #: metal1 at 34/10 over 34/0; sky130 labels li at 67/5 over 67/20), so it is
    #: declared data. Primitives and observations never spell these numbers.
    annotation_layers: Mapping[str, Tuple[int, int]] = field(
        default_factory=lambda: {"metal1": (34, 10)})
    #: The cell-boundary layer. Also declared data: GF180 uses 0/0, sky130 uses
    #: 236/0 (prBoundary). Writing GF180's 0/0 on a sky130 layout puts the frame
    #: on a layer the process does not read as a boundary.
    boundary_layer: Tuple[int, int] = (0, 0)
    #: Which conductor roles a cut connects, keyed by cut role. Expresses the
    #: contact stack without naming a process (gf180: contact -> metal1;
    #: sky130: licon -> li, mcon -> li -> metal1).
    cut_stack: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    #: Unit convention of the CDL this PDK's SPICE export uses for W/L. Only an
    #: adapter hint: geometry must still confirm every converted dimension.
    cdl_units: str = "metre"
    #: Parameters a CDL instance may carry beyond W/L/M/NF. Anything listed here
    #: is preserved verbatim, never silently mapped onto W/L.
    cdl_extra_params: Tuple[str, ...] = ()
    #: Device marker layers a valid instance must be covered by, keyed by role.
    #: An empty tuple means this process adds no such marker.
    device_markers: Tuple[str, ...] = ()
    #: Extra marker each *model* must carry, keyed by model name. This is how a
    #: flavour (HVT, LVT, ...) is distinguished without a per-PDK if-branch.
    model_markers: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    #: Per-layer-role geometric freedom, parsed from the deck's angle rules.
    #: A role absent from this map has no angle rule in the deck: its freedom is
    #: unknown, and callers must treat that as "no free rotation", not as "any".
    freedom: Mapping[str, LayerFreedom] = field(default_factory=dict)
    #: What this engine can *verify*: ``rotate_orthogonal`` (quarter turns) and,
    #: when added, ``rotate_non_orthogonal`` (any other permitted step). A
    #: freedom the engine cannot check is declared but withheld, never rounded.
    capability: FrozenSet[str] = frozenset({"rotate_orthogonal"})
    #: Layer roles whose every edge the deck pins to one exact length (a cut
    #: that must be an ``n x n`` square), read from ``edges.without_length``
    #: checks. This is what lets :mod:`clay.learning.freedom` *prove* that a
    #: non-orthogonal placement of a part containing that cut has no legal
    #: realisation on the grid, instead of merely declining to try it.
    exact_edges: Mapping[str, RuleValue] = field(default_factory=dict)
    #: Why quarter turns and reflections of a DRC-clean rigid part stay
    #: DRC-clean on this process (empty = not declared, then orthogonal moves
    #: fall back to the per-layer angle rules). The argument is that every rule
    #: the engine draws against is a width/space/enclosure/area/angle-set rule,
    #: all of which are invariant under those isometries about a grid point;
    #: the declaration must name any direction-dependent rule it excludes.
    orthogonal_isometries: str = ""
    #: Directions (degrees) the engine can ``construct``, ``place`` and
    #: ``verify`` on this process. A direction is executable only when all
    #: three hold. Empty falls back to the ``capability`` flags. Today every
    #: constructor, the placement model and the local verifier are axis-aligned,
    #: so all three are the quarter turns; a 45-degree constructor, an octagon
    #: placement model and a diagonal-aware verifier would each extend its list.
    direction_capability: Mapping[str, Tuple[int, ...]] = field(default_factory=dict)
    #: Device class of every model this pack can construct (``mos``,
    #: ``res_poly``, ``cap_mim``, ``cap_var``; see ``DEVICE_CLASS_TERMINALS``).
    #: A MOS model bound in ``model_polarity`` is ``mos`` without being listed.
    device_classes: Mapping[str, str] = field(default_factory=dict)
    #: Layers that only frame a cell (boundary/prBoundary drawings). They are
    #: excluded when a layout's *material* extent is measured.
    frame_layers: Tuple[Tuple[int, int], ...] = ()
    #: How this process builds a contacted diffusion chain of a device class
    #: that is not a plain MOS (step S3, design doc §5): ``diffusion_role``
    #: (the layer role the diffusion is drawn on) and ``rule_overrides`` (rule
    #: key -> the pack key that replaces it; applied only where the pack
    #: defines the replacement). A class absent here is drawn on ``active``
    #: with the pack's own rules. This is process knowledge - a varactor is n+
    #: tap in an n-well on SKY130 - so it lives in the pack, not in the flow.
    chain_construction: Mapping[str, Mapping[str, object]] = field(default_factory=dict)

    def device_class(self, model: str) -> str:
        """Class of a netlist model; unknown models fail closed."""
        if model in self.device_classes:
            return self.device_classes[model]
        if model in self.model_polarity:
            return "mos"
        raise KeyError(f"{self.pdk_id}: device model {model!r} has no device class; "
                       f"known: {sorted(set(self.device_classes) | set(self.model_polarity))}")

    # -- validation ---------------------------------------------------------
    def __post_init__(self):
        if not self.pdk_id:
            raise ValueError("pack needs a pdk_id")
        if not self.recipe_prefix:
            object.__setattr__(self, "recipe_prefix", self.pdk_id)
        if type(self.grid_nm) is not int or self.grid_nm <= 0:
            raise ValueError("grid_nm must be a positive int")
        for role in REQUIRED_ROLES:
            if role not in self.layers:
                raise ValueError(f"{self.pdk_id}: required layer role {role!r} missing")
        for role in self.markers:
            if role not in self.layers:
                raise ValueError(f"{self.pdk_id}: declared marker {role!r} has no layer")
        for role in self.layers:
            if role not in REQUIRED_ROLES and role not in OPTIONAL_ROLES:
                raise ValueError(f"{self.pdk_id}: unknown layer role {role!r}")
        for key in REQUIRED_KEYS:
            if key not in self.r:
                raise ValueError(f"{self.pdk_id}: required rule {key!r} missing")
            self.r[key]  # touch: raises if the entry is malformed
        for key in self.derived:
            if key in self.r:
                raise ValueError(f"{self.pdk_id}: {key!r} cannot be both a rule and derived")
        for kind in ("nmos", "pmos"):
            if kind not in self.min_length:
                raise ValueError(f"{self.pdk_id}: min_length missing for {kind!r}")

    # -- convenience accessors ---------------------------------------------
    def nm(self, key: str) -> int:
        """Integer nanometres for a semantic rule or derived quantity."""
        try:
            return self.r[key].value
        except KeyError:
            pass
        try:
            return self.derived[key].value
        except KeyError:
            raise KeyError(
                f"{self.pdk_id} has no rule/derived quantity {key!r}; "
                f"known rules: {sorted(self.r)}; derived: {sorted(self.derived)}"
            ) from None

    def rv(self, key: str) -> RuleValue:
        return self.r.get(key) or self.derived[key]

    def has(self, key: str) -> bool:
        return key in self.r or key in self.derived

    def layer(self, role: str) -> Tuple[int, int]:
        try:
            return self.layers[role]
        except KeyError:
            raise KeyError(f"{self.pdk_id} has no layer role {role!r}") from None

    def polarity(self, model: str) -> str:
        try:
            return self.model_polarity[model]
        except KeyError:
            raise KeyError(
                f"{self.pdk_id}: device model {model!r} is not bound to a polarity; "
                f"known: {sorted(self.model_polarity)}"
            ) from None

    def length_rule(self, kind: str) -> int:
        return self.min_length[kind].value

    def recipe(self, kind: str) -> str:
        """Contract recipe name for this process, e.g. ``gf180_5v_learned_contacts_v1``."""
        if kind not in ("contacts", "geometry"):
            raise ValueError(f"unknown recipe kind {kind!r}")
        return f"{self.recipe_prefix}_learned_{kind}_v1"

    def pitch(self) -> int:
        """Cut pitch along the active height."""
        return self.nm("cut") + self.nm("cut_space")

    # -- honesty report -----------------------------------------------------
    def report(self) -> Dict[str, object]:
        counts = {Provenance.DECK: 0, Provenance.DERIVED: 0, Provenance.CONVENTION: 0}
        conventions = []
        for key, value in list(self.r.items()) + list(self.derived.items()):
            counts[value.provenance] += 1
            if value.provenance == Provenance.CONVENTION:
                conventions.append({"key": key, "value_nm": value.value, "why": value.source})
        return {
            "pdk_id": self.pdk_id,
            "family": self.family,
            "grid_nm": self.grid_nm,
            "rules_total": len(self.r) + len(self.derived),
            "by_provenance": counts,
            "conventions": conventions,
            "markers": list(self.markers),
            "layers": {k: list(v) for k, v in sorted(self.layers.items())},
            "deck_sha256": self.deck_sha256,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# derived quantities, shared by every PDK
# ---------------------------------------------------------------------------


def _grid_up(value: int, grid: int) -> int:
    return int(ceil(value / grid)) * grid


#: Deck token -> semantic role. The decks name layers in their own vocabulary
#: (gf180: comp/poly2; sky130: diff/poly/m1), so this is the one place that
#: translation lives. A role missing here simply gets no freedom entry.
_ANGLE_TOKEN_ROLES = {
    "gf180mcu_5v": {
        "comp": "active", "poly2": "poly", "contact": "contact",
        "metal1": "metal1", "metal2": "metal2",
        "nwell": "nwell", "nplus": "nplus", "pplus": "pplus",
        "dualgate": "dualgate", "v5_xtor": "v5_xtor",
    },
    "gf180mcu_3v3": {
        "comp": "active", "poly2": "poly", "contact": "contact",
        "metal1": "metal1", "metal2": "metal2",
        "nwell": "nwell", "nplus": "nplus", "pplus": "pplus",
    },
    "sky130_1v8": {
        # Each token is the deck's own name for the layer the semantic role is
        # drawn on (see SKY130_LAYERS). ``metal1`` is li (67/20) and
        # ``metal2`` is met1 (68/20) in this pack, so the deck's ``li`` and
        # ``m1`` tokens map onto those roles respectively - mapping ``m1`` onto
        # ``metal1`` (as before) read met1's rule for the li layer.
        "diff": "active", "tap": "tap", "poly": "poly", "licon": "contact",
        "li": "metal1",
        # The mcon angle rule is published under the id "ct_angle", not
        # "mcon_angle"; the deck's own naming, not ours, decides the token.
        "ct": "cut2", "m1": "metal2",
        "via3": "cut3", "m3": "metal3", "m4": "metal4",
        "nwell": "nwell", "nsdm": "nplus", "psdm": "pplus", "hvtp": "hvtp",
        "lvtn": "lvtn", "npc": "npc", "rpm": "rpm",
        # polyres (66/13) and capm (89/44) have no angle rule in the deck:
        # their non-orthogonal freedom stays unknown, on purpose.
    },
}


def _freedom(pack_id: str, layers: Mapping[str, Tuple[int, int]],
             angles: Mapping[str, Tuple[int, int]]) -> Dict[str, LayerFreedom]:
    """Map parsed deck angle rules onto semantic layer roles.

    A role with no angle rule in the deck gets **no** entry: its freedom is
    unknown. Callers must read a missing entry as "not free to rotate", never
    as "any rotation", so an unstated rule cannot become permissiveness.

    Several deck tokens may land on one role. Every one of their rules applies
    to that role, so the role's step is the lcm of theirs and all sources are
    recorded; the result does not depend on the order of the token table.
    """
    tokens = _ANGLE_TOKEN_ROLES.get(pack_id, {})
    steps: Dict[str, int] = {}
    sources: Dict[str, list] = {}
    for token, role in tokens.items():
        if role not in layers or token not in angles:
            continue
        step, hi = angles[token]
        if step > hi:
            raise ValueError(
                f"{pack_id}:{token} angle rule says {step} degree edges but "
                f"with_angle upper bound is {hi}; deck is inconsistent")
        prev = steps.get(role)
        steps[role] = step if prev is None else prev * step // gcd(prev, step)
        sources.setdefault(role, []).append(f"deck {token}_angle")
    return {role: LayerFreedom(step, Provenance.DECK, " + ".join(sorted(sources[role])))
            for role, step in steps.items()}


def _exact_edges(deck_exact: Mapping[str, int], layers: Mapping[str, Tuple[int, int]],
                 bindings: Mapping[str, str]) -> Dict[str, RuleValue]:
    """Bind ``role -> exact-edge rule id`` against the parsed exact-edge checks.

    Unlike a numeric rule there is no fallback: an exact-edge claim is only
    made when the deck's check expression says so. A binding whose rule id is
    absent is simply not recorded (the proof it would enable is not made).
    """
    out: Dict[str, RuleValue] = {}
    for role, rule_id in bindings.items():
        if role in layers and rule_id in deck_exact:
            out[role] = RuleValue(deck_exact[rule_id], Provenance.DECK,
                                  f"{rule_id} (edges.without_length: every edge exactly "
                                  f"{deck_exact[rule_id]} nm)")
    return out


#: What the engine can execute today: axis-aligned construction, placement and
#: verification only. Shared by every pack; a process gains a direction only
#: when all three abilities are implemented for it.
ORTHOGONAL_ENGINE = {"construct": (0, 90, 180, 270), "place": (0, 90, 180, 270),
                     "verify": (0, 90, 180, 270)}


#: Why quarter turns and mirrors preserve legality on the two foundry decks.
#: Each statement names the direction-dependent rules found in the deck and
#: why the engine is outside their scope (checked by searching every
#: ``.output`` description for horizontal/vertical/direction/orient/parallel/
#: perpendicular; the device-relative ones are invariant by construction).
_ISOMETRY_GF180 = (
    "quarter turns and reflections about a grid point preserve every width, "
    "space, enclosure, area, grid and angle-set rule; the deck's direction words "
    "(DF/NP/PP/MDN/MDP/ESD/HRES/LRES/PRES/EF 'width direction', 'parallel to gate', "
    "'perpendicular to Poly2') are relative to the transistor and rotate with it; "
    "the one absolute rule, O.PL.ORT (gate width along X), is scoped to otp_mk, a "
    "layer this engine never draws. Official DRC remains the arbiter.")
_ISOMETRY_SKY130 = (
    "quarter turns and reflections about a grid point preserve every width, "
    "space, enclosure, area, grid and angle-set rule; no rule description in the "
    "sky130A deck names an absolute direction. Official DRC remains the arbiter.")



def _derive(pack_id: str, r: Mapping[str, RuleValue], grid: int) -> Dict[str, RuleValue]:
    """Compute the pad/access quantities the primitives need, from the rules.

    Every formula below is written in terms of semantic rules only, so it holds
    for any PDK that supplies those rules.  The GF180 numbers reproduce the
    hand-written constants in the original ``build_row`` exactly:

        pad_half_x        170 = cut_half 110 + metal_over_cut 60
        pad_half_y        215 = grid_up(ceil(metal_area / 2*pad_half_x) / 2)
        gate_pad_half     190 = grid_up(sqrt(metal_area) / 2)
        poly_over_cut     180 = cut_half 110 + cut_to_poly 70
        gate_clear_active 280 = poly_endcap 220 + metal_over_cut 60
        first_sd_contact  180 = cut_to_active 70 + cut_half 110
    """
    def v(key: str) -> int:
        return r[key].value

    cut_half = v("cut") // 2
    metal_area = v("metal_area")

    pad_half_x = cut_half + v("metal_over_cut")
    # A landing pad must reach the minimum metal area; height drives the rest.
    full_height = ceil(metal_area / (2 * pad_half_x)) if pad_half_x else metal_area
    pad_half_y = _grid_up(max(pad_half_x, ceil(full_height / 2)), grid)

    gate_pad_half = _grid_up(max(cut_half + v("metal_over_cut"), ceil(sqrt(metal_area) / 2)), grid)

    derived = {
        "cut_half": RuleValue(cut_half, Provenance.DERIVED, "cut // 2"),
        "cut_pitch": RuleValue(v("cut") + v("cut_space"), Provenance.DERIVED, "cut + cut_space"),
        "pad_half_x": RuleValue(
            pad_half_x, Provenance.DERIVED, "cut//2 + metal_over_cut"),
        "pad_half_y": RuleValue(
            pad_half_y, Provenance.DERIVED,
            "grid_up(ceil(metal_area / (2*pad_half_x)) / 2)  [metal_area minimum]"),
        "gate_pad_half": RuleValue(
            gate_pad_half, Provenance.DERIVED, "grid_up(sqrt(metal_area) / 2)  [metal_area minimum]"),
        "poly_over_cut": RuleValue(
            cut_half + v("cut_to_poly"), Provenance.DERIVED, "cut//2 + cut_to_poly"),
        "gate_clear_active": RuleValue(
            max(v("poly_endcap") + v("metal_over_cut"),
                v("gate_cut_to_active") + cut_half),
            Provenance.DERIVED,
            "max(poly_endcap + metal_over_cut, gate_cut_to_active + cut//2)  "
            "[end cap and gate-cut-to-active must both hold]"),
        "first_sd_contact_x": RuleValue(
            v("cut_to_active") + cut_half, Provenance.DERIVED, "cut_to_active + cut//2"),
        "side_extension": RuleValue(
            max(v("sd_overhang"), v("cut_to_active") + v("cut") + v("cut_to_gate")),
            Provenance.DERIVED, "max(sd_overhang, cut_to_active + cut + cut_to_gate)"),
        # Two S/D landing pads across one gate must keep metal spacing. In a
        # short-channel process the rule-based side extension can be too small
        # for that, so the constructor raises it using this width budget:
        #   width - 2*first_sd_contact_x - 2*pad_half_x >= metal_space
        # A PDK-independent geometric obligation, not a process constant.
        "pad_spacing_width": RuleValue(
            2 * (v("cut_to_active") + cut_half) + 2 * pad_half_x + v("metal_space"),
            Provenance.DERIVED,
            "2*first_sd_contact_x + 2*pad_half_x + metal_space  [pads across one gate]"),
        "shared_segment": RuleValue(
            2 * v("cut_to_gate") + v("cut"), Provenance.DERIVED, "2*cut_to_gate + cut"),
        # Two rows of opposite polarity must clear each other on *both* the well
        # and the implant layers, because both extend past the diffusion and
        # both have an opposite-polarity spacing rule. The well needs
        # (well_over_active + well_to_opposite_active); the implant needs
        # (implant_over_active + implant_to_opposite_active). The binding is
        # whichever is larger -- on sky130 that is the implant (760 vs 520), on
        # GF180 the well (1200 vs 390). Using only the well term made sky130
        # rows collide once the well rule was bound correctly.
        "opposite_row_clearance": RuleValue(
            max(v("well_over_active") + v("well_to_opposite_active"),
                v("implant_over_active") + v("implant_to_opposite_active")),
            Provenance.DERIVED,
            "max(well_over_active + well_to_opposite_active, "
            "implant_over_active + implant_to_opposite_active)"),
    }
    del pack_id
    return derived


# ---------------------------------------------------------------------------
# builder helper
# ---------------------------------------------------------------------------


def _rules(table: Mapping[str, RuleValue]) -> Dict[str, RuleValue]:
    for key, value in table.items():
        if not isinstance(value, RuleValue):
            raise TypeError(f"{key}: expected RuleValue, got {type(value).__name__}")
    return dict(table)


def _deck_or(deck: Mapping[str, Tuple[int, bool]], rule_id: str, key: str,
             fallback: Optional[Tuple[int, str]] = None) -> RuleValue:
    """A rule value read from the parsed deck, else a labelled fallback.

    The deck value wins whenever ``rule_id`` exists, so binding a key to the
    wrong id cannot be rescued by its fallback. The one mismatch the parser can
    detect is dimensional: an ``*_area`` key must bind an area rule (nm^2) and
    every other key a length rule (nm). Anything else is refused.
    """
    if rule_id in deck:
        value, is_area = deck[rule_id]
        if is_area != key.endswith("_area"):
            raise ValueError(
                f"{key!r} bound to {rule_id!r}, which is a "
                f"{'area' if is_area else 'length'} rule; the key expects a "
                f"{'area' if key.endswith('_area') else 'length'}")
        return RuleValue(value, Provenance.DECK, rule_id)
    if fallback is None:
        raise KeyError(f"rule {rule_id!r} for {key!r} not found in deck and no fallback given")
    value, why = fallback
    return RuleValue(value, Provenance.CONVENTION, f"deck silent on {rule_id}; {why}")


def _implied_area(deck: Mapping[str, Tuple[int, bool]], width_rule: str, key: str,
                  fallback_width: int) -> RuleValue:
    """Area floor implied by a min-width rule: width in both axes => width^2."""
    width = _deck_or(deck, width_rule, key + "_width", (fallback_width, f"{width_rule} min width"))
    return RuleValue(width.value * width.value, Provenance.DERIVED,
                     f"{width_rule}^2 (no deck area rule; implied by min width "
                     f"{width.value} nm, source {width.provenance})")


# ---------------------------------------------------------------------------
# GF180MCU 5 V
# ---------------------------------------------------------------------------

GF180_DECK_DIR = os.path.join(
    "pdks", "gf180mcu", "libraries", "gf180mcu_fd_pr", "latest",
    "rules", "klayout", "drc", "rule_decks",
)

GF180_LAYERS = {
    "active": (22, 0), "poly": (30, 0), "contact": (33, 0), "metal1": (34, 0),
    "nwell": (21, 0), "nplus": (32, 0), "pplus": (31, 0),
    "dualgate": (55, 0), "v5_xtor": (112, 1),
}


#: Cut roles whose edge length the deck pins exactly, and the rule that does.
GF180_EXACT_EDGES = {"contact": "CO.1"}


def build_gf180_5v(*, deck_dir: Optional[str] = None) -> Pack:
    """GF180MCU 5 V pack. Deck values are parsed when the decks are present."""
    root = deck_dir if deck_dir is not None else _find_project_path(GF180_DECK_DIR)
    deck = parse_deck_dir(root) if root else {}
    angles = parse_deck_angles_dir(root) if root else {}
    exact = parse_deck_exact_edges_dir(root) if root else {}

    def rule(key: str, rule_id: str, fallback: Optional[Tuple[int, str]] = None) -> RuleValue:
        # A hand-written fallback reproduces the consolidated constant that was
        # in device_placement's old rule table before this module existed, so a
        # missing deck degrades to the same numbers rather than to a guess.
        return _deck_or(deck, rule_id, key, fallback)

    r = _rules({
        "grid": RuleValue(5, Provenance.CONVENTION, "5 nm layout grid (deck x.1b ongrid 0.005)"),
        "cut": rule("cut", "CO.1", (220, "CO.1 Min/max contact size")),
        "cut_space": rule("cut_space", "CO.2a", (250, "CO.2a min contact spacing")),
        # CO.4 is "COMP overlap of contact" and CO.3 "Poly2 overlap of contact"
        # (deck text). They were bound the other way round; both are 70 nm, so
        # the swap changes provenance only, not geometry.
        "cut_to_active": rule("cut_to_active", "CO.4", (70, "CO.4 COMP overlap of contact")),
        "cut_to_poly": rule("cut_to_poly", "CO.3", (70, "CO.3 Poly2 overlap of contact")),
        "cut_to_gate": rule("cut_to_gate", "CO.7", (150, "CO.7 contact to gate spacing")),
        "gate_cut_to_active": rule("gate_cut_to_active", "CO.8",
                                   (170, "CO.8 poly contact to COMP spacing")),
        "metal_width": rule("metal_width", "M1.1", (230, "M1.1 min metal1 width")),
        "metal_space": rule("metal_space", "M1.2a", (230, "M1.2a min metal1 spacing")),
        "metal_area": rule("metal_area", "M1.3", (144400, "M1.3 min metal1 area")),
        # CO.6a/b give 60 nm; the original recipe documented 60 as conservative.
        "metal_over_cut": rule("metal_over_cut", "CO.6a", (60, "CO.6a metal1 enclosure of contact")),
        "active_space": rule("active_space", "DF.3a_5V", (360, "DF.3a_5V min COMP spacing")),
        "sd_overhang": rule("sd_overhang", "DF.6_5V", (400, "DF.6_5V COMP extend beyond gate")),
        "width_min": rule("width_min", "DF.1a_5V", (300, "DF.1a_5V min COMP width")),
        "poly_width": rule("poly_width", "PL.1_5V", (200, "PL.1_5V Poly2 width")),
        "poly_space": rule("poly_space", "PL.3a_5V", (240, "PL.3a_5V Poly2 space on COMP")),
        "poly_endcap": rule("poly_endcap", "PL.4_5V", (220, "PL.4_5V Poly2 end cap")),
        # N-well encloses its own active islands by DF.16_5V (nwell to ncomp);
        # two rows of opposite polarity need that on each side of the boundary.
        "well_over_active": rule("well_over_active", "NW.2a_5V",
                                 (600, "nwell enclosure of own COMP (modelling choice)")),
        "well_to_opposite_active": rule("well_to_opposite_active", "DF.16_5V",
                                        (600, "DF.16_5V nwell to ncomp space")),
        "well_space": rule("well_space", "NW.2a_5V", (740, "NW.2a_5V nwell spacing")),
        "well_width": rule("well_width", "NW.1a_5V", (860, "NW.1a_5V nwell width")),
        # Field poly under a gate must keep clear of neighbouring active.
        "field_poly_active": rule("field_poly_active", "PL.5a_5V",
                                  (300, "PL.5a_5V Poly2 space on COMP/Field")),
        "implant_over_active": rule("implant_over_active", "NP.5a",
                                    (230, "NP.5a implant overlap of channel")),
        "implant_space": rule("implant_space", "NP.2", (400, "NP.2 implant spacing")),
        "implant_area": rule("implant_area", "NP.8a", (350000, "NP.8a min implant area")),
        "implant_to_opposite_active": rule("implant_to_opposite_active", "NP.3a",
                                           (160, "NP.3a implant to opposite active")),
        "oxide_over_active": rule("oxide_over_active", "DV.6", (240, "DV.6 dualgate encloses COMP")),
        "oxide_over_poly": rule("oxide_over_poly", "DV.8", (400, "DV.8 dualgate encloses Poly2")),
        "oxide_width": rule("oxide_width", "DV.5", (700, "DV.5 dualgate width")),
    })
    # The well-to-well spacing the original recipe used between the two rows is
    # the N-well/COMP rule (DF.16), not the N-well/N-well rule (NW.2a).  Keep it
    # as its own semantic key so a PDK with a different pairing can differ.
    r["well_to_active"] = rule("well_to_active", "DF.16_5V", (600, "DF.16_5V nwell to ncomp space"))
    r["well_over_active"] = r["well_to_active"]
    # Routed field poly to COMP: the same deck rule as field_poly_active, under
    # the routing key every pack spells the same way (clay.routing.rules).
    r["poly_to_active"] = r["field_poly_active"]

    return Pack(
        pdk_id="gf180mcu_5v",
        family="GF180MCU 5 V (single finger, contacted)",
        grid_nm=r["grid"].value,
        layers=dict(GF180_LAYERS),
        r=r,
        derived=_derive("gf180mcu_5v", r, r["grid"].value),
        markers=("dualgate", "v5_xtor"),
        model_polarity={"nfet_05v0": "nmos", "pfet_05v0": "pmos"},
        min_length={
            # PL.2_5V states the 5 V channel-length rule but the deck encodes the
            # numbers in code (0.6 um N / 0.5 um P) rather than in the output
            # description, so the parser cannot recover them from the text.
            # They are recorded here as consolidated values, not as a guess.
            "nmos": RuleValue(600, Provenance.DECK,
                              "PL.2_5V Gate Width (Channel Length): 0.6 um (pl_2_5v_n)"),
            "pmos": RuleValue(500, Provenance.DECK,
                              "PL.2_5V Gate Width (Channel Length): 0.5 um (pl_2_5v_p)"),
        },
        extra={"well_nactive_space": 600},
        deck_sha256=_deck_sha(root) if root else "",
        source=root or "<deck not found: consolidated constants>",
        recipe_prefix="gf180_5v",
        annotation_layers={"metal1": (34, 10)},
        # GF180's cell boundary is drawn on 0/0.
        boundary_layer=(0, 0),
        # One cut role: a contact joins the diffusion/poly conductor to metal1.
        cut_stack={"contact": ("sd", "poly", "metal1")},
        cdl_units="metre",
        cdl_extra_params=(),
        device_markers=("dualgate", "v5_xtor"),
        model_markers={},
        freedom=_freedom("gf180mcu_5v", GF180_LAYERS, angles),
        exact_edges=_exact_edges(exact, GF180_LAYERS, GF180_EXACT_EDGES),
        orthogonal_isometries=_ISOMETRY_GF180,
        direction_capability=ORTHOGONAL_ENGINE,
        # The engine verifies axis-aligned geometry only, so a 45-degree
        # freedom present in the deck is declared but not yet usable.
        # ``structural_lowering`` means a verified construction provider exists
        # for this process; callers gate on this, not on the pdk_id string.
        capability=frozenset({"rotate_orthogonal", "structural_lowering"}),
    )


# ---------------------------------------------------------------------------
# GF180MCU 3.3 V
# ---------------------------------------------------------------------------

#: Rule keys whose GF180 deck id depends on the voltage class. Every other key
#: (cut, metal1, implant) is a single process-wide rule shared with the 5 V pack.
_GF180_3V3_RULES = {
    "active_space": ("DF.3a_3.3V", 280, "DF.3a_3.3V min COMP spacing"),
    "sd_overhang": ("DF.6_3.3V", 240, "DF.6_3.3V COMP extend beyond gate"),
    "width_min": ("DF.1a_3.3V", 220, "DF.1a_3.3V min COMP width"),
    "poly_width": ("PL.1_3.3V", 180, "PL.1_3.3V Poly2 width"),
    "poly_space": ("PL.3a_3.3V", 240, "PL.3a_3.3V Poly2 space on COMP"),
    "poly_endcap": ("PL.4_3.3V", 220, "PL.4_3.3V Poly2 end cap"),
    # DF.4c: N-well overlap of PCOMP. The 5 V pack binds DF.16 (same 600 nm
    # value there); at 3.3 V both are 430 nm, the enclosure rule is the right id.
    "well_over_active": ("DF.4c_3.3V", 430, "DF.4c_3.3V Nwell overlap of PCOMP"),
    "well_to_opposite_active": ("DF.16_3.3V", 430, "DF.16_3.3V nwell to ncomp space"),
    "well_space": ("NW.2a_3.3V", 600, "NW.2a_3.3V nwell spacing"),
    "well_width": ("NW.1a_3.3V", 860, "NW.1a_3.3V nwell width"),
    "field_poly_active": ("PL.5a_3.3V", 100, "PL.5a_3.3V Poly2 space to COMP"),
}


def build_gf180_3v3(*, deck_dir: Optional[str] = None) -> Pack:
    """GF180MCU 3.3 V pack (``nfet_03v3`` / ``pfet_03v3``).

    Same layer map and process-wide cut/metal/implant rules as the 5 V pack;
    the voltage-class rules come from the ``_3.3V`` deck ids. 3.3 V devices are
    drawn *outside* DUALGATE, so this pack declares no oxide or 5 V marker.
    """
    root = deck_dir if deck_dir is not None else _find_project_path(GF180_DECK_DIR)
    deck = parse_deck_dir(root) if root else {}
    angles = parse_deck_angles_dir(root) if root else {}
    exact = parse_deck_exact_edges_dir(root) if root else {}
    base = build_gf180_5v(deck_dir=deck_dir)
    r = {k: v for k, v in base.r.items()
         if k not in ("oxide_over_active", "oxide_over_poly", "oxide_width")}
    for key, (rule_id, fallback, why) in _GF180_3V3_RULES.items():
        r[key] = _deck_or(deck, rule_id, key, (fallback, why))
    r["well_to_active"] = r["well_to_opposite_active"]
    r["poly_to_active"] = r["field_poly_active"]   # follow the 3.3 V PL.5a value
    r = _rules(r)
    layers = {k: v for k, v in GF180_LAYERS.items() if k not in ("dualgate", "v5_xtor")}
    return Pack(
        pdk_id="gf180mcu_3v3",
        family="GF180MCU 3.3 V (contacted MOS)",
        grid_nm=r["grid"].value,
        layers=layers,
        r=r,
        derived=_derive("gf180mcu_3v3", r, r["grid"].value),
        markers=(),
        model_polarity={"nfet_03v3": "nmos", "pfet_03v3": "pmos"},
        min_length={
            # PL.2_3.3V: 0.28 um for both polarities (parsed from the deck text).
            "nmos": _deck_or(deck, "PL.2_3.3V", "n_length", (280, "PL.2_3.3V gate length")),
            "pmos": _deck_or(deck, "PL.2_3.3V", "p_length", (280, "PL.2_3.3V gate length")),
        },
        extra={"well_nactive_space": r["well_to_opposite_active"].value},
        deck_sha256=_deck_sha(root) if root else "",
        source=root or "<deck not found: consolidated constants>",
        recipe_prefix="gf180_3v3",
        annotation_layers={"metal1": (34, 10)},
        boundary_layer=(0, 0),
        cut_stack={"contact": ("sd", "poly", "metal1")},
        cdl_units="metre",
        cdl_extra_params=(),
        device_markers=(),
        model_markers={},
        freedom=_freedom("gf180mcu_3v3", layers, angles),
        exact_edges=_exact_edges(exact, layers, GF180_EXACT_EDGES),
        orthogonal_isometries=_ISOMETRY_GF180,
        direction_capability=ORTHOGONAL_ENGINE,
        capability=frozenset({"rotate_orthogonal"}),
        device_classes={"nfet_03v3": "mos", "pfet_03v3": "mos"},
        frame_layers=((0, 0),),
    )


# ---------------------------------------------------------------------------
# SKY130 1.8 V (sky130A)
# ---------------------------------------------------------------------------

SKY130_DECK_DIR = os.path.join("pdks", "sky130_pdk", "libs.tech", "klayout", "drc")

SKY130_LAYERS = {
    "active": (65, 20),   # diff
    "poly": (66, 20),
    "contact": (66, 44),  # licon
    # li is the first routing layer over licon, so it fills the ``metal1``
    # semantic role. Anything above it (mcon, met1) is outside the contact-chain
    # operator, which stops at the first conductor it can land a pad on.
    "metal1": (67, 20),
    "metal2": (68, 20),
    "cut2": (67, 44),     # mcon
    "nwell": (64, 20),
    "hvtp": (78, 44),     # HVT PMOS select
    "lvtn": (125, 44),    # LVT select (lvt nfet, lvt varactor)
    "tap": (65, 44),      # tap diffusion; the varactor channel is poly over n+ tap
    "npc": (95, 20),      # nitride poly cut around poly licons
    "polyres": (66, 13),  # poly resistor body id
    "rpm": (86, 20),      # p+ poly resistor implant
    "metal3": (70, 20), "cut3": (70, 44), "metal4": (71, 20),
    "capm": (89, 44),     # MIM capacitor top plate
    "nplus": (93, 44),    # nsdm
    "pplus": (94, 20),    # psdm
    # sky130A has no dualgate/v5_xtor equivalents for 1.8 V devices; the
    # primitives skip marker layers a pack does not declare.
}


#: sky130 cut roles with an exact-edge check (licon.1_a/b: 0.17 um).
SKY130_EXACT_EDGES = {"contact": "licon.1_a/b"}


def build_sky130_1v8(*, deck_dir: Optional[str] = None) -> Pack:
    """SKY130A 1.8 V pack, built from the same semantic vocabulary as GF180.

    The decks state fewer of the quantities the chain operator needs (there is
    no sky130 equivalent of ``DF.6`` active-overhang or an implant enclosure
    rule for the core devices).  Where the deck is silent the value is a
    labelled convention, not a fabricated foundry rule.
    """
    root = deck_dir if deck_dir is not None else _find_project_path(SKY130_DECK_DIR)
    deck = parse_deck_dir(root) if root else {}
    angles = parse_deck_angles_dir(root) if root else {}
    exact = parse_deck_exact_edges_dir(root) if root else {}
    # The full sky130 deck also needs the .lydrc entry point for some rules.
    deck = {**_deny_underscore_free(deck)}

    def rule(key: str, rule_id: str, fallback: Optional[Tuple[int, str]] = None) -> RuleValue:
        return _deck_or(deck, rule_id, key, fallback)

    r = _rules({
        "grid": RuleValue(5, Provenance.CONVENTION, "5 nm layout grid (deck x.1b ongrid 0.005)"),
        "cut": rule("cut", "licon.1_a/b", (170, "licon.1_a/b licon width")),
        "cut_space": rule("cut_space", "licon.2", (170, "licon.2 min licon spacing")),
        "cut_to_active": rule("cut_to_active", "licon.5", (40, "licon.5 diff enclosure of licon")),
        "cut_to_poly": rule("cut_to_poly", "licon.8", (50, "licon.8 poly enclosure of licon")),
        # licon-to-gate spacing is its own rule in sky130 (licon.11 = 55 nm).
        # Binding it to licon.8 was wrong: licon.8 is the *poly enclosure* of the
        # licon, a different constraint on a different pair of shapes.
        "cut_to_gate": rule("cut_to_gate", "licon.11", (55, "licon.11 licon spacing to gate")),
        # sky130 states no gate-cut-to-active spacing rule; the only related
        # check is licon.17 (a cut may not touch both poly and diff). The gate
        # cut is placed off-channel in the field, and this clearance is our
        # modelling choice, labelled as such.
        "gate_cut_to_active": rule("gate_cut_to_active", "licon.17",
                                   (170, "no deck spacing rule; modelling choice")),
        "metal_width": rule("metal_width", "li.1", (170, "li.1 min li width")),
        "metal_space": rule("metal_space", "li.3", (170, "li.3 min li spacing")),
        "metal_area": rule("metal_area", "li.6", (56100, "li.6 min li area")),
        "metal_over_cut": rule("metal_over_cut", "li.5", (80, "li.5 li enclosure of licon")),
        "active_space": rule("active_space", "difftap.3", (270, "difftap.3 min difftap spacing")),
        # Active extension beyond the gate is poly.7 ("min. source/drain
        # length", 250 nm). It was bound to difftap.1_a (min diff *width*, 150)
        # under a comment claiming no deck rule existed; _deck_or found that id
        # and silently used the wrong quantity with provenance 'deck'.
        "sd_overhang": rule("sd_overhang", "poly.7", (250, "poly.7 min source/drain length")),
        "width_min": rule("width_min", "difftap.1_a", (150, "difftap.1_a min diff width")),
        "poly_width": rule("poly_width", "poly.1a", (150, "poly.1a min poly width")),
        "poly_space": rule("poly_space", "poly.2", (210, "poly.2 min poly spacing")),
        # The poly end cap is poly.8 (130 nm). It was bound to poly.1a (min
        # poly *width*, 150), the same defect as sd_overhang above.
        "poly_endcap": rule("poly_endcap", "poly.8", (130, "poly.8 min poly endcap")),
        # Well rules. These were previously bound to nwell.1 / nwell.2a, which
        # are *well width* (840) and *well-to-well spacing* (1270) -- neither is
        # an enclosure or an opposite-polarity spacing. Using them made the row
        # clearance 840+1270 = 2110 nm, which inflated the reproduced area 2.1x
        # over the reference cell. The reference geometry pins the answer: its
        # nwell overlaps the PMOS by exactly 180 (difftap.8) and sits 420 from
        # the NMOS, above the 340 minimum of difftap.9.
        "well_over_active": rule("well_over_active", "difftap.8",
                                 (180, "difftap.8 min p+diff enclosure by nwell")),
        "well_to_opposite_active": rule("well_to_opposite_active", "difftap.9",
                                        (340, "difftap.9 min n+diff spacing to nwell")),
        # nwell.2a is a genuine same-well spacing rule; keep it for well_space.
        "well_space": rule("well_space", "nwell.2a", (1270, "nwell.2a nwell spacing")),
        "well_width": rule("well_width", "nwell.1", (840, "nwell.1 min nwell width")),
        "implant_over_active": rule("implant_over_active", "n/psdm.5a",
                                    (125, "n/psdm.5a min n/psdm enclosure of diff")),
        "implant_space": rule("implant_space", "nsd.2", (380, "nsd.2 implant spacing")),
        # sky130 states no n/psdm *area* rule. It was bound to nsd.1, a *width*
        # (380 nm), which then served as a 380 nm^2 area floor -- a vacuous
        # check. The area floor implied by the width rule is width^2: a legal
        # nsdm polygon is at least nsd.1 wide in both axes.
        "implant_area": _implied_area(deck, "nsd.1", "implant_area", 380),
        # n+ diffusion to p+ diffusion is its own spacing rule (n/psdm.7 = 130).
        # Binding this to nsd.1 (the *minimum nsdm width*, 380) was wrong twice
        # over: wrong quantity and wrong shape pair.
        "implant_to_opposite_active": rule("implant_to_opposite_active", "n/psdm.7",
                                           (130, "n/psdm.7 n/psdm diff spacing to opposite")),
        # sky130 declares no oxide or voltage marker for 1.8 V devices; the
        # primitives must not invent them, so these keys stay absent.
        # -- optional keys read only where the pack has them (``pack.has``) --
        # npc must enclose every poly licon (licon.15).
        "npc_over_cut": rule("npc_over_cut", "licon.15", (100, "licon.15 npc enclosure of poly licon")),
        "npc_width": rule("npc_width", "npc.1", (270, "npc.1 min npc width")),
        "npc_space": rule("npc_space", "npc.2", (270, "npc.2 min npc spacing")),
        # A threshold select layer must enclose the *gate* (hvtp.3 / lvtn.4b);
        # the implant enclosure of the active (125) is the wrong, smaller number.
        "vt_marker_over_gate": rule("vt_marker_over_gate", "hvtp.3",
                                    (180, "hvtp.3 / lvtn.4b select enclosure of gate")),
        "vt_marker_width": rule("vt_marker_width", "lvtn.1a", (380, "lvtn.1a / hvtp.1 width")),
        "vt_marker_space": rule("vt_marker_space", "lvtn.2", (380, "lvtn.2 / hvtp.2 spacing")),
        # Varactor (poly over n+ tap in n-well).
        "tap_cut_enclosure": rule("tap_cut_enclosure", "licon.7",
                                  (120, "licon.7 tap enclosure of licon")),
        "var_cut_to_gate": rule("var_cut_to_gate", "licon.10",
                                (250, "licon.10 licon spacing to varac channel")),
        "var_well_over_poly": rule("var_well_over_poly", "varac.5",
                                   (150, "varac.5 nwell enclosure of varactor poly")),
        "var_min_length": rule("var_min_length", "varac.1", (180, "varac.1 varactor L")),
        "var_min_width": rule("var_min_width", "varac.2", (1000, "varac.2 varactor W")),
        # High-resistance poly resistor (rpm.*, poly.9).
        "rpm_over_res": rule("rpm_over_res", "rpm.3", (200, "rpm.3 rpm enclosure of poly resistor")),
        "res_implant_over_res": rule("res_implant_over_res", "rpm.4",
                                     (110, "rpm.4 psdm enclosure of poly resistor")),
        "npc_over_res": rule("npc_over_res", "rpm.5", (95, "rpm.5 npc enclosure of poly resistor")),
        "rpm_width": rule("rpm_width", "rpm.1a", (1270, "rpm.1a min rpm width")),
        "rpm_space": rule("rpm_space", "rpm.2", (840, "rpm.2 min rpm spacing")),
        "res_space": rule("res_space", "poly.9", (480, "poly.9 poly resistor to poly/diff")),
        "res_width_min": rule("res_width_min", "poly.3", (330, "poly.3 min poly resistor width")),
        # MIM capacitor (capm.*, via3.*, m3/m4).
        "cap_width_min": rule("cap_width_min", "capm.1", (1000, "capm.1 min capm width")),
        "cap_space": rule("cap_space", "capm.2a", (840, "capm.2a min capm spacing")),
        "plate_space": rule("plate_space", "capm.2b_a", (1200, "capm.2b_a bottom plate spacing")),
        "plate_over_cap": rule("plate_over_cap", "capm.3_a", (140, "capm.3_a m3 enclosure of capm")),
        "cap_over_via": rule("cap_over_via", "capm.4", (140, "capm.4 capm enclosure of via3")),
        "via3": rule("via3", "via3.1a", (200, "via3.1a via3 size")),
        "via3_space": rule("via3_space", "via3.2", (200, "via3.2 via3 spacing")),
        "metal4_over_via3": rule("metal4_over_via3", "m4.3", (65, "m4.3 m4 enclosure of via3")),
        # Second conductor level (met1, role metal2) and the cut that joins it to
        # li (mcon, role cut2). Read by the routing primitives through
        # clay.routing.rules; absent keys simply make that level unroutable.
        "metal2_width": rule("metal2_width", "m1.1", (140, "m1.1 min met1 width")),
        "metal2_space": rule("metal2_space", "m1.2", (140, "m1.2 min met1 spacing")),
        "metal2_area": rule("metal2_area", "m1.6", (83000, "m1.6 min met1 area")),
        "cut2": rule("cut2", "ct.1", (170, "ct.1 mcon size")),
        "cut2_space": rule("cut2_space", "ct.2", (190, "ct.2 mcon spacing")),
        # li enclosure of mcon: the deck states no positive li-over-mcon value
        # that the parser recovers (ct.4 is an exact-overlap check), so 0.
        "metal1_over_cut2": rule("metal1_over_cut2", "ct.4",
                                 (0, "ct.4 mcon inside li; no positive enclosure parsed")),
        # met1 enclosure of mcon: m1.4 is 30 nm on all sides and m1.5 60 nm on
        # one of two adjacent sides. 60 nm on every side satisfies both.
        "metal2_over_cut2": rule("metal2_over_cut2", "m1.5", (60, "m1.5 met1 enclosure of mcon")),
        # Field poly to diffusion: a routed poly wire must not come closer. A key
        # of its own (not field_poly_active) so existing sky130 consumers that
        # branch on pack.has("field_poly_active") keep their recorded behaviour.
        "poly_to_active": rule("poly_to_active", "poly.4", (75, "poly.4 poly spacing to diff")),
    })
    # licon.14 is sky130's poly-licon-to-difftap spacing (190 nm). The earlier
    # binding fell back to a 170 nm modelling choice under licon.17, which is a
    # topology rule, not a spacing; the deck states the real number.
    r["gate_cut_to_active"] = rule("gate_cut_to_active", "licon.14",
                                   (190, "licon.14 poly licon spacing to difftap"))

    return Pack(
        pdk_id="sky130_1v8",
        family="SKY130A 1.8 V (single finger, contacted)",
        grid_nm=r["grid"].value,
        layers=dict(SKY130_LAYERS),
        r=r,
        derived=_derive("sky130_1v8", r, r["grid"].value),
        markers=(),               # no dualgate / v5_xtor on sky130A
        model_polarity={
            "sky130_fd_pr__nfet_01v8": "nmos",
            "sky130_fd_pr__pfet_01v8": "pmos",
            "nfet_01v8": "nmos",
            "pfet_01v8": "pmos",
            # Library-qualified variants used by the standard-cell CDLs. They are
            # the same polarity with a different threshold implant; the marker
            # that distinguishes them is a device_marker, not a separate rule set.
            "sky130_fd_pr__pfet_01v8_hvt": "pmos",
            "pfet_01v8_hvt": "pmos",
            "sky130_fd_pr__nfet_01v8_lvt": "nmos",
            "nfet_01v8_lvt": "nmos",
        },
        min_length={
            "nmos": rule("n_length", "poly.1a", (150, "poly.1a min poly width = min gate length")),
            "pmos": rule("p_length", "poly.1a", (150, "poly.1a min poly width = min gate length")),
        },
        extra={"well_nactive_space": 1270},
        deck_sha256=_deck_sha(root) if root else "",
        source=root or "<deck not found>",
        recipe_prefix="sky130_1v8",
        # SkyWater labels both conductor levels.
        annotation_layers={"metal1": (67, 5), "metal2": (68, 5),
                           "metal3": (70, 5), "metal4": (71, 5)},
        # SkyWater's cell boundary is prBoundary, drawn on 236/0. Its bbox is
        # exactly the LEF size (1.38 x 2.72 um on inv_1), which is how the
        # number was confirmed against the shipped library.
        boundary_layer=(236, 0),
        # licon joins diffusion/poly to the first conductor; mcon joins that
        # conductor to the next metal level.
        cut_stack={"contact": ("sd", "poly", "metal1"),
                   "cut2": ("metal1", "metal2")},
        cdl_units="micrometre",
        # SkyWater's SPICE export carries these instance params; they are
        # preserved verbatim and never mapped onto W/L.
        cdl_extra_params=("sa", "sb", "sd", "topography", "area", "perim"),
        device_markers=(),
        model_markers={"pfet_01v8_hvt": ("hvtp",),
                       "sky130_fd_pr__pfet_01v8_hvt": ("hvtp",),
                       "nfet_01v8_lvt": ("lvtn",),
                       "sky130_fd_pr__nfet_01v8_lvt": ("lvtn",),
                       "sky130_fd_pr__cap_var_lvt": ("lvtn",),
                       "sky130_fd_pr__cap_var_hvt": ("hvtp",)},
        freedom=_freedom("sky130_1v8", SKY130_LAYERS, angles),
        exact_edges=_exact_edges(exact, SKY130_LAYERS, SKY130_EXACT_EDGES),
        orthogonal_isometries=_ISOMETRY_SKY130,
        direction_capability=ORTHOGONAL_ENGINE,
        capability=frozenset({"rotate_orthogonal"}),
        device_classes={
            **{m: "res_poly" for m in (
                "sky130_fd_pr__res_high_po", "sky130_fd_pr__res_high_po_0p35",
                "sky130_fd_pr__res_high_po_0p69", "sky130_fd_pr__res_high_po_1p41",
                "sky130_fd_pr__res_high_po_2p85", "sky130_fd_pr__res_high_po_5p73")},
            "sky130_fd_pr__cap_mim_m3_1": "cap_mim",
            "sky130_fd_pr__cap_var_lvt": "cap_var",
            "sky130_fd_pr__cap_var_hvt": "cap_var",
        },
        # prBoundary: 235/4 drawing and 236/0 boundary purpose.
        frame_layers=((235, 4), (236, 0)),
        # The varactor is the chain construction on n+ tap in an n-well, with
        # the varactor's own cut rules (varac.*, licon tap enclosure).
        chain_construction={"cap_var": {
            "diffusion_role": "tap",
            "rule_overrides": {"cut_to_gate": "var_cut_to_gate",
                               "cut_to_active": "tap_cut_enclosure"}}},
    )


def _deny_underscore_free(deck: Dict[str, Tuple[int, bool]]) -> Dict[str, Tuple[int, bool]]:
    """Hook for deck post-processing; currently an identity (kept explicit)."""
    return deck


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


#: Deck directories under an installed PDK root, per environment variable.
#: The variables are the ones ``scripts/setup_open_pdks.py`` and the PDK
#: adapters use; GF180 is listed for both the upstream source tree and the
#: open_pdks / volare install layout.
_DECK_ENV = {
    GF180_DECK_DIR: (("CLAY_GF180MCU_PATH", "GF180MCU_ROOT"), (
        os.path.join("libraries", "gf180mcu_fd_pr", "latest", "rules", "klayout", "drc", "rule_decks"),
        os.path.join("libs.tech", "klayout", "drc", "rule_decks"),
    )),
    SKY130_DECK_DIR: (("CLAY_SKY130_PATH", "SKY130_ROOT"), (
        os.path.join("libs.tech", "klayout", "drc"),
    )),
}


def _find_project_path(relative: str) -> Optional[str]:
    """Locate a deck directory: the PDK named by its environment variable
    first, then ``relative`` under the source tree root. ``None`` when absent;
    pack builders then fall back to their labelled conventions and record an
    empty ``deck_sha256``."""
    envs, subdirs = _DECK_ENV.get(relative, ((), ()))
    for env in envs:
        pdk_root = os.environ.get(env)
        if not pdk_root:
            continue
        for sub in subdirs:
            candidate = os.path.join(pdk_root, sub)
            if os.path.isdir(candidate):
                return candidate
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    candidate = os.path.join(root, relative)
    return candidate if os.path.isdir(candidate) else None


def require_deck(pack: "Pack") -> "Pack":
    """Refuse a pack built without its DRC deck.

    Without the deck the size rules keep their labelled fallbacks, but angle
    and exact-edge rules are missing, so generated layouts can fail their own
    verification. Training and generation call this so that the gap is an
    explicit error instead of a silently wrong layout.
    """
    if not pack.deck_sha256:
        env = "CLAY_SKY130_PATH" if pack.family.lower().startswith("sky130") else "CLAY_GF180MCU_PATH"
        raise RuntimeError(
            f"DRC deck for {pack.pdk_id!r} not found. Point {env} at the installed "
            "open PDK (see scripts/setup_open_pdks.py) and run again.")
    return pack


_BUILDERS = {
    "gf180mcu_5v": build_gf180_5v,
    "gf180mcu": build_gf180_5v,
    "gf180mcu_3v3": build_gf180_3v3,
    "sky130_1v8": build_sky130_1v8,
    "sky130": build_sky130_1v8,
    "sky130a": build_sky130_1v8,
}

_CACHE: Dict[str, Pack] = {}


def known_packs() -> Tuple[str, ...]:
    return tuple(sorted(_BUILDERS))


def get_pack(pdk_id: str) -> Pack:
    """Return the semantic pack for ``pdk_id`` (cached per id)."""
    key = pdk_id.strip().lower()
    if key not in _BUILDERS:
        raise KeyError(
            f"no semantic rule pack for {pdk_id!r}; known: {', '.join(known_packs())}. "
            "Adding a PDK means adding a builder in clay/pdk/semantic.py."
        )
    if key not in _CACHE:
        _CACHE[key] = _BUILDERS[key]()
    return _CACHE[key]


def default_pack() -> Pack:
    """The pack the existing GF180 experiment binds to when none is given."""
    return get_pack("gf180mcu_5v")


__all__ = [
    "Pack", "RuleValue", "Provenance",
    "REQUIRED_ROLES", "OPTIONAL_ROLES", "REQUIRED_KEYS",
    "parse_deck", "parse_deck_dir",
    "build_gf180_5v", "build_gf180_3v3", "build_sky130_1v8", "DEVICE_CLASS_TERMINALS",
    "get_pack", "default_pack", "known_packs",
]
