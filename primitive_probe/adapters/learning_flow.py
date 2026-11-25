"""Adapters for the primitives of the learned GF180 inverter flow (clay/learning).

Each adapter: case params -> production primitive call(s) -> the project's own
``materialize`` -> klayout Layout. Nothing here draws output geometry; the only
shapes created are INPUT fixtures (primitive_probe.fixtures).
"""
from clay.learning import contact_primitives as contacts
from clay.learning import geometry_primitives as geometry
from clay.learning import routing_primitives as routing
from clay.learning import generate as flat_generate
from clay.learning.circuit import Mos
from clay.layout.synth.area_placement import solve_area
from clay.pdk.device_placement import build_contacted_device

import hashlib
import json

from primitive_probe.core import register, ProbeOutput, PROJECT_ROOT
from primitive_probe import fixtures

MODEL = {'nmos': 'nfet_05v0', 'pmos': 'pfet_05v0'}
BODY = {'nmos': 'VPW', 'pmos': 'VNW'}


def _library(p, default_path):
    """Policy either literal in the case, or read from a frozen project library file.

    library_file is relative to the project root; library_key walks into it
    (e.g. ["geometry","contacts"]). The file's SHA-256 is recorded in outputs.
    """
    if 'library' in p:
        return p['library'], None
    path = PROJECT_ROOT / p['library_file']
    raw = path.read_bytes()
    lib = json.loads(raw.decode('utf-8'))
    for key in p.get('library_key', default_path):
        lib = lib[key]
    return lib, {'library_file': p['library_file'], 'library_sha256': hashlib.sha256(raw).hexdigest(),
                 'library_key': p.get('library_key', default_path)}


def _devices(kind, count, w_nm, l_nm, nets):
    return [Mos(name=f'M{kind[0].upper()}{i}', model=MODEL[kind], drain=nets['output'],
                gate=nets['gate'], source=nets['rail'], body=BODY[kind], w_nm=w_nm, l_nm=l_nm)
            for i in range(count)]


def _row_summary(row):
    return {'active_bbox_nm': [row['active'].left, row['active'].bottom, row['active'].right, row['active'].top],
            'terminals': [[net, [b.left, b.bottom, b.right, b.top]] for net, b in row['terminals']]}


def _build_and_materialize(ctx, p, plan, geometry_plan):
    kind = p['kind']
    devices = _devices(kind, len(p['chain']) - 1, p['w_nm'], p['l_nm'], p['nets'])
    row = ctx.call(contacts.build_row, devices, p['chain'], plan, p['nets']['gate'],
                   p['nets']['output'], p['nets']['rail'], geometry_plan)
    layout = ctx.call(contacts.materialize, {kind: row}, {kind: (0, 0)})
    return row, layout


# ------------------------------------------------------------------ constructors
@register('build_row', targets=(contacts.build_row, contacts.materialize), category='constructor',
          summary='Diffusion-chain MOS row constructor (active/poly/contact/M1/implant/well/marker).',
          params_doc='''
kind: nmos|pmos; w_nm, l_nm: target W/L; chain: e.g. ["output","rail"] (gate count = len-1);
contact_plan: {"count":n,"centers_nm":[...]} given literally (planner NOT exercised here);
geometry_plan: null or {"segment_lengths_nm":[...],"contact_x_in_segment_nm":[...]};
nets: {"gate":..,"output":..,"rail":..}''',
          render_path='contacts.build_row -> contacts.materialize -> Layout.write')
def build_row_probe(ctx, p):
    row, layout = _build_and_materialize(ctx, p, p['contact_plan'], p.get('geometry_plan'))
    return ProbeOutput(layout=layout, outputs=_row_summary(row))


@register('build_contacted_device', targets=(build_contacted_device, flat_generate.materialize), category='constructor',
          summary='Single contacted MOS device constructor of the two-band (v1/v2 recipe) flow.',
          params_doc='''
device: {"name","model":nfet_05v0|pfet_05v0,"drain","gate","source","body","w_nm","l_nm"};
contact_rows: false (v1 single S/D contact) | true (v2 rows from W)''',
          render_path='device_placement.build_contacted_device -> generate.materialize -> Layout.write')
def build_contacted_device_probe(ctx, p):
    dev = Mos(**p['device'])
    generated = ctx.call(build_contacted_device, dev, contact_rows=p.get('contact_rows', False))
    layout, _, bindings = ctx.call(flat_generate.materialize, [generated], {dev.name: (0, 0)}, None, None)
    return ProbeOutput(layout=layout, outputs={'sd_contact_rows': generated.sd_contact_rows,
                                               'bindings': bindings})


# ------------------------------------------------------------------ planners
@register('contact_plan', targets=(contacts.contact_plan, contacts.build_row, contacts.materialize),
          category='planner',
          summary='Learned S/D contact count + position planner; rendered through build_row.',
          params_doc='''
library: a clay.contact-primitive.v1 dict, OR library_file (+library_key, default [geometry,contacts]) = frozen project library;
kind, w_nm (planner input = width), l_nm, chain, nets: used only to render the plan via build_row''',
          render_path='contacts.contact_plan -> contacts.build_row -> contacts.materialize')
def contact_plan_probe(ctx, p):
    lib, source = _library(p, ['geometry', 'contacts'])
    plan = ctx.call(contacts.contact_plan, lib, p['kind'], p['w_nm'])
    row, layout = _build_and_materialize(ctx, p, plan, None)
    return ProbeOutput(layout=layout, outputs={'library_source': source, 'contact_plan': plan, **_row_summary(row)})


@register('geometry_plan',
          targets=(geometry.geometry_plan, contacts.contact_plan, contacts.build_row, contacts.materialize),
          category='planner',
          summary='Learned diffusion segment length / contact x planner; rendered through build_row.',
          params_doc='''
library: a clay.geometry-primitive.v1 dict, OR library_file (+library_key, default [geometry]);
kind, chain (planner inputs); w_nm, l_nm, nets: used to render via build_row''',
          render_path='geometry.geometry_plan (+contact_plan) -> contacts.build_row -> contacts.materialize')
def geometry_plan_probe(ctx, p):
    lib, source = _library(p, ['geometry'])
    gp = ctx.call(geometry.geometry_plan, lib, p['kind'], p['chain'])
    plan = ctx.call(contacts.contact_plan, lib['contacts'], p['kind'], p['w_nm'])
    row, layout = _build_and_materialize(ctx, p, plan, gp)
    return ProbeOutput(layout=layout, outputs={'library_source': source, 'geometry_plan': gp, 'contact_plan': plan, **_row_summary(row)})


# ------------------------------------------------------------------ relations
@register('solve_area_relation', targets=(solve_area, contacts.materialize), category='relation',
          summary='Area placer with clearance and soft relations (align:left/right/center, p_above_n).',
          params_doc='''
rectangles: {"A":[w,h],"B":[w,h]} (fixtures); clearances: [["A","B",nm]];
preferences: [["A","B","align:left"|"align:right"|"align:center"|"p_above_n"]] (may be empty);
bands: optional {"A":"n","B":"p"}; seconds: solver budget''',
          render_path='area_placement.solve_area -> contacts.materialize(fixture rects, solver origins)')
def solve_area_probe(ctx, p):
    rects = {n: tuple(wh) for n, wh in p['rectangles'].items()}
    outer = {n: (0, 0, w, h) for n, (w, h) in rects.items()}
    clear = {(a, b): nm for a, b, nm in p['clearances']}
    prefs = [tuple(x) for x in p.get('preferences', [])]
    solved = ctx.call(solve_area, rects, outer, clear, prefs, grid=5,
                      seconds=p.get('seconds', 5), bands=p.get('bands'))
    rows = {n: fixtures.row({'active': [[0, 0, w, h]]}, terminals=[[n, [0, 0, w, h]]])
            for n, (w, h) in rects.items()}
    layout = ctx.call(contacts.materialize, rows, solved.origins_nm)
    return ProbeOutput(layout=layout, outputs={'origins_nm': solved.origins_nm, 'area_nm2': solved.area_nm2,
                                               'status': solved.status, 'secondary_status': solved.secondary_status,
                                               'preference_cost': solved.preference_cost})


# ------------------------------------------------------------------ transforms / assembly
@register('turn', targets=(contacts.turn, contacts.materialize), category='transform',
          summary='Quarter-turn rotation of a row with active-origin normalization.',
          params_doc='''
row: {"layers":{"active":[[..]],"poly":[[..]]},"terminals":[[net,[..]]]} (fixture, an asymmetric shape);
quarter_turns: 0..3; display_offset_nm: [dx,dy] where the UNROTATED input is shown on layer 200''',
          render_path='contacts.turn -> contacts.materialize')
def turn_probe(ctx, p):
    src = fixtures.row(p['row']['layers'], p['row'].get('terminals', ()))
    dx, dy = p.get('display_offset_nm', [-5000, 0])
    for name, boxes in p['row']['layers'].items():
        for b in boxes:
            fixtures.display(ctx, [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy], f'INPUT {name}')
    turned = ctx.call(contacts.turn, src, p['quarter_turns'])
    layout = ctx.call(contacts.materialize, {'turned': turned}, {'turned': (0, 0)})
    return ProbeOutput(layout=layout, outputs=_row_summary(turned))


@register('materialize', targets=(contacts.materialize,), category='assembly',
          summary='Row assembly: translation by origins and same-layer marker/implant consolidation.',
          params_doc='''
rows: {"R1":{"layers":{...},"terminals":[...]}, ...} (fixtures); origins: {"R1":[x,y], ...}''',
          render_path='contacts.materialize -> Layout.write')
def materialize_probe(ctx, p):
    rows = {n: fixtures.row(r['layers'], r.get('terminals', ())) for n, r in p['rows'].items()}
    layout = ctx.call(contacts.materialize, rows, {n: tuple(o) for n, o in p['origins'].items()})
    return ProbeOutput(layout=layout)


# ------------------------------------------------------------------ routing
@register('orthogonal_path', targets=(routing.orthogonal_path, routing.segment, contacts.materialize),
          category='routing',
          summary='Single-layer obstacle-aware orthogonal path search + wire segment constructor.',
          params_doc='''
start, goal: [x,y]; obstacles: [[x0,y0,x1,y1], ...] (already-expanded keep-outs, fixtures);
width_nm; axis_fraction (0..1); vertical: bool (gate axis)''',
          render_path='routing.orthogonal_path -> routing.segment -> contacts.materialize (metal1)')
def orthogonal_path_probe(ctx, p):
    obstacles = fixtures.region(*p['obstacles'])
    for b in p['obstacles']:
        fixtures.display(ctx, b, 'OBSTACLE (input keep-out)')
    s, g = fixtures.point(p['start']), fixtures.point(p['goal'])
    for name, q in (('START', s), ('GOAL', g)):
        fixtures.display(ctx, [q[0] - 20, q[1] - 20, q[0] + 20, q[1] + 20], name)
    path, cost = ctx.call(routing.orthogonal_path, s, g, obstacles, p['width_nm'],
                          p['axis_fraction'], p['vertical'])
    wires = [ctx.call(routing.segment, a, b, p['width_nm']) for a, b in zip(path, path[1:])]
    row = {'regions': {'metal1': fixtures.boxes_to_region(wires)}, 'terminals': []}
    layout = ctx.call(contacts.materialize, {'route': row}, {'route': (0, 0)})
    return ProbeOutput(layout=layout, outputs={'path_points_nm': path, 'cost': cost})
