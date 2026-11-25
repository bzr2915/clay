"""Generic, circuit-agnostic SPICE/CDL front end for layout generation.

Why this module exists
----------------------
The inverter experiment read its target through ``circuit.parse_cdl`` and
``contact_primitives.inverter_groups``: one flat subcircuit, ``M`` instances
only, one common gate, one output. Every later primitive inherited that shape,
which is the coupling this module removes. Here a netlist is any hierarchy of
subcircuits whose leaves are devices of a class the rule pack can construct
(``Pack.device_class``): MOS, poly resistor, MIM capacitor, MOS varactor.

Nothing here knows a process. The pack supplies

  * the class of each model (``device_classes`` / ``model_polarity``),
  * the CDL length unit for bare numbers (``cdl_units``),
  * the layout grid every dimension must sit on.

Strictness is kept from the inverter parser: an instance the pack cannot
classify is an error, never silently dropped. The one deliberate exception is
*parasitic* ``R``/``C`` elements without a model (layout-extracted netlists
carry thousands); those are counted in ``FlatCircuit.dropped_parasitics``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
from typing import Dict, List, Mapping, Optional, Tuple

from clay.pdk.semantic import DEVICE_CLASS_TERMINALS

SCHEMA = "clay.flat-circuit.v1"

_SUFFIX_NM = {"": None, "m": Decimal("1e6"), "u": Decimal("1e3"), "n": Decimal(1),
              "p": Decimal("0.001"), "f": Decimal("0.000001")}
_TOKEN = re.compile(r"""\S+?=(?:'[^']*'|"[^"]*"|\{[^}]*\}|\S+)|'[^']*'|\S+""")


@dataclass(frozen=True)
class Device:
    """One leaf device after flattening.

    ``nets`` maps the class's terminal names (``DEVICE_CLASS_TERMINALS``) to
    nets. ``w_nm``/``l_nm`` are the *total* drawn width/length; ``nf`` is the
    finger count the netlist requests and ``m`` the parallel multiplicity.
    ``kind`` is the diffusion polarity for MOS and varactors (``nmos``/``pmos``)
    and ``None`` for passives without diffusion.
    """

    name: str
    cls: str
    model: str
    nets: Mapping[str, str]
    w_nm: int
    l_nm: int
    nf: int = 1
    m: int = 1
    kind: Optional[str] = None

    def net(self, terminal: str) -> str:
        return self.nets[terminal]


@dataclass(frozen=True)
class FlatCircuit:
    name: str
    ports: Tuple[str, ...]
    devices: Tuple[Device, ...]
    pdk: str
    dropped_parasitics: int = 0
    hierarchy: Tuple[str, ...] = field(default_factory=tuple)

    def by_class(self, cls: str) -> Tuple[Device, ...]:
        return tuple(d for d in self.devices if d.cls == cls)

    def nets(self) -> Tuple[str, ...]:
        seen = []
        for d in self.devices:
            for n in d.nets.values():
                if n not in seen:
                    seen.append(n)
        return tuple(seen)


def _logical_lines(text: str) -> List[str]:
    lines: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        # An inline ``$`` comment only when free-standing: extracted netlists
        # use ``$`` inside device and net names (``X$1``, ``\\$5``).
        line = re.split(r"\s\$\s", line, maxsplit=1)[0].rstrip()
        if line.startswith("+"):
            if not lines:
                raise ValueError("orphan continuation line")
            lines[-1] += " " + line[1:].strip()
        else:
            lines.append(line)
    return lines


def _tokens(line: str) -> List[str]:
    line = re.sub(r"\s*=\s*", "=", line)
    return _TOKEN.findall(line)


def length_nm(value: str, *, bare_unit: str, grid: int) -> int:
    """A SPICE length to integer nm on the pack grid.

    A suffix (``u``, ``n``, ...) is explicit; a bare number takes the pack's
    ``cdl_units`` (``metre`` for GF180 CDL, ``micrometre`` for SKY130 SPICE).
    """
    m = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)", value.strip())
    if not m:
        raise ValueError(f"unsupported dimension expression {value!r}")
    suffix = m[2].lower()
    if suffix not in _SUFFIX_NM:
        raise ValueError(f"unsupported dimension suffix in {value!r}")
    scale = _SUFFIX_NM[suffix]
    if scale is None:
        scale = {"metre": Decimal("1e9"), "micrometre": Decimal("1e3")}[bare_unit]
    nm = Decimal(m[1]) * scale
    if nm <= 0 or nm != nm.to_integral_value():
        raise ValueError(f"dimension {value!r} is not a positive whole number of nm")
    nm = int(nm)
    if nm > 10_000_000:
        # 10 mm: no on-chip device is this large. Almost always a bare number
        # read with the wrong unit convention (pack ``cdl_units``).
        raise ValueError(f"dimension {value!r} = {nm} nm is implausible; "
                         f"bare numbers are read as {bare_unit}s for this pack")
    if nm % grid:
        raise ValueError(f"dimension {value!r} = {nm} nm is off the {grid} nm grid")
    return nm


def _count(value: str) -> int:
    try:
        v = Decimal(value.strip("'\""))
    except InvalidOperation:
        raise ValueError(f"multiplicity {value!r} is not a number") from None
    if v != v.to_integral_value() or v < 1:
        raise ValueError(f"multiplicity {value!r} must be a positive integer")
    return int(v)


def _split_params(tokens: List[str]) -> Tuple[List[str], Dict[str, str]]:
    positional, params = [], {}
    for tok in tokens:
        if "=" in tok and not tok.startswith(("'", '"')):
            k, v = tok.split("=", 1)
            key = k.lower()
            if key in params:
                raise ValueError(f"duplicate parameter {k!r}")
            params[key] = v
        elif params:
            raise ValueError(f"positional token {tok!r} after parameters")
        else:
            positional.append(tok)
    return positional, params


def _clean_net(net: str, alias: Optional[re.Pattern]) -> str:
    net = net.replace("\\", "")
    if alias is not None:
        net = alias.sub("", net)
    return net


def parse_spice(text: str, pack, *, top: Optional[str] = None,
                net_alias: Optional[str] = None) -> FlatCircuit:
    """Parse and flatten a SPICE/CDL hierarchy into leaf devices.

    ``net_alias`` is an optional regex whose matches are removed from every net
    name; layout-extracted netlists split one net into terminal stubs
    (``GND__t11``), and ``r"__t\\d+$"`` folds them back. It is a property of the
    *input file*, so the caller states it; nothing is guessed.
    """
    alias = re.compile(net_alias) if net_alias else None
    subckts: Dict[str, Tuple[Tuple[str, ...], List[List[str]]]] = {}
    current = None
    globals_: List[str] = []
    for line in _logical_lines(text):
        tok = _tokens(line)
        head = tok[0].lower()
        if head == ".subckt":
            if current is not None:
                raise ValueError("nested .subckt is not supported")
            ports = tuple(t for t in tok[2:] if "=" not in t and t.lower() != "params:")
            current = (tok[1], ports, [])
        elif head == ".ends":
            if current is None:
                raise ValueError(".ends without .subckt")
            if current[0] in subckts:
                raise ValueError(f"duplicate subcircuit {current[0]!r}")
            subckts[current[0]] = (current[1], current[2])
            current = None
        elif head == ".global":
            globals_.extend(tok[1:])
        elif head.startswith("."):
            continue                       # .end/.option/.include/.param: no devices
        elif current is not None:
            current[2].append(tok)
        else:
            raise ValueError(f"device outside any .subckt: {line[:60]!r}")
    if current is not None:
        raise ValueError("unterminated .subckt")
    if not subckts:
        raise ValueError("netlist contains no .subckt")
    if top is None:
        used = {t[-1] for _, body in subckts.values() for t in body
                if t[0][0].lower() == "x" and _split_params(t[1:])[0]
                and _split_params(t[1:])[0][-1] in subckts}
        roots = [n for n in subckts if n not in used]
        if len(roots) != 1:
            raise ValueError(f"cannot choose a top cell among {roots}; pass top=")
        top = roots[0]
    if top not in subckts:
        raise ValueError(f"top cell {top!r} not in netlist")

    grid = pack.nm("grid")
    devices: List[Device] = []
    skipped = [0]
    visited: List[str] = []
    glob = {_clean_net(g, alias) for g in globals_}

    def expand(cell: str, prefix: str, binding: Mapping[str, str], stack: Tuple[str, ...]):
        if cell in stack:
            raise ValueError(f"recursive subcircuit {cell!r}")
        visited.append(cell)
        ports, body = subckts[cell]

        def net_of(local: str) -> str:
            local = _clean_net(local, alias)
            if local in binding:
                return binding[local]
            if local in glob:
                return local
            return prefix + local

        for tok in body:
            name = tok[0]
            letter = name[0].lower()
            positional, params = _split_params(tok[1:])
            if letter in ("r", "c") and len(positional) == 2 and not params:
                skipped[0] += 1
                continue
            if letter in ("r", "c") and len(positional) == 3 and \
                    _is_number(positional[2]):
                skipped[0] += 1            # R1 a b 10k / C1 a b 1f: parasitic value
                continue
            if letter == "x" and positional and positional[-1] in subckts:
                child = positional[-1]
                child_ports = subckts[child][0]
                actual = positional[:-1]
                if len(actual) != len(child_ports):
                    raise ValueError(f"{prefix}{name}: {len(actual)} nets for "
                                     f"{len(child_ports)} ports of {child}")
                expand(child, f"{prefix}{name}/",
                       {_clean_net(p, alias): net_of(a) for p, a in zip(child_ports, actual)},
                       stack + (cell,))
                continue
            if letter not in ("x", "m", "r", "c"):
                raise ValueError(f"unsupported instance {name!r}")
            if not positional:
                raise ValueError(f"instance {name!r} has no model")
            model = positional[-1]
            try:
                cls = pack.device_class(model)
            except KeyError as e:
                raise ValueError(str(e)) from None
            terminals = DEVICE_CLASS_TERMINALS[cls]
            nets = positional[:-1]
            if len(nets) != len(terminals):
                raise ValueError(f"{prefix}{name}: class {cls} expects {len(terminals)} "
                                 f"terminals, got {len(nets)}")
            unit = pack.cdl_units
            if "w" not in params or "l" not in params:
                raise ValueError(f"{prefix}{name}: W and L are required")
            w = length_nm(params["w"], bare_unit=unit, grid=grid)
            l = length_nm(params["l"], bare_unit=unit, grid=grid)
            nf = _count(params.get("nf", "1"))
            m = _count(params.get("m", "1")) * _count(params.get("mult", "1")) \
                * _count(params.get("mf", "1"))
            kind = None
            if cls == "mos":
                kind = pack.polarity(model)
            elif cls == "cap_var":
                kind = "nmos"              # n+ tap channel in an n-well
            devices.append(Device(prefix + name, cls, model,
                                  {t: net_of(n) for t, n in zip(terminals, nets)},
                                  w, l, nf, m, kind))

    expand(top, "", {_clean_net(p, alias): _clean_net(p, alias) for p in subckts[top][0]}, ())
    if not devices:
        raise ValueError("flattened netlist has no devices")
    if len({d.name for d in devices}) != len(devices):
        raise ValueError("duplicate flattened device names")
    return FlatCircuit(top, tuple(_clean_net(p, alias) for p in subckts[top][0]),
                       tuple(devices), pack.pdk_id, skipped[0], tuple(dict.fromkeys(visited)))


def _is_number(token: str) -> bool:
    return re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?[a-zA-Z]*", token) is not None


def summary(circuit: FlatCircuit) -> Dict[str, object]:
    """Counts a reviewer can check against the source netlist by hand."""
    out: Dict[str, object] = {"cell": circuit.name, "ports": list(circuit.ports),
                              "devices": len(circuit.devices),
                              "dropped_parasitics": circuit.dropped_parasitics}
    by: Dict[str, int] = {}
    for d in circuit.devices:
        key = d.cls if d.cls != "mos" else d.kind
        by[key] = by.get(key, 0) + 1
    out["by_class"] = dict(sorted(by.items()))
    out["fingers"] = sum(d.nf * d.m for d in circuit.devices if d.cls in ("mos", "cap_var"))
    return out
