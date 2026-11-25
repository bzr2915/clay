"""verify 提交：对输入版图运行工程局部检查器。"""
from clay.learning import contact_primitives as contacts
from clay.learning.circuit import parse_cdl, PlacementContract
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    layout = fixtures.load_gds(p['layout_gds'])
    circuit = ctx.call(parse_cdl, p['netlist'])
    contract = PlacementContract(**p['contract'])
    result = ctx.call(contacts.verify, layout, circuit, contract, p['plans'])
    return {'passed': result['passed'], 'failed': result['failed'], 'checks': result['checks']}
