"""build_contacted_device 提交：GF180 5 V 单指 NMOS，经 generate.materialize 出图。"""
from clay.learning import generate
from clay.learning.circuit import Mos
from clay.pdk import device_placement as dp


def run(ctx, p):
    dev = ctx.call(dp.build_contacted_device, Mos(**p['device']))
    layout, top, bindings = ctx.call(generate.materialize, [dev], p['origins'], None, None)
    return {'layout': ctx.layout(layout)}
