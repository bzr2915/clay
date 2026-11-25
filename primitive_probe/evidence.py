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
