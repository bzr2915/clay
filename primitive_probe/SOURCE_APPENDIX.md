# 附录：验证系统全部自写源代码

以下是探针系统中**由验证者新增**的全部代码，原样列出并附 SHA-256。审阅要点：
1. 不存在任何复刻原语功能的计算（无尺寸、位置、规则的推导）；
2. adapters/ 中只有输入组装和 `ctx.call(<clay.* 函数>)`；
3. 输出几何只来自工程函数返回的 Layout 或坐标。

| 文件 | 行数 | SHA-256 |
|---|---|---|
| primitive_probe/__init__.py | 1 | c7005c60f2b19e9b66a787aba6aa659aaade849cf791e769521c6d9acaf60799 |
| primitive_probe/__main__.py | 282 | b33449141f3fe386df09933a07f689e0668d9640d573662353b00d8b7230c69c |
| primitive_probe/adapters/__init__.py | 13 | 4ab9db47f0837de024fba9af7d4d7028cd29f454d108dde356993a5133775aa9 |
| primitive_probe/adapters/flow_decisions.py | 110 | aee5fb4637270a6a2337bd6b0cd28991e3b03b76acf953f2804d317f10a04e42 |
| primitive_probe/adapters/learning_flow.py | 193 | f058f196ebfac40a20fa8b84555d9112d365c51bf62df5c9e404f48199602bc6 |
| primitive_probe/adapters/symmetry_placer.py | 38 | 64644f9eee1a5cc5cc3ee33fda90f3b7ba3657dd803daf906e1a693fee5851a5 |
| primitive_probe/catalog.py | 145 | 1cb1c8ba8a412971620d978cc6f858e52c73fa05622374275bc9ae9e0f613095 |
| primitive_probe/core.py | 264 | d26769248e225c13cee24f3a644e164cd25e5923d394df3583a402d9686477bf |
| primitive_probe/evidence.py | 273 | 74092720abb7bb74e8a30e2dfdaff8a7d36577de37f6f032409b7a1200be322b |
| primitive_probe/fixtures.py | 67 | ac6133abaf217702a7d7616b9b8822e0d94c0129570d721f6704f8211dc43743 |
| primitive_probe/render.py | 201 | 2a057f96f500cdc76d2dc26af95caf02c4bff74a42c516735cb140eac51d7139 |
| tests/test_primitive_probe.py | 96 | e745addd9d841260b132f8a195822a721a361d2e54c8316d68e7be3467a6716c |

## primitive_probe/__init__.py

```python
"""Manual single-primitive verification probe for CLAY layout primitives."""
```

## primitive_probe/__main__.py

```python
"""Manual verification probe for single layout primitives.

  python -m primitive_probe list                      # registered primitives + case coverage
  python -m primitive_probe audit                     # static isolation audit of adapters
  python -m primitive_probe template ADAPTER          # print a case-file skeleton
  python -m primitive_probe run [IDS...] [--out DIR] [--open]
"""
import argparse
import datetime as dt
import json
import platform
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from primitive_probe import core, render, evidence  # noqa: E402
from primitive_probe.adapters import load_all  # noqa: E402

KLAYOUT = Path(r'D:\Klayout\klayout_app.exe')


def cmd_list(_):
    cases = core.load_cases()
    for a in core.REGISTRY.values():
        n = sum(c['adapter'] == a.name for c in cases)
        targets = ', '.join(core.production_identity(t)['module'] + '.' + core.production_identity(t)['qualname']
                            for t in a.targets)
        print(f'{a.name:22s} [{a.category}] cases={n}{"  <-- NO CASE" if not n else ""}\n    targets: {targets}')
        for c in cases:
            if c['adapter'] == a.name:
                print(f"      用例 {c['id']:40s} {c['title']}")
    orphan = [c['id'] for c in cases if c['adapter'] not in core.REGISTRY]
    if orphan:
        print('Cases with unknown adapter:', orphan)
    print('\n查看某用例的可改参数: python -m primitive_probe show <用例id>')
    print('用自己的参数运行:     python -m primitive_probe try <用例id> --set 路径=值 --expect "预期" --open')


def cmd_audit(_):
    problems = core.audit_adapters() + core.audit_no_copied_code()
    print('\n'.join(problems) if problems else
          'audit OK: adapters import no geometry library and read no expected results; '
          'no project code line copied into the probe; no production target re-defined; case files are data only')
    return 1 if problems else 0


def cmd_template(args):
    a = core.REGISTRY[args.adapter]
    print(json.dumps({'id': f'{a.name}_NEW', 'adapter': a.name, 'title': '…',
                      'purpose': '…', 'params': {'_doc': a.params_doc},
                      'expect_rejection': False,
                      'expected': {'derivation': 'PDK rule / design intent used to write the expectations',
                                   'statements': ['…'], 'shapes': [{'bbox_nm': [0, 0, 0, 0], 'label': '…'}]}},
                     indent=2, ensure_ascii=False))


def run_case(case, run_dir):
    adapter = core.REGISTRY.get(case['adapter'])
    if adapter is None:
        raise ValueError(f"{case['id']}: unknown adapter {case['adapter']}")
    case_dir = run_dir / case['id']; case_dir.mkdir(parents=True, exist_ok=True)
    ctx = core.ProbeContext(case['id'])
    out = error = None
    tracer = evidence.LineTracer()
    try:
        with tracer:
            out = adapter.fn(ctx, case['params'])
        missing = ctx.missing_targets(adapter)
        if missing:
            raise RuntimeError(f'adapter did not execute declared production targets: {missing}')
        status = 'production executed'
    except Exception as e:  # production rejections are a legitimate observable result
        error = f'{type(e).__name__}: {e}'
        status = 'production raised (see error)'
        (case_dir / 'traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
    if ctx.calls:
        evidence.write_executed_lines(tracer, ctx.calls, case_dir / 'EXECUTED_LINES.md')
    if case.get('expect_rejection'):
        status += ' — case expects rejection'
    prod = case_dir / 'production_only.gds'
    if out is not None:
        render.write_production_only(out, prod)
        if out.context_gds:
            import shutil
            shutil.copyfile(ROOT / out.context_gds, case_dir / 'input_context.gds')
    render.write_review(case, ctx, out or core.ProbeOutput(), prod, case_dir / 'review.gds')
    render.write_case_report(case, adapter, ctx, out, status, error, case_dir)
    render.write_result(case, adapter, out, status, error, prod, case_dir)
    manifest = {'schema': 'clay.primitive-probe-run.v1', 'case': {k: v for k, v in case.items() if k != '_file'},
                'case_file': f"primitive_probe/{case['_file']}", 'adapter': adapter.name,
                'render_path': adapter.render_path, 'status': status, 'error': error,
                'production_calls': ctx.calls,
                'loaded_production_files': evidence.loaded_clay_files(), 'display_fixtures': ctx.fixtures,
                'production_outputs': None if out is None else core._jsonable(out.outputs),
                'production_extents': None if out is None else out.extents,
                'production_markers': None if out is None else out.markers,
                'expected_source': 'human-written literals from the case file; never computed'}
    (case_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                                            encoding='utf-8')
    return {'id': case['id'], 'adapter': adapter.name, 'status': status,
            'statements': case['expected']['statements'],
            'functions': '; '.join(sorted({c['module'] + '.' + c['qualname'] for c in ctx.calls}))}


def cmd_run(args):
    problems = core.audit_adapters() + core.audit_no_copied_code()
    if problems:
        print('Refusing to run, adapter audit failed:\n' + '\n'.join(problems)); return 1
    cases = core.load_cases(set(args.ids) if args.ids else None, include_archive=args.archive)
    if not cases:
        print('No matching cases'); return 1
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = Path(args.out) if args.out else ROOT / 'data' / 'primitive_generalization' / 'primitive_probe_runs' / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    render.write_lyp(run_dir / 'layers.lyp')
    rows = []
    for case in cases:
        r = run_case(case, run_dir); rows.append(r)
        print(f"{r['id']:34s} {r['status']}")
    render.write_sheets(run_dir, rows)
    (run_dir / 'run_info.json').write_text(json.dumps({'python': platform.python_version(),
        'argv': sys.argv, 'time': stamp, 'cases': [r['id'] for r in rows]}, indent=2), encoding='utf-8')
    print(f'\nArtifacts: {run_dir}\nFill in: {run_dir / "review_sheet.csv"}')
    if args.open:
        if not KLAYOUT.exists():
            print('KLayout not found at', KLAYOUT)
        else:
            for r in rows:
                subprocess.Popen([str(KLAYOUT), '-l', str(run_dir / 'layers.lyp'), str(run_dir / r['id'] / 'review.gds')])
    return 0


def cmd_provenance(args):
    ok = evidence.provenance(args.run, args.trace)
    print(('OK' if ok else 'MISMATCH') + f' -> {Path(args.run) / "PROVENANCE.md"}')
    return 0 if ok else 1


def cmd_sever(args):
    cases = core.load_cases(set(args.ids) if args.ids else None, include_archive=args.archive)
    lines = ['# 切断测试（severance）', '',
             '对每个用例、每个被测工程函数：运行期间把该函数的函数体临时替换为“调用即报错”，再运行用例。',
             '若结果真正来自该工程函数，用例必然失败；若探针内藏有复制或重写的代码，用例仍会产出结果。',
             '不修改任何文件；运行后恢复原函数，并再次正常运行确认恢复。', '',
             '| 用例 | 被切断的工程函数 | 位置 | 切断后的结果 | 依赖成立 | 文件未改动 |', '|---|---|---|---|---|---|']
    ok = True
    for c in cases:
        a = core.REGISTRY[c['adapter']]
        if c.get('expect_rejection'):
            continue
        for r in evidence.severance(c, a):
            ok &= bool(r['depends']) and r['file_unchanged']
            lines.append(f"| {c['id']} | `{r['function']}` | {r['file']} | {r['outcome']} | "
                         f"{'✔' if r['depends'] else '✘'} | {'✔' if r['file_unchanged'] else '✘'} |")
            print(f"{c['id']:34s} {r['function']:62s} {'depends' if r['depends'] else 'NOT DEPENDENT'}")
    lines += ['', f"结论：{'所有用例的结果都依赖其声明的全部工程函数 ✔' if ok else '存在不依赖的函数 ✘'}", '']
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(chr(10).join(lines), encoding='utf-8')
    print(out)
    return 0 if ok else 1


def cmd_catalog(args):
    from primitive_probe.catalog import write_catalog
    write_catalog(args.out)
    print(args.out)


def cmd_appendix(args):
    evidence.source_appendix(args.out)
    print(args.out)


def _set_path(obj, dotted, raw):
    """devices.A.w=1500 -> obj['devices']['A']['w'] = 1500 (value parsed as JSON, else string)."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    keys = dotted.split('.')
    for k in keys[:-1]:
        obj = obj[int(k)] if isinstance(obj, list) else obj.setdefault(k, {})
    last = keys[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


def cmd_show(args):
    case = next((c for c in core.load_cases(include_archive=True) if c['id'] == args.case), None)
    if case is None:
        print('Unknown case; see: python -m primitive_probe list'); return 1
    print(f"用例 {case['id']}  原语 {case['adapter']}\n{case['title']}\n\n可修改的参数（--set 路径=值）：")
    def walk(v, path):
        if isinstance(v, dict) and v and not (path.endswith('library') or path.endswith('policies')):
            for k, x in v.items():
                walk(x, f'{path}.{k}' if path else k)
        else:
            print(f'  {path} = {json.dumps(v, ensure_ascii=False)[:100]}')
    walk(case['params'], '')
    print(f"\n参数含义与可填值：primitive_probe/PARAMETERS_zh.md 中的 {case['adapter']} 一节")
    print('\n预期：'); [print(f'  E{i}: {s}') for i, s in enumerate(case['expected']['statements'], 1)]


def cmd_try(args):
    base = next((c for c in core.load_cases(include_archive=True) if c['id'] == args.case), None)
    if base is None:
        print('Unknown case; see: python -m primitive_probe list'); return 1
    case = json.loads(json.dumps(base))
    for item in args.set or []:
        if '=' not in item:
            print(f'--set needs PATH=VALUE, got {item}'); return 1
        path, raw = item.split('=', 1)
        _set_path(case['params'], path, raw)
    case['id'] = args.name or f"TRY_{base['id']}"
    case['title'] = f"[自定义] {base['title']}"
    case['_file'] = f"(command line, based on {base['_file']})"
    case['purpose'] = f"基于 {base['id']} 的自定义输入，修改项：{args.set or '无'}"
    case['expected'] = {'derivation': '使用者在命令行中手写',
                        'statements': args.expect or ['（未填写预期：请在运行前用 --expect 写出预期）']}
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = Path(args.out) if args.out else ROOT / 'data' / 'primitive_generalization' / 'primitive_probe_runs' / f'try_{stamp}'
    run_dir.mkdir(parents=True, exist_ok=True)
    render.write_lyp(run_dir / 'layers.lyp')
    r = run_case(case, run_dir)
    render.write_sheets(run_dir, [r])
    print(json.dumps(case['params'], indent=1, ensure_ascii=False)[:1500])
    print(f"\n{r['status']}\n结果：{run_dir / case['id']}")
    if args.save:
        out = core.CASE_DIR / f"{case['id']}.json"
        out.write_text(json.dumps({k: v for k, v in case.items() if k != '_file'}, indent=2, ensure_ascii=False) + '\n',
                       encoding='utf-8')
        print('已保存为正式用例：', out)
    if args.open and KLAYOUT.exists():
        subprocess.Popen([str(KLAYOUT), '-l', str(run_dir / 'layers.lyp'), str(run_dir / case['id'] / 'review.gds')])
    return 0


def main(argv=None):
    load_all()
    ap = argparse.ArgumentParser(prog='python -m primitive_probe', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list').set_defaults(fn=cmd_list)
    sub.add_parser('audit').set_defaults(fn=cmd_audit)
    t = sub.add_parser('template'); t.add_argument('adapter'); t.set_defaults(fn=cmd_template)
    r = sub.add_parser('run'); r.add_argument('ids', nargs='*', help='case ids or adapter names (default: all)')
    r.add_argument('--out'); r.add_argument('--open', action='store_true', help='open review.gds files in KLayout')
    r.add_argument('--archive', action='store_true', help='also include primitive_probe/cases_archive (evidence/negative controls)')
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser('show', help='show a case\'s editable parameters and expectations')
    s.add_argument('case'); s.set_defaults(fn=cmd_show)
    y = sub.add_parser('try', help='run an existing case with your own parameter values')
    y.add_argument('case', help='base case id, e.g. S01_symmetry_equal_pair')
    y.add_argument('--set', action='append', metavar='PATH=VALUE', help='e.g. devices.A.w=1500 (repeatable)')
    y.add_argument('--expect', action='append', metavar='TEXT', help='your expectation, written BEFORE running (repeatable)')
    y.add_argument('--name', help='id for this custom run'); y.add_argument('--out')
    y.add_argument('--open', action='store_true'); y.add_argument('--save', action='store_true',
                   help='also save as a permanent case in primitive_probe/cases/')
    y.set_defaults(fn=cmd_try)
    v = sub.add_parser('provenance', help='compare probe-executed code with a full-flow trace and disk')
    v.add_argument('--run', required=True); v.add_argument('--trace'); v.set_defaults(fn=cmd_provenance)
    sv = sub.add_parser('sever', help='severance test: disconnect each production target and confirm the case fails')
    sv.add_argument('ids', nargs='*'); sv.add_argument('--archive', action='store_true')
    sv.add_argument('--out', default=str(ROOT / 'data' / 'primitive_generalization' / 'primitive_probe_runs' / 'SEVERANCE.md'))
    sv.set_defaults(fn=cmd_sever)
    k = sub.add_parser('catalog', help='write CASE_CATALOG_zh.md: primitive -> case files -> parameters')
    k.add_argument('--out', default=str(ROOT / 'primitive_probe' / 'CASE_CATALOG_zh.md')); k.set_defaults(fn=cmd_catalog)
    a = sub.add_parser('appendix', help='write all probe source code with SHA-256 to a markdown file')
    a.add_argument('--out', default=str(ROOT / 'primitive_probe' / 'SOURCE_APPENDIX.md')); a.set_defaults(fn=cmd_appendix)
    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == '__main__':
    sys.exit(main())
```

## primitive_probe/adapters/__init__.py

```python
"""Adapter modules. Every *.py here is imported automatically and statically audited.

To probe a NEW primitive: add a module (or a function in an existing one) with
@register(..., targets=(the_production_function, ...)), then add a case JSON in
primitive_probe/cases/. See primitive_probe/README_zh.md.
"""
import importlib
import pkgutil


def load_all():
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f'{__name__}.{info.name}')
```

## primitive_probe/adapters/flow_decisions.py

```python
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
```

## primitive_probe/adapters/learning_flow.py

```python
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
```

## primitive_probe/adapters/symmetry_placer.py

```python
"""Adapter for the production MILP symmetry placer (clay/layout/annotate_to_solver.py).

The placer returns coordinates (PlacedDevice.extent, axis_x), not a Layout, so the
probe copies those returned coordinates VERBATIM into ProbeOutput.extents/markers.
No position, mirror or axis is computed here.
"""
from clay.layout.annotate_to_solver import SolverConstraints, solve_placement_from_constraints

from primitive_probe.core import register, ProbeOutput
from primitive_probe import fixtures


@register('symmetry_placement', targets=(solve_placement_from_constraints,), category='relation',
          summary='Mirror-pair / on-axis symmetry constraint of the MILP placer.',
          params_doc='''
devices: {"A":{"w":..,"h":..,"kind":"nmos"|"pmos"}, ...} (plain rectangles, fixtures);
symmetry_pairs: [["A","B"], ...]; symmetry_axis: "x" (vertical mirror line);
on_axis_devices: ["C", ...]; time_limit_s''',
          render_path='annotate_to_solver.solve_placement_from_constraints -> returned extents/axis copied to GDS')
def symmetry_probe(ctx, p):
    devs = {n: fixtures.Extent(d['w'], d['h']) for n, d in p['devices'].items()}
    constraints = SolverConstraints(
        cell_name=ctx.case_id,
        symmetry_pairs=tuple(tuple(x) for x in p.get('symmetry_pairs', [])),
        symmetry_axis=p.get('symmetry_axis', 'x'),
        on_axis_devices=tuple(p.get('on_axis_devices', [])),
        device_kinds={n: d['kind'] for n, d in p['devices'].items()})
    sol = ctx.call(solve_placement_from_constraints, constraints, devs,
                   time_limit_s=p.get('time_limit_s', 10))
    extents = [{'layer': (22, 0), 'bbox_nm': list(pd.extent),
                'label': f'{name} mirror={pd.mirror} row={pd.row}'} for name, pd in sol.placed.items()]
    markers = [{'kind': 'vline' if constraints.symmetry_axis == 'x' else 'hline',
                'at_nm': sol.axis_x, 'label': f'production axis_x={sol.axis_x}'}]
    return ProbeOutput(extents=extents, markers=markers,
                       outputs={'success': sol.success, 'status': sol.status, 'axis_x': sol.axis_x,
                                'placed': {n: {'extent': list(pd.extent), 'mirror': pd.mirror, 'row': pd.row}
                                           for n, pd in sol.placed.items()},
                                'full_extent': list(sol.full_extent)})
```

## primitive_probe/catalog.py

```python
"""Generate CASE_CATALOG_zh.md: every case file, every parameter, its type, value and meaning."""
import fnmatch
import json
from pathlib import Path

from primitive_probe import core

# meaning of parameter paths per adapter; '*' matches one path segment (e.g. a device name)
MEANING = {
    'build_row': {
        'kind': '器件极性，决定 N+/P+、是否生成 N 阱、栅接入朝上(NMOS)/朝下(PMOS)',
        'w_nm': '沟道宽 W = 有源区高度（5 的倍数）',
        'l_nm': '沟道长 L = 每条多晶栅宽度（NMOS ≥600，PMOS ≥500）',
        'chain': '扩散段从左到右的网络角色；栅数 = 段数 − 1',
        'contact_plan.count': '每个 S/D 段的接触孔行数',
        'contact_plan.centers_nm': '各行接触孔中心 y 坐标（孔距通常 470；工程不检查越界）',
        'geometry_plan': 'null = 默认段长（外段 440、共享段 520）；或给出段长与孔位',
        'geometry_plan.segment_lengths_nm': '各扩散段长度（外段 ≥440，共享段 ≥520）',
        'geometry_plan.contact_x_in_segment_nm': '各段孔中心距本段左边的距离',
        'nets.gate': '栅网络名（仅文本标签）', 'nets.output': '输出网络名（仅文本标签）',
        'nets.rail': '电源轨网络名（仅文本标签）',
    },
    'build_contacted_device': {
        'device.name': '器件实例名', 'device.model': '模型：nfet_05v0 / pfet_05v0（决定极性）',
        'device.drain': '漏极网络名', 'device.gate': '栅极网络名', 'device.source': '源极网络名',
        'device.body': '衬底网络名', 'device.w_nm': '沟道宽 W（≥360，5 的倍数）',
        'device.l_nm': '沟道长 L（NMOS ≥600，PMOS ≥500）', 'device.m': '并联倍数，只能为 1',
        'device.nf': '指数，只能为 1',
        'contact_rows': 'false = 每端 1 个孔（v1）；true = 按 W 放满多行孔（v2）',
    },
    'contact_plan': {
        'kind': '使用哪个极性的策略', 'w_nm': '规划器输入：有源区宽度 W',
        'l_nm': '仅用于出图：沟道长 L', 'chain': '仅用于出图：扩散链', 'nets.*': '仅用于出图：网络名标签',
        'library': '写在用例中的接触策略库（clay.contact-primitive.v1）',
        'library_file': '改为读取工程冻结的策略库文件', 'library_key': '在库文件中取哪一层',
    },
    'geometry_plan': {
        'kind': '规划器输入：极性', 'chain': '规划器输入：扩散链（每段须有对应策略键）',
        'w_nm': '决定孔行数并用于出图', 'l_nm': '仅用于出图：沟道长 L', 'nets.*': '仅用于出图：网络名标签',
        'library': '写在用例中的几何策略库（clay.geometry-primitive.v1）',
        'library_file': '改为读取工程冻结的策略库文件', 'library_key': '在库文件中取哪一层',
    },
    'solve_area_relation': {
        'rectangles.*': '一个待放矩形：[宽, 高]（5 的倍数）',
        'clearances': '每对矩形的最小净距 [名1, 名2, nm]；须覆盖所有对，顺序同 rectangles',
        'preferences': '关系偏好 [a, b, 关系]，关系为 align:left / align:right / align:center / p_above_n；可为空',
        'bands': '可选：分两带，名字 → 带标签', 'seconds': '求解时间上限（秒）',
    },
    'symmetry_placement': {
        'devices.*.w': '矩形宽', 'devices.*.h': '矩形高',
        'devices.*.kind': 'nmos / pmos（决定分行：NMOS 在下、PMOS 在上）',
        'symmetry_pairs': '镜像对 [[左, 右], ...]；空列表 = 不声明对称（对照组）',
        'on_axis_devices': '必须被对称轴平分的器件名列表',
        'symmetry_axis': '对称轴方向，"x" = 竖直镜像线（唯一验证过的取值）',
        'time_limit_s': '求解时间上限（秒）',
    },
    'turn': {
        'row.layers.*': '输入形状：该图层上的 bbox 列表（必须有 active）',
        'row.terminals': '端口标签 [网络名, bbox]', 'quarter_turns': '逆时针旋转 90° 的次数：0–3',
        'display_offset_nm': '未旋转输入（青色参考图）的显示偏移 [dx, dy]',
    },
    'materialize': {
        'rows.*.layers.*': '某行在该图层上的 bbox 列表', 'rows.*.terminals': '该行的端口标签 [网络名, bbox]',
        'origins.*': '该行的平移量 [x, y]（每行都必须给出）',
    },
    'orthogonal_path': {
        'start': '起点 [x, y]', 'goal': '终点 [x, y]', 'obstacles': '禁区矩形 bbox 列表（可为空）',
        'width_nm': '金属线宽（建议 10 的倍数，≥230）',
        'axis_fraction': '0–1，越大越偏好沿 vertical 指定方向走线', 'vertical': '栅方向是否竖直（true/false）',
    },
    'inverter_topology': {'netlist': 'CDL 网表文本（输入）', 'contract.*': '布局合约字段（电源/衬底网络名、PDK、模式等）'},
    'solve_area_core': {'items.*.rect': '有源区矩形 [宽, 高]', 'items.*.outer': '全部图层外框（相对矩形原点），决定面积',
                        'items.*.core': '核心框，决定第二目标', 'clearance_nm': '矩形两两净距',
                        'use_core_boxes': 'true = 流程模式；false = 对照组', 'seconds': '求解时间上限（秒）'},
    'relation_features': {'layout_gds': '被测量的反相器版图路径（输入）', 'netlist': 'CDL 网表文本', 'contract.*': '布局合约字段'},
    'relation_rank': {'library_file': '冻结关系库（提供特征学习均值）', 'weights.relations': '关系项权重',
                      'weights.wirelength_proxy': '线长项权重', 'candidates': '候选列表：name / passed / area_nm2 / features / hpwl_total_nm'},
    'candidate_verify': {'layout_gds': '待检查版图路径（输入）', 'netlist': 'CDL 网表文本', 'contract.*': '布局合约字段',
                         'plans.*': '该极性期望的接触计划 {count, centers_nm}'},
}
STOP = ('library', 'contact_plan', 'geometry_plan')  # show these objects as one row when they are policies


def _type(v):
    if v is None: return 'null'
    if isinstance(v, bool): return '布尔'
    if isinstance(v, int): return '整数'
    if isinstance(v, float): return '小数'
    if isinstance(v, str): return '字符串'
    if isinstance(v, list): return '列表'
    return '对象'


def _rows(params, adapter):
    out = []
    def walk(v, path):
        meaning = _meaning(adapter, path)
        policy_obj = path == 'library'
        if isinstance(v, dict) and v and not policy_obj and not (meaning and path in ('geometry_plan',) and v is None):
            if meaning and not any(_meaning(adapter, f'{path}.{k}') for k in v):
                out.append((path, v, meaning)); return
            for k, x in v.items():
                walk(x, f'{path}.{k}' if path else k)
        else:
            out.append((path, v, meaning))
    walk(params, '')
    return out


def _meaning(adapter, path):
    table = MEANING.get(adapter, {})
    if path in table:
        return table[path]
    for pat, text in table.items():
        if '*' in pat and fnmatch.fnmatchcase(path, pat) and pat.count('.') == path.count('.'):
            return text
    return ''


def _fmt(v):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 70 else s[:67] + '…'


def write_catalog(path):
    cases = core.load_cases()
    lines = ['# 原语 — 测试文件 — 参数 对照表', '',
             '> 本文件由 `python -m primitive_probe catalog` 根据 `primitive_probe/cases/*.json` 自动生成；'
             '新增或修改用例后请重新生成。参数的取值范围与工程检查详见 [PARAMETERS_zh.md](PARAMETERS_zh.md)。', '',
             '## 总览', '', '| 原语（适配器） | 类别 | 被测工程函数 | 测试文件 |', '|---|---|---|---|']
    for a in core.REGISTRY.values():
        fn = '<br>'.join(f"`{core.production_identity(t)['module']}.{core.production_identity(t)['qualname']}`" for t in a.targets)
        ids = '<br>'.join(f"[{c['id']}](#{c['id'].lower()})" for c in cases if c['adapter'] == a.name)
        lines.append(f'| [{a.name}](#原语-{a.name.replace("_", "-")}) | {a.category} | {fn} | {ids} |')
    for a in core.REGISTRY.values():
        lines += ['', f'## 原语 {a.name}', '', f'- 作用：{a.summary}', f'- 出图路径：`{a.render_path}`',
                  f'- 详细说明：PARAMETERS_zh.md 中的 {a.name} 一节', '']
        for c in (c for c in cases if c['adapter'] == a.name):
            lines += [f"### {c['id']}", '', f"文件：`primitive_probe/{c['_file']}` — {c['title']}"
                      + ('（负对照：预期工程拒绝）' if c.get('expect_rejection') else ''), '',
                      '| 参数路径（`--set` 用） | 类型 | 当前值 | 作用 |', '|---|---|---|---|']
            for p, v, m in _rows(c['params'], a.name):
                lines.append(f'| `{p}` | {_type(v)} | `{_fmt(v)}` | {m or "—"} |')
            lines.append('')
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')
```

## primitive_probe/core.py

```python
"""Probe core: adapter registry, production-call recording and isolation audit.

Design rule (the reason this package exists)
--------------------------------------------
A probe must exercise the project's OWN primitive code. Therefore:

* An adapter only assembles inputs (from a human-written case file) and calls
  production functions through ``ProbeContext.call``. Every call is recorded with
  the callee's module, qualified name, source file, source line and SHA-256 of
  both the file and the function source, so the reviewer can check exactly which
  production code produced the output.
* Only callables defined in ``clay.*`` modules may be called through ``ctx.call``.
* After the adapter returns, every production target declared at registration
  must have actually been called; otherwise the run fails.
* ``audit_adapters()`` statically rejects adapter modules that import KLayout
  (adapters cannot draw output geometry themselves) or that compute expected
  results (no ``expected`` access in adapters). Input shapes come only from
  ``primitive_probe.fixtures`` and are marked as fixtures in the manifest.
* Expected results are NEVER computed by code. They are literal text/boxes in
  the case JSON, written and reviewed by a human. The comparison is manual.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PACKAGE_DIR = Path(__file__).resolve().parent
ADAPTER_DIR = PACKAGE_DIR / 'adapters'
CASE_DIR = PACKAGE_DIR / 'cases'
ARCHIVE_DIR = PACKAGE_DIR / 'cases_archive'   # evidence/negative-control cases, run with --archive
PROJECT_ROOT = PACKAGE_DIR.parent


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def production_identity(fn: Callable) -> dict:
    """Identify a production callable by module, source location and hashes."""
    target = inspect.unwrap(fn)
    module = getattr(target, '__module__', '') or ''
    if not module.startswith('clay.'):
        raise PermissionError(f'{fn!r} is not project production code (module {module!r}); '
                              'probes may only call functions defined under clay.*')
    source_file = Path(inspect.getsourcefile(target)).resolve()
    lines, first_line = inspect.getsourcelines(target)
    try:
        rel = source_file.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        rel = str(source_file)
    return {'module': module, 'qualname': target.__qualname__, 'file': rel,
            'line': first_line, 'file_sha256': _sha(source_file.read_bytes()),
            'function_source_sha256': _sha(''.join(lines).encode('utf-8'))}


@dataclass
class Adapter:
    name: str
    fn: Callable
    targets: tuple            # production callables that MUST be executed
    category: str             # constructor | planner | relation | transform | assembly | routing
    summary: str
    params_doc: str
    render_path: str          # how the production output reaches GDS
    geometry: bool = True     # False: decision/checker primitive, result is data, not a layout


REGISTRY: dict[str, Adapter] = {}


def register(name: str, *, targets: tuple, category: str, summary: str,
             params_doc: str, render_path: str, geometry: bool = True):
    """Register an adapter. ``targets`` are the production functions under test."""
    def wrap(fn):
        if name in REGISTRY:
            raise ValueError(f'Duplicate adapter {name}')
        for t in targets:
            production_identity(t)  # fails early if a target is not clay.* code
        REGISTRY[name] = Adapter(name, fn, tuple(targets), category, summary,
                                 params_doc.strip(), render_path, geometry)
        return fn
    return wrap


def _jsonable(value: Any, depth: int = 0):
    """Best-effort JSON snapshot of production inputs/outputs for the manifest."""
    if depth > 30:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in value]
    if hasattr(value, '__dataclass_fields__'):
        return {k: _jsonable(getattr(value, k), depth + 1) for k in value.__dataclass_fields__}
    # KLayout objects: Box / Region / Layout etc. -> textual form
    cls = type(value).__name__
    if cls == 'Region':
        return {'Region': [str(p) for p in value.each()]}
    if cls == 'Layout':
        return {'Layout': 'klayout.db.Layout (written to production_only.gds)'}
    return repr(value)


@dataclass
class ProbeContext:
    case_id: str
    calls: list = field(default_factory=list)
    fixtures: list = field(default_factory=list)     # display-only input shapes
    _called: set = field(default_factory=set)

    def call(self, fn: Callable, *args, **kwargs):
        """Execute a production function and record identity, inputs and output."""
        identity = production_identity(fn)
        record = {'order': len(self.calls) + 1, **identity,
                  'args': _jsonable(args), 'kwargs': _jsonable(kwargs)}
        try:
            result = fn(*args, **kwargs)
        except Exception as error:  # record production rejection, then re-raise
            record['raised'] = f'{type(error).__name__}: {error}'
            self.calls.append(record)
            self._called.add(inspect.unwrap(fn))
            raise
        record['returned'] = _jsonable(result)
        self.calls.append(record)
        self._called.add(inspect.unwrap(fn))
        return result

    def missing_targets(self, adapter: Adapter):
        return [production_identity(t)['qualname'] for t in adapter.targets
                if inspect.unwrap(t) not in self._called]


@dataclass
class ProbeOutput:
    """What an adapter hands back. Geometry fields must originate from production.

    layout   : klayout Layout object RETURNED BY a production function (e.g. the
               project's ``materialize``). Written unmodified to production_only.gds.
    extents  : list of {'layer': (L, D), 'bbox_nm': [...], 'label': str} whose
               coordinates are copied VERBATIM from a production return value
               (used when the production primitive returns coordinates, not a
               Layout, e.g. the MILP symmetry placer).
    markers  : production-returned scalars to display, e.g. a symmetry axis:
               {'kind': 'vline'|'hline', 'at_nm': int, 'label': str}
    outputs  : JSON summary of the production decision (shown in REVIEW.md).
    """
    layout: Any = None
    extents: list = field(default_factory=list)
    markers: list = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    context_gds: str = ''     # project-relative INPUT layout to copy next to the report (not an output)


# --------------------------------------------------------------------------- audit
FORBIDDEN_IMPORTS = ('klayout', 'pya', 'primitive_probe.render', 'gdstk', 'gdspy', 'shapely')


def audit_adapters() -> list[str]:
    """Static isolation check of every adapter module. Returns a list of violations."""
    problems = []
    for path in sorted(ADAPTER_DIR.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            for n in names:
                if any(n == f or n.startswith(f + '.') for f in FORBIDDEN_IMPORTS):
                    problems.append(f'{path.name}:{node.lineno} imports {n} (adapters may not draw geometry)')
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and node.slice.value == 'expected':
                problems.append(f'{path.name}:{node.lineno} reads case["expected"] (expected results must stay manual)')
            if isinstance(node, ast.Attribute) and node.attr == 'expected':
                problems.append(f'{path.name}:{node.lineno} reads .expected')
    return problems


CODE_IN_JSON = ('def ', 'lambda', 'import ', 'exec(', 'eval(', '__import__', 'subprocess', 'klayout')
COPY_WINDOW = 3
_SKIP_PREFIX = ('import ', 'from ', '#', '@', chr(34) * 3, chr(39) * 3)


def _norm(line):
    return ' '.join(line.strip().split())


def _meaningful(line):
    n = _norm(line)
    return len(n) >= 25 and not n.startswith(_SKIP_PREFIX)


def audit_no_copied_code() -> list[str]:
    """No line of project code may appear in the probe; no probe function may shadow a target.

    Compares every meaningful (>=25 chars, non-import/comment) line of the probe's own
    source and test file against every line of clay/**/*.py, checks no probe function or
    class has the name of a production target, and checks case JSON files hold data only.
    """
    problems = []
    production = {}   # normalized line -> {file: first line number}
    for path in (PROJECT_ROOT / 'clay').rglob('*.py'):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for i, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if _meaningful(line):
                production.setdefault(_norm(line), {}).setdefault(rel, i)
    own = [p for p in PACKAGE_DIR.rglob('*.py') if '__pycache__' not in p.parts]
    own.append(PROJECT_ROOT / 'tests' / 'test_primitive_probe.py')
    target_names = {production_identity(t)['qualname'] for a in REGISTRY.values() for t in a.targets}
    for path in own:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        text = path.read_text(encoding='utf-8')
        lines = [(i, _norm(l)) for i, l in enumerate(text.splitlines(), 1) if _meaningful(l)]
        # a copied block: COPY_WINDOW consecutive meaningful lines all present in one project file
        for k in range(len(lines) - COPY_WINDOW + 1):
            window = lines[k:k + COPY_WINDOW]
            files = set.intersection(*(set(production.get(n, {})) for _, n in window))
            if files:
                f = sorted(files)[0]
                problems.append(f'{rel}:{window[0][0]}-{window[-1][0]} block identical to {f}:'
                                f'{production[window[0][1]][f]}: {window[0][1][:60]}')
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in target_names:
                problems.append(f'{rel}:{node.lineno} defines {node.name}, the name of a production target')

    def walk(v, name):
        if isinstance(v, dict):
            for x in v.values():
                walk(x, name)
        elif isinstance(v, list):
            for x in v:
                walk(x, name)
        elif isinstance(v, str) and any(t in v for t in CODE_IN_JSON):
            problems.append(f'{name}: string looks like code: {v[:60]}')
    for path in sorted(list(CASE_DIR.glob('*.json')) + list(ARCHIVE_DIR.glob('*.json'))):
        walk(json.loads(path.read_text(encoding='utf-8')), path.name)
    return problems


def load_cases(selected=None, include_archive=False) -> list[dict]:
    cases = []
    dirs = [CASE_DIR] + ([ARCHIVE_DIR] if include_archive else [])
    for path in sorted(p for d in dirs for p in d.glob('*.json')):
        case = json.loads(path.read_text(encoding='utf-8'))
        case['_file'] = path.relative_to(PACKAGE_DIR).as_posix()
        for key in ('id', 'adapter', 'title', 'params', 'expected'):
            if key not in case:
                raise ValueError(f'{path.name}: missing field {key}')
        if not case['expected'].get('statements'):
            raise ValueError(f'{path.name}: expected.statements must list human-checkable items')
        if selected is None or case['id'] in selected or case['adapter'] in selected:
            cases.append(case)
    ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate case id')
    return cases
```

## primitive_probe/evidence.py

```python
"""Evidence tools: provenance comparison, executed-line tracing, source appendix.

None of these produce or alter layout. They answer three reviewer questions:
1. Is the code the probe executed byte-identical to the code the full CLAY flow
   executed (composition trace) and to the code on disk?          -> provenance()
2. Which lines of the project's primitive code actually ran?       -> LineTracer
3. What exactly is the probe's own source?                         -> source_appendix()
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from primitive_probe.core import PACKAGE_DIR, PROJECT_ROOT, load_cases

CLAY_DIR = PROJECT_ROOT / 'clay'


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ 1. provenance
def loaded_clay_files():
    """SHA-256 of every clay/ module loaded in this process (incl. data such as RULES)."""
    out = {}
    for mod in list(sys.modules.values()):
        f = getattr(mod, '__file__', None)
        if f and Path(f).suffix == '.py' and Path(f).resolve().is_relative_to(CLAY_DIR.resolve()):
            out[Path(f).resolve().relative_to(PROJECT_ROOT).as_posix()] = sha_file(f)
    return dict(sorted(out.items()))


def _loaded_files(run_dir):
    files = defaultdict(set)
    for m in sorted(Path(run_dir).glob('*/manifest.json')):
        for f, h in json.loads(m.read_text(encoding='utf-8')).get('loaded_production_files', {}).items():
            files[f].add(h)
    return files


def _probe_calls(run_dir):
    calls = []
    for m in sorted(Path(run_dir).glob('*/manifest.json')):
        doc = json.loads(m.read_text(encoding='utf-8'))
        for c in doc['production_calls']:
            calls.append({**c, 'case': doc['case']['id']})
    return calls


def _independence_scan():
    """Production code must not know about the probe or any case (no special-casing)."""
    ids = [c['id'] for c in load_cases()]
    pattern = re.compile(r'(^|\n)\s*(from|import)\s+primitive_probe\b')
    hits = []
    for path in CLAY_DIR.rglob('*.py'):
        text = path.read_text(encoding='utf-8', errors='replace')
        if pattern.search(text):
            hits.append(f'{path.relative_to(PROJECT_ROOT).as_posix()}: imports primitive_probe')
        for cid in ids:
            if cid in text:
                hits.append(f'{path.relative_to(PROJECT_ROOT).as_posix()}: mentions case id {cid}')
    return hits


def _plan_cross_check(calls, trace):
    """Same planner inputs in probe and in the full flow must give the same outputs."""
    rows = []
    events = [e for e in trace['events'] if e['operation'] in ('contact_plan', 'geometry_plan')]
    for c in calls:
        if c['qualname'] not in ('contact_plan', 'geometry_plan'):
            continue
        lib, kind, third = c['args'][0], c['args'][1], c['args'][2]
        for e in events:
            if e['operation'] != c['qualname'] or e['inputs']['kind'] != kind:
                continue
            if c['qualname'] == 'contact_plan':
                same_input = e['inputs']['width_nm'] == third and e['inputs']['policy'] == lib['contact_policies'][kind]
            else:
                # the flow records an empty policy for geometry_plan; compare the whole frozen library instead
                same_input = e['inputs']['chain'] == third and trace['inputs']['library'].get('geometry') == lib
            if same_input:
                rows.append({'case': c['case'], 'operation': c['qualname'], 'trace_event': e['id'],
                             'kind': kind, 'input': third, 'probe_output': c.get('returned'),
                             'flow_output': e['outputs'], 'identical': c.get('returned') == e['outputs']})
    return rows


def provenance(run_dir, trace_path=None):
    run_dir = Path(run_dir)
    calls = _probe_calls(run_dir)
    if not calls:
        raise SystemExit(f'No probe manifests with production calls under {run_dir}')
    files = defaultdict(set)
    for c in calls:
        files[c['file']].add(c['file_sha256'])
    # every loaded clay module that the full flow also registers (e.g. PDK RULES) is compared too
    trace_files = set(json.loads(Path(trace_path).read_text(encoding='utf-8'))['runtime']['implementation_sha256']) if trace_path else set()
    for f, hs in _loaded_files(run_dir).items():
        if f in files or f in trace_files:
            files[f] |= hs
    trace = json.loads(Path(trace_path).read_text(encoding='utf-8')) if trace_path else None
    recorded = trace['runtime']['implementation_sha256'] if trace else {}
    table, ok = [], True
    for f in sorted(files):
        disk = sha_file(PROJECT_ROOT / f)
        probe = sorted(files[f])
        flow = recorded.get(f)
        same_probe = probe == [disk]
        same_flow = None if flow is None else flow == disk
        ok &= same_probe and same_flow is not False
        table.append((f, probe, disk, flow, same_probe, same_flow))
    independence = _independence_scan()
    ok &= not independence
    cross = _plan_cross_check(calls, trace) if trace else []
    ok &= all(r['identical'] for r in cross)
    lines = ['# 溯源比对报告（PROVENANCE）', '',
             f'- 探针运行目录：`{run_dir}`', f'- 完整流程执行记录：`{trace_path or "未提供"}`'
             + (f'（SHA-256 {sha_file(trace_path)}）' if trace_path else ''), '',
             '## A. 探针调用/加载的工程文件 = 完整流程运行的文件 = 磁盘当前文件', '',
             '| 文件 | 探针运行时 SHA-256 | 磁盘当前 | 完整流程记录 | 探针=磁盘 | 流程=磁盘 |', '|---|---|---|---|---|---|']
    for f, probe, disk, flow, sp, sf in table:
        lines.append(f"| {f} | {', '.join(p[:16] for p in probe)} | {disk[:16]} | "
                     f"{flow[:16] if flow else '（流程未登记此文件）'} | {'✔' if sp else '✘'} | "
                     f"{'—' if sf is None else ('✔' if sf else '✘')} |")
    lines += ['', '“流程未登记”表示该文件不属于 clay/learning 反相器流程的登记清单（如 MILP 对称放置器），'
              '只能依据“探针=磁盘”和第 C 节的执行行记录来证明。', '',
              '## B. 工程代码不认识探针（无针对用例的特判）', '',
              '扫描范围：clay/**/*.py；规则：不得 import primitive_probe，不得出现任何用例 id。', '',
              ('结果：无命中 ✔' if not independence else '结果：发现命中 ✘\n\n' + '\n'.join(f'- {h}' for h in independence)), '',
              '## C. 同一输入下，探针与完整流程中的原语输出一致', '']
    if cross:
        lines += ['| 用例 | 原语 | 流程事件 | kind | 输入 | 探针输出 | 流程输出 | 一致 |', '|---|---|---|---|---|---|---|---|']
        for r in cross:
            lines.append(f"| {r['case']} | {r['operation']} | {r['trace_event']} | {r['kind']} | {r['input']} | "
                         f"`{json.dumps(r['probe_output'], ensure_ascii=False)[:120]}` | "
                         f"`{json.dumps(r['flow_output'], ensure_ascii=False)[:120]}` | {'✔' if r['identical'] else '✘'} |")
    else:
        lines.append('无可比对项（未提供执行记录，或输入没有重合）。')
    lines += ['', f"## 结论：{'全部一致 ✔' if ok else '存在不一致 ✘，探针结果不可作为工程原语的证据'}", '']
    (run_dir / 'PROVENANCE.md').write_text('\n'.join(lines), encoding='utf-8')
    return ok


# ------------------------------------------------------------------ 2. executed lines
class LineTracer:
    """sys.settrace recorder of executed (file, line) inside clay/ only."""

    def __init__(self):
        self.hits = defaultdict(set)
        self.root = str(CLAY_DIR.resolve()).lower()

    def _local(self, frame, event, arg):
        if event == 'line':
            self.hits[frame.f_code.co_filename].add(frame.f_lineno)
        return self._local

    def _global(self, frame, event, arg):
        name = frame.f_code.co_filename
        if name.lower().startswith(self.root):
            self.hits[name].add(frame.f_lineno)
            return self._local
        return None

    def __enter__(self):
        sys.settrace(self._global)
        return self

    def __exit__(self, *exc):
        sys.settrace(None)


def write_executed_lines(tracer, calls, path):
    """Annotate the source of every called production function with executed lines."""
    import inspect, importlib
    out = ['# 实际执行的工程源码行（sys.settrace 记录）', '',
           '`▶` 表示本用例运行时该行被执行；`·` 表示未执行（如未触发的分支）。源码直接读自磁盘工程文件。', '']
    seen = set()
    for c in calls:
        key = (c['module'], c['qualname'])
        if key in seen:
            continue
        seen.add(key)
        obj = importlib.import_module(c['module'])
        for part in c['qualname'].split('.'):
            obj = getattr(obj, part)
        src_file = str(Path(inspect.getsourcefile(obj)).resolve())
        hit = next((v for k, v in tracer.hits.items() if str(Path(k).resolve()).lower() == src_file.lower()), set())
        src, first = inspect.getsourcelines(obj)
        n_exec = sum((first + i) in hit for i in range(len(src)))
        out += [f"## `{c['module']}.{c['qualname']}` — {c['file']}:{first}（执行 {n_exec}/{len(src)} 行）", '', '```text']
        for i, text in enumerate(src):
            ln = first + i
            out.append(f"{'▶' if ln in hit else '·'} {ln:5d} | {text.rstrip()}")
        out += ['```', '']
    other = sorted((str(Path(k).relative_to(PROJECT_ROOT)) if Path(k).is_relative_to(PROJECT_ROOT) else k, len(v))
                   for k, v in tracer.hits.items())
    out += ['## 本用例触及的全部 clay/ 文件', '', '| 文件 | 执行的不同行数 |', '|---|---|']
    out += [f'| {f} | {n} |' for f, n in other]
    Path(path).write_text('\n'.join(out) + '\n', encoding='utf-8')


# ------------------------------------------------------------------ 3. source appendix
def source_appendix(path):
    files = sorted(p for p in PACKAGE_DIR.rglob('*.py') if '__pycache__' not in p.parts)
    files.append(PROJECT_ROOT / 'tests' / 'test_primitive_probe.py')
    out = ['# 附录：验证系统全部自写源代码', '',
           '以下是探针系统中**由验证者新增**的全部代码，原样列出并附 SHA-256。审阅要点：',
           '1. 不存在任何复刻原语功能的计算（无尺寸、位置、规则的推导）；',
           '2. adapters/ 中只有输入组装和 `ctx.call(<clay.* 函数>)`；',
           '3. 输出几何只来自工程函数返回的 Layout 或坐标。', '',
           '| 文件 | 行数 | SHA-256 |', '|---|---|---|']
    for f in files:
        out.append(f'| {f.relative_to(PROJECT_ROOT).as_posix()} | {len(f.read_text(encoding="utf-8").splitlines())} | {sha_file(f)} |')
    for f in files:
        out += ['', f'## {f.relative_to(PROJECT_ROOT).as_posix()}', '', '```python',
                f.read_text(encoding='utf-8').rstrip(), '```']
    Path(path).write_text('\n'.join(out) + '\n', encoding='utf-8')


# ------------------------------------------------------------------ 4. severance
class SeveredError(RuntimeError):
    """Raised by a production function whose body was temporarily disconnected."""


SEVER_TOKEN = 'PRIMITIVE_PROBE_SEVERED'


def _severed(*args, **kwargs):
    # runs with the PRODUCTION module's globals, so only builtins may be used here
    raise RuntimeError('PRIMITIVE_PROBE_SEVERED')


def severance(case, adapter):
    """For each production target: disconnect its body in-process, rerun the adapter.

    The function object's __code__ is swapped, so EVERY reference to it (including
    names imported with `from ... import`) is affected. If the probe produced its
    result from a copy or re-implementation, it would still succeed; a genuine
    dependency makes it fail with SeveredError. Files are never modified and the
    original code object is restored in `finally`.
    """
    import inspect
    from primitive_probe.core import ProbeContext, production_identity
    rows = []
    for target in adapter.targets:
        fn = inspect.unwrap(target)
        ident = production_identity(fn)
        original = fn.__code__
        before = sha_file(PROJECT_ROOT / ident['file'])
        fn.__code__ = _severed.__code__
        try:
            adapter.fn(ProbeContext(case['id'] + '_severed'), case['params'])
            outcome, depends = '仍然产出结果', False
        except Exception as e:  # noqa: BLE001
            chain, cur = [], e
            while cur is not None:
                chain.append(str(cur)); cur = cur.__cause__ or cur.__context__
            depends = any(SEVER_TOKEN in m for m in chain)
            outcome = ('失败：被切断的函数被调用（结果依赖该函数）' if depends
                       else f'失败，但原因不是切断：{type(e).__name__}: {e}')
        finally:
            fn.__code__ = original
        rows.append({'function': f"{ident['module']}.{ident['qualname']}", 'file': f"{ident['file']}:{ident['line']}",
                     'outcome': outcome, 'depends': depends,
                     'file_unchanged': before == sha_file(PROJECT_ROOT / ident['file'])})
    # sanity: after restoring, the adapter works again
    adapter.fn(ProbeContext(case['id'] + '_restored'), case['params'])
    return rows
```

## primitive_probe/fixtures.py

```python
"""Input fixtures. The ONLY place a probe creates shapes, and only as INPUTS.

Shapes made here are handed to production code (e.g. two rectangles given to a
placement relation) or shown for reference on probe layer 200 (display fixtures).
They are never presented as primitive output.
"""
import klayout.db as db


def box(b):
    x0, y0, x1, y1 = (int(v) for v in b)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f'Fixture box must be [x0,y0,x1,y1] with x1>x0, y1>y0: {b}')
    return db.Box(x0, y0, x1, y1)


def region(*boxes):
    r = db.Region()
    for b in boxes:
        r.insert(box(b))
    return r


def row(layers, terminals=(), kind=None):
    """A row-shaped input in the format the project's turn()/materialize() consume.

    layers: {'active': [[x0,y0,x1,y1], ...], 'poly': [...], ...}
    terminals: [[net, [x0,y0,x1,y1]], ...]
    """
    regions = {name: region(*bs) for name, bs in layers.items()}
    active = regions['active'].bbox() if 'active' in regions and not regions['active'].is_empty() else None
    return {'regions': regions, 'active': active,
            'terminals': [(net, box(b)) for net, b in terminals], 'kind': kind}


def boxes_to_region(boxes):
    """Collect production-returned Box objects into one Region (no new geometry)."""
    r = db.Region()
    for b in boxes:
        r.insert(b)
    return r


def point(p):
    return (int(p[0]), int(p[1]))


class Extent:
    """Plain rectangle object exposing .extent(), the interface the MILP placer reads."""
    def __init__(self, w, h):
        self.w, self.h = int(w), int(h)

    def extent(self):
        return (0, 0, self.w, self.h)


def display(ctx, bbox, label):
    """Register a display-only input shape (drawn on probe layer 200/0)."""
    ctx.fixtures.append({'bbox_nm': [int(v) for v in bbox], 'label': label})


def load_gds(relative_path):
    """Read an existing GDS (e.g. produced by the project's full flow) as an INPUT."""
    from primitive_probe.core import PROJECT_ROOT
    layout = db.Layout()
    layout.read(str(PROJECT_ROOT / relative_path))
    return layout
```

## primitive_probe/render.py

```python
"""Write probe artifacts: production-only GDS, review overlay GDS, layer file, review sheets.

Output geometry in production_only.gds is the Layout object returned by the
project's own materialize (written unmodified), or coordinates copied verbatim
from a production return value. review.gds = that file + clearly separated
probe layers (200-203) holding inputs, human-written expectations and markers.
"""
import csv
import json
from pathlib import Path

import klayout.db as db

PROBE_LAYERS = {
    'fixture': (200, 0), 'fixture_text': (200, 1),
    'expected': (201, 0), 'expected_text': (201, 1),
    'marker': (202, 0), 'marker_text': (202, 1),
    'extent_text': (203, 0),
}
NAMED = {
    (22, 0): ('COMP / active', '#ff8080', 'I5'), (30, 0): ('Poly2', '#ff0000', 'I9'),
    (33, 0): ('Contact', '#000000', 'I0'), (34, 0): ('Metal1', '#0000ff', 'I13'),
    (34, 10): ('Metal1 label', '#0000ff', 'I1'), (21, 0): ('Nwell', '#80c080', 'I1'),
    (32, 0): ('Nplus', '#c0c000', 'I1'), (31, 0): ('Pplus', '#c08000', 'I1'),
    (55, 0): ('Dualgate', '#808080', 'I1'), (112, 1): ('V5_XTOR', '#a0a0ff', 'I1'),
    (0, 0): ('PR boundary (materialize)', '#404040', 'I1'),
    (200, 0): ('PROBE input fixture', '#00a0a0', 'I1'), (200, 1): ('PROBE input text', '#00a0a0', 'I1'),
    (201, 0): ('PROBE EXPECTED (human)', '#ff00ff', 'I1'), (201, 1): ('PROBE expected text', '#ff00ff', 'I1'),
    (202, 0): ('PROBE marker (prod. value)', '#ff8000', 'I0'), (202, 1): ('PROBE marker text', '#ff8000', 'I1'),
    (203, 0): ('PROBE placed-extent label', '#000000', 'I1'),
}


def write_lyp(path):
    items = []
    for (layer, dt), (name, color, dither) in NAMED.items():
        width = 3 if layer == 201 else 1
        items.append(f'''  <properties>
   <frame-color>{color}</frame-color><fill-color>{color}</fill-color>
   <dither-pattern>{dither}</dither-pattern><visible>true</visible><transparent>false</transparent>
   <width>{width}</width><line-style>{'I2' if layer == 201 else 'I0'}</line-style>
   <name>{layer}/{dt} {name}</name><source>{layer}/{dt}@1</source>
  </properties>''')
    Path(path).write_text('<?xml version="1.0" encoding="utf-8"?>\n<layer-properties>\n'
                          + '\n'.join(items) + '\n</layer-properties>\n', encoding='utf-8')


def _box(b):
    return db.Box(*(int(v) for v in b))


def write_production_only(out, path):
    if out.layout is not None:
        out.layout.write(str(path))
        return
    if not out.extents:
        return
    layout = db.Layout(); layout.dbu = .001
    top = layout.create_cell('PROBE_PRODUCTION_EXTENTS')
    for e in out.extents:
        top.shapes(layout.layer(*e['layer'])).insert(_box(e['bbox_nm']))
        c = _box(e['bbox_nm']).center()
        top.shapes(layout.layer(*PROBE_LAYERS['extent_text'])).insert(db.Text(e['label'], db.Trans(c)))
    layout.write(str(path))


def write_review(case, ctx, out, production_path, path):
    layout = db.Layout(); layout.dbu = .001
    if production_path.exists():
        layout.read(str(production_path))
        top = layout.top_cell()
    else:
        top = layout.create_cell('PROBE_NO_PRODUCTION_OUTPUT')
    L = {k: layout.layer(*v) for k, v in PROBE_LAYERS.items()}
    for f in ctx.fixtures:
        b = _box(f['bbox_nm'])
        top.shapes(L['fixture']).insert(b)
        top.shapes(L['fixture_text']).insert(db.Text(f['label'], db.Trans(b.p1)))
    for i, s in enumerate(case['expected'].get('shapes', []), 1):
        b = _box(s['bbox_nm'])
        top.shapes(L['expected']).insert(b)
        top.shapes(L['expected_text']).insert(db.Text(f"EXP{i}: {s.get('label', '')}", db.Trans(b.p2)))
    full = top.bbox() if not top.bbox().empty() else db.Box(0, 0, 1000, 1000)
    pad = 1000
    for m in out.markers:
        a = int(m['at_nm'])
        b = (db.Box(a - 10, full.bottom - pad, a + 10, full.top + pad) if m['kind'] == 'vline'
             else db.Box(full.left - pad, a - 10, full.right + pad, a + 10))
        top.shapes(L['marker']).insert(b)
        top.shapes(L['marker_text']).insert(db.Text(m['label'], db.Trans(b.p2)))
    top.shapes(L['expected_text']).insert(
        db.Text(f"CASE {case['id']}: {case['title']}", db.Trans(full.left, full.top + pad)))
    layout.write(str(path))


def _md_json(value):
    return '```json\n' + json.dumps(value, indent=2, ensure_ascii=False) + '\n```'


def write_case_report(case, adapter, ctx, out, status, error, case_dir):
    exp = case['expected']
    lines = [f"# 原语人工验证：{case['id']}", '',
             '> **运行结果（工程实际输出）单独见同目录 `RESULT.md`**；本文件是输入、预期与人工核对表。', '', f"**标题**：{case['title']}", '',
             f"**被测原语适配器**：`{adapter.name}`（类别：{adapter.category}）— {adapter.summary}", '',
             f"**出图路径**：`{adapter.render_path}`", '',
             f"**运行状态**：{status}" + (f"\n\n**生产代码抛出**：`{error}`" if error else ''), '']
    if case.get('purpose'):
        lines += ['## 目的', '', case['purpose'], '']
    lines += ['## 实际执行的工程代码（证明输出来自工程 primitive，而非探针脚本）', '',
              '| # | 函数 | 文件:行 | 文件 SHA-256 (前 16) | 函数源码 SHA-256 (前 16) |', '|---|---|---|---|---|']
    for c in ctx.calls:
        lines.append(f"| {c['order']} | `{c['module']}.{c['qualname']}` | {c['file']}:{c['line']} | "
                     f"{c['file_sha256'][:16]} | {c['function_source_sha256'][:16]} |")
    lines += ['', '完整参数与返回值见 `manifest.json`。', '',
              '## 输入（来自用例文件，人工编写）', '', _md_json(case['params']), '']
    if not adapter.geometry:
        lines += ['## 说明：本原语是决策/检查类，输出为数据而非版图', '',
                  '下方“工程原语返回”即被测结果，请与预期逐项比对。'
                  + (' `input_context.gds` 是交给原语的**输入**版图（来自工程完整流程），不是原语的输出。'
                     if out is not None and out.context_gds else ''), '']
    if out is not None and out.outputs:
        lines += ['## 运行结果：工程原语返回的数据（与 RESULT.md 相同）', '', _md_json(out.outputs), '']
    lines += ['## 预期结果（人工编写，非程序计算）—— 请在 KLayout 中逐项核对并填写', '']
    if exp.get('derivation'):
        lines += ['**预期推导依据**：' + exp['derivation'], '']
    lines += ['| # | 预期 | 实测/观察（人工填写） | 通过? (Y/N) |', '|---|---|---|---|']
    for i, s in enumerate(exp['statements'], 1):
        lines.append(f'| E{i} | {s} |  |  |')
    if exp.get('shapes'):
        lines += ['', '预期图形框（品红虚框，层 201/0，标注 `EXPi`）：', '']
        for i, s in enumerate(exp['shapes'], 1):
            lines.append(f"- EXP{i}: bbox {s['bbox_nm']} — {s.get('label', '')}")
    lines += ['', '## 查看方法', '',
              '1. KLayout 打开 `production_only.gds`：仅包含工程代码产生的图形（未叠加任何探针层）。',
              '2. 打开 `review.gds` 并加载 `../layers.lyp`（File > Load Layer Properties）：在相同图形上叠加'
              '：200/x 输入夹具（青色）、201/x 人工预期框（品红虚线）、202/x 工程返回值标记（橙色，例如对称轴）。',
              '3. 用 Ruler（快捷键 K）测量后填写上表。两份 GDS 中工程图层内容一致；如需确认，可用 KLayout XOR 比较。',
              '', '审核人：__________    日期：__________    结论：通过 / 不通过 / 存疑', '']
    (case_dir / 'REVIEW.md').write_text('\n'.join(lines), encoding='utf-8')


def write_sheets(run_dir, rows):
    with open(run_dir / 'review_sheet.csv', 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['case_id', 'adapter', 'production_functions', 'run_status', 'item', 'expected',
                    'observed_manual', 'pass_manual', 'reviewer', 'date'])
        for r in rows:
            for i, s in enumerate(r['statements'], 1):
                w.writerow([r['id'], r['adapter'], r['functions'], r['status'], f'E{i}', s, '', '', '', ''])
    index = ['# 原语人工验证运行索引', '', '| 用例 | 适配器 | 状态 | 报告 |', '|---|---|---|---|']
    for r in rows:
        index.append(f"| {r['id']} | {r['adapter']} | {r['status']} | [{r['id']}/REVIEW.md]({r['id']}/REVIEW.md) |")
    (run_dir / 'INDEX.md').write_text('\n'.join(index) + '\n', encoding='utf-8')


def write_result(case, adapter, out, status, error, prod_path, case_dir):
    """RESULT.md: ONLY what the production code returned / produced. No expectation, no verdict.

    For layouts, every shape is read back from production_only.gds (the file the
    project's own code wrote) and listed per layer with its bounding box.
    """
    lines = [f"# 运行结果：{case['id']}", '',
             '本文件只记录**工程原语的实际输出**，不含预期、不做判断。预期与人工对比见同目录 REVIEW.md。', '',
             f'- 运行状态：{status}']
    if error:
        lines.append(f'- 工程代码抛出：`{error}`（完整信息见 traceback.txt）')
    lines.append('')
    if out is not None and out.outputs:
        lines += ['## 工程原语返回的数据', '', _md_json(out.outputs), '']
    if prod_path.exists():
        layout = db.Layout(); layout.read(str(prod_path))
        top = layout.top_cell()
        lines += ['## 工程生成的版图（读自 production_only.gds）', '',
                  '坐标单位 nm；bbox = [x0, y0, x1, y1]（左下, 右上）。', '',
                  '| 图层 | # | bbox_nm | 宽 × 高 | 面积 nm² | 顶点数 |', '|---|---|---|---|---|---|']
        texts = []
        for idx in sorted(layout.layer_indexes(), key=lambda i: (layout.get_info(i).layer, layout.get_info(i).datatype)):
            info = layout.get_info(idx)
            name = f'{info.layer}/{info.datatype} {NAMED.get((info.layer, info.datatype), ("",))[0]}'.strip()
            polys = sorted(db.Region(top.begin_shapes_rec(idx)).merged().each(),
                           key=lambda q: (q.bbox().left, q.bbox().bottom))
            for k, q in enumerate(polys, 1):
                b = q.bbox()
                lines.append(f'| {name} | {k} | [{b.left}, {b.bottom}, {b.right}, {b.top}] | '
                             f'{b.width()} × {b.height()} | {q.area()} | {q.num_points()} |')
            for sh in top.shapes(idx).each():
                if sh.is_text():
                    texts.append((name, sh.text.string, sh.text.x, sh.text.y))
        if texts:
            lines += ['', '文本标签：', '', '| 图层 | 文本 | 位置 (x, y) |', '|---|---|---|']
            lines += [f'| {n} | {t} | ({x}, {y}) |' for n, t, x, y in texts]
        lines.append('')
    elif adapter.geometry and not error:
        lines += ['（未生成版图）', '']
    if out is not None and out.extents:
        lines += ['## 工程返回的坐标（原样）', '', _md_json(out.extents), '']
    if out is not None and out.markers:
        lines += ['## 工程返回的标记值（原样）', '', _md_json(out.markers), '']
    if out is not None and out.context_gds:
        lines += [f'注：`input_context.gds` 是交给原语的**输入**（{out.context_gds}），不是输出。', '']
    (case_dir / 'RESULT.md').write_text('\n'.join(lines), encoding='utf-8')
```

## tests/test_primitive_probe.py

```python
"""Framework tests for primitive_probe. These do NOT judge primitive correctness
(that is the human review); they check the probe cannot launder its own geometry."""
import json

import klayout.db as db
import pytest

from primitive_probe import core
from primitive_probe.adapters import load_all
from primitive_probe.__main__ import run_case

load_all()
PROBE_LAYERS = {200, 201, 202, 203}


def test_adapter_isolation_audit():
    assert core.audit_adapters() == []


def test_no_project_code_copied_into_probe():
    assert core.audit_no_copied_code() == []


def test_copy_detector_catches_a_copied_primitive(tmp_path, monkeypatch):
    # positive control: a verbatim copy of a production function must be reported
    import inspect
    from clay.learning import contact_primitives
    canary = core.PACKAGE_DIR / 'adapters' / '_pytest_copy_canary.py'
    canary.write_text(inspect.getsource(contact_primitives.contact_plan), encoding='utf-8')
    try:
        found = core.audit_no_copied_code()
    finally:
        canary.unlink()
    assert any('_pytest_copy_canary.py' in f and 'block identical' in f for f in found)
    assert any('defines contact_plan' in f for f in found)


def test_every_case_has_registered_adapter_and_every_adapter_a_case():
    cases = core.load_cases()
    assert {c['adapter'] for c in cases} <= set(core.REGISTRY)
    assert set(core.REGISTRY) <= {c['adapter'] for c in cases}


def test_non_project_callables_are_rejected():
    with pytest.raises(PermissionError):
        core.ProbeContext('x').call(json.dumps, {})


def _layers(path):
    layout = db.Layout(); layout.read(str(path))
    out = {}
    for i in layout.layer_indexes():
        info = layout.get_info(i)
        reg = db.Region(layout.top_cell().begin_shapes_rec(i))
        if not reg.is_empty():
            out[(info.layer, info.datatype)] = reg
    return out


@pytest.mark.parametrize('case', core.load_cases(include_archive=True), ids=lambda c: c['id'])
def test_case_runs_production_and_review_matches(case, tmp_path):
    result = run_case(case, tmp_path)
    d = tmp_path / case['id']
    manifest = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
    called = {c['module'] + '.' + c['qualname'] for c in manifest['production_calls']}
    assert called and all(c.startswith('clay.') for c in called)
    if case.get('expect_rejection'):
        assert 'raised' in result['status'] and not (d / 'production_only.gds').exists()
        return
    assert result['status'] == 'production executed'
    if not core.REGISTRY[case['adapter']].geometry:
        assert manifest['production_outputs'] and not (d / 'production_only.gds').exists()
        return
    prod, review = _layers(d / 'production_only.gds'), _layers(d / 'review.gds')
    # 203/0 = text labels of production-returned extents (symmetry placer); other probe layers forbidden
    assert prod and not (set(prod) & {(l, t) for l in PROBE_LAYERS - {203} for t in range(4)})
    for key, reg in prod.items():  # overlay never alters production layers
        assert (reg ^ review[key]).is_empty()
    assert set(review) - set(prod) <= {(l, t) for l in PROBE_LAYERS for t in range(4)}


def test_severance_detects_dependency_and_independence():
    from primitive_probe import evidence
    from clay.learning import contact_primitives
    u10 = next(c for c in core.load_cases() if c['id'] == 'U10_custom_inverter_topology')
    adapter = core.REGISTRY[u10['adapter']]
    # every declared target is a real dependency
    assert all(r['depends'] and r['file_unchanged'] for r in evidence.severance(u10, adapter))
    # control: severing an UNRELATED project function must not break the case
    original = contact_primitives.turn.__code__
    contact_primitives.turn.__code__ = evidence._severed.__code__
    try:
        out = adapter.fn(core.ProbeContext('control'), u10['params'])
    finally:
        contact_primitives.turn.__code__ = original
    assert out.outputs['gate_net'] == 'A'
```
