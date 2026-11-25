"""contact_policy_plan 提交：学习到的接触孔策略在两个宽度上的规划（含容量截断）。"""
from clay.learning import contact_primitives as cp
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    wide = ctx.call(cp.contact_plan_from_policy, p['policy'], p['wide_nm'], pack=pack)
    narrow = ctx.call(cp.contact_plan_from_policy, p['policy'], p['narrow_nm'], pack=pack)
    return {'wide': ctx.raw(wide), 'narrow': ctx.raw(narrow)}
