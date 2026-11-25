"""fit_library 提交：由一条手写观测拟合电路级原语库。"""
from clay.learning import circuit_library as cl
from clay.pdk.semantic import get_pack


def run(ctx, p):
    lib = ctx.call(cl.fit_library, p['observations'], {'sky130_1v8': get_pack('sky130_1v8')})
    return {'lib': ctx.raw(lib)}
