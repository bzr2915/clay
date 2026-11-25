"""执行器（骨架阶段的最小闭环）：跑通"调用工程函数 → 取实际值 → 比对 → 判定"。

流程：
  1. check（bindcheck + spec schema）
  2. 载入 bind.run，用 ProbeContext 执行（ctx.call 只放行 clay.* 并记录溯源）
  3. 按 spec.actual 的 from 路径，从 bind 的返回值里取值（框架挑值，AI 不能加工）
  4. compare 逐条判定
  5. 生成 RESULT.json 与 REPORT.md

TODO（阶段二）：
  · 版图输出（form='layout'）：把 ctx.layout 登记的 Layout 写出 production_only.gds，并支持
    "layout.polygons[22/0]" 这类按图层取 bbox 的取值路径
  · 切断测试（severance）：复用 primitive_probe.evidence.severance
  · 提交文件哈希 + 框架代码哈希打包进报告
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import klayout.db as db

from primitive_probe.core import ProbeContext, PROJECT_ROOT
from primitive_verify import bindcheck, compare, expected_schema, registry, resolve, severance

DIR = Path(__file__).resolve().parent
PRIMS = DIR / 'primitives'
OUT = PROJECT_ROOT / 'data' / 'primitive_generalization' / 'primitive_verify_runs'


class _LayerView:
    """版图的只读视图：layout.polygons[<图层>] -> 该层全部多边形的 bbox 列表。[0] 取第一个。"""

    def __init__(self, layout):
        self._layout = layout

    @property
    def polygons(self):
        return _Layers(self._layout)

    @property
    def top_cell(self):
        return self._layout.top_cell().name


class _Layers:
    def __init__(self, layout):
        self._layout = layout

    def __getitem__(self, key):
        info = str(key)
        layer, _, datatype = info.partition('/')
        try:
            index = self._layout.layer(int(layer), int(datatype or 0))
        except (TypeError, ValueError) as e:
            raise resolve.ResolveError(f'bad layer key {key!r}') from e
        if index < 0:
            return []
        region = db.Region(self._layout.top_cell().begin_shapes_rec(index)).merged()
        return [polygon.bbox() for polygon in
                sorted(region.each(), key=lambda q: (q.bbox().left, q.bbox().bottom))]


class _LayoutProxy:
    """ctx.layout(<Layout>) 的返回值：既能被写进 GDS，也能按图层取值。"""

    def __init__(self, layout):
        self.layout = layout

    @property
    def polygons(self):
        return _Layers(self.layout)

    @property
    def top_cell(self):
        return self.layout.top_cell().name


class Ctx(ProbeContext):
    """ProbeContext + 登记版图/原始值。bind.py 只能看到 call / layout / raw 三个方法。"""
    def __init__(self, name):
        super().__init__(name)
        self.primitive = name          # 原语名（供测量视图查找），不随实例标签变化
        self.layouts = []

    def layout(self, obj):
        proxy = _LayoutProxy(obj)
        self.layouts.append(proxy)
        return proxy

    def raw(self, value):
        return value

    def measure(self, obj):
        """框架自有的测量视图（views.py），bind.py 只点名，不定义派生量。"""
        from primitive_verify import views
        return views.apply(self.primitive, obj)


def _load_bind(path):
    spec = importlib.util.spec_from_file_location('primitive_verify_submission', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


def run(name, archive=False):
    primitive = registry.lookup(name)
    base = PRIMS / name
    problems = [f'bind.py: {p}' for p in bindcheck.check(base / 'bind.py')]
    spec = expected_schema.load(base / 'spec.json')
    problems += [f'spec.json: {p}' for p in expected_schema.validate(spec, name)]
    for row in spec['actual']:
        try:
            resolve.check_syntax(row['from'])
        except resolve.ResolveError as e:
            problems.append(f"spec.json: actual {row['name']} 路径不合法：{e}")
    if problems:
        _write(name, spec, [], 'FAIL', problems, None, None, [], None)
        print('\n'.join(problems)); return 1

    fn = _load_bind(base / 'bind.py')
    ctx = Ctx(name)
    raised = None
    try:
        produced = fn(ctx, spec['inputs'])
    except Exception as e:  # noqa: BLE001
        produced, raised = {}, f'{type(e).__name__}: {e}'

    # 声明的被测函数必须全部执行
    missing = [t for t in primitive.targets
               if registry.import_target(t) not in {__import__('inspect').unwrap(c) for c in ctx._called}]

    rows, resolved = [], {}
    for actual, expected in zip(spec['actual'], spec['expected']):
        if raised and expected.get('how') != 'reject':
            rows.append({'name': actual['name'], 'from': actual['from'], 'ok': None,
                         'detail': f'工程函数未返回结果：{raised}'}); continue
        try:
            root = ({'raised': raised} if raised else {}) if expected.get('how') == 'reject' else produced
            value = resolve.resolve(root, actual['from'])
        except resolve.ResolveError as e:
            rows.append({'name': actual['name'], 'from': actual['from'], 'ok': None, 'detail': str(e)}); continue
        ref = resolved.get(expected.get('ref')) if expected.get('how') == 'eq_other' else None
        verdict = compare.compare(value, expected,
                                  ref_value=ref if ref is not None else compare._MISSING)
        resolved[actual['name']] = value
        rows.append({'name': actual['name'], 'from': actual['from'],
                     'actual': _short(value), **verdict})

    out_dir = _out_dir(name)
    gds = None
    if getattr(ctx, 'layouts', None):
        out_dir.mkdir(parents=True, exist_ok=True)
        gds = out_dir / 'production_only.gds'
        ctx.layouts[0].layout.write(str(gds))

    # 切断测试：结果必须依赖每个被测工程函数
    sever_rows, sever_ok = [], True
    if not raised:
        sever_rows, sever_ok = severance.check(name, fn, spec['inputs'], lambda: Ctx(name))
    else:
        sever_rows, sever_ok = [], None   # 负对照（期望工程拒绝）不做切断

    result = compare.verdict(rows)
    if sever_ok is False:
        result = 'FAIL'
        rows.append({'name': 'SEVERANCE', 'from': '-', 'ok': False,
                     'detail': '存在不依赖被测函数的输出，结果不可作为工程函数的证据'})
    if missing:
        result = 'FAIL'
        rows.append({'name': 'TARGETS', 'from': '-', 'ok': False,
                     'detail': f'声明的被测函数未被调用：{missing}'})
    _write(name, spec, rows, result, [], ctx.calls, out_dir, sever_rows, sever_ok)
    _print(name, result, rows)
    return 0 if result == 'PASS' else 1


def _short(value):
    if hasattr(value, 'bbox'):
        b = value.bbox()
        return f'[{b.left}, {b.bottom}, {b.right}, {b.top}]'
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + '…'


def _out_dir(name):
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    return OUT / f'{name}_{stamp}'


def _hashes(name):
    """提交文件与判定逻辑的 SHA-256：固定预期与判定规则，可事后核对。"""
    import hashlib
    from primitive_probe.core import PROJECT_ROOT as PROOT
    files = {f'primitive_verify/{name}_submission': PRIMS / name / 'bind.py',
             f'primitive_verify/{name}_spec': PRIMS / name / 'spec.json'}
    for name_ in ('compare.py', 'resolve.py', 'bindcheck.py', 'expected_schema.py', 'runner.py', 'severance.py'):
        files[f'framework:{name_}'] = DIR / name_
    out = {}
    for key, path in files.items():
        if path.exists():
            out[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _write(name, spec, rows, result, problems, calls, out, sever_rows=(), sever_ok=None):
    out = out or _out_dir(name)
    out.mkdir(parents=True, exist_ok=True)
    entry = registry.lookup(name)
    review = ('已冻结（人已复核）' if entry.status == 'frozen'
              else f'待复核（登记来源：{entry.provenance}），本结果不计入冻结结果')
    (out / 'RESULT.json').write_text(json.dumps(
        {'primitive': name, 'verdict': result, 'review_status': entry.status,
         'provenance': entry.provenance, 'rows': rows, 'problems': problems,
         'severance': list(sever_rows), 'severance_ok': sever_ok, 'hashes': _hashes(name),
         'inputs': spec['inputs'], 'derivation': spec['derivation'],
         'production_calls': calls or []}, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    lines = [f'# 原语验证结果：{name}', '', f'**判定：{result}**', '', f'条目状态：{review}', '',
             '判定由 primitive_verify/compare.py 完成，与提交者无关。', '',
             '| # | 取实际值 | 比较方式 | 实际值 | 预期 | 结果 |', '|---|---|---|---|---|---|']
    for r, e in zip(rows, spec['expected'] + [{}] * len(rows)):
        mark = {True: '✔', False: '✘', None: '?'}[r.get('ok')]
        lines.append(f"| {r.get('name')} | `{r.get('from')}` | {r.get('how', '-')} | "
                     f"{r.get('actual', '-')} | {e.get('value', '-')} | {mark} {r.get('detail', '')} |")
    if sever_rows:
        lines += ['', '## 切断测试（结果是否真的依赖工程函数）', '',
                  '| 被测工程函数 | 位置 | 结果 |', '|---|---|---|']
        for r in sever_rows:
            lines.append(f"| `{r['function']}` | {r['file']} | {'✔ ' if r['depends'] else '✘ '}{r['outcome']} |")
    if problems:
        lines += ['', '## 提交检查问题', ''] + [f'- {p}' for p in problems]
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return out


def _print(name, result, rows):
    for r in rows:
        mark = {True: 'OK  ', False: 'FAIL', None: '?   '}[r.get('ok')]
        print(f"  {mark} {r.get('name'):6s} {r.get('from'):28s} {r.get('detail', '')}")
    entry = registry.lookup(name)
    note = '' if entry.status == 'frozen' else '  [待复核：不计入冻结结果]'
    print(f'{name}: {result}{note}')
