"""Rewrite AE expressions so array math works in plain JavaScript.

AE's expression engines evaluate ``[1, 2] + [3, 4]`` element-wise. Plain JS does
not, so every binary ``+ - * /`` (and compound assignment) is rewritten to a call
of ``__bin(op, a, b)``, which handles numbers, strings and arrays.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import esprima

_OPS = {"+", "-", "*", "/"}


def _children(node: Any):
    for key, val in vars(node).items():
        if key in ("range", "loc", "type"):
            continue
        if isinstance(val, list):
            for v in val:
                if hasattr(v, "type"):
                    yield v
        elif hasattr(val, "type"):
            yield val


def _rewrite(node: Any, src: str) -> str:
    t = node.type
    if t == "BinaryExpression" and node.operator in _OPS:
        return f"__bin({node.operator!r}, {_rewrite(node.left, src)}, {_rewrite(node.right, src)})"
    if t == "AssignmentExpression" and node.operator in ("+=", "-=", "*=", "/="):
        left = src[node.left.range[0]:node.left.range[1]]
        return f"{_rewrite(node.left, src)} = __bin({node.operator[0]!r}, {left}, {_rewrite(node.right, src)})"
    if t == "UnaryExpression" and node.operator == "-":
        return f"__neg({_rewrite(node.argument, src)})"
    start, end = node.range
    out = []
    pos = start
    for child in sorted(_children(node), key=lambda c: c.range[0]):
        cs, ce = child.range
        if cs < pos:
            continue
        out.append(src[pos:cs])
        out.append(_rewrite(child, src))
        pos = ce
    out.append(src[pos:end])
    return "".join(out)


@lru_cache(maxsize=512)
def transpile(src: str) -> str:
    try:
        tree = esprima.parseScript(src, range=True, tolerant=True)
    except Exception:
        return src
    return _rewrite(tree, src)


PRELUDE = r"""
function __bin(op, a, b) {
  if (a && a.__ref) a = a.toJSON();
  if (b && b.__ref) b = b.toJSON();
  var aa = Array.isArray(a), ba = Array.isArray(b);
  if (!aa && !ba) {
    if (op === '+') return a + b;
    if (op === '-') return a - b;
    if (op === '*') return a * b;
    return a / b;
  }
  if (aa && ba) {
    if (op === '+' || op === '-') {
      var n = Math.max(a.length, b.length), r = [];
      for (var i = 0; i < n; i++) { var x = a[i] || 0, y = b[i] || 0; r.push(op === '+' ? x + y : x - y); }
      return r;
    }
    if (op === '*') return a.map(function(x, i) { return x * (b[i] === undefined ? 1 : b[i]); });
    return a.map(function(x, i) { return x / (b[i] === undefined ? 1 : b[i]); });
  }
  if (aa) return a.map(function(x) { return op === '+' ? x + b : op === '-' ? x - b : op === '*' ? x * b : x / b; });
  return b.map(function(y) { return op === '+' ? a + y : op === '-' ? a - y : op === '*' ? a * y : a / y; });
}
function __neg(a) { if (a && a.__ref) a = a.toJSON(); return Array.isArray(a) ? a.map(function(x) { return -x; }) : -a; }
"""
