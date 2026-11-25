"""切断测试：确认测试结果确实依赖被测工程函数。

做法：运行期间把被测函数的 __code__ 临时换成"调用即报错"，再跑一遍 bind.run。
  · 若结果真来自工程函数 → 必然失败（异常链中含 SEVER_TOKEN）
  · 若绑定里另有一份计算/副本 → 仍会产出结果 → 判为"不依赖"
不修改任何文件，测试后恢复原函数并再跑一次确认恢复正常。
"""
import inspect

from primitive_probe.core import production_identity
from primitive_verify import registry

SEVER_TOKEN = 'PRIMITIVE_VERIFY_SEVERED'


def _severed(*args, **kwargs):
    # 以工程模块的全局命名空间执行，只能使用内建
    raise RuntimeError('PRIMITIVE_VERIFY_SEVERED')


def _depends_on(error):
    chain, cur = [], error
    while cur is not None:
        chain.append(str(cur))
        cur = cur.__cause__ or cur.__context__
    return any(SEVER_TOKEN in m for m in chain)


def check(name, fn, params, make_ctx):
    """返回 (rows, ok)。每行记录一个被测函数在切断后的表现。"""
    rows, ok = [], True
    for spec in registry.lookup(name).targets:
        target = registry.import_target(spec)
        unwrapped = inspect.unwrap(target)
        ident = production_identity(unwrapped)
        original = unwrapped.__code__
        unwrapped.__code__ = _severed.__code__
        try:
            fn(make_ctx(), params)
            outcome, depends = '切断后仍产出结果（结果不依赖该函数）', False
        except Exception as e:  # noqa: BLE001
            depends = _depends_on(e)
            outcome = '切断后失败：被切断的函数被调用' if depends else f'切断后因其它原因失败：{type(e).__name__}'
        finally:
            unwrapped.__code__ = original
        ok &= depends
        rows.append({'function': f"{ident['module']}.{ident['qualname']}",
                     'file': f"{ident['file']}:{ident['line']}", 'depends': depends, 'outcome': outcome})
    # 恢复后必须仍能正常运行
    fn(make_ctx(), params)
    return rows, ok
