"""netlist_symmetry 提交：差分对 + 尾管的对合自同构。"""
from clay.learning import netlist, symmetry
from clay.pdk.semantic import get_pack


def run(ctx, p):
    c = netlist.parse_spice(p['text'], get_pack(p['pdk']))
    s = ctx.call(symmetry.netlist_symmetry, c)
    return {'s': ctx.raw(s.as_dict())}
