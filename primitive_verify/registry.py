"""原语登记表：每个要做单原语验证的原语在这里登记。

一条记录描述：原语名 -> 被测工程函数（完整导入路径）、输出形态、来源与复核状态。
输出形态决定框架如何读回真实输出：
  'layout'  工程函数返回 klayout Layout（或可作为 Layout 写出）→ 框架直接写 GDS
  'data'    工程函数返回 dict/对象/数字 → 按 spec.json 中的路径取字面值
  'coords'  工程函数返回坐标集合（如放置器返回 extents）→ 同样按路径取值

修改规则（2026-11-15 起，见 README_zh.md §5）
---------------------------------------------
* AI（LLM 学到新原语后）可以**追加**条目，必须写 provenance='ai'、status='pending_review'。
* 任何人都不得修改或删除 status='frozen' 的条目；frozen_entries.json 记录了它们的内容，
  测试 tests/test_primitive_classification.py 会比对，改动即失败。
* 只有人可以把条目改为 'frozen'，并同时把它写入 frozen_entries.json（即"批准"）。
* 'pending_review' 的条目照常运行验证，但结果不计入冻结结果（报告中注明"待复核"）。
"""
from dataclasses import dataclass, field

PROVENANCES = ('human', 'ai')      # 谁登记了这个条目
STATUSES = ('frozen', 'pending_review')   # 条目与其 spec 是否已由人复核


@dataclass(frozen=True)
class Primitive:
    name: str
    targets: tuple          # 被测工程函数的 "模块:函数名" 列表，全部必须被执行
    form: str               # 'layout' | 'data' | 'coords'
    flow: str = ''          # 可选：该原语所属完整流程的 trace 路径（用于 provenance 比对）
    notes: str = ''
    provenance: str = 'human'
    status: str = 'frozen'

    def __post_init__(self):
        if self.provenance not in PROVENANCES:
            raise ValueError(f'{self.name}: provenance 只能是 {PROVENANCES}')
        if self.status not in STATUSES:
            raise ValueError(f'{self.name}: status 只能是 {STATUSES}')


# 比较器学习（2026-11-11）中由 AI 按用户要求登记的条目；spec 未经人工复核。
_AI = dict(provenance='ai', status='pending_review')
# 人登记的条目，但验证提交（bind.py / spec.json）由 AI 补齐，spec 未经人工复核。
_SPEC_PENDING = dict(provenance='human', status='pending_review')

REGISTRY = {p.name: p for p in [
    Primitive('inverter_topology', ('clay.learning.circuit:parse_cdl',
                                    'clay.learning.contact_primitives:inverter_groups'), 'data'),
    Primitive('contact_plan', ('clay.learning.contact_primitives:contact_plan',
                               'clay.learning.contact_primitives:build_row',
                               'clay.learning.contact_primitives:materialize'), 'layout'),
    Primitive('geometry_plan', ('clay.learning.geometry_primitives:geometry_plan',
                                'clay.learning.contact_primitives:contact_plan',
                                'clay.learning.contact_primitives:build_row',
                                'clay.learning.contact_primitives:materialize'), 'layout'),
    Primitive('build_row', ('clay.learning.contact_primitives:build_row',
                            'clay.learning.contact_primitives:materialize'), 'layout'),
    Primitive('build_contacted_device', ('clay.pdk.device_placement:build_contacted_device',
                                  'clay.learning.generate:materialize'), 'layout', **_SPEC_PENDING),
    Primitive('turn', ('clay.learning.contact_primitives:turn',
                       'clay.learning.contact_primitives:materialize'), 'layout'),
    Primitive('materialize', ('clay.learning.contact_primitives:materialize',), 'layout'),
    Primitive('solve_area', ('clay.layout.synth.area_placement:solve_area',
                             'clay.learning.contact_primitives:materialize'), 'layout'),
    Primitive('symmetry_placement',
              ('clay.layout.annotate_to_solver:solve_placement_from_constraints',), 'coords'),
    Primitive('orthogonal_path', ('clay.learning.routing_primitives:orthogonal_path',
                                  'clay.learning.routing_primitives:segment',
                                  'clay.learning.contact_primitives:materialize'), 'layout',
              **_SPEC_PENDING),
    Primitive('relation_features', ('clay.learning.placement_relations:features',), 'data'),
    Primitive('relation_rank', ('clay.learning.placement_relations:rank',), 'data'),
    Primitive('candidate_verify', ('clay.learning.contact_primitives:verify',), 'data'),
    # ---- circuit-general primitives (comparator learning, 2026-11-11) ----
    Primitive('netlist_flatten', ('clay.learning.netlist:parse_spice',), 'data', **_AI),
    Primitive('netlist_symmetry', ('clay.learning.symmetry:netlist_symmetry',), 'data', **_AI),
    Primitive('finger_fold', ('clay.learning.diffusion_chains:fold_devices',), 'data', **_AI),
    Primitive('diffusion_chains', ('clay.learning.diffusion_chains:diffusion_chains',), 'data',
              **_AI),
    Primitive('contact_policy_plan',
              ('clay.learning.contact_primitives:contact_plan_from_policy',), 'data', **_AI),
    Primitive('build_chain', ('clay.learning.contact_primitives:build_row',
                              'clay.learning.contact_primitives:materialize'), 'layout', **_AI),
    Primitive('build_varactor', ('clay.learning.contact_primitives:build_row',
                                 'clay.learning.contact_primitives:materialize'), 'layout', **_AI),
    Primitive('build_poly_resistor', ('clay.learning.passive_primitives:build_poly_resistor',
                                      'clay.learning.contact_primitives:materialize'), 'layout',
              **_AI),
    Primitive('build_mim_cap', ('clay.learning.passive_primitives:build_mim_cap',
                                'clay.learning.contact_primitives:materialize'), 'layout', **_AI),
    Primitive('reflect', ('clay.learning.contact_primitives:reflect',
                          'clay.learning.contact_primitives:materialize'), 'layout', **_AI),
    Primitive('materialize_close', ('clay.learning.contact_primitives:materialize',), 'layout',
              **_AI),
    Primitive('block_from_row', ('clay.learning.passive_primitives:block_from_row',), 'data', **_AI),
    Primitive('solve_blocks', ('clay.layout.synth.area_placement:solve_blocks',), 'coords', **_AI),
    Primitive('circuit_verify', ('clay.learning.circuit_verify:verify_circuit_layout',), 'data',
              **_AI),
    Primitive('observe_layout', ('clay.learning.circuit_library:observe_layout',), 'data', **_AI),
    Primitive('fit_library', ('clay.learning.circuit_library:fit_library',), 'data', **_AI),
]}


def lookup(name):
    if name not in REGISTRY:
        raise KeyError(f'未登记的原语 {name!r}。新增原语请先在 primitive_verify/registry.py 追加'
                       f'条目（AI 追加时写 provenance=\'ai\'、status=\'pending_review\'）。')
    return REGISTRY[name]


def import_target(spec):
    """'pkg.mod:qualname' -> 函数对象。只允许 clay.* 模块。"""
    import importlib
    module, _, qual = spec.partition(':')
    if not module.startswith('clay.'):
        raise PermissionError(f'{spec} 不是工程代码（必须在 clay.* 下）')
    obj = importlib.import_module(module)
    for part in qual.split('.'):
        obj = getattr(obj, part)
    return obj
