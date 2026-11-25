"""netlist_flatten 提交：解析并展开一个两级层次 SPICE（sky130 pack）。"""
from clay.learning import netlist
from clay.pdk.semantic import get_pack


def run(ctx, p):
    c = ctx.call(netlist.parse_spice, p['text'], get_pack(p['pdk']))
    return {'c': ctx.raw(c)}
