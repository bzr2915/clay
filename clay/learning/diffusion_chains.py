"""Finger folding and diffusion-sharing chains for arbitrary MOS netlists.

This is the general replacement for the inverter's "one NMOS row + one PMOS
row" assumption. Two operations, both stated on nets only:

``fold_devices``
    Split each MOS/varactor into fingers: the netlist's own ``nf * m``, times
    an optional extra fold factor that brings each finger under a target
    width. Folding never changes total W or L; it is a layout freedom the
    decision step chooses, not a learned imitation.

``diffusion_chains``
    Order fingers of one *compatible group* (same diffusion class, model, L,
    finger W and well domain) into chains where neighbouring channels share a
    diffusion segment. Sharing is admitted only where the shared terminals are
    on the same net -- the paper's condition (3), ``v(t_a) = v(t_b)``. Each
    group is decomposed into the fewest trails of its net multigraph (an Euler
    decomposition). When a netlist symmetry ``sigma`` is supplied, chains are
    built so that ``sigma`` maps the chain set onto itself: a component and its
    mirror component get mirror-image chains, and a sigma-invariant component
    gets one palindromic chain where the trail structure allows it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "clay.diffusion-chains.v1"


@dataclass(frozen=True)
class Finger:
    device: str
    index: int
    cls: str
    kind: str
    model: str
    l_nm: int
    w_nm: int
    gate: str
    source: str
    drain: str
    well: Optional[str]          # net of the n-well this diffusion sits in, else None

    @property
    def key(self) -> Tuple[str, int]:
        return (self.device, self.index)

    @property
    def group(self) -> Tuple:
        return (self.cls, self.kind, self.model, self.l_nm, self.w_nm, self.well)


@dataclass(frozen=True)
class Chain:
    name: str
    group: Tuple
    fingers: Tuple[Finger, ...]
    segment_nets: Tuple[str, ...]
    gate_nets: Tuple[str, ...]
    mirror_of: Optional[str] = None      # name of the chain this one mirrors
    self_symmetric: bool = False

    @property
    def size(self) -> int:
        return len(self.fingers)

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "cls": self.group[0], "kind": self.group[1],
                "model": self.group[2], "l_nm": self.group[3], "w_nm": self.group[4],
                "well": self.group[5], "fingers": [f"{f.device}#{f.index}" for f in self.fingers],
                "segment_nets": list(self.segment_nets), "gate_nets": list(self.gate_nets),
                "mirror_of": self.mirror_of, "self_symmetric": self.self_symmetric}


def fold_factor(device, target_nm: Optional[int], *, pack) -> int:
    """Smallest extra fold ``k`` putting each finger at or under ``target_nm``.

    A fold is admissible only if the finger width stays on grid and at or
    above the diffusion minimum (``width_min``; ``var_min_width`` for a
    varactor). With no admissible fold under the target the largest admissible
    fold below it is used, and with none at all the device keeps its own nf.
    """
    base = device.nf * device.m
    if target_nm is None:
        return 1
    grid = pack.nm("grid")
    minimum = pack.nm("var_min_width") if device.cls == "cap_var" and pack.has("var_min_width") \
        else pack.nm("width_min")
    # A finger must also hold one contacted S/D column.
    enclosure = pack.nm("tap_cut_enclosure") if device.cls == "cap_var" and \
        pack.has("tap_cut_enclosure") else pack.nm("cut_to_active")
    minimum = max(minimum, pack.nm("cut") + 2 * enclosure)
    finger = device.w_nm // device.nf
    best = 1
    k = 1
    while finger // k >= minimum:
        if finger % k == 0 and (finger // k) % grid == 0:
            best = k
            if finger // k <= target_nm:
                return k
        k += 1
    del base
    return best


def fold_devices(circuit, target_nm: Optional[int] = None, *, pack,
                 targets: Optional[Mapping[str, int]] = None) -> Tuple[Finger, ...]:
    """Fingers of every diffusion device; ``targets`` may set a per-device target."""
    out: List[Finger] = []
    for d in circuit.devices:
        if d.cls not in ("mos", "cap_var"):
            continue
        tgt = targets.get(d.name, target_nm) if targets else target_nm
        k = fold_factor(d, tgt, pack=pack)
        n = d.nf * d.m * k
        if d.w_nm % (d.nf * k):
            raise ValueError(f"{d.name}: W={d.w_nm} does not split into {d.nf * k} fingers")
        fw = d.w_nm // (d.nf * k)
        if d.cls == "mos":
            gate, s, dr = d.net("g"), d.net("s"), d.net("d")
            well = d.net("b") if d.kind == "pmos" else None
        else:                                # varactor: gate = top, both sides = bot
            gate, s, dr = d.net("top"), d.net("bot"), d.net("bot")
            well = d.net("bot")
        for i in range(n):
            # Alternate S/D so a multi-finger device chains as s d s d ... by itself.
            a, b = (s, dr) if i % 2 == 0 else (dr, s)
            out.append(Finger(d.name, i, d.cls, d.kind, d.model, d.l_nm, fw, gate, a, b, well))
    return tuple(out)


# --------------------------------------------------------------------------
# trails
# --------------------------------------------------------------------------

def _trails(fingers: Sequence[Finger], start_pref: Optional[Sequence[str]] = None) -> List[List[Tuple[Finger, str, str]]]:
    """Minimum trail decomposition of a connected finger multigraph.

    Returns trails as lists of ``(finger, from_net, to_net)``. Odd-degree nets
    are paired by virtual edges, one Euler circuit/trail is walked, and the
    walk is cut at the virtual edges: ``max(1, odd/2)`` trails, the minimum.
    Deterministic: edges are visited in input order.
    """
    adj: Dict[str, List[int]] = {}
    edges: List[Tuple[Optional[Finger], str, str]] = []
    for f in fingers:
        edges.append((f, f.source, f.drain))
    for i, (_, a, b) in enumerate(edges):
        adj.setdefault(a, []).append(i)
        adj.setdefault(b, []).append(i)
    odd = [n for n, es in adj.items() if len(es) % 2]
    virtual = set()
    for a, b in zip(odd[1::2], odd[2::2]):
        i = len(edges)
        edges.append((None, a, b))
        adj[a].append(i)
        adj[b].append(i)
        virtual.add(i)
    if odd:
        start = odd[0]
    elif start_pref:
        start = next((n for n in start_pref if n in adj), next(iter(adj)))
    else:
        start = next(iter(adj))
    used = [False] * len(edges)
    ptr = {n: 0 for n in adj}
    stack: List[Tuple[str, Optional[int]]] = [(start, None)]
    circuit: List[Tuple[str, Optional[int]]] = []
    while stack:
        node, via = stack[-1]
        es = adj[node]
        while ptr[node] < len(es) and used[es[ptr[node]]]:
            ptr[node] += 1
        if ptr[node] == len(es):
            circuit.append(stack.pop())
        else:
            e = es[ptr[node]]
            used[e] = True
            _, a, b = edges[e]
            stack.append((b if a == node else a, e))
    circuit.reverse()
    walk: List[Tuple[int, str, str]] = []
    for (prev, _), (node, e) in zip(circuit, circuit[1:]):
        walk.append((e, prev, node))
    trails: List[List[Tuple[Finger, str, str]]] = [[]]
    for e, a, b in walk:
        if e in virtual:
            trails.append([])
        else:
            trails[-1].append((edges[e][0], a, b))
    # An Euler *circuit* cut at a virtual edge wraps around: join the tail onto the head.
    if odd and len(trails) > 1 and trails[0] and trails[-1] and trails[-1][-1][2] == trails[0][0][1]:
        trails[0] = trails[-1] + trails[0]
        trails.pop()
    return [t for t in trails if t]


def _components(fingers: Sequence[Finger]) -> List[List[Finger]]:
    parent: Dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f in fingers:
        parent[find(f.source)] = find(f.drain)
    comps: Dict[str, List[Finger]] = {}
    for f in fingers:
        comps.setdefault(find(f.source), []).append(f)
    return list(comps.values())


def _split(trail, max_fingers: Optional[int]):
    if not max_fingers or len(trail) <= max_fingers:
        return [trail]
    n = -(-len(trail) // max_fingers)
    size = -(-len(trail) // n)
    return [trail[i:i + size] for i in range(0, len(trail), size)]


def _as_chain(name, trail, **kw) -> Chain:
    fingers = []
    for f, a, b in trail:
        # Orient the finger along the trail: its left terminal is ``a``.
        if (f.source, f.drain) != (a, b):
            f = Finger(f.device, f.index, f.cls, f.kind, f.model, f.l_nm, f.w_nm, f.gate, a, b, f.well)
        fingers.append(f)
    nets = [trail[0][1]] + [b for _, _, b in trail]
    return Chain(name, trail[0][0].group, tuple(fingers), tuple(nets),
                 tuple(f.gate for f in fingers), **kw)


def _map_finger(f: Finger, sym, by_key) -> Finger:
    return by_key[(sym.mate(f.device), f.index)]


def _mirror_trail(trail, sym, by_key):
    """sigma applied to a trail, walked in reverse (the mirror image)."""
    out = []
    for f, a, b in reversed(trail):
        g = _map_finger(f, sym, by_key)
        out.append((g, sym.net_map.get(b, b), sym.net_map.get(a, a)))
    return out


def _symmetric_trail(comp, sym, by_key):
    """One sigma-palindromic trail through a sigma-invariant component, or None.

    Pick one finger of every mirror pair (the half ``H``), walk an Euler trail
    of ``H`` that ends on a sigma-fixed net, then append its mirror image.
    sigma-fixed fingers are only admitted as a closed walk at that centre net.
    """
    fixed_net = lambda n: sym.net_map.get(n, n) == n
    pairs, fixed = [], []
    seen = set()
    for f in comp:
        g = _map_finger(f, sym, by_key)
        if g.key == f.key:
            fixed.append(f)
        elif f.key not in seen:
            seen.update({f.key, g.key})
            pairs.append((f, g))
    side_a, side_b = set(), set()
    half = []
    for f, g in pairs:
        score_f = sum((n in side_a) - (n in side_b) for n in (f.source, f.drain) if not fixed_net(n))
        score_g = sum((n in side_a) - (n in side_b) for n in (g.source, g.drain) if not fixed_net(n))
        pick = f if score_f >= score_g else g
        half.append(pick)
        for n in (pick.source, pick.drain):
            if not fixed_net(n):
                side_a.add(n)
                side_b.add(sym.net_map.get(n, n))
    if not half:
        return None
    if len(_components(half)) != 1:
        return None
    trails = _trails(half)
    if len(trails) != 1:
        return None
    t = trails[0]
    # Make the trail end on a sigma-fixed net (reverse it if only the start is).
    if not fixed_net(t[-1][2]):
        if fixed_net(t[0][1]):
            t = [(f, b, a) for f, a, b in reversed(t)]
        else:
            return None
    centre = t[-1][2]
    middle = []
    if fixed:
        if any(not (fixed_net(f.source) and fixed_net(f.drain)) for f in fixed):
            return None
        ct = _trails(fixed, start_pref=[centre])
        if len(ct) != 1 or ct[0][0][1] != centre or ct[0][-1][2] != centre:
            return None
        middle = ct[0]
    return t + middle + _mirror_trail(t, sym, by_key)


def diffusion_chains(fingers: Sequence[Finger], symmetry=None, *,
                     max_fingers: Optional[int] = None, share: bool = True) -> Tuple[Chain, ...]:
    """Chains covering every finger exactly once (see module docstring).

    ``share=False`` gives every device its own chain (fingers of one device
    still share their own S/D), which is the training layouts' style and the
    baseline the decision step compares against.
    """
    by_key = {f.key: f for f in fingers}
    groups: Dict[Tuple, List[Finger]] = {}
    for f in fingers:
        groups.setdefault(f.group, []).append(f)
    chains: List[Chain] = []
    names = count()
    done = set()

    def new_name():
        return f"c{next(names)}"

    for group in sorted(groups, key=str):
        members = groups[group]
        if share:
            comps = _components(members)
        else:
            per_dev: Dict[str, List[Finger]] = {}
            for f in members:
                per_dev.setdefault(f.device, []).append(f)
            comps = list(per_dev.values())
        comps.sort(key=lambda c: (min(f.key for f in c)))
        for comp in comps:
            keys = frozenset(f.key for f in comp)
            if keys & done:
                continue
            if symmetry is None:
                for trail in _trails(comp):
                    for piece in _split(trail, max_fingers):
                        chains.append(_as_chain(new_name(), piece))
                done |= keys
                continue
            mate_keys = frozenset((symmetry.mate(d), i) for d, i in keys)
            if mate_keys != keys:
                if not mate_keys <= set(by_key):
                    raise ValueError("symmetry maps a finger outside the netlist")
                for trail in _trails(comp):
                    for piece in _split(trail, max_fingers):
                        a = _as_chain(new_name(), piece)
                        b = _as_chain(new_name(), _mirror_trail(piece, symmetry, by_key),
                                      mirror_of=a.name)
                        chains.extend([a, b])
                done |= keys | mate_keys
                continue
            sym_trail = _symmetric_trail(comp, symmetry, by_key)
            if sym_trail is not None and (not max_fingers or len(sym_trail) <= max_fingers):
                chains.append(_as_chain(new_name(), sym_trail, self_symmetric=True))
                done |= keys
                continue
            # Fallback: mirror pairs as separate mirrored chains, fixed fingers alone.
            pair_half, fixed = [], []
            seen = set()
            for f in comp:
                g = _map_finger(f, symmetry, by_key)
                if g.key == f.key:
                    fixed.append(f)
                elif f.key not in seen:
                    seen.update({f.key, g.key})
                    pair_half.append(f)
            for sub in _components(pair_half) if pair_half else []:
                for trail in _trails(sub):
                    for piece in _split(trail, max_fingers):
                        a = _as_chain(new_name(), piece)
                        b = _as_chain(new_name(), _mirror_trail(piece, symmetry, by_key),
                                      mirror_of=a.name)
                        chains.extend([a, b])
            for sub in _components(fixed) if fixed else []:
                for trail in _trails(sub):
                    for piece in _split(trail, max_fingers):
                        chains.append(_as_chain(new_name(), piece, self_symmetric=_palindrome(piece, symmetry)))
            done |= keys
    covered = sorted(f.key for c in chains for f in c.fingers)
    if covered != sorted(f.key for f in fingers):
        raise ValueError("chains do not cover every finger exactly once")
    for c in chains:
        for f, a, b in zip(c.fingers, c.segment_nets, c.segment_nets[1:]):
            if {f.source, f.drain} != {a, b} and not (f.source == f.drain == a == b):
                raise ValueError(f"chain {c.name} shares diffusion across distinct nets")
    return tuple(chains)


def _palindrome(trail, sym) -> bool:
    nets = [trail[0][1]] + [b for _, _, b in trail]
    gates = [f.gate for f, _, _ in trail]
    m = lambda n: sym.net_map.get(n, n) if sym else n
    return [m(n) for n in reversed(nets)] == nets and [m(g) for g in reversed(gates)] == gates


def chains_report(chains: Sequence[Chain]) -> Dict[str, object]:
    shared = sum(c.size - 1 for c in chains)
    return {"schema": SCHEMA, "chains": len(chains), "fingers": sum(c.size for c in chains),
            "shared_segments": shared,
            "mirror_pairs": sum(1 for c in chains if c.mirror_of),
            "self_symmetric": sum(1 for c in chains if c.self_symmetric),
            "max_fingers": max((c.size for c in chains), default=0)}
