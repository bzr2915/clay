"""observe_layout 提交：对一个已知结构的输入 GDS 做训练观测。"""
from clay.learning import circuit_library as cl
from clay.learning import netlist
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    c = netlist.parse_spice(p['netlist'], pack)
    o = ctx.call(cl.observe_layout, p['sample'], p['gds'], c, pack)
    return {'o': ctx.raw(o)}
