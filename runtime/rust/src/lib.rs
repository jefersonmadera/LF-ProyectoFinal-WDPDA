//! Esqueleto del runtime Rust — Semana 1.
//!
//! Espejo del runtime C (`runtime/c/wdpda.c`) con la misma API
//! conceptual: `init` / `feed` / `finish`, pila fija, sin asignación
//! dinámica en el código del runtime (la pila es un arreglo fijo
//! dentro del contexto; las pruebas sí pueden usar `Vec` porque no
//! son código del runtime).
//!
//! Limitaciones de Semana 1 (las mismas que en C, para que el
//! informe no afirme de más): solo equilibrio de llaves/corchetes,
//! cadenas y escapes (incluido `\u` con 4 hexadecimales), validación
//! UTF-8 y límite de profundidad. NO valida números, literales ni la
//! estructura completa de JSON, ni pares sustitutos `\u`, ni aplica
//! un esquema. Eso llega con el parser y la integración del WDPDA.

pub const MAX_DEPTH: usize = 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok,
    Depth,
    Mismatch,
    Unbalanced,
    String,
    Utf8,
    Control,
    Byte,
}

#[derive(Debug, Clone, Copy)]
pub struct Context {
    stack: [u8; MAX_DEPTH],
    depth: usize,
    max_depth: usize,
    pub max_depth_seen: usize,
    in_string: bool,
    escape: bool,
    unicode_left: usize,
    utf8_left: usize,
    utf8_lo: u8,
    utf8_hi: u8,
    pub offset: u64,
    pub error_offset: u64,
    pub status: Status,
}

impl Context {
    pub fn init(max_depth: usize) -> Self {
        let max_depth = max_depth.clamp(1, MAX_DEPTH);
        Context {
            stack: [0; MAX_DEPTH],
            depth: 0,
            max_depth,
            max_depth_seen: 0,
            in_string: false,
            escape: false,
            unicode_left: 0,
            utf8_left: 0,
            utf8_lo: 0x80,
            utf8_hi: 0xBF,
            offset: 0,
            error_offset: 0,
            status: Status::Ok,
        }
    }

    fn fail(&mut self, status: Status) -> Status {
        self.status = status;
        self.error_offset = self.offset;
        status
    }

    fn step_structural(&mut self, b: u8) -> Result<(), Status> {
        if self.in_string {
            if self.unicode_left > 0 {
                if !b.is_ascii_hexdigit() {
                    return Err(self.fail(Status::String));
                }
                self.unicode_left -= 1;
                return Ok(());
            }
            if self.escape {
                self.escape = false;
                match b {
                    b'u' => {
                        self.unicode_left = 4;
                        return Ok(());
                    }
                    b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => return Ok(()),
                    _ => return Err(self.fail(Status::String)),
                }
            }
            match b {
                b'\\' => {
                    self.escape = true;
                    Ok(())
                }
                b'"' => {
                    self.in_string = false;
                    Ok(())
                }
                0x00..=0x1F => Err(self.fail(Status::Control)),
                _ => Ok(()),
            }
        } else {
            match b {
                b'"' => {
                    self.in_string = true;
                    Ok(())
                }
                b'{' | b'[' => {
                    if self.depth >= self.max_depth {
                        return Err(self.fail(Status::Depth));
                    }
                    self.stack[self.depth] = b;
                    self.depth += 1;
                    self.max_depth_seen = self.max_depth_seen.max(self.depth);
                    Ok(())
                }
                b'}' | b']' => {
                    let want = if b == b'}' { b'{' } else { b'[' };
                    if self.depth == 0 || self.stack[self.depth - 1] != want {
                        return Err(self.fail(Status::Mismatch));
                    }
                    self.depth -= 1;
                    Ok(())
                }
                0x09 | 0x0A | 0x0D | 0x20 => Ok(()),
                0x00..=0x1F => Err(self.fail(Status::Control)),
                _ => Ok(()),
            }
        }
    }

    pub fn feed(&mut self, data: &[u8]) -> Status {
        if self.status != Status::Ok {
            return self.status;
        }
        for &b in data {
            if self.utf8_left > 0 {
                if !(self.utf8_lo..=self.utf8_hi).contains(&b) {
                    return self.fail(Status::Utf8);
                }
                self.utf8_left -= 1;
                self.utf8_lo = 0x80;
                self.utf8_hi = 0xBF;
                self.offset += 1;
                continue;
            }
            if b < 0x80 {
                if let Err(s) = self.step_structural(b) {
                    return s;
                }
            } else if !self.in_string {
                return self.fail(Status::Byte);
            } else {
                let (left, lo, hi) = match b {
                    0xC2..=0xDF => (1, 0x80, 0xBF),
                    0xE0 => (2, 0xA0, 0xBF),
                    0xE1..=0xEC | 0xEE | 0xEF => (2, 0x80, 0xBF),
                    0xED => (2, 0x80, 0x9F),
                    0xF0 => (3, 0x90, 0xBF),
                    0xF1..=0xF3 => (3, 0x80, 0xBF),
                    0xF4 => (3, 0x80, 0x8F),
                    _ => return self.fail(Status::Utf8),
                };
                self.utf8_left = left;
                self.utf8_lo = lo;
                self.utf8_hi = hi;
            }
            self.offset += 1;
        }
        Status::Ok
    }

    pub fn finish(&mut self) -> Status {
        if self.status != Status::Ok {
            return self.status;
        }
        if self.utf8_left > 0 {
            return self.fail(Status::Utf8);
        }
        if self.in_string || self.escape || self.unicode_left > 0 {
            return self.fail(Status::String);
        }
        if self.depth != 0 {
            return self.fail(Status::Unbalanced);
        }
        Status::Ok
    }
}

/// Atajo: alimenta toda la entrada y luego `finish`.
pub fn validate(data: &[u8], max_depth: usize) -> Status {
    let mut ctx = Context::init(max_depth);
    let s = ctx.feed(data);
    if s != Status::Ok {
        return s;
    }
    ctx.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(data: &[u8], chunk: usize) -> Status {
        let mut ctx = Context::init(MAX_DEPTH);
        for part in data.chunks(chunk) {
            if ctx.feed(part) != Status::Ok {
                return ctx.status;
            }
        }
        ctx.finish()
    }

    #[test]
    fn chunk_equivalence() {
        // "ñ" = C3 B1, "😀" = F0 9F 98 80 (escapes \x, porque un
        // literal de bytes crudo no admite caracteres no ASCII).
        let data = b"{\"a\": [1, 2, {\"b\": \"\xC3\xB1\xF0\x9F\x98\x80\"}], \"c\": true}";
        assert_eq!(run(data, data.len()), Status::Ok);
        assert_eq!(run(data, 1), Status::Ok);
        assert_eq!(run(data, 7), Status::Ok);
    }

    #[test]
    fn mismatch_and_depth() {
        assert_eq!(run(br#"{"a": [1, 2}"#, 4096), Status::Mismatch);
        let deep: Vec<u8> = std::iter::repeat(b'[').take(600).collect();
        let mut ctx = Context::init(512);
        assert_eq!(ctx.feed(&deep), Status::Depth);
        assert_eq!(validate(br#"{"a": "x"#, MAX_DEPTH), Status::String);
    }
}
