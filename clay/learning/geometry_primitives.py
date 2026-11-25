"""First-stage geometry learning; no source coordinates or per-cell lookup.

Learn pooled diffusion lengths above PDK bounds and feasible contact offsets,
conditioned on polarity, terminal role and outer/shared segment class. Each
island has equal weight. This is deliberately NOT a general polygon learner.

Every bound comes from the semantic rule pack, so the learned quantity
(``excess_contact_units``) stays dimensionless and the same operator runs on a
different process without changing this file.
"""
from collections import defaultdict
from math import isfinite
from statistics import mean

from clay.pdk.semantic import default_pack
from clay.learning import contact_primitives as contacts

SCHEMA = 'clay.geometry-primitive.v2'
#: Contract recipe name for the GF180 pack; other packs derive their own
#: via ``Pack.recipe('geometry')``.
RECIPE = default_pack().recipe('geometry')


def _pack(pack):
    return default_pack() if pack is None else pack


def segment_minimum(shared, *, pack=None):
    r = _pack(pack)
    if shared:
        return r.nm('shared_segment')
    return r.nm('side_extension')


def center_interval(length, shared, *, pack=None):
    """Outer intervals are measured from the outside edge towards the gate."""
    r = _pack(pack)
    half = r.nm('cut') // 2
    lo = (r.nm('cut_to_gate') if shared else r.nm('cut_to_active')) + half
    hi = length - r.nm('cut_to_gate') - half
    if hi < lo:
        raise ValueError('Diffusion cannot contain a legal contact')
    return lo, hi


def fit(observations, contracts, *, pack=None):
    p = _pack(pack)
    if len(observations) != len(contracts) or not observations:
        raise ValueError('Need paired training observations/contracts')
    contact_library, contact_evidence = contacts.fit(observations, contracts, pack=p)
    cut = p.nm('cut')
    pooled = defaultdict(list)
    evidence = []
    for obs, ct in zip(observations, contracts):
        for island in obs['islands']:
            if not island.get('active_is_rectangle', False):
                raise ValueError('Geometry v2 requires observed rectangular active; do not replace a polygon by its bbox')
            a = island['active_bbox_nm']
            role_rail = ct.low if island['kind']=='nmos' else ct.high
            segments = island['diffusion_segments']
            per_island = defaultdict(list)
            for i, s in enumerate(segments):
                shared = 0 < i < len(segments)-1
                role = 'rail' if s['net']==role_rail else 'output'
                key = f"{island['kind']}:{'shared' if shared else 'outer'}:{role}"
                left, _, right, _ = s['bbox_nm']
                length = right-left
                residual = (length-segment_minimum(shared,pack=p))/cut
                if residual < 0:
                    raise ValueError('Observed diffusion below supported rule-pack bounds')
                lo, hi = center_interval(length, shared, pack=p)
                xs = {x+a[0]-left for x, y in s['contact_centers_local_nm']}
                if len(xs)!=1:
                    raise ValueError('Geometry v2 supports one contact column per segment')
                x = next(iter(xs))
                if i == len(segments)-1: x = length-x
                if not lo <= x <= hi:
                    raise ValueError('Observed contact violates supported enclosure/spacing')
                # A zero-width feasible interval does not identify a preference.
                fraction = (x-lo)/(hi-lo) if hi>lo else None
                per_island[key].append((residual, fraction))
            for key, values in per_island.items():
                identifiable = [v[1] for v in values if v[1] is not None]
                row = {'sample':obs['sample_id'], 'feature':key,
                       'excess_contact_units':mean(v[0] for v in values),
                       'contact_fraction':mean(identifiable) if identifiable else None}
                pooled[key].append(row)
                evidence.append(row)
    policies = {}
    for key, rows in sorted(pooled.items()):
        identifiable = [r['contact_fraction'] for r in rows if r['contact_fraction'] is not None]
        policies[key] = {'excess_contact_units':mean(r['excess_contact_units'] for r in rows),
                         'contact_fraction':mean(identifiable) if identifiable else None,
                         'island_count':len(rows), 'position_island_count':len(identifiable)}
    lib = {'schema':SCHEMA, 'pdk':p.pdk_id,
           'rule_pack':dict(contact_library['rule_pack']),
           'contacts':contact_library,
           'geometry_policies':policies, 'operator':'rectangular_chain_with_learned_segments_v1'}
    validate_library(lib, pack=p)
    return lib, {'sources':contact_evidence['sources'], 'geometry_examples':evidence,
                 'contact_evidence':contact_evidence,
                 'fit':'island-weighted mean of rule-normalized excess and feasible contact-center fraction',
                 'not_learned':'rectangle family, W/L semantics, PDK rules, gate access, wells/implants, placement objective',
                 'limitations':'two parents; no reliable W/L-dependent geometry regression; no nonrectangular shapes or unseen contact counts'}


V1_SCHEMA = 'clay.geometry-primitive.v1'
#: v1 libraries carried no rule-pack binding and were implicitly fitted for the
#: GF180 5 V experiment. Migration is sound only for that pack.
_V1_PDK = 'gf180mcu_5v'


def upgrade_library(lib, *, pack=None):
    """Migrate a v1 geometry library to the current schema.

    The result records ``migrated_from`` so the schema change stays visible.
    The embedded contact library is upgraded through its own path.
    """
    p = _pack(pack)
    if lib.get('schema') == SCHEMA:
        return lib
    if lib.get('schema') != V1_SCHEMA:
        raise ValueError(f'Unknown learned geometry schema/fields: {lib.get("schema")!r}')
    if lib.get('pdk') != _V1_PDK or p.pdk_id != _V1_PDK:
        raise ValueError(
            f'v1 library was fitted for {_V1_PDK!r}; it cannot be migrated to {p.pdk_id!r}. '
            'Refit against the target rule pack instead')
    upgraded = {k: v for k, v in lib.items() if k != 'schema'}
    upgraded['schema'] = SCHEMA
    upgraded['contacts'] = contacts.upgrade_library(lib['contacts'], pack=p)
    upgraded['rule_pack'] = dict(upgraded['contacts']['rule_pack'])
    upgraded['migrated_from'] = V1_SCHEMA
    return upgraded


def validate_library(lib, *, pack=None):
    p = _pack(pack)
    lib = upgrade_library(lib, pack=p)
    if set(lib) - {'migrated_from'} != {'schema','pdk','rule_pack','contacts',
                                        'geometry_policies','operator'} or lib['schema']!=SCHEMA:
        raise ValueError('Unknown learned geometry schema/fields')
    if lib['pdk']!=p.pdk_id or lib['operator']!='rectangular_chain_with_learned_segments_v1':
        raise ValueError('Unsupported geometry operator/PDK')
    contacts.validate_library(lib['contacts'], pack=p)
    if lib['rule_pack'].get('cut_pitch_nm')!=p.nm('cut_pitch'):
        raise ValueError('Geometry library was fitted against a different rule pack; refit required')
    if not lib['geometry_policies']: raise ValueError('Missing geometry policies')
    for key, p in lib['geometry_policies'].items():
        if key not in {f'{k}:{c}:{r}' for k in ['nmos','pmos'] for c in ['outer','shared'] for r in ['rail','output']}:
            raise ValueError('Unknown geometric role')
        if set(p)!={'excess_contact_units','contact_fraction','island_count','position_island_count'}:
            raise ValueError('Unknown geometry policy fields')
        e, f = p['excess_contact_units'], p['contact_fraction']
        if not isinstance(e,(int,float)) or not isfinite(e) or e<0:
            raise ValueError('Invalid geometry excess')
        if f is not None and (not isinstance(f,(int,float)) or not isfinite(f) or not 0<=f<=1):
            raise ValueError('Invalid contact fraction')
        if type(p['island_count']) is not int or p['island_count']<1 or type(p['position_island_count']) is not int or not 0<=p['position_island_count']<=p['island_count']:
            raise ValueError('Invalid geometry evidence counts')
        if (f is None)!=(p['position_island_count']==0):
            raise ValueError('Contact fraction lacks evidence')


def geometry_plan(lib, kind, chain, *, pack=None):
    p_pack = _pack(pack)
    validate_library(lib, pack=p_pack)
    lengths, centers, decisions = [], [], []
    grid = p_pack.nm('grid')
    cut = p_pack.nm('cut')
    for i, role in enumerate(chain):
        shared = 0 < i < len(chain)-1
        key = f"{kind}:{'shared' if shared else 'outer'}:{role}"
        if key not in lib['geometry_policies']:
            raise ValueError(f'No learned geometry for {key}')
        p = lib['geometry_policies'][key]
        minimum = segment_minimum(shared, pack=p_pack)
        length = max(minimum, round((minimum+p['excess_contact_units']*cut)/grid)*grid)
        lo, hi = center_interval(length, shared, pack=p_pack)
        fraction = p['contact_fraction']
        if fraction is None and hi>lo:
            raise ValueError('Contact position unidentifiable in training for an expanded interval')
        x = lo if fraction is None else round((lo+fraction*(hi-lo))/grid)*grid
        if not lo<=x<=hi: raise ValueError('Rounded contact outside legal interval')
        if i==len(chain)-1: x=length-x
        lengths.append(length); centers.append(x)
        decisions.append({'feature':key, 'pdk_lower_bound_nm':minimum,
                          'learned_excess_nm':length-minimum, 'evidence_islands':p['island_count'],
                          'contact_position_identifiable':fraction is not None})
    return {'segment_lengths_nm':lengths, 'contact_x_in_segment_nm':centers, 'decisions':decisions}


def generate(lib, circuit, contract, output, *, primitive_probe=None, probe_output=None, pack=None):
    p = _pack(pack)
    validate_library(lib, pack=p)
    result = contacts._generate(lib['contacts'], circuit, contract, output,
                               geometry_planner=lambda kind, chain: geometry_plan(lib, kind, chain, pack=p),
                               primitive_probe=primitive_probe, probe_output=probe_output, pack=p)
    result.update(schema=SCHEMA, pdk=p.pdk_id,
                  learned='chain topology, contact count/y-offset; role-conditioned diffusion excess and contact x-fraction',
                  manual='rectangle operator family, rule pack adapter, gate pads, external taps, 16 orientations and area objective',
                  scope='basic geometry prototype; no free polygon learning, routing or full DRC/LVS/EM')
    return result
