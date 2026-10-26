"""Validador UTF-8 por AFD, diseñado para streaming por bloques.

El estado que persiste entre bloques es deliberadamente pequeño:
``remaining`` (cuántos bytes de continuación faltan) y ``lo`` / ``hi``
(el rango permitido para el *primer* byte de continuación). Ese rango es
lo que rechaza, por construcción, las secuencias sobrelargas, los
sustitutos UTF-16 (U+D800..U+DFFF) y los puntos de código > U+10FFFF:

- Lead E0 -> primer byte de continuación en A0..BF (no sobrelarga).
- Lead ED -> primer byte de continuación en 80..9F (no sustitutos).
- Lead F0 -> primer byte de continuación en 90..BF (no sobrelarga).
- Lead F4 -> primer byte de continuación en 80..8F (no > U+10FFFF).
- Otros leads multibyte -> 80..BF.

Un byte de continuación partido entre dos ``feed`` no es un error: el
validador se queda esperando el resto. Solo ``finish()`` convierte una
secuencia incompleta en error.
"""

from __future__ import annotations


class Utf8Error(ValueError):
    """UTF-8 inválido, con el offset en bytes donde se detectó."""

    def __init__(self, message: str, offset: int):
        super().__init__(f"{message} (byte {offset})")
        self.offset = offset


class Utf8Validator:
    """AFD de validación UTF-8 con estado persistente entre bloques."""

    def __init__(self) -> None:
        self.remaining = 0  # bytes de continuación que faltan
        self.lo = 0x80
        self.hi = 0xBF
        self.offset = 0  # bytes consumidos en total

    def feed(self, data: bytes) -> None:
        """Valida ``data``. No emite nada: solo avanza el AFD."""
        for b in data:
            if self.remaining == 0:
                if b < 0x80:
                    pass  # ASCII
                elif 0xC2 <= b <= 0xDF:
                    self.remaining, self.lo, self.hi = 1, 0x80, 0xBF
                elif b == 0xE0:
                    self.remaining, self.lo, self.hi = 2, 0xA0, 0xBF
                elif 0xE1 <= b <= 0xEC:
                    self.remaining, self.lo, self.hi = 2, 0x80, 0xBF
                elif b == 0xED:
                    self.remaining, self.lo, self.hi = 2, 0x80, 0x9F
                elif 0xEE <= b <= 0xEF:
                    self.remaining, self.lo, self.hi = 2, 0x80, 0xBF
                elif b == 0xF0:
                    self.remaining, self.lo, self.hi = 3, 0x90, 0xBF
                elif 0xF1 <= b <= 0xF3:
                    self.remaining, self.lo, self.hi = 3, 0x80, 0xBF
                elif b == 0xF4:
                    self.remaining, self.lo, self.hi = 3, 0x80, 0x8F
                else:
                    # C0, C1 (sobrelargas), F5..FF y bytes de continuación
                    # sueltos fuera de secuencia.
                    raise Utf8Error("byte UTF-8 inválido", self.offset)
            else:
                if not (self.lo <= b <= self.hi):
                    raise Utf8Error("byte de continuación UTF-8 inválido", self.offset)
                self.remaining -= 1
                self.lo, self.hi = 0x80, 0xBF
            self.offset += 1

    def finish(self) -> None:
        """Error si la entrada terminó en medio de una secuencia."""
        if self.remaining:
            raise Utf8Error("secuencia UTF-8 incompleta al final", self.offset)

    def reset(self) -> None:
        self.remaining = 0
        self.lo, self.hi = 0x80, 0xBF
        self.offset = 0
