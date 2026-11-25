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
