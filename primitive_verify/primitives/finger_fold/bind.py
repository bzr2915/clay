"""finger_fold 提交：按目标指宽额外折叠。"""
from clay.learning import netlist
from clay.learning import diffusion_chains as dc
from clay.pdk.semantic import get_pack


def run(ctx, p):
    pack = get_pack(p['pdk'])
    c = netlist.parse_spice(p['text'], pack)
    f = ctx.call(dc.fold_devices, c, p['target_nm'], pack=pack)
    return {'f': ctx.raw(f)}
