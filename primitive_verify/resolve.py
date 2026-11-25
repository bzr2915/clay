"""取值解析：把 spec.json 中 actual 条目的 from 路径，解析为工程返回值上的真实值。

这是框架**唯一**读取实际输出的入口。语法（故意做得极简，避免变成一门语言）：

    name                     对象属性 / dict 键 / 下标（整数或引号字符串）
    name.attr                属性或 dict['attr']
    name[0]                  下标
    name['key']              引号键（也接受 "key"）
    name.attr[2].bbox        混用
    len(name)                仅允许 len(...)：取元素个数（列表/集合/Region 均可）

不允许函数调用（除 len）、算术、切片、比较、条件。解析失败即判 INCONCLUSIVE，
不会回退到"猜一个值"。
"""
import re

_TOKEN = re.compile(r"""\s*(\[\s*(?:'([^']*)'|"([^"]*)"|(-?\d+)|([0-9]+/[0-9]+))\s*\]|\.([A-Za-z_]\w*)|([A-Za-z_]\w*))""")


class ResolveError(Exception):
    pass


def _len_of(value):
    for attr in ('__len__',):
        if hasattr(value, attr):
            try:
                return len(value)
            except TypeError:
                break
    if hasattr(value, 'count'):          # klayout Region / 列表风格对象
        return value.count()
    raise ResolveError('cannot take length')


def check_syntax(path):
    """Validate a path expression without touching any object. Raises ResolveError."""
    path = path.strip()
    if path.startswith('len(') and path.endswith(')'):
        return check_syntax(path[4:-1])
    pos = 0
    if not path:
        raise ResolveError('empty path')
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m:
            raise ResolveError(f'bad path syntax at {path[pos:]!r}')
        pos = m.end()


def resolve(root, path):
    """Resolve a path expression against `root`. Raises ResolveError on any miss."""
    path = path.strip()
    if path.startswith('len(') and path.endswith(')'):
        return _len_of(resolve(root, path[4:-1]))
    pos, value, seen = 0, root, False
    while pos < len(path):
        m = _TOKEN.match(path, pos)
        if not m:
            raise ResolveError(f'bad path syntax at {path[pos:]!r}')
        pos = m.end()
        seen = True
        key, quoted, dquoted, index, layer, attr, first = m.groups()
        if attr is not None or first is not None:
            k = attr if attr is not None else first      # .attr 或首个标识符
        elif quoted is not None:
            k = quoted
        elif dquoted is not None:
            k = dquoted
        elif layer is not None:
            k = layer                                    # 图层键，如 [22/0]
        else:
            k = int(index)
        value = _step(value, k)
    if not seen:
        raise ResolveError('empty path')
    return value


def _step(value, k):
    if isinstance(value, dict):
        if k not in value:
            raise ResolveError(f'key {k!r} not in result')
        return value[k]
    if isinstance(k, int) and not isinstance(value, (str, bytes)):
        try:
            return value[k]
        except (TypeError, IndexError, KeyError) as e:
            raise ResolveError(f'index {k} failed: {e}') from e
    if hasattr(type(value), '__getitem__') and not hasattr(value, k):
        try:
            return value[k]
        except (KeyError, IndexError, TypeError) as e:
            raise ResolveError(f'item {k!r} missing on {type(value).__name__}') from e
    try:
        return getattr(value, k)
    except AttributeError as e:
        raise ResolveError(f'attribute {k!r} missing on {type(value).__name__}') from e
