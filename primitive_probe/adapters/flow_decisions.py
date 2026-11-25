"""Adapters for the remaining primitives of the learned inverter flow (internal_flow_final4):

  inverter_topology   -> contact_primitives.inverter_groups      (bind_inverter_topology)
  solve_area_core     -> area_placement.solve_area(core_boxes=)  (solve_area as the flow calls it)
  relation_features   -> placement_relations.features            (score_candidates, measuring part)
  relation_rank       -> placement_relations.rank                (score_candidates, selection part)
  candidate_verify    -> contact_primitives.verify               (verify_candidate)

Only input assembly and ctx.call(<clay function>) — no primitive logic is reproduced here.
"""
import hashlib
import json

from clay.learning import contact_primitives as contacts
from clay.learning import placement_relations as relations
from clay.learning.circuit import parse_cdl, PlacementContract
from clay.layout.synth.area_placement import solve_area

from primitive_probe.core import register, ProbeOutput, PROJECT_ROOT
from primitive_probe import fixtures



def _read_json(rel):
    raw = (PROJECT_ROOT / rel).read_bytes()
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


@register('inverter_topology', targets=(parse_cdl, contacts.inverter_groups), category='decision', geometry=False,
          summary='Netlist -> inverter roles: NMOS/PMOS groups, input (gate) net and output net.',
          params_doc='''
netlist: CDL text (one .SUBCKT); contract: PlacementContract fields
(high, low, n_body, p_body, pdk, mode, floorplan="free_single_pair_rotations", device_recipe, grid_nm, solver_seconds)''',
          render_path='circuit.parse_cdl -> contact_primitives.inverter_groups (data only)')
def inverter_topology_probe(ctx, p):
    circuit = ctx.call(parse_cdl, p['netlist'])
    contract = PlacementContract(**p['contract'])
    groups, gate, output = ctx.call(contacts.inverter_groups, circuit, contract)
    return ProbeOutput(outputs={'groups': {k: [d.name for d in v] for k, v in groups.items()},
                                'gate_net': gate, 'output_net': output})


@register('solve_area_core', targets=(solve_area, contacts.materialize), category='relation',
          summary='Area placer as the inverter flow calls it: material-bbox area first, then compact core bbox.',
          params_doc='''
items: {"N":{"rect":[w,h],"outer":[x0,y0,x1,y1],"core":[x0,y0,x1,y1]}, ...} (boxes relative to the rect origin);
clearance_nm: active-to-active clearance of the pair; use_core_boxes: true (flow mode) | false (control);
seconds. Drawing: 22/0 = rect (active), 30/0 = core box, 34/0 = outer box (layers used only as carriers).''',
          render_path='area_placement.solve_area(core_boxes) -> contacts.materialize(fixture boxes, solver origins)')
def solve_area_core_probe(ctx, p):
    items = p['items']
    names = list(items)
    rect = {n: tuple(items[n]['rect']) for n in names}
    outer = {n: tuple(items[n]['outer']) for n in names}
    core = {n: tuple(items[n]['core']) for n in names} if p.get('use_core_boxes', True) else None
    clear = {(names[i], names[j]): p['clearance_nm'] for i in range(len(names)) for j in range(i + 1, len(names))}
    solved = ctx.call(solve_area, rect, outer, clear, grid=5, seconds=p.get('seconds', 5), core_boxes=core)
    rows = {n: fixtures.row({'active': [[0, 0, *rect[n]]], 'poly': [items[n]['core']], 'metal1': [items[n]['outer']]},
                            terminals=[[n, [0, 0, *rect[n]]]]) for n in names}
    layout = ctx.call(contacts.materialize, rows, solved.origins_nm)
    return ProbeOutput(layout=layout, outputs={'origins_nm': solved.origins_nm, 'area_nm2': solved.area_nm2,
                                               'status': solved.status, 'secondary_status': solved.secondary_status})


@register('relation_features', targets=(relations.features, parse_cdl), category='decision', geometry=False,
          summary='Measures the learned relation features (gate axes, gate-access inwardness, output alignment) on an inverter layout.',
          params_doc='''
layout_gds: project-relative GDS of a two-row inverter (input, e.g. from the full flow);
netlist, contract: as in inverter_topology''',
          render_path='placement_relations.features(input layout) (data only; input copied as input_context.gds)')
def relation_features_probe(ctx, p):
    layout = fixtures.load_gds(p['layout_gds'])
    circuit = ctx.call(parse_cdl, p['netlist'])
    contract = PlacementContract(**p['contract'])
    measured = ctx.call(relations.features, layout, circuit, contract)
    return ProbeOutput(outputs=measured, context_gds=p['layout_gds'])


@register('relation_rank', targets=(relations.rank,), category='decision', geometry=False,
          summary='Selects a candidate by area/Amin + w_rel*mean squared relation error + w_wire*HPWL/HPWLmin.',
          params_doc='''
library_file: frozen relation library (clay.placement-relations.v1);
candidates: [{"name","passed":bool,"area_nm2":int,"features":{5 features},"hpwl_total_nm":int}, ...];
weights: {"relations":float,"wirelength_proxy":float}''',
          render_path='placement_relations.rank (data only)')
def relation_rank_probe(ctx, p):
    lib, sha = _read_json(p['library_file'])
    trials = [{'candidate': c['name'], 'verification': {'passed': c['passed']},
               'solver': {'area_nm2': c['area_nm2']}} for c in p['candidates']]
    measurements = [{'features': c['features'], 'hpwl_total_nm': c['hpwl_total_nm']} for c in p['candidates']]
    selected = ctx.call(relations.rank, trials, measurements, lib, p['weights'])
    return ProbeOutput(outputs={'library_file': p['library_file'], 'library_sha256': sha,
                                'selected_index': selected, 'selected_name': p['candidates'][selected]['name'],
                                'ranking': {t['candidate']: t.get('ranking') for t in trials}})


@register('candidate_verify', targets=(contacts.verify, parse_cdl), category='decision', geometry=False,
          summary='Local GF180 rule / topology checker applied to each placement candidate.',
          params_doc='''
layout_gds: project-relative GDS to check (input); netlist, contract;
plans: {"nmos":{"count","centers_nm"},"pmos":{...}} expected contact plans''',
          render_path='contact_primitives.verify(input layout) (data only; input copied as input_context.gds)')
def candidate_verify_probe(ctx, p):
    layout = fixtures.load_gds(p['layout_gds'])
    circuit = ctx.call(parse_cdl, p['netlist'])
    contract = PlacementContract(**p['contract'])
    result = ctx.call(contacts.verify, layout, circuit, contract, p['plans'])
    return ProbeOutput(outputs={'passed': result['passed'], 'failed': result['failed'],
                                'check_count': result['check_count'], 'checks': result['checks']},
                       context_gds=p['layout_gds'])
