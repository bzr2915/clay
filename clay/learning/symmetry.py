"""Netlist mirror symmetry: an involutive automorphism of the device-net graph.

A differential circuit is symmetric under a swap of its two halves: inputs,
outputs and every internal node trade places, devices map onto equal devices,
and shared nodes (supplies, clock, tail) stay put. That swap ``sigma`` is what
a "matched pair" means structurally, independent of instance or net names.

The search is name-free. Seeds are pairs of *ports* that an automorphism could
exchange; for each seed the bipartite device/net graph is matched against a
copy in which the two seeds have swapped labels. ``networkx``'s VF2 matcher
enumerates automorphisms; the first *involution* (``sigma(sigma(x)) == x``)
that moves the most devices wins. Ports must map to ports, which keeps the
cell's interface fixed.

The result is an *observation about the netlist*. Whether layouts honour it
(and about which axis) is learned elsewhere, from training layouts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

import networkx as nx
from networkx.algorithms import isomorphism

SCHEMA = "clay.netlist-symmetry.v1"


@dataclass(frozen=True)
class Symmetry:
    net_map: Mapping[str, str]
    device_map: Mapping[str, str]
    seed: Tuple[str, str]
    pairs: Tuple[Tuple[str, str], ...]          # (a, sigma(a)) with a < sigma(a)
    fixed: Tuple[str, ...]                      # devices with sigma(d) == d
    candidates_tried: int = 0

    def mate(self, device: str) -> str:
        return self.device_map.get(device, device)

    def as_dict(self) -> Dict[str, object]:
        return {"schema": SCHEMA, "seed": list(self.seed), "pairs": [list(p) for p in self.pairs],
                "fixed": list(self.fixed),
                "net_swaps": sorted([a, b] for a, b in self.net_map.items() if a < b),
                "candidates_tried": self.candidates_tried}


def _device_label(d) -> Tuple:
    return (d.cls, d.model, d.w_nm, d.l_nm, d.nf * d.m)


def _graph(circuit, port_label) -> nx.Graph:
    g = nx.Graph()
    for d in circuit.devices:
        g.add_node(("d", d.name), label=("dev",) + _device_label(d))
        roles: Dict[str, List[str]] = {}
        for term, net in d.nets.items():
            # Source and drain are interchangeable for a MOS; record them as one role.
            # Likewise the two ends of a resistor.
            role = "sd" if (d.cls == "mos" and term in ("s", "d")) else                 "ends" if (d.cls == "res_poly" and term in ("a", "b")) else term
            roles.setdefault(net, []).append(role)
        for net, rl in roles.items():
            g.add_node(("n", net), label=port_label(net))
            g.add_edge(("d", d.name), ("n", net), label=tuple(sorted(rl)))
    return g


def _structure_key(circuit, dev_map, net_map) -> Tuple:
    """Name-free description of an involution, used before names in the tie-break.

    Prefer swapping more devices, then more nets, then pairs of larger devices
    (sorted device labels of the moved pairs). Two involutions equal under this
    key differ only in which equal-looking devices they pair, and only then do
    names decide.
    """
    labels = {d.name: _device_label(d) for d in circuit.devices}
    moved_pairs = sorted(tuple(sorted((repr(labels[a]), repr(labels[b]))))
                         for a, b in dev_map.items() if a < b)
    nets_swapped = sum(1 for a, b in net_map.items() if a != b)
    devices_moved = sum(1 for a, b in dev_map.items() if a != b)
    return (-devices_moved, -nets_swapped, tuple(moved_pairs))


def netlist_symmetries(circuit, *, max_iterations: int = 5000) -> Tuple[Symmetry, ...]:
    """Every involutive port-preserving automorphism found, best first.

    The options of the symmetry-mapping decision level (design doc §6.3, S4).
    Each seed port pair contributes the involutions among its first
    ``max_iterations`` VF2 mappings, so the set is complete only when no seed
    reaches that limit. Ordered by the declared rule of :func:`netlist_symmetry`
    (structure first, names last); a mapping found from several seeds is kept
    once, with the first seed that found it.

    Deterministic by construction (a primitive must be a function of its
    input): the VF2 matcher fills internal ``set``s with graph nodes, and with
    string-tuple nodes their iteration order followed Python's per-process hash
    seed. Nodes are therefore relabelled to integers, whose hashes do not
    depend on the seed, and the result is sorted by the rule, never left in
    discovery order.
    """
    ports = set(circuit.ports)
    nets_present = set(circuit.nets())

    def base_label(net):
        return ("port",) if net in ports else ("net",)

    # Cheap colour refinement to keep only seed pairs that could swap.
    g0 = _graph(circuit, base_label)
    colours = nx.weisfeiler_lehman_subgraph_hashes(g0, node_attr="label", iterations=4)

    def colour(net):
        return colours[("n", net)][-1]

    port_list = [p for p in circuit.ports if p in nets_present]
    seeds = [(a, b) for i, a in enumerate(port_list) for b in port_list[i + 1:]
             if colour(a) == colour(b)]
    found: Dict[Tuple, Tuple[Symmetry, int]] = {}
    tried = 0
    for a, b in seeds:
        tried += 1

        def lab1(net, a=a, b=b):
            return ("seedA",) if net == a else ("seedB",) if net == b else base_label(net)

        def lab2(net, a=a, b=b):
            return ("seedB",) if net == a else ("seedA",) if net == b else base_label(net)

        g1 = nx.convert_node_labels_to_integers(_graph(circuit, lab1), label_attribute="orig")
        g2 = nx.convert_node_labels_to_integers(_graph(circuit, lab2), label_attribute="orig")
        o1 = nx.get_node_attributes(g1, "orig")
        o2 = nx.get_node_attributes(g2, "orig")
        gm = isomorphism.GraphMatcher(
            g1, g2, node_match=lambda x, y: x["label"] == y["label"],
            edge_match=lambda x, y: x["label"] == y["label"])
        for count, raw in enumerate(gm.isomorphisms_iter()):
            if count >= max_iterations:
                break
            mapping = {o1[k]: o2[v] for k, v in raw.items()}
            if any(mapping[mapping[k]] != k for k in mapping):
                continue
            net_map = {k[1]: v[1] for k, v in mapping.items() if k[0] == "n"}
            dev_map = {k[1]: v[1] for k, v in mapping.items() if k[0] == "d"}
            pairs = tuple(sorted((k, v) for k, v in dev_map.items() if k < v))
            key = (_structure_key(circuit, dev_map, net_map), pairs,
                   tuple(sorted(net_map.items())))
            if key not in found:
                found[key] = (Symmetry(net_map, dev_map, (a, b), pairs,
                                       tuple(sorted(k for k, v in dev_map.items() if k == v))),
                              tried)
    return tuple(Symmetry(s.net_map, s.device_map, s.seed, s.pairs, s.fixed, tried)
                 for key, (s, _) in sorted(found.items(), key=lambda kv: kv[0]))


def netlist_symmetry(circuit, *, max_iterations: int = 5000) -> Optional[Symmetry]:
    """Best involutive port-preserving automorphism, or ``None`` if there is none.

    The first of :func:`netlist_symmetries`: ranked by a declared rule rather
    than by discovery order, structure first (:func:`_structure_key`), names
    last. This is the mapping a construction uses when the symmetry mapping is
    fixed rather than searched (``fixed_choices`` in the certificate).
    """
    found = netlist_symmetries(circuit, max_iterations=max_iterations)
    return found[0] if found else None


def _twin_names(circuit) -> Dict[str, str]:
    """Device -> the representative of its twin class.

    Twins have the same label (class, model, size, multiplicity) and the same
    connections, with MOS source/drain and resistor ends interchangeable.
    Exchanging two twins is itself an automorphism of the netlist that fixes
    every net, so it changes nothing a layout depends on.
    """
    groups: Dict[Tuple, List[str]] = {}
    for d in circuit.devices:
        roles = []
        for term, net in d.nets.items():
            role = "sd" if (d.cls == "mos" and term in ("s", "d")) else \
                "ends" if (d.cls == "res_poly" and term in ("a", "b")) else term
            roles.append((role, net))
        groups.setdefault((_device_label(d), tuple(sorted(roles))), []).append(d.name)
    return {name: min(names) for names in groups.values() for name in names}


def symmetry_classes(circuit, symmetries) -> Tuple[Tuple[Symmetry, ...], ...]:
    """Group mappings that differ only in which twins they exchange.

    The reduce step of the symmetry-mapping level (design doc §6.3, S4). Two
    mappings are grouped when renaming every device to its twin representative
    makes their pairs, fixed devices and net maps equal; then one is the other
    composed with a twin exchange, which is a netlist automorphism fixing all
    nets, so they yield the same construction up to the names of identical
    devices. Sound: mappings that are not provably equivalent stay apart.
    Classes keep the order of their first member (the rule order), and each
    class lists its members in that order.
    """
    twin = _twin_names(circuit)
    classes: Dict[Tuple, List[Symmetry]] = {}
    for s in symmetries:
        key = (tuple(sorted(tuple(sorted((twin[a], twin[b]))) for a, b in s.pairs)),
               tuple(sorted(twin[f] for f in s.fixed)),
               tuple(sorted(s.net_map.items())))
        classes.setdefault(key, []).append(s)
    return tuple(tuple(members) for members in classes.values())
