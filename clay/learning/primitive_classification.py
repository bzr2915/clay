"""Load and validate the primitive classification (primitive_classification.yaml).

Why a loader instead of importing the YAML everywhere
-----------------------------------------------------
The classification is the *description* of the primitive set; the two harnesses
(``primitive_probe`` and ``primitive_verify``) each hold the *implementation*
registry. Keeping them apart lets a validator check that all three agree,
which is the point: before this, the two registries disagreed with each other
and nothing noticed.

The axes are closed vocabularies. A value outside them is an error, so a new
primitive cannot invent a category that nothing else groups under.

Capability signature (GLOBAL_COORDINATOR_DESIGN_zh.md §4.4, step S2)
--------------------------------------------------------------------
Every entry also states, in closed vocabularies, what flows through it:
``signature`` (typed data inputs, scalar parameters, one output type),
``composition`` (a fixed ``step``; one of several producers chosen by device
class, ``dispatch``; or a genuine option, ``alternative``), ``applies_to``,
``determinism`` and ``cost``. :func:`check_signature` holds the signature to
the code; :mod:`clay.decision.index` answers retrieval questions from it.
"""
from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

import yaml

from clay.pdk import semantic
from clay.pdk.semantic import Pack

ROOT = Path(__file__).resolve().parents[2]   # clay/learning/x.py -> project root
PATH = ROOT / "primitive_classification.yaml"

SCHEMA = "clay.primitive-classification.v1"

STAGES = ("observe", "decide", "plan", "construct", "transform", "assemble", "verify")
FORMS = ("layout", "coords", "data")
FREEDOM_EFFECTS = ("none", "translation", "rotation", "structure", "net")
#: The part a ``decide`` primitive plays in one decision level of the
#: coordinator (GLOBAL_COORDINATOR_DESIGN_zh.md §4.2, §6.2). Solving is not a
#: role: solvers are ``plan`` primitives a level calls. Every other stage is
#: ``none``.
DECISION_ROLES = ("none", "admissibility", "options", "reduce", "bound", "feedback")

# ---------------------------------------------------------------- signatures
#: Data types that flow between primitives. Closed: a misspelt type is an
#: error, not a new type that nothing produces.
TYPES = (
    # inputs from outside the compile
    "NetlistText", "Pack", "Packs", "Library", "InverterLibrary", "RelationLibrary",
    "Contract",
    # training only: a generation run is never given one (it reads the netlist
    # and the frozen library, never a reference layout)
    "ReferenceLayout",
    # circuit
    "Netlist", "InverterCircuit", "Symmetry", "Device", "Devices", "Fingers", "Chain", "Chains",
    "Units", "PlacementIntents",
    # construction
    "ContactPolicy", "ContactPlan", "ContactPlans", "GeometryPlan", "ResistorStyle",
    "Row", "Rows", "DeviceCell", "Path", "Region",
    # placement
    "Block", "Blocks", "BlockSpace", "BlockSpaces", "Rectangles", "Boxes", "Clearances",
    "SolverConstraints", "Placement",
    # result and checks
    "Layout", "Verification", "Observation", "Observations", "RelationFeatures",
    "Trials", "Ranking",
    # decision
    "Freedom", "PartFreedom", "ActionPlan", "Admission", "AngleVerdicts",
    "Orientations", "FoldTargets", "RotationClass", "Bound",
    "Symmetries", "SymmetryClasses", "Candidate", "Feedback",
    # routing (clay.routing, docs/ROUTING_LEARNING_DESIGN_zh.md)
    "RoutingRules", "WireDirections", "StripResult", "Terminals", "NetSignatures",
    "RoutingObservation", "RoutingObservations", "RoutingLibrary", "NetPolicy",
    "TrunkCandidates", "NetSpecs", "RouteGraph", "RouteSolution", "RoutedLayout",
    "RouteVerification",
)
#: Types a compile is given rather than computes.
EXTERNAL_TYPES = ("NetlistText", "Pack", "Packs", "Library", "InverterLibrary",
                  "RelationLibrary", "Contract")
#: Given only to a training run; never to generation.
TRAINING_TYPES = ("ReferenceLayout",)
#: Made by the coordinator core itself, not by a primitive: a solved
#: construction candidate (the record a feedback primitive judges).
CORE_TYPES = ("Candidate",)
#: A collection is available once its element is: gathering is the core's job.
COLLECTIONS = {"Devices": "Device", "Rows": "Row", "Blocks": "Block",
               "ContactPlans": "ContactPlan", "Observations": "Observation",
               "Packs": "Pack", "BlockSpaces": "BlockSpace",
               "RoutingObservations": "RoutingObservation"}
#: A part is available once the whole that contains it is (a field read).
PARTS = {"ContactPolicy": "Library", "ResistorStyle": "Library", "Device": "Netlist",
         "Chain": "Chains"}
#: ``step``: a fixed pipeline step, no choice. ``dispatch``: one of several
#: producers of a type, chosen by device class (``applies_to`` must be
#: disjoint). ``alternative``: a genuine option of a decision level; only
#: entries of one ``alternative_group`` are options for each other.
#: ``engine``: only ever called inside another primitive (``solve_blocks``
#: inside ``place_blocks``); never composed directly, so composition
#: derivation does not offer it.
COMPOSITIONS = ("step", "dispatch", "alternative", "engine")
#: ``pure``: covered by the audited determinism proof (design doc §15).
#: ``unaudited``: not yet audited; makes no claim either way.
DETERMINISM = ("pure", "unaudited")
COSTS = ("cheap", "solver")
OPTIONS_DOMAINS = ("complete", "truncated")
DEVICE_CLASSES = tuple(semantic.DEVICE_CLASS_TERMINALS)


@dataclass(frozen=True)
class Signature:
    """What flows through a primitive: typed inputs, scalar parameters, output.

    ``inputs`` maps a function parameter to the type it carries and must be
    present for the primitive to run; ``optional_inputs`` may be absent (the
    function has a default and a flow may omit them). ``params`` are the
    remaining parameters (θ coordinates and settings u); naming them lets a
    diagnostic point at the parameter that caused it.
    """
    inputs: Mapping[str, str]
    params: Tuple[str, ...]
    output: str
    optional_inputs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        for t in self.types():
            if t not in TYPES:
                raise ValueError(f"unknown type {t!r}; allowed {TYPES}")
        names = [*self.inputs, *self.optional_inputs, *self.params]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            raise ValueError(f"{repeated} listed more than once in the signature")

    def types(self) -> Tuple[str, ...]:
        return (tuple(self.inputs.values()) + tuple(self.optional_inputs.values())
                + (self.output,))

    def names(self) -> Tuple[str, ...]:
        return (*self.inputs, *self.optional_inputs, *self.params)


@dataclass(frozen=True)
class Classification:
    name: str
    stage: str
    form: str
    freedom_effect: str
    pdk_binding: Tuple[str, ...]
    summary: str
    aliases: Mapping[str, Tuple[str, ...]]
    # Keys the primitive reads only when the pack defines them (``pack.has``).
    # Empty is the normal case; a primitive declares these when skipping a layer
    # is a legitimate behaviour rather than an error.
    optional_binding: Tuple[str, ...] = ()
    #: Set when the primitive is bound to one process by construction rather
    #: than by the rule keys it reads: no other pack can serve it even if every
    #: key it names happened to exist. Empty means process-neutral, which is now
    #: true of every primitive in the table.
    process_specific: str = ""
    decision_role: str = "none"
    #: ``module:function`` of the code this entry describes. Lets the table be
    #: checked against the code (:func:`resolve`) instead of drifting from it.
    implementation: str = ""
    signature: Optional[Signature] = None
    composition: str = "step"
    alternative_group: str = ""
    #: Device classes the primitive serves; empty means not device-specific.
    applies_to: Tuple[str, ...] = ()
    #: Only for ``decision_role: options``: is the option set it returns the
    #: whole space (``complete``) or a chosen subset (``truncated``)? A
    #: truncated set must appear in the certificate scope.
    options_domain: str = ""
    determinism: str = "unaudited"
    cost: str = "cheap"
    #: Inputs the function writes into. A primitive should be a pure function;
    #: one that is not says so here rather than hiding it.
    mutates: Tuple[str, ...] = field(default=())

    def __post_init__(self):
        if self.stage not in STAGES:
            raise ValueError(f"{self.name}: unknown stage {self.stage!r}; allowed {STAGES}")
        if self.form not in FORMS:
            raise ValueError(f"{self.name}: unknown form {self.form!r}; allowed {FORMS}")
        if self.freedom_effect not in FREEDOM_EFFECTS:
            raise ValueError(f"{self.name}: unknown freedom_effect "
                             f"{self.freedom_effect!r}; allowed {FREEDOM_EFFECTS}")
        if self.decision_role not in DECISION_ROLES:
            raise ValueError(f"{self.name}: unknown decision_role {self.decision_role!r}; "
                             f"allowed {DECISION_ROLES}")
        if self.decision_role != "none" and self.stage != "decide":
            raise ValueError(f"{self.name}: decision_role {self.decision_role!r} needs stage "
                             f"'decide', not {self.stage!r}")
        if self.implementation and ":" not in self.implementation:
            raise ValueError(f"{self.name}: implementation must be 'module:function', "
                             f"got {self.implementation!r}")
        if not self.summary.strip():
            raise ValueError(f"{self.name}: needs a summary")
        overlap = set(self.pdk_binding) & set(self.optional_binding)
        if overlap:
            raise ValueError(
                f"{self.name}: {sorted(overlap)} declared as both required and optional; "
                "a key is one or the other")
        self._check_capability()

    def _check_capability(self):
        if self.composition not in COMPOSITIONS:
            raise ValueError(f"{self.name}: unknown composition {self.composition!r}; "
                             f"allowed {COMPOSITIONS}")
        if (self.composition == "alternative") != bool(self.alternative_group):
            raise ValueError(f"{self.name}: alternative_group is required for, and only "
                             "for, composition 'alternative'")
        if self.composition == "dispatch" and not self.applies_to:
            raise ValueError(f"{self.name}: a dispatch primitive must say which device "
                             "classes it serves (applies_to)")
        unknown = [c for c in self.applies_to if c not in DEVICE_CLASSES]
        if unknown:
            raise ValueError(f"{self.name}: unknown device classes {unknown}; "
                             f"allowed {DEVICE_CLASSES}")
        if (self.decision_role == "options") != bool(self.options_domain):
            raise ValueError(f"{self.name}: options_domain is required for, and only for, "
                             "decision_role 'options'")
        if self.options_domain and self.options_domain not in OPTIONS_DOMAINS:
            raise ValueError(f"{self.name}: unknown options_domain {self.options_domain!r}")
        if self.determinism not in DETERMINISM:
            raise ValueError(f"{self.name}: unknown determinism {self.determinism!r}; "
                             f"allowed {DETERMINISM}")
        if self.cost not in COSTS:
            raise ValueError(f"{self.name}: unknown cost {self.cost!r}; allowed {COSTS}")
        if self.mutates:
            if self.signature is None or not set(self.mutates) <= (
                    set(self.signature.inputs) | set(self.signature.optional_inputs)):
                raise ValueError(f"{self.name}: mutates must name typed inputs")
            if self.determinism == "pure":
                raise ValueError(f"{self.name}: a primitive that writes its inputs is not pure")


def _signature(name: str, raw) -> Signature:
    if not isinstance(raw, dict) or set(raw) - {"inputs", "optional_inputs", "params", "output"}:
        raise ValueError(f"{name}: signature takes only inputs, optional_inputs, params "
                         "and output")
    if "output" not in raw:
        raise ValueError(f"{name}: signature needs an output type")
    try:
        return Signature(inputs=dict(raw.get("inputs") or {}),
                         params=tuple(raw.get("params") or ()),
                         output=raw["output"],
                         optional_inputs=dict(raw.get("optional_inputs") or {}))
    except ValueError as e:
        raise ValueError(f"{name}: {e}") from None


def load(path: Optional[Path] = None) -> Dict[str, Classification]:
    """Parse and validate the classification file.

    Every entry must name its ``implementation`` and give a ``signature``
    (step S2): a primitive that cannot say what it consumes and produces cannot
    be composed by anything but hand-written code.
    """
    raw = yaml.safe_load(Path(path or PATH).read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA:
        raise ValueError(f"unexpected schema {raw.get('schema')!r}")
    axes = raw.get("axes", {})
    for key, expected in (("stage", STAGES), ("form", FORMS),
                          ("freedom_effect", FREEDOM_EFFECTS),
                          ("decision_role", DECISION_ROLES)):
        declared = tuple(axes.get(key, ()))
        if declared != expected:
            raise ValueError(
                f"axes.{key} in the file is {declared} but the loader knows {expected}; "
                "the two must agree or the vocabulary is not closed")
    out: Dict[str, Classification] = {}
    for name, body in raw.get("primitives", {}).items():
        if name in out:
            raise ValueError(f"duplicate primitive {name!r}")
        aliases_raw = body.get("aliases") or {}
        if not isinstance(aliases_raw, dict):
            raise ValueError(f"{name}: aliases must be a mapping, got {type(aliases_raw).__name__}")
        aliases = {}
        for harness, value in aliases_raw.items():
            # A single alias may legitimately be written as a bare scalar in the
            # YAML. ``tuple("turn")`` would silently become ('t','u','r','n'),
            # so a scalar is normalised to a one-element tuple here rather than
            # relying on the author to remember the brackets.
            if isinstance(value, str):
                aliases[harness] = (value,)
            elif isinstance(value, (list, tuple)):
                aliases[harness] = tuple(value)
            else:
                raise ValueError(f"{name}: alias list for {harness!r} must be a string or list")
        if not body.get("implementation"):
            raise ValueError(f"{name}: implementation is required")
        if not body.get("signature"):
            raise ValueError(f"{name}: signature is required")
        out[name] = Classification(
            name=name,
            stage=body["stage"],
            form=body["form"],
            freedom_effect=body["freedom_effect"],
            pdk_binding=tuple(body.get("pdk_binding") or ()),
            summary=body["summary"],
            aliases=aliases,
            optional_binding=tuple(body.get("optional_binding") or ()),
            process_specific=str(body.get("process_specific") or ""),
            decision_role=str(body.get("decision_role") or "none"),
            implementation=str(body["implementation"]),
            signature=_signature(name, body["signature"]),
            composition=str(body.get("composition") or "step"),
            alternative_group=str(body.get("alternative_group") or ""),
            applies_to=tuple(body.get("applies_to") or ()),
            options_domain=str(body.get("options_domain") or ""),
            determinism=str(body.get("determinism") or "unaudited"),
            cost=str(body.get("cost") or "cheap"),
            mutates=tuple(body.get("mutates") or ()),
        )
    if not out:
        raise ValueError("classification lists no primitives")
    return out


def by_stage(table: Mapping[str, Classification]) -> Dict[str, Tuple[str, ...]]:
    groups: Dict[str, list] = {stage: [] for stage in STAGES}
    for item in table.values():
        groups[item.stage].append(item.name)
    return {k: tuple(sorted(v)) for k, v in groups.items()}


def by_decision_role(table: Mapping[str, Classification]) -> Dict[str, Tuple[str, ...]]:
    """``decide`` primitives grouped by the part they play in a decision level.

    This is the query the coordinator asks ("which primitives offer options?",
    "which give a lower bound?") instead of naming functions in code.
    """
    groups: Dict[str, list] = {role: [] for role in DECISION_ROLES if role != "none"}
    for item in table.values():
        if item.decision_role != "none":
            groups[item.decision_role].append(item.name)
    return {k: tuple(sorted(v)) for k, v in groups.items()}


def resolve(item: Classification):
    """Import and return the callable an entry's ``implementation`` names.

    Raises if the entry names none, or names something that does not exist or
    is not callable: the table must describe code that is actually there.
    """
    if not item.implementation:
        raise ValueError(f"{item.name}: no implementation declared")
    module_name, _, attr = item.implementation.partition(":")
    target = importlib.import_module(module_name)
    for part in attr.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{item.name}: {item.implementation} is not callable")
    return target


def check_signature(item: Classification) -> None:
    """Hold the declared signature to the function's real parameters.

    Every declared input and parameter must be a parameter of the function, and
    every parameter without a default must be declared (as an input or a
    parameter): otherwise the table describes a call the code cannot take.
    """
    if item.signature is None:
        raise ValueError(f"{item.name}: no signature declared")
    params = inspect.signature(resolve(item)).parameters
    declared = set(item.signature.names())
    unknown = sorted(declared - set(params))
    if unknown:
        raise ValueError(f"{item.name}: signature names {unknown}, which "
                         f"{item.implementation} does not take")
    required = sorted(n for n, p in params.items()
                      if p.default is inspect.Parameter.empty
                      and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD))
    missing = [n for n in required if n not in declared]
    if missing:
        raise ValueError(f"{item.name}: required parameters {missing} of "
                         f"{item.implementation} are not in the signature")


#: A rule key read from a pack: ``pack.nm("k")``, ``p.has('k')``, ``r.nm("k")``
#: or the common alias ``r = pack.nm`` called as ``r("k")``.
_RULE_READ = re.compile(r"""(?:\.nm|\.has|\br)\(\s*['"]([a-z][a-z0-9_]*)['"]\s*\)""")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\(")


def scan_rule_keys(item: Classification, depth: int = 2) -> frozenset:
    """Rule keys the primitive's code reads, found statically (step S3b).

    Scans the implementation's source and, up to ``depth`` levels, the
    functions of the same module it calls by name. A key passed through a
    variable (``rule_overrides``) is not seen: the scan is a lower bound on
    what is read, so it can show an undeclared key but not prove there is none.
    """
    target = resolve(item)
    module = inspect.getmodule(target)
    seen, keys = set(), set()
    frontier = [target]
    for _ in range(depth + 1):
        nxt = []
        for fn in frontier:
            if fn in seen:
                continue
            seen.add(fn)
            try:
                src = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            keys.update(_RULE_READ.findall(src))
            for name in _CALL.findall(src):
                helper = getattr(module, name, None)
                if inspect.isfunction(helper) and inspect.getmodule(helper) is module:
                    nxt.append(helper)
        frontier = nxt
    return frozenset(keys)


def check_pdk_bindings(table: Mapping[str, Classification], pack: Pack,
                       name: str) -> None:
    """Every semantic key a primitive declares must resolve in ``pack``.

    This is what makes "can this primitive run on this PDK?" a query. A
    primitive that reads a key the pack does not define would fail at call time
    inside geometry code; catching it here names the primitive and the key.
    """
    item = table.get(name)
    if item is None:
        raise KeyError(f"primitive {name!r} is not classified")
    if item.process_specific and item.process_specific != pack.pdk_id:
        raise ValueError(
            f"primitive {name!r} is bound to process {item.process_specific!r} by "
            f"construction, not by its rule keys; pack {pack.pdk_id!r} cannot serve it")
    missing = [key for key in item.pdk_binding if not pack.has(key)]
    if missing:
        raise ValueError(
            f"primitive {name!r} needs {missing} from pack {pack.pdk_id!r}, "
            f"which does not define them (stage {item.stage})")
    # optional_binding is deliberately not checked against this pack: a key that
    # is absent is precisely the case it exists for. It is still checked against
    # the process-independent vocabulary so a typo cannot pass as "this process
    # simply does not have it".
    other = [key for key in item.optional_binding if key not in vocabulary()]
    if other:
        raise ValueError(
            f"primitive {name!r} lists optional keys {other} that no pack in "
            f"{semantic.known_packs()} defines; optional is not a licence to misspell")


_VOCAB: Optional[frozenset] = None


def vocabulary() -> frozenset:
    """Every semantic rule key any known pack can define, plus required keys.

    Optional bindings are checked against this rather than against one pack, so
    "the process lacks this layer" and "this key does not exist" stay distinct.
    """
    global _VOCAB
    if _VOCAB is None:
        keys = set(semantic.REQUIRED_KEYS)
        for pdk_id in semantic.known_packs():
            pack = semantic.get_pack(pdk_id)
            keys.update(pack.r)
            keys.update(pack.derived)
        _VOCAB = frozenset(keys)
    return _VOCAB


def runnable_on(table: Mapping[str, Classification], pack: Pack) -> Tuple[str, ...]:
    """Primitives whose declared rule bindings are all satisfiable by ``pack``.

    Optional keys never exclude a pack: they mark the branches the primitive
    skips when the process has no such layer. ``process_specific`` does exclude,
    and is checked here rather than left to the accident of a rule key that only
    one process defines.
    """
    return tuple(sorted(
        name for name in table
        if (not table[name].process_specific
            or table[name].process_specific == pack.pdk_id)
        and all(pack.has(key) for key in table[name].pdk_binding)))


__all__ = ["Classification", "Signature", "load", "by_stage", "by_decision_role", "resolve",
           "check_signature", "check_pdk_bindings", "runnable_on", "vocabulary", "STAGES",
           "FORMS", "FREEDOM_EFFECTS", "DECISION_ROLES", "TYPES", "EXTERNAL_TYPES",
           "TRAINING_TYPES", "CORE_TYPES", "COLLECTIONS", "PARTS", "COMPOSITIONS", "DETERMINISM", "COSTS",
           "OPTIONS_DOMAINS", "DEVICE_CLASSES", "PATH"]
