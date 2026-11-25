"""circuit_verify 提交：同一版图分别对正确网表与错栅网表做检查。"""
from clay.learning import circuit_verify as cv
from clay.learning import contact_primitives as cp
from clay.learning import netlist
from clay.learning.circuit import Mos
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    good = netlist.parse_spice(p['good'], pack)
    bad = netlist.parse_spice(p['bad'], pack)
    row = cp.build_row([Mos(**p['devices'][0]), Mos(**p['devices'][1])], None, p['plan'], None, None,
                       None, pack=pack, gate_nets=p['gate_nets'], segment_nets=p['segment_nets'],
                       kind='nmos')
    layout = cp.materialize({'r': row}, {'r': [0, 0]}, pack=pack, merge='close')
    ok = ctx.call(cv.verify_circuit_layout, layout, good, pack)
    ko = ctx.call(cv.verify_circuit_layout, layout, bad, pack)
    return {'ok': ctx.raw(ok), 'ko': ctx.raw(ko)}
