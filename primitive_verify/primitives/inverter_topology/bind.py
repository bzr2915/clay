"""inverter_groups 提交：识别网表中的器件分工（只调用工程函数）。"""
from clay.learning import contact_primitives as contacts
from clay.learning.circuit import parse_cdl, PlacementContract, Mos


def run(ctx, p):
    circuit = ctx.call(parse_cdl, p['netlist'])
    contract = PlacementContract(**p['contract'])
    groups, gate, output = ctx.call(contacts.inverter_groups, circuit, contract)
    return {'groups': groups, 'gate_net': gate, 'output_net': output}
