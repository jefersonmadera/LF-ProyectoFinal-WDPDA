"""Pruebas Semana 1 — Fase 1 (análisis léxico).

Regla de oro de la Semana 1: el resultado debe ser idéntico
tokenizando la entrada completa, en bloques de 1 byte y en bloques
de 7 bytes, incluso cuando un escape ``\\u``, un carácter UTF-8
multibyte o un número queda partido entre dos bloques.
"""

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler import JsonTokenizer, LexerError, XmlTokenizer  # noqa: E402

VALID = (
    b'{"nombre": "Jeferson", "edad": 21, "activo": true, '
    b'"nada": null, "xs": [1, -2.5e-10, 1e400, 0, -0]}',
    b'"\\u0041\\uD83D\\uDE00"',  # A + 😀 por pares sustitutos
    '"ñ😀caf\u00e9"'.encode("utf-8"),
    b'[true, false, null, "a\\/b\\n\\t"]',
)

MALFORMED_JSON = [
    b"01",                    # cero inicial seguido de dígito
    b"1.", b"1e", b"1e+", b"-", b".5",
    b'"\\x"',                 # escape inválido
    b'"\\u12"',               # escape \u incompleto
    b'"\\uD800"',             # sustituto alto sin pareja
    b'"\\uDC00"',             # sustituto bajo suelto
    b'"abc',                  # cadena sin cerrar
    b'"a\x01b"',              # control sin escapar
    b'"\xc0\xaf"',            # UTF-8 sobrelargo
    b'"\xed\xa0\x80"',        # sustituto codificado en UTF-8
    b'"\xf4\x90\x80\x80"',    # punto de código > U+10FFFF
    b"tru", b"truex", b"nul",
    b"{'a': 1}",              # comillas simples no son JSON
    b"\xff",                  # byte inválido fuera de cadena
]

XML = b'<libro autor="x" pag="10"><titulo>Hola</titulo><vacio/><!-- nota --></libro>'


def chunked(tokenizer_cls, data, chunk):
    tok = tokenizer_cls()
    out = []
    for i in range(0, len(data), chunk):
        out += tok.feed(data[i:i + chunk])
    out += tok.finish()
    return out


class TestJsonLexer(unittest.TestCase):
    def test_valid_token_kinds(self):
        toks = JsonTokenizer().tokenize(VALID[0])
        kinds = [t.kind for t in toks]
        self.assertEqual(kinds[0], "{")
        self.assertIn("STRING", kinds)
        self.assertIn("NUMBER", kinds)
        self.assertIn("TRUE", kinds)
        self.assertIn("NULL", kinds)
        # Los números se guardan como lexema, nunca como float.
        numbers = [t.value for t in toks if t.kind == "NUMBER"]
        self.assertIn("1e400", numbers)

    def test_string_escapes_and_utf8(self):
        self.assertEqual(JsonTokenizer().tokenize(VALID[1])[0].value, "A\U0001F600")
        self.assertEqual(JsonTokenizer().tokenize(VALID[2])[0].value, "ñ😀café")

    def test_chunk_equivalence(self):
        for data in VALID:
            whole = JsonTokenizer().tokenize(data)
            self.assertEqual(chunked(JsonTokenizer, data, 1), whole,
                             f"bloques de 1 byte difieren en {data!r}")
            self.assertEqual(chunked(JsonTokenizer, data, 7), whole,
                             f"bloques de 7 bytes difieren en {data!r}")

    def test_chunk_split_inside_escape_and_utf8(self):
        # Escape \u partido, y ñ (2 bytes) / 😀 (4 bytes) partidos.
        for data in (b'"\\uD83D\\uDE00"', '"a😀bñ"'.encode("utf-8")):
            self.assertEqual(chunked(JsonTokenizer, data, 1),
                             JsonTokenizer().tokenize(data))

    def test_malformed(self):
        for bad in MALFORMED_JSON:
            with self.subTest(bad=bad):
                with self.assertRaises((LexerError, ValueError)):
                    JsonTokenizer().tokenize(bad)

    def test_offsets(self):
        toks = JsonTokenizer().tokenize(b'{"a": 12}')
        by_kind = {t.kind: t for t in toks}
        self.assertEqual(by_kind["STRING"].offset, 1)
        self.assertEqual(by_kind["NUMBER"].offset, 6)
        self.assertEqual(by_kind["NUMBER"].value, "12")


class TestXmlLexer(unittest.TestCase):
    def test_tags_and_text(self):
        toks = XmlTokenizer().tokenize(XML)
        kinds = [t.kind for t in toks]
        self.assertEqual(kinds[0], "TAG_OPEN")
        self.assertIn("TAG_CLOSE", kinds)
        self.assertIn("TAG_EMPTY", kinds)
        self.assertIn("COMMENT", kinds)
        self.assertEqual(toks[0].value, "libro")

    def test_chunk_equivalence(self):
        whole = XmlTokenizer().tokenize(XML)
        self.assertEqual(chunked(XmlTokenizer, XML, 1), whole)
        self.assertEqual(chunked(XmlTokenizer, XML, 7), whole)

    def test_doctype_rejected(self):
        with self.assertRaises((LexerError, ValueError)):
            XmlTokenizer().tokenize(b"<!DOCTYPE html><a/>")

    def test_unclosed_tag_rejected(self):
        with self.assertRaises((LexerError, ValueError)):
            XmlTokenizer().tokenize(b"<a")

    def test_text_split(self):
        data = "<a>hola mundo</a>".encode()
        self.assertEqual(chunked(XmlTokenizer, data, 1),
                         XmlTokenizer().tokenize(data))


class TestCRuntime(unittest.TestCase):
    """Compila el esqueleto C y comprueba equivalencia por bloques."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path("/tmp/wdpda_test_cli")
        root = Path(__file__).resolve().parent.parent / "runtime" / "c"
        subprocess.run(
            ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
             "-o", str(cls.tmp), str(root / "wdpda.c"), str(root / "main.c")],
            check=True)

    def run_cli(self, data: bytes, chunk: int, max_depth: int = 1024):
        path = Path("/tmp/wdpda_test_input.json")
        path.write_bytes(data)
        proc = subprocess.run(
            [str(self.tmp), str(path), str(chunk), str(max_depth)],
            capture_output=True, text=True)
        return proc.returncode, proc.stdout.strip()

    def test_c_chunk_equivalence(self):
        data = b'{"a": [1, 2, {"b": "x"}], "c": true}'
        for chunk in (1, 7, 4096):
            code, out = self.run_cli(data, chunk)
            self.assertEqual(code, 0, out)
            self.assertTrue(out.startswith("OK"), out)

    def test_c_errors(self):
        for data in (b'{"a": [1, 2}', b'{"a": "abc}', b'{"a": "x\xc0\xaf"}'):
            code, out = self.run_cli(data, 1)
            self.assertNotEqual(code, 0, out)
        code, out = self.run_cli(b"[" * 600 + b"]" * 600, 4096, 512)
        self.assertIn("DEPTH", out)
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
