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
