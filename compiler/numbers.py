"""Números JSON como decimales exactos, sin ``float``.

Un número se normaliza a ``(signo, dígitos, exponente)`` con valor
``(-1)^signo × int(dígitos) × 10^exponente``, sin ceros a la izquierda
ni a la derecha en ``dígitos`` (el cero es ``dígitos == ""``). Con esa
forma:

- comparar contra una constante (``minimum``, ``maximum``...) es
  comparar primero el signo, luego la magnitud ``len(dígitos)+exp`` y
  luego los dígitos como cadenas: exacto para ``1e400`` o enteros de
  40 dígitos, que un ``double`` no representa;
- "es entero" es ``exp >= 0`` (``1.0`` y ``1e3`` son enteros, como en
  JSON Schema);
- ``multipleOf`` se decide con aritmética modular sobre los dígitos.

El runtime C/Rust hace exactamente lo mismo en streaming, con un búfer
fijo de ``MAX_DIGITS`` dígitos significativos (registro acotado). Un
número con más dígitos significativos se sigue comparando bien (basta
saber si hay algún dígito distinto de cero después del búfer), pero
``multipleOf`` sobre él se reporta como límite excedido. Este módulo
implementa el mismo límite para que Python, C y Rust coincidan.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_DIGITS = 64
EXP_LIMIT = 10 ** 15  # el exponente se satura en ±EXP_LIMIT


@dataclass(frozen=True)
class Dec:
    neg: bool
    digits: str  # sin ceros iniciales ni finales; "" = cero
    exp: int
    overflow: bool = False  # hubo dígitos significativos > MAX_DIGITS

    def is_zero(self) -> bool:
        return self.digits == ""

    def is_integer(self) -> bool:
        return self.is_zero() or self.exp >= 0

    def __str__(self) -> str:
        if self.is_zero():
            return "0"
        s = ("-" if self.neg else "") + self.digits
        return s + (f"e{self.exp}" if self.exp else "")

    def to_float(self) -> float:
        try:
            return float(str(self))
        except OverflowError:
            return float("-inf") if self.neg else float("inf")


def parse_number(lexeme: str) -> Dec:
    """Normaliza un lexema JSON (ya validado por el AFD del lexer)."""
    s = lexeme
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    exp = 0
    if "e" in s or "E" in s:
        s, e = s.replace("E", "e").split("e", 1)
        exp = int(e)
    if "." in s:
        ip, fp = s.split(".", 1)
    else:
        ip, fp = s, ""
    digits = (ip + fp).lstrip("0")
    exp -= len(fp)
    stripped = digits.rstrip("0")
    exp += len(digits) - len(stripped)
    digits = stripped
    exp = max(-EXP_LIMIT, min(EXP_LIMIT, exp))
    if not digits:
        return Dec(False, "", 0)
    overflow = len(digits) > MAX_DIGITS
    return Dec(neg, digits, exp, overflow)


def _cmp_mag(a: Dec, b: Dec) -> int:
    """Compara |a| con |b| (ambos distintos de cero)."""
    ma, mb = len(a.digits) + a.exp, len(b.digits) + b.exp
    if ma != mb:
        return -1 if ma < mb else 1
    n = max(len(a.digits), len(b.digits))
    da, db = a.digits.ljust(n, "0"), b.digits.ljust(n, "0")
    return (da > db) - (da < db)


def compare(a: Dec, b: Dec) -> int:
    """-1, 0 o 1 según a < b, a == b, a > b (exacto)."""
    if a.is_zero() and b.is_zero():
        return 0
    if a.is_zero():
        return 1 if b.neg else -1
    if b.is_zero():
        return -1 if a.neg else 1
    if a.neg != b.neg:
        return -1 if a.neg else 1
    c = _cmp_mag(a, b)
    return -c if a.neg else c


def multiple_of(a: Dec, m: Dec) -> bool | None:
    """¿a es múltiplo de m? None si excede el registro acotado."""
    if a.is_zero():
        return True
    if a.overflow or len(m.digits) > 18:
        return None
    k = a.exp - m.exp
    if k < 0:
        # a.digits no termina en 0, así que a/m no puede ser entero.
        return False
    B = int(m.digits)
    r = 0
    for ch in a.digits:
        r = (r * 10 + (ord(ch) - 48)) % B
    return (r * pow(10, k, B)) % B == 0


def as_count(d: Dec) -> int | None:
    """Valor entero no negativo pequeño (para minLength, maxItems...)."""
    if d.neg and not d.is_zero():
        return None
    if not d.is_integer():
        return None
    if d.is_zero():
        return 0
    if len(d.digits) + d.exp > 9:
        return None
    return int(d.digits) * 10 ** d.exp
