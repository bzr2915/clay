"""Small, explicit induction of diffusion chains and contact policies.

PDK neutrality
--------------
Nothing in this module knows about GF180. Every number comes from a
:class:`clay.pdk.semantic.Pack` (the *only* PDK input) and every layer is
addressed by semantic role (``active``, ``poly``, ``contact``, ``metal1``,
``nwell``, ``nplus``/``pplus``, optional markers). Swapping the pack swaps the
process: the same behaviour runs on GF180MCU 5 V or SKY130A 1.8 V, and a PDK
that lacks a marker layer simply does not get one.

Learned: observed chain net patterns, count-vs-width stump, dimensionless
contact offsets. Manual: feature vocabulary, fitting loss, rectangular
constructors and exhaustive orientation search. Not learned DRC.
"""
from dataclasses import asdict
import json
from pathlib import Path
from statistics import mean
import klayout.db as db

from clay.pdk.semantic import default_pack
from clay.layout.gds_write import write_gds
from clay.layout.synth.area_placement import solve_area
from clay.learning.freedom import (
    Action, Part, ROTATE, build_freedom, enforce, executable_rotations)
from clay.learning.generate import measure_layout
from clay.rotations import normalize_to_origin, turn_transform

SCHEMA = 'clay.contact-primitive.v2'


def _pack(pack):
    """The pack to use; ``None`` means the historical GF180 default."""
    return default_pack() if pack is None else pack


def _grid_up(value, grid):
    """Round ``value`` up to the next multiple of ``grid``."""
    return -(-int(value) // int(grid)) * int(grid)


def _polarity(pack, device):
    """Device polarity from the pack's explicit model binding.

    A pack binds the device models it supports (``Pack.model_polarity``). An
    unbound model is a fail-closed error: the operator must not silently treat
    a differently-sized device (e.g. a 3.3 V nfet) as if it were the pack's
    device, because the rule pack carries that voltage class's constraints only.
    """
    try:
        return pack.polarity(device.model)
    except KeyError:
        raise ValueError(
            f"device model {device.model!r} is not bound by rule pack {pack.pdk_id!r}; "
            f"known models: {sorted(pack.model_polarity)}") from None


def inverter_groups(circuit, contract, *, pack=None):
    p = _pack(pack)
    if contract.pdk != p.pdk_id:
        raise ValueError(f'Contract pdk {contract.pdk!r} does not match rule pack {p.pdk_id!r}')
    if contract.mode != 'free_placement_external_taps' or contract.floorplan != 'free_single_pair_rotations':
        raise ValueError('Unsupported placement contract')
    if contract.grid_nm != p.nm('grid') or not 0 < contract.solver_seconds <= 120:
        raise ValueError('Invalid grid/solver budget')
    groups = {k: [d for d in circuit.devices if _polarity(p, d) == k] for k in ('nmos', 'pmos')}
    if not all(groups.values()):
        raise ValueError('Both polarities required')
    ins = {d.gate for d in circuit.devices}
    if len(ins) != 1:
        raise ValueError('Not a common-gate inverter')
    output = set()
    for kind, devices in groups.items():
        rail = contract.low if kind == 'nmos' else contract.high
        body = contract.n_body if kind == 'nmos' else contract.p_body
        if rail not in circuit.ports or body not in circuit.ports:
            raise ValueError('Missing supply/body ports')
        if len({(d.w_nm, d.l_nm, d.model) for d in devices}) != 1:
            raise ValueError('Nonuniform parallel group')
        for d in devices:
            if d.m != 1 or d.nf != 1 or d.body != body:
                raise ValueError('Unsupported multiplicity/body')
            terminals = {d.source, d.drain}
            if len(terminals) != 2 or rail not in terminals:
                raise ValueError('Not parallel inverter MOS')
            output.update(terminals - {rail})
    if len(output) != 1 or len(ins | output | {contract.high, contract.low}) != 4:
        raise ValueError('Invalid inverter nets')
    return groups, next(iter(ins)), next(iter(output))


def limits(width, count, *, pack=None):
    """Legal range for the first contact centre along the active height."""
    r = _pack(pack)
    lo = r.nm('cut_to_active') + r.nm('cut') // 2
    hi = width - lo - (count - 1) * r.nm('cut_pitch')
    if hi < lo:
        raise ValueError('Predicted contact count exceeds physical capacity')
    return lo, hi


def fit(observations, contracts, *, pack=None):
    p = _pack(pack)
    if len({o['gds_sha256'] for o in observations}) != len(observations):
        raise ValueError('Duplicate training source')
    if len({o['sample_id'] for o in observations}) != len(observations):
        raise ValueError('Repeated training parent')
    pitch = p.nm('cut_pitch')
    policies = {}
    evidence = {}
    patterns = {}
    for kind in ('nmos', 'pmos'):
        examples = []
        chains = []
        for obs, ct in zip(observations, contracts):
            rail = ct.low if kind == 'nmos' else ct.high
            circ = obs['circuit']
            gates = {d['gate'] for d in circ['devices']}
            if len(gates) != 1:
                raise ValueError('Training must be an inverter')
            gate = next(iter(gates))
            outs = set()
            for d in circ['devices']:
                if _model_polarity(p, d['model']) == kind:
                    outs.update({d['source'], d['drain']} - {rail})
            if len(outs) != 1:
                raise ValueError('Ambiguous output')
            output = next(iter(outs))
            for island in obs['islands']:
                if island['kind'] != kind:
                    continue
                if not island['vertical_gates']:
                    raise ValueError('Training extractor expects vertical gates')
                a = island['active_bbox_nm']
                w = a[3] - a[1]
                counts = {s['contact_count'] for s in island['diffusion_segments']}
                if len(counts) != 1:
                    raise ValueError('Nonuniform per-segment contacts need richer policy')
                count = next(iter(counts))
                lo, hi = limits(w, count, pack=p)
                if count < 1:
                    raise ValueError('Uncontacted segment')
                alphas = []
                for s in island['diffusion_segments']:
                    ys = sorted(y for x, y in s['contact_centers_local_nm'])
                    if any(b - a != pitch for a, b in zip(ys, ys[1:])):
                        raise ValueError('Observed pitch outside current operator vocabulary')
                    alpha = (ys[0] - lo) / (hi - lo) if hi > lo else .5
                    if not 0 <= alpha <= 1:
                        raise ValueError('Observed contact outside legal margin')
                    alphas.append(alpha)
                chain = ['rail' if s['net'] == rail else 'output' if s['net'] == output else 'unknown'
                         for s in island['diffusion_segments']]
                if 'unknown' in chain or island['gate_nets'] != [gate] * island['gate_count']:
                    raise ValueError('Unbound training chain')
                if any(x == y for x, y in zip(chain, chain[1:])):
                    raise ValueError('Degenerate chain')
                if chain[::-1] < chain:
                    chain = chain[::-1]
                chains.append({'gate_count': island['gate_count'], 'segments': chain})
                examples.append({'sample': obs['sample_id'], 'w_nm': w, 'count': count,
                                 'alpha': mean(alphas)})
        if not examples:
            raise ValueError('Missing polarity training')
        # Parent/island weighting; a three-segment active is one example, not
        # three independent samples. A single width split is identifiable only
        # as an empirical classifier, not as a physical law.
        counts = sorted({e['count'] for e in examples})
        candidates = [{'kind': 'constant', 'count': n} for n in counts]
        widths = sorted({e['w_nm'] for e in examples})
        for a, b in zip(widths, widths[1:]):
            for low in counts:
                for high in counts:
                    if low != high:
                        candidates.append({'kind': 'width_stump', 'threshold_nm': (a + b) / 2,
                                           'low': low, 'high': high})

        def pred(pol, w):
            return pol['count'] if pol['kind'] == 'constant' else \
                pol['low'] if w <= pol['threshold_nm'] else pol['high']

        def score(pol):
            return (sum((pred(pol, e['w_nm']) - e['count']) ** 2 for e in examples),
                    pol['kind'] != 'constant')

        best = min(candidates, key=score)
        policies[kind] = {'rows': best,
                          'first_contact_free_margin_fraction': mean(e['alpha'] for e in examples)}
        patterns[kind] = [dict(gate_count=k, segments=list(s))
                          for k, s in sorted({(c['gate_count'], tuple(c['segments'])) for c in chains})]
        evidence[kind] = {'examples': examples, 'count_squared_error': score(best)[0],
                          'position_fraction_residuals':
                              [e['alpha'] - policies[kind]['first_contact_free_margin_fraction']
                               for e in examples]}
    lib = {'schema': SCHEMA, 'pdk': p.pdk_id,
           # Recorded so a library cannot silently apply to a different process.
           'rule_pack': {'pdk': p.pdk_id, 'grid_nm': p.nm('grid'),
                         'cut_pitch_nm': p.nm('cut_pitch'), 'deck_sha256': p.deck_sha256},
           'patterns': patterns, 'contact_policies': policies,
           'binding': 'target_W_L_models_and_nets',
           'operator': 'rectangular_diffusion_chain_v1'}
    return lib, {'sources': [o['sample_id'] for o in observations], 'fits': evidence,
                 'limitations': 'empirical fit on the available parents; width, cell family and '
                                'sharing may be confounded; no current/EM or cross-PDK guarantee',
                 'manual_priors': 'role vocabulary, width stump family, normalized placement, '
                                  'minimum-pitch constructor, area search'}


def _model_polarity(pack, model):
    try:
        return pack.polarity(model)
    except KeyError:
        return 'nmos' if model.startswith('n') else 'pmos'


V1_SCHEMA = 'clay.contact-primitive.v1'
#: v1 libraries carried no rule-pack binding and were implicitly fitted for the
#: GF180 5 V experiment. Migrating one is only sound for that pack, whose
#: numbers this repository reproduces bit-for-bit; any other pack must refit.
_V1_PDK = 'gf180mcu_5v'


def upgrade_library(lib, *, pack=None):
    """Migrate a v1 contact library to the current schema.

    Returns a new dict carrying ``migrated_from`` so the provenance change is
    visible in every downstream report. A v2 library is returned unchanged.
    """
    p = _pack(pack)
    if lib.get('schema') == SCHEMA:
        return lib
    if lib.get('schema') != V1_SCHEMA:
        raise ValueError(f'Unknown learned contact schema/fields: {lib.get("schema")!r}')
    if lib.get('pdk') != _V1_PDK or p.pdk_id != _V1_PDK:
        raise ValueError(
            f'v1 library was fitted for {_V1_PDK!r}; it cannot be migrated to {p.pdk_id!r}. '
            'Refit against the target rule pack instead')
    upgraded = {k: v for k, v in lib.items() if k != 'schema'}
    upgraded['schema'] = SCHEMA
    upgraded['rule_pack'] = {'pdk': p.pdk_id, 'grid_nm': p.nm('grid'),
                             'cut_pitch_nm': p.nm('cut_pitch'),
                             'deck_sha256': p.deck_sha256}
    upgraded['migrated_from'] = V1_SCHEMA
    return upgraded


def validate_library(lib, *, pack=None):
    p = _pack(pack)
    lib = upgrade_library(lib, pack=p)
    if set(lib) - {'migrated_from'} != {'schema', 'pdk', 'rule_pack', 'patterns',
                                        'contact_policies', 'binding', 'operator'} \
            or lib['schema'] != SCHEMA:
        raise ValueError('Unknown learned contact schema/fields')
    # A library fitted against one process must not silently apply to another:
    # the dimensionless parts could transfer, the nm-valued ones must not.
    # Checked before the operator/binding test so a process mismatch reports
    # its actual cause instead of a generic "unsupported program".
    bound = lib['rule_pack']
    if bound.get('pdk') != p.pdk_id or bound.get('cut_pitch_nm') != p.nm('cut_pitch'):
        raise ValueError(
            f"Library was fitted for {bound.get('pdk')!r} (cut pitch {bound.get('cut_pitch_nm')} nm) "
            f"and cannot be applied to {p.pdk_id!r} (cut pitch {p.nm('cut_pitch')} nm); refit required")
    if lib['pdk'] != p.pdk_id or lib['binding'] != 'target_W_L_models_and_nets' \
            or lib['operator'] != 'rectangular_diffusion_chain_v1':
        raise ValueError('Unsupported contact program')
    if set(lib['patterns']) != {'nmos', 'pmos'} or set(lib['contact_policies']) != {'nmos', 'pmos'}:
        raise ValueError('Missing roles')
    for kind in ('nmos', 'pmos'):
        for c in lib['patterns'][kind]:
            if set(c) != {'gate_count', 'segments'} or type(c['gate_count']) != int or c['gate_count'] < 1:
                raise ValueError('Invalid chain')
            s = c['segments']
            if len(s) != c['gate_count'] + 1 or set(s) != {'rail', 'output'} \
                    or any(a == b for a, b in zip(s, s[1:])):
                raise ValueError('Invalid chain nets')
        pol = lib['contact_policies'][kind]
        if set(pol) != {'rows', 'first_contact_free_margin_fraction'} \
                or not 0 <= pol['first_contact_free_margin_fraction'] <= 1:
            raise ValueError('Invalid offset')
        r = pol['rows']
        ks = {'kind', 'count'} if r.get('kind') == 'constant' else {'kind', 'threshold_nm', 'low', 'high'}
        if set(r) != ks or r['kind'] not in {'constant', 'width_stump'}:
            raise ValueError('Invalid count predictor')
        if r['kind'] == 'width_stump' and not 0 < r['threshold_nm'] < 1e9:
            raise ValueError('Invalid threshold')
        if any(type(r[k]) != int or r[k] < 1
               for k in (['count'] if r['kind'] == 'constant' else ['low', 'high'])):
            raise ValueError('Invalid row count')


def contact_plan(lib, kind, width, *, pack=None):
    p = _pack(pack)
    validate_library(lib, pack=p)
    pol = lib['contact_policies'][kind]
    r = pol['rows']
    n = r['count'] if r['kind'] == 'constant' else r['low'] if width <= r['threshold_nm'] else r['high']
    lo, hi = limits(width, n, pack=p)
    grid = p.nm('grid')
    first = round((lo + pol['first_contact_free_margin_fraction'] * (hi - lo)) / grid) * grid
    return {'count': n, 'centers_nm': [first + i * p.nm('cut_pitch') for i in range(n)]}


class _Rules:
    """Pack view with per-construction rule overrides (e.g. a varactor's larger
    cut-to-channel spacing). Overrides name *pack keys*, never numbers, so a
    construction can only substitute one deck rule for another."""

    def __init__(self, pack, overrides=None):
        self.pack = pack
        self.over = {k: pack.nm(v) for k, v in (overrides or {}).items()}

    def nm(self, key):
        return self.over[key] if key in self.over else self.pack.nm(key)

    def has(self, key):
        return key in self.over or self.pack.has(key)


def chain_dimensions(k, l, n_contacts, *, pack=None, rule_overrides=None):
    """Segment lengths and gate-access layout of a ``k``-channel chain.

    Shared by :func:`build_row` and by callers that need a block's size before
    building it. Every term is a geometric obligation of the construction,
    stated in rule keys:

    * end segments hold one cut column with ``cut_to_active``/``cut_to_gate``
      and keep the two pads across a gate ``metal_space`` apart;
    * shared segments hold one cut column with ``cut_to_gate`` on both sides
      and keep *neighbouring* pads ``metal_space`` apart;
    * gate contacts alternate sides (``stagger``) when two neighbouring gate
      pads on one side would violate poly or metal spacing.
    """
    p = _pack(pack)
    r = _Rules(p, rule_overrides)
    grid = r.nm('grid')
    cut = r.nm('cut')
    half = cut // 2
    phx = r.nm('pad_half_x')
    cut_to_gate = r.nm('cut_to_gate')
    cut_to_active = r.nm('cut_to_active')
    first_x = cut_to_active + half
    side = max(r.nm('sd_overhang'), cut_to_active + cut + cut_to_gate)
    pad_budget = 2 * first_x + 2 * phx + r.nm('metal_space')
    side = max(side, (pad_budget - k * l + 1) // 2)
    shared = 2 * cut_to_gate + cut
    shared = max(shared, _grid_up(2 * phx + r.nm('metal_space') - l, grid))
    gph = r.nm('gate_pad_half')
    gate_pad_width = _grid_up(max(l, cut + 2 * r.nm('cut_to_poly')), grid)
    pitch_needed = max(gate_pad_width + r.nm('poly_space'), 2 * gph + r.nm('metal_space'))
    stagger = k > 1 and l + shared < pitch_needed
    if stagger and 2 * (l + shared) < pitch_needed:
        shared = max(shared, _grid_up(-(-pitch_needed // 2) - l, grid))
    if k > 1:
        # The end pad sits at ``first_x``, the first interior pad at the centre
        # of the first shared segment; they too must keep metal spacing.
        need = r.nm('metal_space') + 2 * phx + first_x - l - shared // 2
        side = max(side, _grid_up(need, grid))
    return {'side': side, 'shared': shared, 'first_x': first_x, 'stagger': stagger,
            'gate_pad_width': gate_pad_width}


def build_row(devices, chain, plan, gate, output, rail, geometry=None, *, pack=None,
              gate_nets=None, segment_nets=None, kind=None, diffusion_role='active',
              well=None, rule_overrides=None, finger_names=None):
    """Build one contacted diffusion chain.

    Layer roles, cut size, enclosures, pad sizes and gate-access offsets all
    come from the pack; the shape of the construction is PDK-independent.

    The original inverter call (one ``gate`` net, ``chain`` of ``rail``/
    ``output`` symbols) is unchanged. The general form names every net:
    ``gate_nets[i]`` is the gate of channel ``i`` and ``segment_nets[j]`` the
    net of diffusion segment ``j`` (``k`` channels, ``k+1`` segments), so one
    chain can carry different devices that share diffusion only at equal nets.
    ``kind`` overrides the polarity for devices the pack does not bind as MOS
    (a varactor is an n-type channel), ``diffusion_role`` names the diffusion
    layer (a varactor channel sits on ``tap``), ``well`` forces an n-well
    around the diffusion and ``rule_overrides`` maps a rule key to the pack key
    that replaces it for this construction.
    """
    p = _pack(pack)
    r = _Rules(p, rule_overrides)
    d = devices[0]
    k = len(devices)
    w = d.w_nm
    l = d.l_nm
    if kind is None:
        kind = _polarity(p, d)
    if well is None:
        well = kind == 'pmos'
    grid = r.nm('grid')
    if any(x.w_nm != w or x.l_nm != l for x in devices):
        raise ValueError('A diffusion chain needs one width and one length')
    if l < p.length_rule(kind) or w % grid or l % grid:
        raise ValueError('Illegal target dimensions')
    if w < r.nm('width_min'):
        raise ValueError('Active narrower than the rule pack minimum')
    if gate_nets is None:
        gate_nets = [gate] * k
    if segment_nets is None:
        segment_nets = [rail if symbol == 'rail' else output for symbol in chain]
    if len(gate_nets) != k or len(segment_nets) != k + 1:
        raise ValueError('Chain nets do not match the channel count')
    dims = chain_dimensions(k, l, len(plan['centers_nm']), pack=p, rule_overrides=rule_overrides)
    side, shared, first_x, stagger = dims['side'], dims['shared'], dims['first_x'], dims['stagger']
    half = r.nm('cut') // 2
    phx, phy = r.nm('pad_half_x'), r.nm('pad_half_y')
    cut_to_gate = r.nm('cut_to_gate')
    cut_to_active = r.nm('cut_to_active')
    lengths = ([side] + [shared] * (k - 1) + [side]) if geometry is None else geometry['segment_lengths_nm']
    if len(lengths) != k + 1:
        raise ValueError('Geometry/chain length mismatch')
    if geometry is not None:
        centers = geometry['contact_x_in_segment_nm']
        if len(centers) != len(lengths):
            raise ValueError('Geometry/contact length mismatch')
        for i, (length, cx) in enumerate(zip(lengths, centers)):
            minimum = shared if 0 < i < k else side
            lo = (cut_to_active if i == 0 else cut_to_gate) + half
            hi = length - (cut_to_active if i == k else cut_to_gate) - half
            if type(length) != int or type(cx) != int or length % grid or cx % grid \
                    or length < minimum or not lo <= cx <= hi:
                raise ValueError('Illegal learned diffusion geometry')
    width = sum(lengths) + k * l
    active = db.Box(0, 0, width, w)
    regs = {key: db.Region() for key in p.layers}
    if diffusion_role not in regs:
        raise ValueError(f'pack {p.pdk_id!r} has no diffusion role {diffusion_role!r}')
    regs[diffusion_role].insert(active)
    terminals = []
    channels = []
    bounds = [(0, lengths[0])]
    x = lengths[0]
    for i in range(k):
        channels.append(db.Box(x, 0, x + l, w))
        bounds.append((x + l, x + l + lengths[i + 1]))
        x += l + lengths[i + 1]
    for i, ((left, right), net) in enumerate(zip(bounds, segment_nets)):
        cx = (first_x if i == 0 else width - first_x if i == k else (left + right) // 2) \
            if geometry is None else left + geometry['contact_x_in_segment_nm'][i]
        ys = plan['centers_nm']
        landing = db.Box(cx - phx, ys[0] - phy, cx + phx, ys[-1] + phy)
        for cy in ys:
            regs['contact'].insert(db.Box(cx - half, cy - half, cx + half, cy + half))
        regs['metal1'].insert(landing)
        terminals.append((net, landing))
    # Gate access derived from the actual SD pads: a constructor rule, not a
    # learned constant and not a reduction in the predicted contact count.
    gph = r.nm('gate_pad_half')
    gclear = r.nm('gate_clear_active')
    pover = r.nm('poly_over_cut')
    endcap = r.nm('poly_endcap')
    # A gate cut must be enclosed by poly. Where the cut is wider than the
    # channel (short-channel processes: sky130 cut 170 > L 150), the cut cannot
    # sit on the channel strip, so it lands on a widened field-poly pad beyond
    # the active - exactly what real layouts do. The pad never touches active,
    # so the channel length is unchanged. For processes where the channel is
    # already wider than the cut, this pad equals the channel width and the
    # geometry is bit-identical to before.
    gate_pad_width = dims['gate_pad_width']
    pad_over_cut = half + r.nm('cut_to_poly')
    gy_top = max(w + gclear, plan['centers_nm'][-1] + phy + r.nm('metal_space') + gph)
    gy_bottom = min(-gclear, plan['centers_nm'][0] - phy - r.nm('metal_space') - gph)
    home_top = kind == 'nmos'
    npc = 'npc' in regs and r.has('npc_over_cut')
    channel_info = []
    for i, c in enumerate(channels):
        top = home_top if not stagger or i % 2 == 0 else not home_top
        gy = gy_top if top else gy_bottom
        gx = (c.left + c.right) // 2 // grid * grid
        regs['poly'].insert(db.Box(c.left, min(-endcap, gy - pover),
                                   c.right, max(w + endcap, gy + pover)))
        if gate_pad_width > l:
            regs['poly'].insert(db.Box(gx - gate_pad_width // 2, gy - pad_over_cut,
                                       gx + gate_pad_width // 2, gy + pad_over_cut))
        cut = db.Box(gx - half, gy - half, gx + half, gy + half)
        regs['contact'].insert(cut)
        if npc:
            regs['npc'].insert(cut.enlarged(r.nm('npc_over_cut')))
        landing = db.Box(gx - gph, gy - gph, gx + gph, gy + gph)
        regs['metal1'].insert(landing)
        terminals.append((gate_nets[i], landing))
        channel_info.append({'device': (finger_names[i] if finger_names else devices[i].name),
                             'gate': gate_nets[i], 'left': segment_nets[i],
                             'right': segment_nets[i + 1], 'box': c})
    if well and 'nwell' in regs:
        nwell = active.enlarged(r.nm('well_over_active'))
        if diffusion_role != 'active' and r.has('var_well_over_poly'):
            # A varactor's n-well must also enclose the poly over its channel.
            hull = db.Region(nwell)
            for c in channels:
                hull.insert(db.Box(c.left, -endcap, c.right, w + endcap).enlarged(
                    r.nm('var_well_over_poly')))
            nwell = hull.bbox()
        regs['nwell'].insert(nwell)
    implant = 'pplus' if kind == 'pmos' else 'nplus'
    if implant in regs:
        regs[implant].insert(active.enlarged(r.nm('implant_over_active')))
    if r.has('oxide_over_active') and r.has('oxide_over_poly'):
        oxide = (regs['active'].sized(r.nm('oxide_over_active'))
                 + regs['poly'].sized(r.nm('oxide_over_poly'))).bbox()
        for role in ('dualgate', 'v5_xtor'):
            if role in regs:
                regs[role].insert(oxide)
    # Model-specific marker layers (sky130's hvtp for pfet_01v8_hvt, ...). The
    # pack states which marker each model must be covered by, so a device
    # flavour is drawn from data rather than a per-process if-branch. Without
    # this, a sky130 HVT PMOS is emitted with no HVT marker at all. A select
    # layer must enclose the *gate*; where the pack states that rule it is
    # used, otherwise the implant enclosure of the active is the fallback.
    marker_over = r.nm('vt_marker_over_gate') if r.has('vt_marker_over_gate') \
        else r.nm('implant_over_active')
    for device in devices:
        for role in p.model_markers.get(device.model, ()):
            if role not in regs:
                raise ValueError(
                    f"pack {p.pdk_id!r} requires marker {role!r} for model "
                    f"{device.model!r} but declares no such layer")
            regs[role].insert(active.enlarged(marker_over))
    return {'regions': regs, 'active': active, 'terminals': terminals, 'plan': plan, 'kind': kind,
            'channels': channel_info, 'diffusion_role': diffusion_role}


def turn(row, n):
    """Rotate a built row by ``n`` quarter turns about its active origin.

    The transform lives in :mod:`clay.rotations`, shared with the device
    builder, so the two callers cannot rotate by different amounts.
    """
    transform = normalize_to_origin(turn_transform(n * 90), row['active'].bbox())
    out = {**row,
           'regions': {k: r.transformed(transform) for k, r in row['regions'].items()},
           'active': row['active'].transformed(transform),
           'terminals': [(t[0], t[1].transformed(transform)) + tuple(t[2:])
                         for t in row['terminals']]}
    # Keep the row's auxiliary geometry in the same frame, as reflect() does,
    # so a turned row can itself be reflected (a mirror pair of a rotated
    # variant) without carrying stale channel boxes.
    if 'channels' in row:
        out['channels'] = [{**c, 'box': c['box'].transformed(transform)} for c in row['channels']]
    if 'extents' in row:
        out['extents'] = {k: v.transformed(transform) for k, v in row['extents'].items()}
    return out


#: Layers merged by *closing* with their own spacing rule: two shapes closer
#: than the rule would violate it, so they are joined; shapes farther apart
#: stay separate. With one shape per layer this equals the old bounding box.
_CLOSE_RULES = {'nplus': 'implant_space', 'pplus': 'implant_space',
                'hvtp': 'vt_marker_space', 'lvtn': 'vt_marker_space',
                'npc': 'npc_space', 'rpm': 'rpm_space'}


def materialize(rows, origins, *, pack=None, cell_name='CLAY_CONTACT_GENERATED', merge='envelope'):
    """Place rows at their solved origins and emit one GDS cell.

    ``merge='envelope'`` (default, the inverter behaviour frozen in the
    primitive_verify spec) collapses ``nwell``/``nplus``/``pplus`` and the
    cell-wide markers each to one bounding box. That is only sound with one
    row per polarity. ``merge='close'`` is the circuit-general rule set used by
    the comparator flow, where marker layers are merged by what each one *is*:

    * cell-wide markers (``dualgate``, ``v5_xtor``): one envelope;
    * ``nwell``: one envelope per well domain (``row['well_domain']``); rows
      without a domain share one, which is the inverter behaviour;
    * implants, threshold selects, ``npc``, ``rpm``: closed with their own
      spacing rule, and an implant never covers opposite-polarity diffusion
      (it is cut back by ``implant_to_opposite_active``).

    Terminals may name the conductor they land on (``(net, box, role)``); the
    label goes on that role's text layer from the pack.
    """
    p = _pack(pack)
    layout = db.Layout()
    layout.dbu = .001
    top = layout.create_cell(cell_name)
    regs = {k: db.Region() for k in p.layers}
    wells = {}
    diff_by_kind = {'nmos': db.Region(), 'pmos': db.Region()}
    terms = []
    for name, row in rows.items():
        t = db.Trans(*origins[name])
        for k, reg in row['regions'].items():
            if k == 'nwell':
                wells.setdefault(row.get('well_domain'), db.Region()).insert(reg.transformed(t))
                continue
            regs[k] += reg.transformed(t)
        if row.get('kind') in diff_by_kind:
            diff_by_kind[row['kind']].insert(row['active'].transformed(t))
        terms.extend((term[0], term[1].transformed(t)) + tuple(term[2:])
                     for term in row['terminals'])
    if merge not in ('envelope', 'close'):
        raise ValueError("merge must be 'envelope' or 'close'")
    if merge == 'envelope':
        wells = {None: sum(wells.values(), db.Region())} if wells else {}
    if 'nwell' in regs:
        for region in wells.values():
            if not region.is_empty():
                regs['nwell'].insert(region.bbox())
    for k in ('nplus', 'pplus') if merge == 'envelope' else ():
        if k in regs and not regs[k].is_empty():
            regs[k] = db.Region(regs[k].bbox())
    for k in ('dualgate', 'v5_xtor'):
        if k in regs and not regs[k].is_empty():
            regs[k] = db.Region(regs[k].bbox())
    for k, rule in _CLOSE_RULES.items() if merge == 'close' else ():
        if k not in regs or regs[k].is_empty():
            continue
        merged = regs[k].merged()
        if p.has(rule):           # also closes notches inside one merged polygon
            s = (p.nm(rule) + 1) // 2
            merged = merged.sized(s).sized(-s)
            if k in ('nplus', 'pplus') and p.has('implant_to_opposite_active'):
                opposite = diff_by_kind['pmos' if k == 'nplus' else 'nmos']
                merged = (merged - opposite.sized(p.nm('implant_to_opposite_active'))) \
                    | regs[k].merged()
        regs[k] = merged
    total = db.Region()
    for k, reg in regs.items():
        top.shapes(layout.layer(*p.layer(k))).insert(reg.merged())
        total += reg
    top.shapes(layout.layer(*p.boundary_layer)).insert(total.bbox())
    # The net-label text layer is declared per conductor role in the pack, not
    # fixed: GF180 labels metal1 at 34/10, sky130 labels li at 67/5. Writing
    # GF180's number on a sky130 layout produced labels no reader could find,
    # which made the round-trip extraction report every S/D segment unlabelled.
    if p.annotation_layers.get('metal1') is None:
        raise ValueError(f"pack {p.pdk_id!r} declares no label layer for metal1; "
                         "net names would be unreadable on the output GDS")
    for term in terms:
        net, b = term[0], term[1]
        role = term[2] if len(term) > 2 else 'metal1'
        label_layer = p.annotation_layers.get(role)
        if label_layer is None:
            raise ValueError(f"pack {p.pdk_id!r} declares no label layer for {role!r}")
        top.shapes(layout.layer(*label_layer)).insert(db.Text(net, db.Trans(b.center())))
    return layout


def verify(layout, circuit, contract, expected_plans, *, routed=False, pack=None):
    from clay.learning.contact_observations import regions, extract
    p = _pack(pack)
    r = p
    regs = regions(layout, pack=p)
    a, poly, c, m, well = (regs[k] for k in ('active', 'poly', 'contact', 'metal1', 'nwell'))
    checks = {}

    def empty(k, v):
        checks[k] = v.is_empty()

    grid = r.nm('grid')
    cut = r.nm('cut')
    checks['nm_dbu'] = layout.dbu == .001
    checks['grid'] = all(v.x % grid == 0 and v.y % grid == 0
                         for region in regs.values() for polygon in region.each()
                         for v in polygon.each_point_hull())
    checks['cut_size'] = all(q.area() == cut ** 2 and q.bbox().width() == cut
                             and q.bbox().height() == cut for q in c.each())
    for label, reg, space in [('active_spacing', a, r.nm('active_space')),
                              ('poly_spacing', poly, r.nm('poly_space')),
                              ('cut_spacing', c, r.nm('cut_space')),
                              ('metal_spacing', m, r.nm('metal_space'))]:
        empty(label, reg.space_check(space))
    empty('poly_width', poly.width_check(r.nm('poly_width')))
    empty('metal_width', m.width_check(r.nm('metal_width')))
    empty('cut_metal_enclosure', c.sized(r.nm('metal_over_cut')) - m)
    checks['metal_area'] = all(q.area() >= r.nm('metal_area') for q in m.each())
    sd = c.inside(a)
    gc = c.inside(poly)
    checks['contact_classification'] = sd.count() + gc.count() == c.count()
    empty('sd_enclosure', sd.sized(r.nm('cut_to_active')) - a)
    empty('gate_enclosure', gc.sized(r.nm('cut_to_poly')) - poly)
    empty('cut_gate', sd.separation_check(a & poly, r.nm('cut_to_gate')))
    empty('gate_cut_active', gc.separation_check(a, r.nm('gate_cut_to_active')))
    pa = a.inside(well)
    na = a.outside(well)
    empty('well_overlap', pa.sized(r.nm('well_over_active')) - well)
    empty('well_nactive', na.separation_check(well, r.nm('well_to_opposite_active')))
    empty('well_width', well.width_check(r.nm('well_width')))
    if r.has('well_space'):
        empty('well_space', well.space_check(r.nm('well_space')))
    for key, act, other in (('nplus', na, pa), ('pplus', pa, na)):
        if key not in regs:
            continue
        empty(key + '_enclosure', act.sized(r.nm('implant_over_active')) - regs[key])
        empty(key + '_opposite', regs[key].separation_check(other, r.nm('implant_to_opposite_active')))
        empty(key + '_spacing', regs[key].space_check(r.nm('implant_space')))
        checks[key + '_area'] = all(q.area() >= r.nm('implant_area') for q in regs[key].each())
    if 'dualgate' in regs and not regs['dualgate'].is_empty():
        if r.has('oxide_over_active'):
            empty('oxide_active', a.sized(r.nm('oxide_over_active')) - regs['dualgate'])
        if r.has('oxide_over_poly'):
            empty('oxide_over_poly', poly.sized(r.nm('oxide_over_poly')) - regs['dualgate'])
        if r.has('oxide_width'):
            empty('oxide_width', regs['dualgate'].width_check(r.nm('oxide_width')))
    if 'v5_xtor' in regs and not regs['v5_xtor'].is_empty():
        empty('voltage_marker', a + poly - regs['v5_xtor'])
    # Every model-specific marker the pack requires must be present and must
    # cover the diffusion of the devices that need it. The marker covers *that
    # model's* active region, not the whole layout: sky130's hvtp covers the
    # PMOS and correctly excludes the NMOS, so checking against all active
    # would fail a legal layout. Without the check an HVT device could be
    # emitted with no HVT layer and every other check would still pass.
    marker_roles = sorted({role for device in circuit.devices
                           for role in p.model_markers.get(device.model, ())})
    for role in marker_roles:
        if role not in regs:
            checks[f'{role}_marker'] = False
            continue
        needs = db.Region()
        for device in circuit.devices:
            if role in p.model_markers.get(device.model, ()):
                # pfet sits inside the well; nfet does not.
                needs += pa if _polarity(p, device) == 'pmos' else na
        empty(f'{role}_marker', needs - regs[role])
    extraction = None
    try:
        extraction = extract(layout, circuit, contract, pack=p)
        checks['target_WL_nets'] = True
        endcap = r.nm('poly_endcap')
        for island in extraction['islands']:
            kind = island['kind']
            plan = expected_plans[kind]
            checks[kind + '_contact_count'] = all(
                s['contact_count'] == plan['count'] for s in island['diffusion_segments'])
            ab = db.Box(*island['active_bbox_nm'])
            for i, ch in enumerate(island['channel_bboxes_nm']):
                cb = db.Box(*ch)
                cap = db.Box(cb.left, ab.bottom - endcap, cb.right, ab.top + endcap) \
                    if island['vertical_gates'] else \
                    db.Box(ab.left - endcap, cb.bottom, ab.right + endcap, cb.top)
                empty(kind + f'_gate_endcap_{i}', db.Region(cap) - poly)
        checks['active_group_count'] = len(extraction['islands']) == \
            len({island['kind'] for island in extraction['islands']})
        checks['gate_contact_count'] = gc.count() == len(circuit.devices)
        if not routed:
            checks['independent_accesses'] = m.count() == \
                sum(i['gate_count'] * 2 + 1 for i in extraction['islands'])
    except ValueError as error:
        checks['target_WL_nets'] = False
        extraction = {'error': str(error)}
    return {'passed': all(checks.values()), 'check_count': len(checks), 'checks': checks,
            'failed': [k for k, v in checks.items() if not v], 'extraction': extraction,
            'pdk': p.pdk_id,
            'scope': f'local {p.pdk_id} checks and labelled topology; external taps; '
                     'no routing, full DRC/LVS or EM qualification'}


def generate(lib, circuit, contract, output, *, primitive_probe=None, probe_output=None,
             pack=None, candidate_budget=None, dedupe_rotations=False):
    """Generate a placement.

    ``dedupe_rotations`` collapses globally equivalent rotations to one
    representative each, so a small ``candidate_budget`` buys distinct
    constructions instead of re-measuring one. It is off by default: a caller
    that ranks over the full candidate alphabet (the relation/geometry
    primitives) needs all 16 quarter-turn pairs present.
    """
    return _generate(lib, circuit, contract, output, primitive_probe=primitive_probe,
                     probe_output=probe_output, pack=pack,
                     candidate_budget=candidate_budget,
                     dedupe=dedupe_rotations)


def _generate(lib, circuit, contract, output, geometry_planner=None, candidate_selector=None,
              recorder=None, primitive_probe=None, probe_output=None, pack=None,
              candidate_budget=None, dedupe=False):
    p = _pack(pack)
    r = p
    validate_library(lib, pack=p)
    expected_recipe = p.recipe('geometry' if geometry_planner else 'contacts')
    if contract.device_recipe != expected_recipe:
        raise ValueError(f'Explicit learned-contact recipe required ({expected_recipe}); '
                         'no fallback to a fixed contact recipe')
    groups, gate, out = inverter_groups(circuit, contract, pack=p)
    if recorder is not None:
        recorder.emit('bind_inverter_topology',
                      {'circuit': asdict(circuit), 'contract': asdict(contract), 'pdk': p.pdk_id},
                      {'groups': {k: [asdict(d) for d in v] for k, v in groups.items()},
                       'gate_net': gate, 'output_net': out}, origin='engineered')
    rows = {}
    plans = {}
    bindings = {}
    for kind, devices in groups.items():
        supported = [pt for pt in lib['patterns'][kind] if pt['gate_count'] == len(devices)]
        if not supported:
            raise ValueError('No learned diffusion-chain pattern for target gate count')
        chain = supported[0]['segments']
        plans[kind] = contact_plan(lib, kind, devices[0].w_nm, pack=p)
        geometry = geometry_planner(kind, chain) if geometry_planner else None
        pattern_event = contact_event = geometry_event = None
        if recorder is not None:
            pattern_event = recorder.emit('select_diffusion_chain',
                {'kind': kind, 'gate_count': len(devices), 'available_patterns': supported},
                {'selected_pattern': supported[0]}, origin='learned')
            contact_event = recorder.emit('contact_plan',
                {'kind': kind, 'width_nm': devices[0].w_nm, 'policy': lib['contact_policies'][kind]},
                plans[kind], origin='learned')
            if geometry is not None:
                geometry_event = recorder.emit('geometry_plan',
                    {'kind': kind, 'chain': chain, 'policy': lib.get('geometry_policies', {})},
                    geometry, origin='learned')
        rows[kind] = build_row(devices, chain, plans[kind], gate, out,
                               contract.low if kind == 'nmos' else contract.high,
                               geometry, pack=p)
        if recorder is not None:
            recorder.emit_build[kind] = recorder.emit('build_row', {
                'kind': kind, 'devices': [asdict(d) for d in devices], 'chain': chain,
                'contact_plan': plans[kind], 'geometry_plan': geometry,
                'gate_net': gate, 'output_net': out,
                'rail_net': contract.low if kind == 'nmos' else contract.high,
                'pattern_event': pattern_event, 'contact_event': contact_event,
                'geometry_event': geometry_event}, recorder.row(rows[kind]), origin='mixed',
                depends_on=[x for x in [pattern_event, contact_event, geometry_event] if x])
        bindings[kind] = {'chain': chain, 'contact_plan': plans[kind],
                          'w_nm': devices[0].w_nm, 'l_nm': devices[0].l_nm}
        if geometry is not None:
            bindings[kind]['geometry_plan'] = geometry
    trials = []
    best = None
    candidate_layouts = []
    clearance = r.nm('opposite_row_clearance')
    # Which orientations may be enumerated is a *freedom* question, answered by
    # the pack - not the literal ``range(4)`` this used to say. The two differ
    # as soon as a deck permits a coarser step (only 90 degrees), or as soon as
    # the engine gains non-orthogonal support, and a literal would silently keep
    # enumerating the old set. The same helper serves routing, so the two search
    # spaces cannot disagree about what is legal.
    # The roles a row covers are the layers it actually draws. Taking "every
    # pack layer that has an angle rule" (as before) silently dropped drawn
    # layers whose freedom is unknown, which bypassed "unknown means refuse".
    row_roles = tuple(sorted({k for kind in ('nmos', 'pmos') for k, reg in rows[kind]['regions'].items()
                              if k in p.layers and not reg.is_empty()}))
    orientation_deg = executable_rotations(p, row_roles)
    if not orientation_deg:
        raise ValueError(
            f'pack {p.pdk_id!r} yields no executable rotation for a row; '
            'refusing to guess an orientation set')
    non_orthogonal = tuple(d for d in orientation_deg if d % 90)
    if non_orthogonal:
        # The set is legal but this constructor is axis-aligned: its rows,
        # contact pitches and solver rectangles are all axis-parallel, so it
        # cannot *realise* those angles. Enumerating them would be worse than
        # useless - ``d // 90`` maps 45 and 0 onto the same quarter turn, so a
        # 45 degree deck would silently measure quarter 0 twice and report the
        # result as a complete sweep. Rather than build a search that cannot
        # mean what it says, refuse and name the gap. The freedom model has
        # already reported these angles as permitted-but-not-executable; this
        # constructor only handles the orthogonal part of that set.
        raise ValueError(
            f'pack {p.pdk_id!r} permits angles {list(non_orthogonal)} that this '
            'axis-aligned constructor cannot realise; non-orthogonal construction '
            'is not implemented (see Pack.capability / rotate_non_orthogonal)')
    else:
        narrowed = ''
    quarter_turns = tuple(d // 90 for d in orientation_deg)
    freedom = build_freedom(p, [Part(kind, row_roles) for kind in ('nmos', 'pmos')])
    #: Angles in degrees, which is what :mod:`clay.learning.actions` speaks.
    legal_deg = tuple(orientation_deg)

    def enforce_orientation(name, quarter):
        """Refuse an orientation before any geometry is rotated into it."""
        enforce(freedom, {name: Action(ROTATE, degrees=quarter * 90)})
    # Build each row once per legal quarter turn. The same objects are reused by
    # both search modes below, so switching modes cannot change the geometry.
    rows_pre = {}
    for kind in ('nmos', 'pmos'):
        rep = {}
        for quarter in quarter_turns:
            enforce_orientation(kind, quarter)
            rr = turn(rows[kind], quarter)
            allr = db.Region()
            corer = db.Region()
            for key, reg in rr['regions'].items():
                allr += reg
                if key in ('active', 'poly', 'contact', 'metal1'):
                    corer += reg
            b, cc = allr.bbox(), corer.bbox()
            rep[quarter] = {'row': rr,
                            'outer': (b.left, b.bottom, b.right, b.top),
                            'core': (cc.left, cc.bottom, cc.right, cc.top),
                            'rect': (rr['active'].width(), rr['active'].height())}
        rows_pre[kind] = rep

    # ``solve_area`` applies its gap between the placement *rectangles* (the
    # active regions), not between the full row envelopes. Each row carries poly
    # gate pads and metal landings that stick out past its active, so the PDK
    # clearance alone under-constrains the pair: on sky130 the rows then overlap
    # and two gate cuts 160 nm apart merge into one 170x330 shape. GF180 never
    # showed this because its 1200 nm clearance happens to exceed both tails.
    #
    # The binding gap is therefore the larger of (a) what the process requires
    # between well/implant of opposite polarity, and (b) what the construction
    # itself needs: both rows' facing extensions plus the widest routing-space
    # rule. (b) is a geometric obligation of this construction, not a per-PDK
    # constant, so it is computed here rather than stored in a pack.
    row_spacing = max(r.nm('poly_space'), r.nm('metal_space'))

    def extensions(rep):
        ab = rep['row']['active'].bbox()
        # Use the *core* box (active/poly/contact/metal1), not the outer box.
        # The outer box includes the well and implant, whose opposite-polarity
        # separation is already the `clearance` term below; counting those again
        # here would double-charge it and inflate GF180's legal 1200 nm gap.
        cx0, cy0, cx1, cy1 = rep['core']
        return (ab.left - cx0, ab.bottom - cy0, cx1 - ab.right, cy1 - ab.top)

    def pair_clearance(rn, rp):
        en = extensions(rows_pre['nmos'][rn])
        ep = extensions(rows_pre['pmos'][rp])
        # The solver may stack the rows in either order or side by side, so take
        # the worst facing pair over both axes. This is the gap the *construction*
        # needs; it is not a per-PDK constant and must not be stored in a pack.
        facing = max(en[1] + ep[3], ep[1] + en[3], en[0] + ep[2], ep[0] + en[2])
        return max(clearance, facing + row_spacing)

    def clearance_grid(rn, rp):
        # Per-plan gaps, in solver units. A single global maximum would be
        # unsound as the basis of the area lower bound: the bound uses the gap,
        # and an inflated gap can exceed the area a plan actually achieves,
        # which would prune a genuine winner.
        gap = pair_clearance(rn, rp)
        return -(-gap // p.nm('grid'))     # ceil to the grid

    if dedupe:
        # The 16 quarter-turn pairs are not 16 constructions: rotating both rows
        # together maps one onto another, and those classes have equal achievable
        # area. Enumerating all 16 re-measures the same construction, which is
        # the documented cause of the budget-4 regression. The loop below keeps
        # one representative per class, orders them by a sound lower bound, and
        # folds each exact solve back into the ordering until nothing changes.
        from clay.learning.actions import Outcome, search
        outs, cores, rects, gaps = {}, {}, {}, {}
        for rn in quarter_turns:
            for rp in quarter_turns:
                key = (('nmos', rn * 90), ('pmos', rp * 90))
                outs[key] = {'nmos': rows_pre['nmos'][rn]['outer'],
                             'pmos': rows_pre['pmos'][rp]['outer']}
                cores[key] = {'nmos': rows_pre['nmos'][rn]['core'],
                              'pmos': rows_pre['pmos'][rp]['core']}
                rects[key] = {'nmos': rows_pre['nmos'][rn]['rect'],
                              'pmos': rows_pre['pmos'][rp]['rect']}
                # The bound must use the very gap the solver will be given for
                # this plan; one global value would be either loose or unsound.
                gaps[key] = clearance_grid(rn, rp) * p.nm('grid')

        def solve_plan(plan, outer, core):
            """Exact inner solve for one plan, reported back to the decider.

            The solver's own status and objective bound travel with the area, so
            an optimality claim can be checked against what the solver actually
            proved rather than inferred from the number.
            """
            key = plan.key()
            rect = rects.get(key)
            if rect is None or not outer or not core:
                return Outcome(plan, None, None, 'NO_GEOMETRY', False)
            quarters = {name: a.degrees // 90 for name, a in plan.actions.items()}
            gap = clearance_grid(quarters['nmos'], quarters['pmos']) * p.nm('grid')
            try:
                placed = solve_area(rect, outer, {('nmos', 'pmos'): gap},
                                    grid=p.nm('grid'),
                                    seconds=contract.solver_seconds, core_boxes=core)
            except ValueError:
                return Outcome(plan, None, None, 'INFEASIBLE', False)
            return Outcome(plan, placed.area_nm2, placed.lower_bound_nm2,
                           placed.status, True)

        decision = search(
            freedom, grid={'nmos': legal_deg, 'pmos': legal_deg},
            outs=outs, cores=cores, clearance=clearance, solve=solve_plan,
            budget=candidate_budget, parts=('nmos', 'pmos'), rects=rects, gaps=gaps)
        planned = []
        for candidate in decision.order:
            if not candidate.evaluated:
                continue
            quarter = {name: a.degrees // 90 for name, a in candidate.plan.actions.items()}
            planned.append((quarter['nmos'], quarter['pmos'], candidate))
        decider_certificate = dict(decision.certificate)
    else:
        # Exhaustive: every legal quarter-turn pair, which is what a ranker over
        # the full candidate alphabet needs. No dedup, no bound pruning. The
        # alphabet comes from the pack (``quarter_turns``), so a process that
        # permits fewer orientations is swept over fewer candidates instead of
        # being asked for orientations its deck forbids.
        planned = [(rn, rp, None) for rn in quarter_turns for rp in quarter_turns]
        decider_certificate = {'mode': 'exhaustive',
                               'plans_total': len(planned),
                               'representatives': len(planned),
                               'orientation_deg': list(orientation_deg),
                               'narrowed': narrowed}
    # Every candidate angle of every row, closed or reported open. This is what
    # makes "the decider only searched quarter turns" a checked statement: the
    # other angles carry a deck rule or a proof, or they are listed as open.
    decider_certificate['angle_verdicts'] = {
        name: [v.as_dict() for v in freedom.part(name).verdicts] for name in ('nmos', 'pmos')}
    decider_certificate['open_angles'] = {
        name: [v.degrees for v in freedom.part(name).verdicts if not v.closed]
        for name in ('nmos', 'pmos')}
    decider_certificate['row_layer_roles'] = list(row_roles)
    for rn, rp, candidate in planned:
        # The gate runs here too, not only on the decider path: this branch
        # constructs pairs directly, and without it an illegal orientation would
        # be materialised and only caught later, if at all.
        enforce(freedom, {name: Action(ROTATE, degrees=q * 90)
                          for name, q in (('nmos', rn), ('pmos', rp))})
        rr = {k: rows_pre[k][q]['row'] for k, q in (('nmos', rn), ('pmos', rp))}
        turn_event = None
        if recorder is not None:
            turn_event = recorder.emit('turn_rows',
                {'quarter_turns': {'nmos': rn, 'pmos': rp},
                 'decided_by': 'action_decider' if candidate is not None else 'exhaustive',
                 'class': list(candidate.class_key) if candidate is not None else None,
                 'lower_bound_nm2': candidate.lower_bound_nm2 if candidate is not None else None,
                 'row_events': {k: recorder.emit_build[k] for k in ('nmos', 'pmos')}},
                {k: recorder.row(rr[k]) for k in ('nmos', 'pmos')}, origin='engineered',
                depends_on=[recorder.emit_build['nmos'], recorder.emit_build['pmos']])
        outer = {k: rows_pre[k][q]['outer'] for k, q in (('nmos', rn), ('pmos', rp))}
        core = {k: rows_pre[k][q]['core'] for k, q in (('nmos', rn), ('pmos', rp))}
        rect = {k: rows_pre[k][q]['rect'] for k, q in (('nmos', rn), ('pmos', rp))}
        gap = clearance_grid(rn, rp) * p.nm('grid')
        solved = solve_area(rect, outer, {('nmos', 'pmos'): gap}, grid=p.nm('grid'),
                            seconds=contract.solver_seconds, core_boxes=core)
        solver_event = None
        if recorder is not None:
            solver_event = recorder.emit('solve_area',
                {'rectangles': rect, 'outer_boxes': outer, 'core_boxes': core,
                 'clearance_nm': gap, 'grid_nm': p.nm('grid'),
                 'solver_seconds': contract.solver_seconds,
                 'quarter_turns': {'nmos': rn, 'pmos': rp}},
                asdict(solved), origin='engineered', depends_on=[turn_event])
        layout = materialize(rr, solved.origins_nm, pack=p)
        candidate_name = f'n{rn}_p{rp}'
        assembly_event = None
        if recorder is not None:
            assembly_event = recorder.emit('materialize',
                {'rows_event': turn_event, 'solver_event': solver_event,
                 'origins_nm': solved.origins_nm, 'candidate': candidate_name},
                recorder.layout(layout), origin='engineered',
                depends_on=[turn_event, solver_event])
        v = verify(layout, circuit, contract, plans, pack=p)
        verify_event = None
        if recorder is not None:
            verify_event = recorder.emit('verify_candidate',
                {'candidate': candidate_name, 'materialization_event': assembly_event},
                {'passed': v['passed'], 'checks': v['checks'], 'failed': v['failed']},
                origin='engineered', depends_on=[assembly_event])
        measurements = measure_layout(layout, layout.top_cell(), pack=p)
        if round(measurements['material_bbox']['area_um2'] * 1e6) != solved.area_nm2:
            raise ValueError('Objective/GDS mismatch')
        trial = {'candidate': candidate_name, 'verification': v,
                 'measurements': measurements, 'solver': asdict(solved)}
        trials.append(trial)
        if candidate_selector is not None:
            candidate_layouts.append(layout)
        if recorder is not None:
            recorder.candidate_events[candidate_name] = {
                'turn_event': turn_event, 'solver_event': solver_event,
                'materialization_event': assembly_event, 'verify_event': verify_event}
        if v['passed'] and (best is None or solved.area_nm2 < best[0]):
            best = (solved.area_nm2, layout, trial)
    if best is None:
        raise ValueError('No legal learned-contact candidate: '
                         + str([t['verification']['failed'] for t in trials]))
    if candidate_selector is not None:
        selected = candidate_selector(trials, candidate_layouts)
        if type(selected) != int or not 0 <= selected < len(trials) \
                or not trials[selected]['verification']['passed']:
            raise ValueError('Selector returned an illegal candidate')
        best = (trials[selected]['solver']['area_nm2'], candidate_layouts[selected], trials[selected])
    probe_metadata = None
    if primitive_probe is not None:
        if probe_output is None:
            raise ValueError('A probe output path is required')
        kind, name, index = _parse_primitive_probe(primitive_probe)
        selected_rotation = best[2]['candidate']
        quarter_turns = {'nmos': int(selected_rotation[1]), 'pmos': int(selected_rotation[4])}
        probe_row = turn(rows[kind], quarter_turns[kind])
        origin = best[2]['solver']['origins_nm'][kind]
        probe_metadata = _write_primitive_probe(probe_row, kind, name, index, origin,
                                                probe_output, selected_rotation, pack=p)
    if recorder is not None:
        selected_name = best[2]['candidate']
        recorder.materialization_ids[selected_name] = \
            recorder.candidate_events[selected_name]['materialization_event']
    write_gds(best[1], output)
    readback = db.Layout()
    readback.read(str(output))
    verified = verify(readback, circuit, contract, plans, pack=p)
    if not verified['passed']:
        raise ValueError('Readback validation failed')
    best[2]['verification'] = verified
    if decider_certificate.get('mode') == 'exhaustive':
        # Exhaustive is only a proof if every inner solve proved its own
        # optimum; a time-limited FEASIBLE answer would leave a gap.
        statuses = {t['candidate']: t['solver']['status'] for t in trials}
        decider_certificate['inner_status'] = statuses
        decider_certificate['proven_optimal_in_model'] = bool(statuses) and all(
            s == 'OPTIMAL' for s in statuses.values()) and not any(
            decider_certificate['open_angles'].values())
    return {'schema': SCHEMA, 'pdk': p.pdk_id, 'circuit': asdict(circuit),
            'bindings': bindings, 'best': best[2], 'trials': trials,
            'contract': asdict(contract), 'reference_inputs': [],
            'learned': 'chain topology, contact row predictor, normalized offset',
            'manual': 'constructors, legal rules, features/fit family, exhaustive 16 rotations',
            'scope': 'only learned gate counts, uniform single-finger parallel groups; '
                     'area is not a standard-cell-compatible metric',
            # The decider's evidence, not just its answer: which equivalence
            # classes it considered, which it retired and by which bound, and
            # whether the winner's inner solve proved optimality inside the
            # two-rectangle model. Without this the "globally optimal" claim is
            # an assertion; with it, a reader can check it.
            'decider': decider_certificate,
            'primitive_probe': probe_metadata}


_PROBE_LAYERS = {'active': 'active', 'diffusion_segment': 'active', 'poly_gate': 'poly',
                 'contact_cut': 'contact', 'metal1_landing': 'metal1', 'nwell': 'nwell',
                 'nplus': 'nplus', 'pplus': 'pplus', 'dualgate': 'dualgate', 'v5_xtor': 'v5_xtor'}


def _parse_primitive_probe(spec):
    try:
        name, kind, index_text = spec.split(':')
        index = int(index_text)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError('Probe must be PRIMITIVE:nmos|pmos:INDEX') from error
    if name not in _PROBE_LAYERS or kind not in {'nmos', 'pmos'} or index < 0:
        raise ValueError('Unknown primitive probe or invalid kind/index')
    return kind, name, index


def _write_primitive_probe(row, kind, name, index, origin, path, candidate, *, pack=None):
    p = _pack(pack)
    if name == 'diffusion_segment':
        region = (db.Region(row['active']) - row['regions']['poly']).merged()
    elif name == 'metal1_landing':
        region = db.Region()
        for _, box in row['terminals']:
            region.insert(box)
    else:
        region = row['regions'][_PROBE_LAYERS[name]].merged()
    polygons = sorted(region.each(),
                      key=lambda q: (q.bbox().left, q.bbox().bottom, q.bbox().right, q.bbox().top))
    if index >= len(polygons):
        raise ValueError(f'{kind} {name} has {len(polygons)} shapes; index {index} is out of range')
    polygon = polygons[index].transformed(db.Trans(*origin))
    layout = db.Layout()
    layout.dbu = .001
    top = layout.create_cell('CLAY_SINGLE_PRIMITIVE_PROBE')
    layer_xy = p.layer(_PROBE_LAYERS[name])
    layer = layout.layer(*layer_xy)
    top.shapes(layer).insert(polygon)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(path))
    check = db.Layout()
    check.read(str(path))
    restored_layer = check.layer(*layer_xy)
    shapes = list(check.top_cell().shapes(restored_layer).each())
    restored = db.Region(check.top_cell().begin_shapes_rec(restored_layer))
    if len(shapes) != 1 or not (restored ^ db.Region(polygon)).is_empty():
        raise ValueError('Primitive probe GDS did not roundtrip exactly')
    metadata = {'source': 'production generator selected candidate row',
                'candidate': candidate, 'pdk': p.pdk_id, 'kind': kind,
                'primitive': name, 'index': index, 'layer': list(layer_xy),
                'polygon_count': 1,
                'bbox_nm': [polygon.bbox().left, polygon.bbox().bottom,
                            polygon.bbox().right, polygon.bbox().top],
                'area_nm2': polygon.area(), 'gds': str(path)}
    path.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


# ---------------------------------------------------------------------------
# Circuit-general additions (comparator learning, v3)
# ---------------------------------------------------------------------------

POLICY_SCHEMA = 'clay.contact-policy.v1'


def contact_capacity(width, *, pack=None, rule_overrides=None):
    """How many cuts fit in one column across an active of ``width`` nm."""
    r = _Rules(_pack(pack), rule_overrides)
    span = width - 2 * r.nm('cut_to_active') - r.nm('cut')
    return span // (r.nm('cut') + r.nm('cut_space')) + 1 if span >= 0 else 0


def contact_plan_from_policy(policy, width, *, pack=None, rule_overrides=None):
    """Contact rows for one diffusion segment from a learned *policy*.

    Unlike :func:`contact_plan`, the policy is not tied to an inverter
    library. Families: ``constant`` (``count``), ``width_stump`` (inverter v2)
    and ``fraction`` (``f`` of the column capacity). Every prediction is
    clipped to ``[1, capacity]``: a count fitted on a wide sample must not be
    forced into a narrow finger, and that clipping is reported in the plan.
    """
    p = _pack(pack)
    r = _Rules(p, rule_overrides)
    rows = policy['rows']
    capacity = contact_capacity(width, pack=p, rule_overrides=rule_overrides)
    if capacity < 1:
        raise ValueError(f'active width {width} cannot hold a single cut')
    if rows['kind'] == 'constant':
        n = rows['count']
    elif rows['kind'] == 'width_stump':
        n = rows['low'] if width <= rows['threshold_nm'] else rows['high']
    elif rows['kind'] == 'fraction':
        n = round(rows['f'] * capacity)
    else:
        raise ValueError(f"unknown contact policy {rows['kind']!r}")
    clipped = max(1, min(int(n), capacity))
    pitch = r.nm('cut') + r.nm('cut_space')
    lo = r.nm('cut_to_active') + r.nm('cut') // 2
    hi = width - lo - (clipped - 1) * pitch
    grid = r.nm('grid')
    alpha = policy.get('first_contact_free_margin_fraction', .5)
    first = lo + ((round(alpha * (hi - lo)) // grid) * grid if hi > lo else 0)
    return {'count': clipped, 'centers_nm': [first + i * pitch for i in range(clipped)],
            'predicted': int(n), 'capacity': capacity}


def reflect(row, axis='y', net_map=None):
    """Mirror a built row and optionally rename its nets.

    ``axis='y'`` mirrors left-right (about a vertical line), ``'x'``
    top-bottom. The result is re-anchored so its active box starts at the
    origin, like :func:`turn`. With ``net_map`` this is how the mirror half of
    a matched pair is produced: the *same* geometry, reflected, carrying the
    mate nets, so the pair is symmetric by construction rather than by luck.
    """
    if axis not in ('x', 'y'):
        raise ValueError("axis must be 'x' or 'y'")
    base = db.Trans(db.Trans.M90 if axis == 'y' else db.Trans.M0)
    transform = normalize_to_origin(base, row['active'].bbox())
    rename = (lambda n: net_map.get(n, n)) if net_map else (lambda n: n)
    out = {**row,
           'regions': {k: rg.transformed(transform) for k, rg in row['regions'].items()},
           'active': row['active'].transformed(transform),
           'terminals': [(rename(t[0]), t[1].transformed(transform)) + tuple(t[2:])
                         for t in row['terminals']]}
    if 'channels' in row:
        out['channels'] = [{**c, 'gate': rename(c['gate']), 'left': rename(c['left']),
                            'right': rename(c['right']), 'box': c['box'].transformed(transform)}
                           for c in row['channels']]
    if 'extents' in row:
        out['extents'] = {k: v.transformed(transform) for k, v in row['extents'].items()}
    return out
