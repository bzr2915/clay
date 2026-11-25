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
