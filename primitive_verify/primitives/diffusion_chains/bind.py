"""diffusion_chains 提交：同网才共享扩散的最少链分解（无对称）。"""
from clay.learning import netlist
from clay.learning import diffusion_chains as dc
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    c = netlist.parse_spice(p['text'], pack)
    f = dc.fold_devices(c, None, pack=pack)
    ch = ctx.call(dc.diffusion_chains, f, None)
    return {'c0': ctx.raw(ch[0].as_dict()), 'c1': ctx.raw(ch[1].as_dict()),
            'c2': ctx.raw(ch[2].as_dict()), 'r': ctx.raw(dc.chains_report(ch))}
