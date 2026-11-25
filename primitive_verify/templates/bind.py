"""primitive_verify 提交模板：bind.py（AI 填装）

================================================================================
给 AI 的填装说明（原样阅读后照做，不要修改本说明）
--------------------------------------------------------------------------------
你要做的只有一件事：把下方 `run(ctx, p)` 补齐，让它调用工程函数并把结果交出来。

允许写的东西（bindcheck.py 会逐条检查，违反即判 FAIL、不会运行）：
  · import clay.* 中的函数（被测原语）；
  · import primitive_probe.fixtures（仅当需要构造输入形状，如矩形/输入版图）；
  · 赋值、函数调用、属性/下标访问、字面量、dict/list/tuple 字面量；
  · ctx.call(<工程函数>, ...)       调用被测工程函数（唯一合法的调用方式）
  · ctx.layout(<Layout 对象>)        登记要写出 GDS 的版图（框架负责写文件并读回）
  · ctx.raw(<任意值>)                登记要参与比对的原始返回值
  · fixtures.row / fixtures.region / fixtures.load_gds / fixtures.Extent  构造输入

禁止写的东西：
  算术（+ - * / // % **）、比较、布尔运算、if/for/while/try/with、推导式、lambda、
  f-string、切片、* 展开，以及 round/max/min/sum/len/int/str 等内建调用。
  如果觉得"需要算一下"，说明该量应当写进 spec.json 的 expected，由框架比对，
  而不是在 bind.py 里算出来。

硬性规则：
  1. 参数一律从 p 取（p 就是 spec.json 的 inputs），不得在代码里写死数值。
  2. 被返回给框架的每个值，必须是工程函数返回值上**原有的**属性/元素，
     不得再加工、换算、拼接。
  3. 不得读取 spec.json，不得出现任何"预期"字样的处理逻辑。
================================================================================
"""
# ↑ 上面的说明是注释，AI 保留即可。下面是需要补齐的骨架。

from clay.learning.contact_primitives import contact_plan   # ← 改成要测的原语函数（可 import 多个）
import primitive_probe.fixtures as fixtures


def run(ctx, p):
    # 1) 输入组装：从 p 取参数，交给工程函数。只允许这样调用：
    plan = ctx.call(contact_plan, p['library'], p['kind'], p['w_nm'])
    # 2) 其余被测函数同样经 ctx.call 调用
    # 3) 版图交给 ctx.layout；数据交给 ctx.raw。键名要与 spec.json 的 actual[].from 一致。
    return {
        'plan': ctx.raw(plan),
        'plan.count': ctx.raw(plan['count']),
        # 'layout': ctx.layout(layout),
    }
