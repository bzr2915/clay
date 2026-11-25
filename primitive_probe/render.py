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
