"""Strict, geometry-free CDL input for the layout placement experiment.

The parser accepts any PDK's MOS models: polarity is derived from the model
name (``nfet``/``pfet``, ``nmos``/``pmos``, ``nch``/``pch``), so a GF180 CDL and
a SKY130 CDL parse through the same strict path. A semantic rule pack can bind
models explicitly via ``Pack.model_polarity`` where naming is ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re


_POLARITY_PATTERNS = (
    ("pmos", ("pfet", "pmos", "pch", "pmos_")),
    ("nmos", ("nfet", "nmos", "nch", "nmos_")),
)


def model_polarity(model: str) -> str:
    """Polarity of a MOS model name, independent of process.

    The name is split on ``_`` and each token is matched by prefix, so a prefixed
    library name such as ``sky130_fd_pr__nfet_01v8`` resolves on its own
    ``nfet`` token rather than on a per-PDK table. ``pfet`` is tested before
    ``fet``-style ambiguity, and nothing matches a bare ``fet``.
    """
    tokens = [t for t in re.split(r"_+", str(model).lower()) if t]
    for kind, needles in _POLARITY_PATTERNS:
        for token in tokens:
            if token in needles:
                return kind
    raise ValueError(
        f"cannot infer polarity from device model {model!r}; "
        "expected an nfet/nmos/pch-style token or an explicit pack binding")


@dataclass(frozen=True)
class Mos:
    name: str
    model: str
    drain: str
    gate: str
    source: str
    body: str
    w_nm: int
    l_nm: int
    m: int = 1
    nf: int = 1

    @property
    def kind(self):
        """Polarity from the device model name, not a per-PDK lookup table.

        A semantic rule pack can bind models explicitly (``Pack.model_polarity``);
        this is the pack-free fallback used by parsing, which must accept any
        PDK's MOS model naming (nfet_05v0, sky130_fd_pr__nfet_01v8, ...).
        """
        return model_polarity(self.model)


@dataclass(frozen=True)
class CircuitGraph:
    name: str
    ports: tuple[str, ...]
    devices: tuple[Mos, ...]


@dataclass(frozen=True)
class PlacementContract:
    high: str
    low: str
    n_body: str
    p_body: str
    pdk: str = "gf180mcu_5v"
    mode: str = "free_placement_external_taps"
    floorplan: str = "two_polarity_bands"
    device_recipe: str = "gf180_5v_single_finger_contacted_v1"
    # External body taps, no inter-device wires or power rails. Each S/D/G has
    # an independent contacted M1 landing. Does NOT promise standard-cell ABI.
    grid_nm: int = 5
    solver_seconds: float = 10.0

    def validate(self, circuit, *, pack=None):
        # The contract names a process; the rule pack supplies that process's
        # grid, recipe names and device models. A contract whose PDK has no
        # pack is rejected as a data error, not silently defaulted.
        from clay.pdk.semantic import default_pack
        p = default_pack() if pack is None else pack
        if self.pdk != p.pdk_id or self.mode != "free_placement_external_taps":
            raise ValueError(f"Unsupported PDK/placement contract: {self.pdk!r} vs pack {p.pdk_id!r}")
        allowed_recipes = {p.recipe("contacts"), p.recipe("geometry"),
                           "single_finger_contacted_v1", "contact_rows_v2"}
        if self.floorplan not in {"two_polarity_bands", "free_single_pair_rotations"} \
                or not any(self.device_recipe.endswith(r) or self.device_recipe == r
                           for r in allowed_recipes):
            raise ValueError("Unsupported floorplan/device recipe")
        if self.grid_nm != p.nm("grid") or not 0 < self.solver_seconds <= 120:
            raise ValueError("Invalid grid or solver budget")
        if self.high == self.low or self.n_body == self.p_body:
            raise ValueError("Distinct supply and body domains required")
        if not {self.high, self.low, self.n_body, self.p_body} <= set(circuit.ports):
            raise ValueError("Supply/body roles must name declared ports")
        for d in circuit.devices:
            if d.m != 1 or d.nf != 1:
                raise ValueError("This version requires M=1 and nf=1 independently")
            if d.body != (self.p_body if d.kind == "pmos" else self.n_body):
                raise ValueError("Unsupported private body domain")


def length_nm(value):
    m = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)", value)
    if not m:
        raise ValueError(f"Unsupported dimension expression: {value}")
    scale = {"": "1e9", "m": "1e6", "u": "1e3", "n": "1", "p": ".001"}
    if m[2].lower() not in scale:
        raise ValueError(f"Unsupported dimension suffix: {value}")
    v = Decimal(m[1]) * Decimal(scale[m[2].lower()])
    if v <= 0 or v != int(v) or int(v) % 5:
        raise ValueError(f"Dimension must be positive and on the 5 nm grid: {value}")
    return int(v)


def parse_cdl(text: str, cell: str | None = None) -> CircuitGraph:
    """Select one subcircuit. Reject unknown content instead of losing devices."""
    logical = []
    for line in text.splitlines():
        line = line.split("$", 1)[0].strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not logical:
                raise ValueError("Orphan continuation")
            logical[-1] += " " + line[1:]
        else:
            logical.append(line)
    blocks, current = {}, None
    for line in logical:
        tok = line.split()
        op = tok[0].lower()
        if op == ".subckt":
            if current is not None or len(tok) < 3 or tok[1] in blocks:
                raise ValueError("Invalid/duplicate SUBCKT")
            current = (tok[1], tuple(tok[2:]), [])
        elif op == ".ends":
            if current is None or (len(tok) > 1 and tok[1] != current[0]):
                raise ValueError("Mismatched ENDS")
            blocks[current[0]] = current
            current = None
        elif current is not None:
            current[2].append(line)
        else:
            raise ValueError(f"Unsupported top-level statement: {line}")
    if current is not None:
        raise ValueError("Unterminated SUBCKT")
    if cell is None:
        if len(blocks) != 1:
            raise ValueError("Specify a cell when CDL has multiple subcircuits")
        cell = next(iter(blocks))
    if cell not in blocks:
        raise ValueError("Requested cell missing")
    name, ports, lines = blocks[cell]
    if len(set(ports)) != len(ports):
        raise ValueError("Duplicate ports")
    devices = []
    for line in lines:
        t = line.split()
        if not t[0].lower().startswith("m") or len(t) < 8:
            raise ValueError(f"Unsupported instance: {line}")
        model = t[5].lower()
        # Polarity comes from the model name, so any PDK's MOS models parse.
        model_polarity(model)
        pairs = re.findall(r"(\w+)\s*=\s*([^\s=]+)", " ".join(t[6:]))
        residue = re.sub(r"\w+\s*=\s*[^\s=]+", "", " ".join(t[6:])).strip()
        params = {k.lower(): v for k, v in pairs}
        if residue or len(params) != len(pairs) or not {"w", "l"} <= params.keys() or params.keys() - {"w", "l", "m", "nf"}:
            raise ValueError(f"Unsupported/duplicate MOS parameters: {line}")
        mult = []
        for key in ("m", "nf"):
            v = Decimal(params.get(key, "1"))
            if v != int(v) or v < 1:
                raise ValueError("Multiplicity/fingers must be positive integers")
            mult.append(int(v))
        devices.append(Mos(t[0], model, *t[1:5], length_nm(params["w"]), length_nm(params["l"]), *mult))
    if not devices or len({d.name for d in devices}) != len(devices):
        raise ValueError("Empty cell or duplicate devices")
    return CircuitGraph(name, ports, tuple(devices))


def match_inverters(circuit, contract):
    """Hand-authored topology hypothesis; learning selects geometry preferences.

    No name/W/L conditions, and no body/source merging. Ambiguity is rejected.
    """
    contract.validate(circuit)
    ns = [d for d in circuit.devices if d.kind == "nmos"]
    ps = [d for d in circuit.devices if d.kind == "pmos"]
    pairs = []
    for n in ns:
        candidates = [p for p in ps if n.gate == p.gate and n.drain == p.drain
                      and n.source == contract.low and p.source == contract.high
                      and len({n.gate, n.drain, contract.low, contract.high}) == 4]
        if len(candidates) != 1:
            raise ValueError("Circuit is outside the unambiguous inverter-composition hypothesis")
        pairs.append((n, candidates[0]))
    if len(pairs) * 2 != len(circuit.devices) or len({p.name for _, p in pairs}) != len(pairs):
        raise ValueError("Primitive does not cover each device exactly once")
    # Net-derived levelization; no instance identifiers in the matching policy.
    ordered, pending = [], list(pairs)
    while pending:
        ready = [pair for pair in pending if not any(other[0].drain == pair[0].gate for other in pending)]
        if not ready:
            raise ValueError("Cyclic inverter network is not supported")
        # Equal independent branches are physically symmetric; dimensions/port
        # order supply a stable tie break under name changes and line reordering.
        ready.sort(key=lambda pair: (circuit.ports.index(pair[0].gate) if pair[0].gate in circuit.ports else -1,
                                    pair[0].w_nm, pair[1].w_nm, pair[0].l_nm, pair[1].l_nm))
        for pair in ready:
            ordered.append(pair)
            pending.remove(pair)
    return tuple(ordered)
