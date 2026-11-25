"""spec.json 的字段白名单与结构校验（AI 提交物的第二道检查）。

只允许这些顶层字段：primitive / inputs / actual / expected / derivation / notes。
不允许任何"判定开关"字段（skip、always_pass、ignore、tolerance_override 等）。
actual 与 expected 必须等长且按顺序一一对应。
"""
import json
from pathlib import Path

TOP_KEYS = {'primitive', 'inputs', 'actual', 'expected', 'derivation', 'notes'}
ACTUAL_KEYS = {'name', 'from'}
EXPECTED_KEYS = {'name', 'how', 'value', 'tol', 'exception', 'ref'}
FORBIDDEN_SUBSTRINGS = ('skip', 'always_pass', 'alwayspass', 'ignore', 'override', 'bypass',
                        'manual_pass', 'auto_pass', 'todo')
HOW_VALUES = {'eq', 'num', 'bbox', 'set', 'count', 'reject', 'eq_other'}


def validate(spec, registered_name):
    problems = []
    if not isinstance(spec, dict):
        return ['spec.json 顶层必须是对象']
    extra = set(spec) - TOP_KEYS
    if extra:
        problems.append(f'spec.json 含不允许的字段：{sorted(extra)}')
    for key in ('primitive', 'inputs', 'actual', 'expected', 'derivation'):
        if key not in spec:
            problems.append(f'spec.json 缺少必填字段 {key}')
    if problems:
        return problems
    if spec['primitive'] != registered_name:
        problems.append(f"primitive 字段 {spec['primitive']!r} 与登记名 {registered_name!r} 不一致")
    text = json.dumps(spec, ensure_ascii=False).lower()
    for bad in FORBIDDEN_SUBSTRINGS:
        if bad in text:
            problems.append(f'spec.json 出现禁用词 {bad!r}（不得包含跳过/放行类开关）')
    actual, expected = spec['actual'], spec['expected']
    if not isinstance(actual, list) or not actual:
        problems.append('actual 必须是非空列表')
        return problems
    if not isinstance(expected, list) or len(expected) != len(actual):
        problems.append('expected 必须与 actual 等长（一一对应）')
        return problems
    for i, a in enumerate(actual, 1):
        if set(a) - ACTUAL_KEYS or 'name' not in a or 'from' not in a:
            problems.append(f'actual[{i}] 字段只能是 {sorted(ACTUAL_KEYS)} 且必须含 name/from')
    for i, e in enumerate(expected, 1):
        if set(e) - EXPECTED_KEYS:
            problems.append(f'expected[{i}] 含不允许的字段：{sorted(set(e) - EXPECTED_KEYS)}')
        if e.get('how') == 'eq_other' and not e.get('ref'):
            problems.append(f'expected[{i}] eq_other 需要 ref（被引用的 actual 名称）')
        if e.get('how') not in HOW_VALUES:
            problems.append(f'expected[{i}].how 必须是 {sorted(HOW_VALUES)} 之一')
        if 'value' not in e and e.get('how') not in ('reject', 'eq_other'):
            problems.append(f'expected[{i}] 缺少 value')
        if a_name := actual[i - 1].get('name'):
            if e.get('name') and e['name'] != a_name:
                problems.append(f'expected[{i}].name {e["name"]!r} 与 actual[{i}].name {a_name!r} 不一致')
    if not str(spec.get('derivation', '')).strip():
        problems.append('derivation 必须写明预期依据（PDK 规则编号 / 公式 / 设计意图）')
    return problems


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))
