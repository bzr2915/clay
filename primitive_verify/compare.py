"""比对引擎：**唯一**的判定处。AI 不得修改本文件（提交时记录其哈希）。

每种比较方式都返回 (ok: bool|None, detail: str)：
    True   一致
    False  不一致（→ 该条目 FAIL）
    None   无法比较（→ 该条目 INCONCLUSIVE，例如类型不匹配但本不该发生）
"""
from math import isclose


def _num_eq(exp, act, tol):
    return isclose(float(exp), float(act), abs_tol=float(tol))


def eq(exp, act, **opts):
    if exp == act:
        return True, 'exactly equal'
    return False, f'expected {exp!r}, actual {act!r}'


def num(exp, act, tol=0, **opts):
    if isinstance(act, bool) or not isinstance(act, (int, float)):
        return None, f'actual is not numeric ({type(act).__name__})'
    ok = _num_eq(exp, act, tol)
    return ok, f'expected {exp} ± {tol}, actual {act}'


def bbox(exp, act, tol=0, **opts):
    """exp/act: [x0,y0,x1,y1] (also accepts [left,bottom,right,top] tuples or klayout Box)."""
    if hasattr(act, 'left') and hasattr(act, 'top'):          # klayout Box
        act = [act.left, act.bottom, act.right, act.top]
    try:
        act = list(act)
    except TypeError:
        return None, f'actual is not a box ({type(act).__name__})'
    if len(act) != 4 or len(exp) != 4:
        return None, f'box needs 4 values, got expected {len(exp)}, actual {len(act)}'
    diffs = [abs(float(a) - float(e)) for a, e in zip(act, exp)]
    ok = all(d <= float(tol) for d in diffs)
    return ok, f'expected {list(exp)} ± {tol}, actual {list(act)}, max diff {max(diffs):g}'


def as_set(exp, act, **opts):
    a = sorted(map(repr, act)) if hasattr(act, '__iter__') and not isinstance(act, dict) else None
    if a is None:
        return None, f'actual is not iterable ({type(act).__name__})'
    e = sorted(map(repr, exp))
    return e == a, f'expected set {e}, actual {a}'


def count(exp, act, **opts):
    try:
        n = len(act)
    except TypeError:
        n = getattr(act, 'count', lambda: None)()
        if n is None:
            return None, f'cannot count {type(act).__name__}'
    return n == exp, f'expected {exp} elements, actual {n}'


def reject(exp, act, **opts):
    """Only meaningful when the production call raised; the runner passes act={'raised': '...'}."""
    if not isinstance(act, dict) or 'raised' not in act:
        return False, 'expected the production function to raise, it returned normally'
    want = opts.get('exception')
    got = act['raised']
    if want and not got.startswith(want):
        return False, f'expected {want}, actual exception {got}'
    return True, f'raised as expected: {got}'


def eq_other(exp, act, tol=0, ref_value=None, **opts):
    """act 必须等于同一次提交中另一条 actual 的实际值（ref_value 由框架传入）。"""
    if ref_value is _MISSING:
        return None, '引用的 actual 尚未解析'
    ok = (act == ref_value) if tol in (0, None) else _num_eq(ref_value, act, tol)
    return ok, f'ref actual {ref_value!r}, this actual {act!r}'


_MISSING = object()
COMPARATORS = {'eq': eq, 'num': num, 'bbox': bbox, 'set': as_set, 'count': count,
               'reject': reject, 'eq_other': eq_other}


def compare(actual, expected, ref_value=None):
    """actual/expected: spec.actual 与 spec.expected 的配对条目。返回单条判定。"""
    how = expected.get('how', 'eq')
    fn = COMPARATORS.get(how)
    if fn is None:
        return {'ok': None, 'how': how, 'detail': f'unknown comparison {how!r}'}
    opts = {k: v for k, v in expected.items() if k not in ('how', 'value', 'expect_raises')}
    if how == 'eq_other':
        opts['ref_value'] = ref_value
    ok, detail = fn(expected.get('value'), actual, **opts)
    return {'ok': ok, 'how': how, 'detail': detail}


def verdict(rows):
    """rows: [{'ok': True/False/None, ...}] -> 'PASS' | 'FAIL' | 'INCONCLUSIVE'"""
    if any(r['ok'] is False for r in rows):
        return 'FAIL'
    if any(r['ok'] is None for r in rows):
        return 'INCONCLUSIVE'
    return 'PASS'
