//! Runtime del validador WDPDA en Rust (Fases 5 y 6).
//!
//! Port fiel de `runtime/c/wdpda_rt.c`: mismo transductor léxico (AFD),
//! mismo DPDA sintáctico dirigido por tabla, mismos carriles del producto
//! y mismos pesos tropicales. Las tablas del autómata las emite
//! `compiler/codegen_rust.py` como `static` (datos de solo lectura).
//!
//! Sin asignación dinámica por payload: todo el estado está en
//! [`Ctx`], que el llamador crea una vez (`Ctx::new_boxed`) y reutiliza.
//! No hay `Vec`, `String` ni `Box` en `feed`/`finish`.

// Es una API pública: el CLI no usa todo (abort, Size/Time, …).
#![allow(dead_code)]

use core::fmt::Write as _;

pub const MAX_DEPTH: usize = 256;
pub const MAX_LANES: usize = 64;
pub const LANE_POOL: usize = 4096;
pub const MAX_PATS: usize = 8;
pub const MAX_ERRORS: usize = 16;
pub const PATH_BYTES: usize = 1024;
pub const ERR_PATH: usize = 256;
pub const ERR_MSG: usize = 160;
pub const UNIQUE_CAP: usize = 4096;
pub const UNIQUE_POOL: usize = 65536;
pub const MAX_DIGITS: usize = 64;
const UQ_SLOTS: usize = 2 * UNIQUE_CAP;

pub const ANY: i32 = -1;
pub const FORBIDDEN: i32 = -2;
pub const NONE: i32 = -3;

pub const OP_GE: u8 = 0;
pub const OP_GT: u8 = 1;
pub const OP_LE: u8 = 2;
pub const OP_LT: u8 = 3;
pub const OP_MUL: u8 = 4;

// ---- Tablas emitidas por el compilador ----------------------------------
pub struct Dfa {
    pub classes: &'static [u8; 256],
    pub ncls: u32,
    pub table: &'static [i32],
    pub accept: &'static [i32],
}

pub struct Const {
    pub neg: bool,
    pub digits: &'static [u8],
    pub exp: i64,
    pub lexeme: &'static str,
}

pub struct Node {
    pub ptr: &'static str,
    pub is_false: bool,
    pub types: u8,
    pub has_enum: bool,
    pub enum_dfa: i32,
    pub enum_nums: &'static [u32],
    pub enum_lits: u8,
    pub has_str: bool,
    pub min_len: i32,
    pub max_len: i32,
    pub pats: &'static [u32],
    pub pat_src: &'static [&'static str],
    pub num_ops: &'static [u8],
    pub num_consts: &'static [u32],
    pub has_obj: bool,
    pub keys_dfa: i32,
    pub keys: &'static [&'static str],
    pub key_child: &'static [i32],
    pub key_declared: &'static [u8],
    pub required: u64,
    pub ppat_dfa: &'static [u32],
    pub ppat_child: &'static [i32],
    pub additional: i32,
    pub min_props: i32,
    pub max_props: i32,
    pub depreq_key: &'static [u8],
    pub depreq_mask: &'static [u64],
    pub depsch_key: &'static [u8],
    pub depsch_child: &'static [i32],
    pub has_arr: bool,
    pub prefix: &'static [i32],
    pub items: i32,
    pub contains: i32,
    pub min_contains: i32,
    pub max_contains: i32,
    pub min_items: i32,
    pub max_items: i32,
    pub unique: bool,
    pub all_of: &'static [i32],
    pub any_of: &'static [i32],
    pub one_of: &'static [i32],
    pub not_: i32,
    pub if_: i32,
    pub then_: i32,
    pub else_: i32,
    pub not_any: bool,
}

pub struct Schema {
    pub name: &'static str,
    pub nodes: &'static [Node],
    pub root: i32,
    pub dfas: &'static [Dfa],
    pub consts: &'static [Const],
    pub uses_unique: bool,
    pub formal_states: u32,
    pub formal_transitions: u32,
}

// ---- Resultado ------------------------------------------------------------
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(i8)]
pub enum Status {
    Running = -1,
    Valid = 0,
    Invalid,
    Malformed,
    Utf8,
    Depth,
    Limit,
    Size,
    Time,
}

impl Status {
    pub fn name(self) -> &'static str {
        match self {
            Status::Running => "RUNNING",
            Status::Valid => "VALID",
            Status::Invalid => "INVALID",
            Status::Malformed => "MALFORMED",
            Status::Utf8 => "UTF8",
            Status::Depth => "DEPTH",
            Status::Limit => "LIMIT",
            Status::Size => "SIZE",
            Status::Time => "TIME",
        }
    }
}

pub const CODE_NAMES: [&str; 28] = [
    "TYPE", "FALSE", "ENUM", "MIN_LENGTH", "MAX_LENGTH", "PATTERN", "MINIMUM",
    "EXCLUSIVE_MINIMUM", "MAXIMUM", "EXCLUSIVE_MAXIMUM", "MULTIPLE_OF",
    "REQUIRED", "ADDITIONAL", "DUPLICATE_KEY", "MIN_PROPERTIES",
    "MAX_PROPERTIES", "DEPENDENT_REQUIRED", "DEPENDENT_SCHEMAS", "ITEMS",
    "MIN_ITEMS", "MAX_ITEMS", "UNIQUE", "CONTAINS", "ANY_OF", "ONE_OF", "NOT",
    "IF_THEN", "IF_ELSE",
];
const WE_TYPE: u8 = 0;
const WE_FALSE: u8 = 1;
const WE_ENUM: u8 = 2;
const WE_MIN_LENGTH: u8 = 3;
const WE_MAX_LENGTH: u8 = 4;
const WE_PATTERN: u8 = 5;
const WE_MINIMUM: u8 = 6;
const WE_EXCLUSIVE_MINIMUM: u8 = 7;
const WE_MAXIMUM: u8 = 8;
const WE_EXCLUSIVE_MAXIMUM: u8 = 9;
const WE_MULTIPLE_OF: u8 = 10;
const WE_REQUIRED: u8 = 11;
const WE_ADDITIONAL: u8 = 12;
const WE_DUPLICATE_KEY: u8 = 13;
const WE_MIN_PROPERTIES: u8 = 14;
const WE_MAX_PROPERTIES: u8 = 15;
const WE_DEPENDENT_REQUIRED: u8 = 16;
const WE_DEPENDENT_SCHEMAS: u8 = 17;
const WE_ITEMS: u8 = 18;
const WE_MIN_ITEMS: u8 = 19;
const WE_MAX_ITEMS: u8 = 20;
const WE_UNIQUE: u8 = 21;
const WE_CONTAINS: u8 = 22;
const WE_ANY_OF: u8 = 23;
const WE_ONE_OF: u8 = 24;
const WE_NOT: u8 = 25;
const WE_IF_THEN: u8 = 26;
const WE_IF_ELSE: u8 = 27;

#[derive(Clone, Copy)]
pub struct Error {
    pub code: u8,
    pub offset: u64,
    pub path: [u8; ERR_PATH],
    pub path_len: usize,
    pub msg: [u8; ERR_MSG],
    pub msg_len: usize,
}

impl Error {
    pub fn path(&self) -> &str {
        core::str::from_utf8(&self.path[..self.path_len]).unwrap_or("?")
    }
    pub fn message(&self) -> &str {
        core::str::from_utf8(&self.msg[..self.msg_len]).unwrap_or("?")
    }
}

/// Búfer de formato fijo (sin asignación).
struct Fixed<const N: usize> {
    buf: [u8; N],
    len: usize,
}

impl<const N: usize> Fixed<N> {
    fn new() -> Self {
        Fixed { buf: [0; N], len: 0 }
    }
    fn as_bytes(&self) -> &[u8] {
        &self.buf[..self.len]
    }
}

impl<const N: usize> core::fmt::Write for Fixed<N> {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for &b in s.as_bytes() {
            if self.len < N {
                self.buf[self.len] = b;
                self.len += 1;
            }
        }
        Ok(())
    }
}

#[derive(Clone, Copy)]
pub struct Lane {
    node: i32,
    parent: i16,
    owner: i16,
    role: u8,
    spec: bool,
    keybit: i8,
    viol: u32,
    acc_all: u32,
    any_min: u32,
    one_min: u32,
    any_n: u16,
    one_n: u16,
    one_zeros: u16,
    not_total: i32,
    if_total: i32,
    then_total: u32,
    else_total: u32,
    dep_add: u32,
    dep_fail: u64,
    seen: u64,
    count: u32,
    pend_prop: i32,
    pend_add: i32,
    pend_pat: u32,
    dst: [i32; 1 + MAX_PATS],
    index: u32,
    contains_count: u32,
    uq_base: u32,
    uq_n: u32,
}

#[derive(Clone, Copy)]
struct Level {
    kind: u8,
    base: u32,
    n: u32,
    count: u32,
    hash_acc: u64,
    key_hash: u64,
    path_start: u32,
    uq_top: u32,
}

// Tokens, estados y acciones del DPDA sintáctico (igual que en C).
const TK_LBRACE: usize = 0;
const TK_RBRACE: usize = 1;
const TK_LBRACK: usize = 2;
const TK_RBRACK: usize = 3;
const TK_COLON: usize = 4;
const TK_COMMA: usize = 5;
const TK_STRING: usize = 6;
const TK_NUMBER: usize = 7;
const TK_TRUE: usize = 8;
const TK_FALSE: usize = 9;
const TK_NULL: usize = 10;
const S_VALUE: u8 = 0;
const S_OBJ_FIRST: u8 = 1;
const S_OBJ_KEY: u8 = 2;
const S_OBJ_COLON: u8 = 3;
const S_OBJ_NEXT: u8 = 4;
const S_ARR_FIRST: u8 = 5;
const S_ARR_NEXT: u8 = 6;
const S_DONE: u8 = 7;
const A_ERR: u8 = 0;
const A_VALUE: u8 = 1;
const A_KEY: u8 = 2;
const A_COLON: u8 = 3;
const A_OCOMMA: u8 = 4;
const A_ACOMMA: u8 = 5;
const A_OEND: u8 = 6;
const A_AEND: u8 = 7;

/// δ del DPDA sintáctico: SYN[estado][token] = acción.
const SYN: [[u8; 11]; 8] = [
    [A_VALUE, A_ERR, A_VALUE, A_ERR, A_ERR, A_ERR, A_VALUE, A_VALUE, A_VALUE, A_VALUE, A_VALUE],
    [A_ERR, A_OEND, A_ERR, A_ERR, A_ERR, A_ERR, A_KEY, A_ERR, A_ERR, A_ERR, A_ERR],
    [A_ERR, A_ERR, A_ERR, A_ERR, A_ERR, A_ERR, A_KEY, A_ERR, A_ERR, A_ERR, A_ERR],
    [A_ERR, A_ERR, A_ERR, A_ERR, A_COLON, A_ERR, A_ERR, A_ERR, A_ERR, A_ERR, A_ERR],
    [A_ERR, A_OEND, A_ERR, A_ERR, A_ERR, A_OCOMMA, A_ERR, A_ERR, A_ERR, A_ERR, A_ERR],
    [A_VALUE, A_ERR, A_VALUE, A_AEND, A_ERR, A_ERR, A_VALUE, A_VALUE, A_VALUE, A_VALUE, A_VALUE],
    [A_ERR, A_ERR, A_ERR, A_AEND, A_ERR, A_ACOMMA, A_ERR, A_ERR, A_ERR, A_ERR, A_ERR],
    [A_ERR; 11],
];
const SYN_EXPECT: [&str; 8] = [
    "se esperaba un valor",
    "se esperaba una clave (cadena)",
    "se esperaba una clave (cadena)",
    "se esperaba ':'",
    "se esperaba ',' o '}'",
    "se esperaba un valor",
    "se esperaba ',' o ']'",
    "datos después del valor JSON",
];

const LX_WS: u8 = 0;
const LX_STR: u8 = 1;
const LX_ESC: u8 = 2;
const LX_HEX: u8 = 3;
const LX_SUR1: u8 = 4;
const LX_SUR2: u8 = 5;
const LX_NUM: u8 = 6;
const LX_LIT: u8 = 7;
const LX_LIT_END: u8 = 8;

const NS_START: u8 = 0;
const NS_ZERO: u8 = 1;
const NS_INT: u8 = 2;
const NS_DOT: u8 = 3;
const NS_FRAC: u8 = 4;
const NS_E: u8 = 5;
const NS_ESIGN: u8 = 6;
const NS_EXP: u8 = 7;

const R_ROOT: u8 = 0;
const R_ALL: u8 = 1;
const R_ANY: u8 = 2;
const R_ONE: u8 = 3;
const R_NOT: u8 = 4;
const R_IF: u8 = 5;
const R_THEN: u8 = 6;
const R_ELSE: u8 = 7;
const R_DEP: u8 = 8;
const R_CONTAINS: u8 = 9;

const T_NULL: u8 = 1;
const T_BOOL: u8 = 2;
const T_INT: u8 = 4;
const T_NUM: u8 = 8;
const T_STR: u8 = 16;
const T_ARR: u8 = 32;
const T_OBJ: u8 = 64;

const LITS: [&[u8]; 3] = [b"true", b"false", b"null"];
const FNV_BASIS: u64 = 0xCBF29CE484222325;
const FNV_PRIME: u64 = 0x100000001B3;

#[inline]
fn mix64(mut x: u64) -> u64 {
    x ^= x >> 30;
    x = x.wrapping_mul(0xBF58476D1CE4E5B9);
    x ^= x >> 27;
    x = x.wrapping_mul(0x94D049BB133111EB);
    x ^= x >> 31;
    x
}

#[derive(Clone, Copy)]
struct Req {
    node: i32,
    owner: i16,
    role: u8,
    spec: bool,
}

/// Estado completo del validador (tamaño fijo).
pub struct Ctx {
    pub fail_fast: bool,
    pub max_depth: u32,
    pub status: Status,
    pub fatal_msg: [u8; ERR_MSG],
    pub fatal_len: usize,
    pub fatal_offset: u64,
    pub offset: u64,
    tok_start: u64,
    pub ntokens: u64,
    pub weight: u64,
    pub errors: [Error; MAX_ERRORS],
    pub nerrors: usize,
    pub error_count: u64,
    syn: u8,
    lx: u8,
    str_key: bool,
    hex_left: u8,
    sur_low: bool,
    hex_val: u32,
    hi_sur: u32,
    u8_left: u8,
    u8_lo: u8,
    u8_hi: u8,
    ncp: u32,
    shash: u64,
    lit_kind: u8,
    lit_pos: u8,
    nst: u8,
    n_neg: bool,
    n_exp_neg: bool,
    n_digits: [u8; MAX_DIGITS],
    n_nd: u32,
    n_zeros: u64,
    n_dropped: u64,
    n_frac: u64,
    n_e: i64,
    n_lex: [u8; 48],
    n_lexlen: usize,
    levels: [Level; MAX_DEPTH],
    pub depth: u32,
    pub max_depth_seen: u32,
    lanes: [Lane; LANE_POOL],
    lane_top: u32,
    pub lanes_peak: u32,
    tmp_base: u32,
    tmp_n: u32,
    path: [u8; PATH_BYTES],
    path_len: u32,
    uq: [u64; UNIQUE_POOL],
    uq_top: u32,
}

struct Dec<'a> {
    neg: bool,
    over: bool,
    d: &'a [u8],
    e: i64,
}

fn cmp_dec(a: &Dec, b: &Const) -> i32 {
    let az = a.d.is_empty();
    let bz = b.digits.is_empty();
    if az && bz {
        return 0;
    }
    if az {
        return if b.neg { 1 } else { -1 };
    }
    if bz {
        return if a.neg { -1 } else { 1 };
    }
    if a.neg != b.neg {
        return if a.neg { -1 } else { 1 };
    }
    let ma = a.d.len() as i64 + a.e;
    let mb = b.digits.len() as i64 + b.exp;
    let mut r: i32;
    if ma != mb {
        r = if ma < mb { -1 } else { 1 };
    } else {
        r = 0;
        let n = a.d.len().max(b.digits.len());
        for i in 0..n {
            let x = if i < a.d.len() { a.d[i] } else { b'0' };
            let y = if i < b.digits.len() { b.digits[i] } else { b'0' };
            if x != y {
                r = if x < y { -1 } else { 1 };
                break;
            }
        }
        if r == 0 && a.over {
            r = 1;
        }
    }
    if a.neg {
        -r
    } else {
        r
    }
}

fn mulmod(a: u64, b: u64, m: u64) -> u64 {
    ((a as u128 * b as u128) % m as u128) as u64
}

fn multiple_of(a: &Dec, exp_norm: i64, m: &Const) -> i32 {
    if a.d.is_empty() {
        return 1;
    }
    if a.over || m.digits.len() > 18 {
        return -1;
    }
    let mut k = exp_norm - m.exp;
    if k < 0 {
        return 0;
    }
    let mut bm: u64 = 0;
    for &c in m.digits {
        bm = bm * 10 + (c - b'0') as u64;
    }
    let mut r: u64 = 0;
    for &c in a.d {
        r = (r * 10 + (c - b'0') as u64) % bm;
    }
    let mut base = 10 % bm;
    let mut p = 1 % bm;
    while k > 0 {
        if k & 1 == 1 {
            p = mulmod(p, base, bm);
        }
        base = mulmod(base, base, bm);
        k >>= 1;
    }
    (mulmod(r, p, bm) == 0) as i32
}

#[inline]
fn dfa_step(d: &Dfa, st: &mut i32, b: u8) {
    if *st >= 0 {
        *st = d.table[*st as usize * d.ncls as usize + d.classes[b as usize] as usize];
    }
}

#[inline]
fn dfa_tag(d: &Dfa, st: i32) -> i32 {
    if st >= 0 {
        d.accept[st as usize]
    } else {
        -1
    }
}

fn is_ws(b: u8) -> bool {
    b == b' ' || b == b'\t' || b == b'\n' || b == b'\r'
}
fn is_punct(b: u8) -> bool {
    matches!(b, b'{' | b'}' | b'[' | b']' | b':' | b',')
}
fn punct_tok(b: u8) -> usize {
    match b {
        b'{' => TK_LBRACE,
        b'}' => TK_RBRACE,
        b'[' => TK_LBRACK,
        b']' => TK_RBRACK,
        b':' => TK_COLON,
        _ => TK_COMMA,
    }
}
fn hexval(b: u8) -> i32 {
    match b {
        b'0'..=b'9' => (b - b'0') as i32,
        b'a'..=b'f' => (b - b'a' + 10) as i32,
        b'A'..=b'F' => (b - b'A' + 10) as i32,
        _ => -1,
    }
}

impl Ctx {
    /// Crea el contexto una sola vez (≈1 MB, en el montón para no
    /// desbordar la pila del hilo). Se reutiliza con [`Ctx::init`].
    pub fn new_boxed() -> Box<Ctx> {
        // Todos los campos son enteros o arreglos de enteros (Status es
        // repr(i8) y 0 = Valid): el patrón de bits cero es válido.
        let b: Box<core::mem::MaybeUninit<Ctx>> = Box::new_zeroed();
        unsafe { b.assume_init() }
    }

    pub fn init(&mut self, max_depth: u32, fail_fast: bool) {
        self.fail_fast = fail_fast;
        self.max_depth = if max_depth == 0 || max_depth as usize > MAX_DEPTH {
            MAX_DEPTH as u32
        } else {
            max_depth
        };
        self.status = Status::Running;
        self.fatal_len = 0;
        self.fatal_offset = 0;
        self.offset = 0;
        self.tok_start = 0;
        self.ntokens = 0;
        self.weight = 0;
        self.nerrors = 0;
        self.error_count = 0;
        self.syn = S_VALUE;
        self.lx = LX_WS;
        self.str_key = false;
        self.u8_left = 0;
        self.depth = 0;
        self.max_depth_seen = 0;
        self.lane_top = 0;
        self.lanes_peak = 0;
        self.tmp_base = 0;
        self.tmp_n = 0;
        self.path_len = 0;
        self.uq_top = 0;
    }

    #[inline]
    fn running(&self) -> bool {
        self.status == Status::Running
    }

    fn fatal(&mut self, st: Status, msg: &str, off: u64) {
        if !self.running() {
            return;
        }
        self.status = st;
        self.fatal_offset = off;
        let n = msg.len().min(ERR_MSG);
        self.fatal_msg[..n].copy_from_slice(&msg.as_bytes()[..n]);
        self.fatal_len = n;
    }

    pub fn abort(&mut self, st: Status, msg: &str) {
        let off = self.offset;
        self.fatal(st, msg, off);
    }

    pub fn fatal_message(&self) -> &str {
        core::str::from_utf8(&self.fatal_msg[..self.fatal_len]).unwrap_or("?")
    }

    fn record(&mut self, code: u8, msg: &[u8], plen: u32) {
        if !self.running() {
            return;
        }
        self.error_count += 1;
        if self.nerrors < MAX_ERRORS {
            let e = &mut self.errors[self.nerrors];
            self.nerrors += 1;
            let n = (plen as usize).min(ERR_PATH - 1);
            e.code = code;
            e.offset = self.tok_start;
            e.path[..n].copy_from_slice(&self.path[..n]);
            e.path_len = n;
            let m = msg.len().min(ERR_MSG);
            e.msg[..m].copy_from_slice(&msg[..m]);
            e.msg_len = m;
        }
        if self.fail_fast {
            if self.weight == 0 {
                self.weight = 1;
            }
            self.status = Status::Invalid;
        }
    }

    fn violate(&mut self, li: u32, code: u8, msg: &[u8], plen: u32) {
        let l = &mut self.lanes[li as usize];
        l.viol += 1;
        if !l.spec {
            self.record(code, msg, plen);
        }
    }

    fn path_byte(&mut self, b: u8) {
        let pl = self.path_len as usize;
        if b == b'~' || b == b'/' {
            if pl + 2 > PATH_BYTES - 1 {
                return;
            }
            self.path[pl] = b'~';
            self.path[pl + 1] = if b == b'~' { b'0' } else { b'1' };
            self.path_len += 2;
            return;
        }
        if pl + 1 > PATH_BYTES - 1 {
            return;
        }
        self.path[pl] = b;
        self.path_len += 1;
    }

    fn path_index(&mut self, start: u32, idx: u32) {
        // Sin write!: se llama una vez por elemento de arreglo.
        let mut tmp = [0u8; 12];
        let mut n = 0;
        let mut v = idx;
        loop {
            tmp[n] = b'0' + (v % 10) as u8;
            n += 1;
            v /= 10;
            if v == 0 {
                break;
            }
        }
        self.path_len = start;
        if (self.path_len as usize) < PATH_BYTES - 1 {
            self.path[self.path_len as usize] = b'/';
            self.path_len += 1;
        }
        for k in (0..n).rev() {
            let b = tmp[k];
            if (self.path_len as usize) < PATH_BYTES - 1 {
                self.path[self.path_len as usize] = b;
                self.path_len += 1;
            }
        }
    }

    // ---- carriles -----------------------------------------------------
    #[allow(clippy::too_many_arguments)]
    fn add_lane(&mut self, s: &Schema, base: u32, node: i32, parent: i32, owner: i32,
                role: u8, spec: bool, keybit: i32, jt: u8, is_obj: bool,
                is_container: bool, plen: u32) -> bool {
        if !self.running() {
            return false;
        }
        if (self.lane_top - base) as usize >= MAX_LANES || self.lane_top as usize >= LANE_POOL {
            let off = self.tok_start;
            self.fatal(Status::Limit, "demasiados carriles en un nivel", off);
            return false;
        }
        let idx = self.lane_top - base;
        let li = self.lane_top;
        self.lane_top += 1;
        if self.lane_top > self.lanes_peak {
            self.lanes_peak = self.lane_top;
        }
        self.lanes[li as usize] = Lane {
            node,
            parent: parent as i16,
            owner: owner as i16,
            role,
            spec,
            keybit: keybit as i8,
            viol: 0,
            acc_all: 0,
            any_min: u32::MAX,
            one_min: u32::MAX,
            any_n: 0,
            one_n: 0,
            one_zeros: 0,
            not_total: -1,
            if_total: -1,
            then_total: 0,
            else_total: 0,
            dep_add: 0,
            dep_fail: 0,
            seen: 0,
            count: 0,
            pend_prop: ANY,
            pend_add: ANY,
            pend_pat: 0,
            dst: [0; 1 + MAX_PATS],
            index: 0,
            contains_count: 0,
            uq_base: 0,
            uq_n: 0,
        };
        let n = &s.nodes[node as usize];
        if n.is_false {
            self.violate(li, WE_FALSE, "el esquema false no acepta ningún valor".as_bytes(), plen);
            return true;
        }
        if n.types & jt == 0 {
            self.violate(li, WE_TYPE, "tipo no permitido".as_bytes(), plen);
        } else if n.has_enum && is_container {
            self.violate(li, WE_ENUM, "el valor no está en enum/const".as_bytes(), plen);
        }
        let ii = idx as i32;
        for &c in n.all_of {
            if !self.add_lane(s, base, c, ii, -1, R_ALL, spec, -1, jt, is_obj, is_container, plen) {
                return false;
            }
        }
        for &c in n.any_of {
            if c == ANY {
                let l = &mut self.lanes[(base + idx) as usize];
                l.any_n += 1;
                l.any_min = 0;
            } else if !self.add_lane(s, base, c, ii, -1, R_ANY, true, -1, jt, is_obj, is_container, plen) {
                return false;
            }
        }
        for &c in n.one_of {
            if c == ANY {
                let l = &mut self.lanes[(base + idx) as usize];
                l.one_n += 1;
                l.one_zeros += 1;
                l.one_min = 0;
            } else if !self.add_lane(s, base, c, ii, -1, R_ONE, true, -1, jt, is_obj, is_container, plen) {
                return false;
            }
        }
        if n.not_ != ANY && !self.add_lane(s, base, n.not_, ii, -1, R_NOT, true, -1, jt, is_obj, is_container, plen) {
            return false;
        }
        if n.if_ != ANY {
            if !self.add_lane(s, base, n.if_, ii, -1, R_IF, true, -1, jt, is_obj, is_container, plen) {
                return false;
            }
            if n.then_ != ANY && !self.add_lane(s, base, n.then_, ii, -1, R_THEN, true, -1, jt, is_obj, is_container, plen) {
                return false;
            }
            if n.else_ != ANY && !self.add_lane(s, base, n.else_, ii, -1, R_ELSE, true, -1, jt, is_obj, is_container, plen) {
                return false;
            }
        }
        if is_obj && n.has_obj {
            for i in 0..n.depsch_child.len() {
                let c = n.depsch_child[i];
                if c != ANY && !self.add_lane(s, base, c, ii, -1, R_DEP, true, n.depsch_key[i] as i32, jt, is_obj, is_container, plen) {
                    return false;
                }
            }
        }
        true
    }

    fn finalize(&mut self, s: &Schema, base: u32, n: u32, plen: u32, parent: Option<usize>) {
        let mut i = n as i64 - 1;
        while i >= 0 && self.running() {
            let li = (base as i64 + i) as usize;
            let l = self.lanes[li];
            let nd = &s.nodes[l.node as usize];
            let mut total: u64 = l.viol as u64 + l.acc_all as u64;
            if !nd.any_of.is_empty() {
                let best = if l.any_n > 0 { l.any_min } else { 0 };
                if best > 0 {
                    total += best as u64;
                    if !l.spec {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "anyOf: ninguna alternativa es válida (mejor rama: {} violaciones)", best);
                        self.record(WE_ANY_OF, m.as_bytes(), plen);
                    }
                }
            }
            if !nd.one_of.is_empty() {
                if l.one_zeros == 0 {
                    total += if l.one_n > 0 { l.one_min as u64 } else { 0 };
                    if !l.spec {
                        self.record(WE_ONE_OF, "oneOf: ninguna alternativa es válida".as_bytes(), plen);
                    }
                } else if l.one_zeros > 1 {
                    total += 1;
                    if !l.spec {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "oneOf: {} alternativas válidas (debe ser exactamente una)", l.one_zeros);
                        self.record(WE_ONE_OF, m.as_bytes(), plen);
                    }
                }
            }
            if nd.not_ != ANY && l.not_total == 0 {
                total += 1;
                if !l.spec {
                    self.record(WE_NOT, "el valor no debe cumplir el esquema de not".as_bytes(), plen);
                }
            }
            if nd.not_any {
                total += 1;
                if !l.spec {
                    self.record(WE_NOT, "not: true no acepta ningún valor".as_bytes(), plen);
                }
            }
            if nd.if_ != ANY {
                if l.if_total == 0 {
                    if nd.then_ != ANY && l.then_total > 0 {
                        total += l.then_total as u64;
                        if !l.spec {
                            self.record(WE_IF_THEN, "cumple if pero no then".as_bytes(), plen);
                        }
                    }
                } else if nd.else_ != ANY && l.else_total > 0 {
                    total += l.else_total as u64;
                    if !l.spec {
                        self.record(WE_IF_ELSE, "no cumple if ni else".as_bytes(), plen);
                    }
                }
            }
            if l.dep_fail != 0 {
                total += l.dep_add as u64;
                for j in (0..nd.depsch_key.len()).rev() {
                    let kb = nd.depsch_key[j];
                    if (l.dep_fail >> kb) & 1 == 1 && !l.spec {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "no cumple dependentSchemas de '{}'", nd.keys[kb as usize]);
                        self.record(WE_DEPENDENT_SCHEMAS, m.as_bytes(), plen);
                    }
                }
            }
            let t: u32 = if total > u32::MAX as u64 { u32::MAX } else { total as u32 };
            if l.parent >= 0 {
                let p = &mut self.lanes[(base as i64 + l.parent as i64) as usize];
                match l.role {
                    R_ALL => p.acc_all += t,
                    R_ANY => {
                        p.any_n += 1;
                        if t < p.any_min {
                            p.any_min = t;
                        }
                    }
                    R_ONE => {
                        p.one_n += 1;
                        if t == 0 {
                            p.one_zeros += 1;
                        }
                        if t < p.one_min {
                            p.one_min = t;
                        }
                    }
                    R_NOT => p.not_total = t as i32,
                    R_IF => p.if_total = t as i32,
                    R_THEN => p.then_total = t,
                    R_ELSE => p.else_total = t,
                    R_DEP => {
                        if (p.seen >> l.keybit) & 1 == 1 && t > 0 {
                            p.dep_add += t;
                            p.dep_fail |= 1u64 << l.keybit;
                        }
                    }
                    _ => {}
                }
            } else if l.owner < 0 {
                self.weight += t as u64;
            } else if let Some(pl) = parent {
                let qb = self.levels[pl].base;
                let q = &mut self.lanes[(qb + l.owner as u32) as usize];
                if l.role == R_CONTAINS {
                    if t == 0 {
                        q.contains_count += 1;
                    }
                } else {
                    q.viol += t;
                }
            }
            i -= 1;
        }
    }

    fn after_value(&mut self, s: &Schema, h: u64) {
        if !self.running() {
            return;
        }
        if self.depth == 0 {
            self.syn = S_DONE;
            return;
        }
        let d = (self.depth - 1) as usize;
        if self.levels[d].kind == b'O' {
            let lv = &mut self.levels[d];
            lv.hash_acc = lv.hash_acc.wrapping_add(mix64(lv.key_hash.wrapping_mul(3).wrapping_add(h)));
            self.syn = S_OBJ_NEXT;
            return;
        }
        self.levels[d].hash_acc = mix64(self.levels[d].hash_acc ^ h);
        let key = if h != 0 { h } else { 1 };
        let (base, n) = (self.levels[d].base, self.levels[d].n);
        for i in 0..n {
            if !self.running() {
                break;
            }
            let li = (base + i) as usize;
            let nd = &s.nodes[self.lanes[li].node as usize];
            if !nd.has_arr || !nd.unique || nd.is_false {
                continue;
            }
            let ub = self.lanes[li].uq_base as usize;
            let mut pos = (mix64(key) as usize) & (UQ_SLOTS - 1);
            let mut found = false;
            while self.uq[ub + pos] != 0 {
                if self.uq[ub + pos] == key {
                    found = true;
                    break;
                }
                pos = (pos + 1) & (UQ_SLOTS - 1);
            }
            if found {
                let pl = self.path_len;
                self.violate(li as u32, WE_UNIQUE, b"elementos repetidos (uniqueItems)", pl);
            } else if self.lanes[li].uq_n as usize >= UNIQUE_CAP {
                let off = self.tok_start;
                self.fatal(Status::Limit, "uniqueItems: el arreglo supera la capacidad acotada", off);
            } else {
                self.uq[ub + pos] = key;
                self.lanes[li].uq_n += 1;
            }
        }
        self.syn = S_ARR_NEXT;
    }

    fn requests(&mut self, s: &Schema, reqs: &mut [Req; MAX_LANES]) -> usize {
        let mut nr = 0usize;
        macro_rules! push {
            ($nd:expr, $ow:expr, $ro:expr, $sp:expr) => {{
                if nr >= MAX_LANES {
                    let off = self.tok_start;
                    self.fatal(Status::Limit, "demasiados carriles en un nivel", off);
                    return nr;
                }
                reqs[nr] = Req { node: $nd, owner: $ow as i16, role: $ro, spec: $sp };
                nr += 1;
            }};
        }
        if self.depth == 0 {
            if s.root != ANY {
                push!(s.root, -1, R_ROOT, false);
            }
            return nr;
        }
        let d = (self.depth - 1) as usize;
        let (base, n, kind) = (self.levels[d].base, self.levels[d].n, self.levels[d].kind);
        if kind == b'O' {
            for i in 0..n {
                let li = (base + i) as usize;
                let l = self.lanes[li];
                let nd = &s.nodes[l.node as usize];
                if l.pend_prop >= 0 {
                    push!(l.pend_prop, i, R_ROOT, l.spec);
                }
                if nd.has_obj {
                    for p in 0..nd.ppat_child.len() {
                        if (l.pend_pat >> p) & 1 == 1 && nd.ppat_child[p] >= 0 {
                            push!(nd.ppat_child[p], i, R_ROOT, l.spec);
                        }
                    }
                }
                if l.pend_add >= 0 {
                    push!(l.pend_add, i, R_ROOT, l.spec);
                }
                let lm = &mut self.lanes[li];
                lm.pend_prop = ANY;
                lm.pend_add = ANY;
                lm.pend_pat = 0;
            }
            return nr;
        }
        let (ps, cnt) = (self.levels[d].path_start, self.levels[d].count);
        self.path_index(ps, cnt);
        for i in 0..n {
            if !self.running() {
                break;
            }
            let li = (base + i) as usize;
            let nd = &s.nodes[self.lanes[li].node as usize];
            if !nd.has_arr || nd.is_false {
                continue;
            }
            let idx = self.lanes[li].index;
            self.lanes[li].index += 1;
            let child = if (idx as usize) < nd.prefix.len() { nd.prefix[idx as usize] } else { nd.items };
            if child == FORBIDDEN {
                let mut m = Fixed::<ERR_MSG>::new();
                let _ = write!(m, "elemento {} no permitido (items: false)", idx);
                let pl = self.path_len;
                self.violate(li as u32, WE_ITEMS, m.as_bytes(), pl);
            } else if child >= 0 {
                let sp = self.lanes[li].spec;
                push!(child, i, R_ROOT, sp);
            }
            if nd.contains == ANY {
                self.lanes[li].contains_count += 1;
            } else if nd.contains >= 0 {
                push!(nd.contains, i, R_CONTAINS, true);
            }
        }
        self.levels[d].count += 1;
        nr
    }

    fn exp_norm(&self) -> i64 {
        (if self.n_exp_neg { -self.n_e } else { self.n_e }) - self.n_frac as i64 + self.n_zeros as i64
    }

    fn type_of_token(&self, tk: usize) -> u8 {
        match tk {
            TK_LBRACE => T_OBJ,
            TK_LBRACK => T_ARR,
            TK_STRING => T_STR,
            TK_TRUE | TK_FALSE => T_BOOL,
            TK_NULL => T_NULL,
            _ => {
                if self.n_nd == 0 || self.exp_norm() >= 0 {
                    T_INT
                } else {
                    T_NUM
                }
            }
        }
    }

    fn begin_value(&mut self, s: &Schema, tk: usize) {
        let mut reqs = [Req { node: 0, owner: 0, role: 0, spec: false }; MAX_LANES];
        let nr = self.requests(s, &mut reqs);
        if !self.running() {
            return;
        }
        let jt = self.type_of_token(tk);
        let is_container = tk == TK_LBRACE || tk == TK_LBRACK;
        let base = self.lane_top;
        let plen = self.path_len;
        for r in reqs.iter().take(nr) {
            if !self.running() {
                break;
            }
            self.add_lane(s, base, r.node, -1, r.owner as i32, r.role, r.spec || r.role == R_CONTAINS, -1, jt,
                          tk == TK_LBRACE, is_container, plen);
        }
        if !self.running() {
            return;
        }
        let n = self.lane_top - base;
        if is_container {
            if self.depth + 1 > self.max_depth {
                let off = self.tok_start;
                self.fatal(Status::Depth, "profundidad máxima superada", off);
                return;
            }
            let d = self.depth as usize;
            self.depth += 1;
            if self.depth > self.max_depth_seen {
                self.max_depth_seen = self.depth;
            }
            self.levels[d] = Level {
                kind: if tk == TK_LBRACE { b'O' } else { b'A' },
                base,
                n,
                count: 0,
                hash_acc: 0,
                key_hash: 0,
                path_start: self.path_len,
                uq_top: self.uq_top,
            };
            if tk == TK_LBRACK {
                for i in 0..n {
                    let li = (base + i) as usize;
                    let nd = &s.nodes[self.lanes[li].node as usize];
                    if nd.has_arr && nd.unique && !nd.is_false {
                        if self.uq_top as usize + UQ_SLOTS > UNIQUE_POOL {
                            let off = self.tok_start;
                            self.fatal(Status::Limit, "uniqueItems: sin espacio en el pool acotado de hashes", off);
                            return;
                        }
                        self.lanes[li].uq_base = self.uq_top;
                        let t = self.uq_top as usize;
                        self.uq[t..t + UQ_SLOTS].fill(0);
                        self.uq_top += UQ_SLOTS as u32;
                    }
                }
            }
            self.syn = if tk == TK_LBRACE { S_OBJ_FIRST } else { S_ARR_FIRST };
        } else {
            self.tmp_base = base;
            self.tmp_n = n;
        }
    }

    fn scalar_checks(&mut self, s: &Schema, tk: usize) {
        let plen = self.path_len;
        let exp_norm = self.exp_norm();
        let nd_digits = self.n_digits;
        let v = Dec {
            neg: self.n_neg && self.n_nd > 0,
            over: self.n_dropped > 0,
            d: &nd_digits[..self.n_nd as usize],
            e: exp_norm + self.n_dropped as i64,
        };
        let lex = self.n_lex;
        let lexs = core::str::from_utf8(&lex[..self.n_lexlen]).unwrap_or("?");
        for i in 0..self.tmp_n {
            if !self.running() {
                break;
            }
            let li = self.tmp_base + i;
            let l = self.lanes[li as usize];
            let n = &s.nodes[l.node as usize];
            if n.is_false {
                continue;
            }
            if tk == TK_STRING {
                if n.has_str && n.min_len >= 0 && self.ncp < n.min_len as u32 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "longitud {} < minLength {}", self.ncp, n.min_len);
                    self.violate(li, WE_MIN_LENGTH, m.as_bytes(), plen);
                }
                if n.has_str && n.max_len >= 0 && self.ncp > n.max_len as u32 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "longitud {} > maxLength {}", self.ncp, n.max_len);
                    self.violate(li, WE_MAX_LENGTH, m.as_bytes(), plen);
                }
                if n.has_enum && !(n.enum_dfa >= 0 && dfa_tag(&s.dfas[n.enum_dfa as usize], l.dst[0]) >= 0) {
                    self.violate(li, WE_ENUM, "el valor no está en enum/const".as_bytes(), plen);
                }
                if n.has_str {
                    for p in 0..n.pats.len() {
                        if dfa_tag(&s.dfas[n.pats[p] as usize], l.dst[1 + p]) < 0 {
                            let mut m = Fixed::<ERR_MSG>::new();
                            let _ = write!(m, "no coincide con pattern '{}'", n.pat_src[p]);
                            self.violate(li, WE_PATTERN, m.as_bytes(), plen);
                        }
                    }
                }
            } else if tk == TK_NUMBER {
                if n.has_enum {
                    let ok = n.enum_nums.iter().any(|&k| cmp_dec(&v, &s.consts[k as usize]) == 0);
                    if !ok {
                        self.violate(li, WE_ENUM, "el valor no está en enum/const".as_bytes(), plen);
                    }
                }
                for k in 0..n.num_ops.len() {
                    let kc = &s.consts[n.num_consts[k] as usize];
                    if n.num_ops[k] == OP_MUL {
                        let r = multiple_of(&v, exp_norm, kc);
                        if r < 0 {
                            self.violate(li, WE_MULTIPLE_OF, "número con demasiados dígitos para multipleOf (límite del registro)".as_bytes(), plen);
                        } else if r == 0 {
                            let mut m = Fixed::<ERR_MSG>::new();
                            let _ = write!(m, "no es múltiplo de {}", kc.lexeme);
                            self.violate(li, WE_MULTIPLE_OF, m.as_bytes(), plen);
                        }
                        continue;
                    }
                    let cmp = cmp_dec(&v, kc);
                    let (bad, code, sym) = match n.num_ops[k] {
                        OP_GE => (cmp < 0, WE_MINIMUM, "<"),
                        OP_GT => (cmp <= 0, WE_EXCLUSIVE_MINIMUM, "<="),
                        OP_LE => (cmp > 0, WE_MAXIMUM, ">"),
                        _ => (cmp >= 0, WE_EXCLUSIVE_MAXIMUM, ">="),
                    };
                    if bad {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "{} {} {}", lexs, sym, kc.lexeme);
                        self.violate(li, code, m.as_bytes(), plen);
                    }
                }
            } else if n.has_enum {
                let bit = if tk == TK_TRUE { 1 } else if tk == TK_FALSE { 2 } else { 4 };
                if n.enum_lits & bit == 0 {
                    self.violate(li, WE_ENUM, "el valor no está en enum/const".as_bytes(), plen);
                }
            }
        }
    }

    fn end_scalar(&mut self, s: &Schema, tk: usize, h: u64) {
        self.scalar_checks(s, tk);
        if !self.running() {
            return;
        }
        let parent = if self.depth > 0 { Some((self.depth - 1) as usize) } else { None };
        let (tb, tn, pl) = (self.tmp_base, self.tmp_n, self.path_len);
        self.finalize(s, tb, tn, pl, parent);
        self.lane_top = self.tmp_base;
        self.tmp_n = 0;
        self.after_value(s, h);
    }

    fn end_container(&mut self, s: &Schema) {
        let d = (self.depth - 1) as usize;
        let lv = self.levels[d];
        let plen = lv.path_start;
        for i in 0..lv.n {
            if !self.running() {
                break;
            }
            let li = lv.base + i;
            let l = self.lanes[li as usize];
            let n = &s.nodes[l.node as usize];
            if n.is_false {
                continue;
            }
            if lv.kind == b'O' && n.has_obj {
                let mut miss = n.required & !l.seen;
                let mut b = 0usize;
                while miss != 0 {
                    if miss & 1 == 1 {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "falta la propiedad requerida '{}'", n.keys[b]);
                        self.violate(li, WE_REQUIRED, m.as_bytes(), plen);
                    }
                    miss >>= 1;
                    b += 1;
                }
                if n.min_props >= 0 && l.count < n.min_props as u32 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "{} propiedades < {}", l.count, n.min_props);
                    self.violate(li, WE_MIN_PROPERTIES, m.as_bytes(), plen);
                }
                if n.max_props >= 0 && l.count > n.max_props as u32 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "{} propiedades > {}", l.count, n.max_props);
                    self.violate(li, WE_MAX_PROPERTIES, m.as_bytes(), plen);
                }
                for dd in 0..n.depreq_key.len() {
                    let kb = n.depreq_key[dd] as usize;
                    if (l.seen >> kb) & 1 == 0 {
                        continue;
                    }
                    let mut mm = n.depreq_mask[dd] & !l.seen;
                    let mut b = 0usize;
                    while mm != 0 {
                        if mm & 1 == 1 {
                            let mut m = Fixed::<ERR_MSG>::new();
                            let _ = write!(m, "'{}' exige '{}'", n.keys[kb], n.keys[b]);
                            self.violate(li, WE_DEPENDENT_REQUIRED, m.as_bytes(), plen);
                        }
                        mm >>= 1;
                        b += 1;
                    }
                }
            } else if lv.kind == b'A' && n.has_arr {
                if n.min_items >= 0 && l.index < n.min_items as u32 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "{} elementos < minItems {}", l.index, n.min_items);
                    self.violate(li, WE_MIN_ITEMS, m.as_bytes(), plen);
                }
                if n.max_items >= 0 && l.index > n.max_items as u32 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "{} elementos > maxItems {}", l.index, n.max_items);
                    self.violate(li, WE_MAX_ITEMS, m.as_bytes(), plen);
                }
                if n.contains != NONE {
                    if l.contains_count < n.min_contains as u32 {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "{} elementos cumplen contains < {}", l.contains_count, n.min_contains);
                        self.violate(li, WE_CONTAINS, m.as_bytes(), plen);
                    }
                    if n.max_contains >= 0 && l.contains_count > n.max_contains as u32 {
                        let mut m = Fixed::<ERR_MSG>::new();
                        let _ = write!(m, "{} elementos cumplen contains > {}", l.contains_count, n.max_contains);
                        self.violate(li, WE_CONTAINS, m.as_bytes(), plen);
                    }
                }
            }
        }
        if !self.running() {
            return;
        }
        let h = if lv.kind == b'O' {
            mix64(lv.hash_acc ^ mix64(5))
        } else {
            mix64(lv.hash_acc ^ mix64(4) ^ lv.count as u64)
        };
        let parent = if self.depth >= 2 { Some((self.depth - 2) as usize) } else { None };
        self.finalize(s, lv.base, lv.n, plen, parent);
        if !self.running() {
            return;
        }
        self.lane_top = lv.base;
        self.uq_top = lv.uq_top;
        self.path_len = lv.path_start;
        self.depth -= 1;
        self.after_value(s, h);
    }

    fn key_begin(&mut self, s: &Schema) {
        let d = (self.depth - 1) as usize;
        let lv = self.levels[d];
        self.path_len = lv.path_start;
        if (self.path_len as usize) < PATH_BYTES - 1 {
            self.path[self.path_len as usize] = b'/';
            self.path_len += 1;
        }
        for i in 0..lv.n {
            let li = (lv.base + i) as usize;
            let n = &s.nodes[self.lanes[li].node as usize];
            let l = &mut self.lanes[li];
            l.dst[0] = if n.has_obj && n.keys_dfa >= 0 { 0 } else { -1 };
            for p in 0..MAX_PATS {
                l.dst[1 + p] = if n.has_obj && p < n.ppat_dfa.len() { 0 } else { -1 };
            }
        }
    }

    fn key_end(&mut self, s: &Schema) {
        let d = (self.depth - 1) as usize;
        self.levels[d].count += 1;
        self.levels[d].key_hash = mix64(self.shash);
        let lv = self.levels[d];
        let plen = self.path_len;
        for i in 0..lv.n {
            if !self.running() {
                break;
            }
            let li = lv.base + i;
            let n = &s.nodes[self.lanes[li as usize].node as usize];
            {
                let l = &mut self.lanes[li as usize];
                l.pend_prop = ANY;
                l.pend_add = ANY;
                l.pend_pat = 0;
            }
            if n.is_false {
                continue;
            }
            self.lanes[li as usize].count += 1;
            if !n.has_obj {
                continue;
            }
            let l = self.lanes[li as usize];
            let idx = if n.keys_dfa >= 0 { dfa_tag(&s.dfas[n.keys_dfa as usize], l.dst[0]) } else { -1 };
            let mut declared = false;
            let mut matched = false;
            if idx >= 0 {
                if (l.seen >> idx) & 1 == 1 {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let _ = write!(m, "clave duplicada '{}'", n.keys[idx as usize]);
                    self.violate(li, WE_DUPLICATE_KEY, m.as_bytes(), plen);
                }
                self.lanes[li as usize].seen |= 1u64 << idx;
                if n.key_declared[idx as usize] != 0 {
                    declared = true;
                    if n.key_child[idx as usize] >= 0 {
                        self.lanes[li as usize].pend_prop = n.key_child[idx as usize];
                    }
                }
            }
            for p in 0..n.ppat_dfa.len() {
                if dfa_tag(&s.dfas[n.ppat_dfa[p] as usize], l.dst[1 + p]) >= 0 {
                    matched = true;
                    self.lanes[li as usize].pend_pat |= 1u32 << p;
                }
            }
            if !declared && !matched {
                if n.additional == FORBIDDEN {
                    let mut m = Fixed::<ERR_MSG>::new();
                    let st = (lv.path_start + 1) as usize;
                    let key = core::str::from_utf8(&self.path[st..plen as usize]).unwrap_or("?");
                    let _ = write!(m, "propiedad adicional no permitida '{}'", key);
                    self.violate(li, WE_ADDITIONAL, m.as_bytes(), plen);
                } else if n.additional >= 0 {
                    self.lanes[li as usize].pend_add = n.additional;
                }
            }
        }
        self.syn = S_OBJ_COLON;
    }

    fn str_begin(&mut self, s: &Schema) {
        self.ncp = 0;
        self.shash = (FNV_BASIS ^ 0x53).wrapping_mul(FNV_PRIME);
        if self.str_key {
            self.key_begin(s);
            return;
        }
        for i in 0..self.tmp_n {
            let li = (self.tmp_base + i) as usize;
            let n = &s.nodes[self.lanes[li].node as usize];
            let l = &mut self.lanes[li];
            l.dst[0] = if n.has_enum && n.enum_dfa >= 0 { 0 } else { -1 };
            for p in 0..MAX_PATS {
                l.dst[1 + p] = if n.has_str && p < n.pats.len() { 0 } else { -1 };
            }
        }
    }

    #[inline]
    fn str_byte(&mut self, s: &Schema, b: u8) {
        self.shash = (self.shash ^ b as u64).wrapping_mul(FNV_PRIME);
        if self.str_key {
            self.path_byte(b);
            let lv = self.levels[(self.depth - 1) as usize];
            for i in 0..lv.n {
                let li = (lv.base + i) as usize;
                let n = &s.nodes[self.lanes[li].node as usize];
                if !n.has_obj {
                    continue;
                }
                let l = &mut self.lanes[li];
                if n.keys_dfa >= 0 {
                    dfa_step(&s.dfas[n.keys_dfa as usize], &mut l.dst[0], b);
                }
                for p in 0..n.ppat_dfa.len() {
                    dfa_step(&s.dfas[n.ppat_dfa[p] as usize], &mut l.dst[1 + p], b);
                }
            }
            return;
        }
        for i in 0..self.tmp_n {
            let li = (self.tmp_base + i) as usize;
            let n = &s.nodes[self.lanes[li].node as usize];
            let l = &mut self.lanes[li];
            if n.has_enum && n.enum_dfa >= 0 {
                dfa_step(&s.dfas[n.enum_dfa as usize], &mut l.dst[0], b);
            }
            if n.has_str {
                for p in 0..n.pats.len() {
                    dfa_step(&s.dfas[n.pats[p] as usize], &mut l.dst[1 + p], b);
                }
            }
        }
    }

    fn str_cp(&mut self, s: &Schema, cp: u32) {
        let mut buf = [0u8; 4];
        let n = if cp < 0x80 {
            buf[0] = cp as u8;
            1
        } else if cp < 0x800 {
            buf[0] = (0xC0 | (cp >> 6)) as u8;
            buf[1] = (0x80 | (cp & 0x3F)) as u8;
            2
        } else if cp < 0x10000 {
            buf[0] = (0xE0 | (cp >> 12)) as u8;
            buf[1] = (0x80 | ((cp >> 6) & 0x3F)) as u8;
            buf[2] = (0x80 | (cp & 0x3F)) as u8;
            3
        } else {
            buf[0] = (0xF0 | (cp >> 18)) as u8;
            buf[1] = (0x80 | ((cp >> 12) & 0x3F)) as u8;
            buf[2] = (0x80 | ((cp >> 6) & 0x3F)) as u8;
            buf[3] = (0x80 | (cp & 0x3F)) as u8;
            4
        };
        for &b in &buf[..n] {
            self.str_byte(s, b);
        }
        self.ncp += 1;
    }

    fn str_end(&mut self, s: &Schema) {
        if self.str_key {
            self.key_end(s);
            return;
        }
        let h = mix64(self.shash);
        self.end_scalar(s, TK_STRING, h);
    }

    fn num_sig(&mut self, d: u8) {
        if (self.n_nd as usize) < MAX_DIGITS && self.n_dropped == 0 {
            self.n_digits[self.n_nd as usize] = d;
            self.n_nd += 1;
        } else {
            self.n_dropped += 1;
        }
    }

    fn num_digit(&mut self, b: u8, frac: bool) {
        if frac {
            self.n_frac += 1;
        }
        if b == b'0' {
            if self.n_nd == 0 && self.n_dropped == 0 {
                return;
            }
            self.n_zeros += 1;
            return;
        }
        while self.n_zeros > 0 {
            self.num_sig(b'0');
            self.n_zeros -= 1;
        }
        self.num_sig(b);
    }

    fn num_begin(&mut self, b: u8) {
        self.n_neg = b == b'-';
        self.n_exp_neg = false;
        self.n_nd = 0;
        self.n_zeros = 0;
        self.n_dropped = 0;
        self.n_frac = 0;
        self.n_e = 0;
        self.n_lexlen = 0;
        if b == b'-' {
            self.nst = NS_START;
        } else if b == b'0' {
            self.nst = NS_ZERO;
            self.num_digit(b, false);
        } else {
            self.nst = NS_INT;
            self.num_digit(b, false);
        }
    }

    fn num_byte(&mut self, b: u8) -> i32 {
        let dig = b.is_ascii_digit();
        match self.nst {
            NS_START => {
                if b == b'0' {
                    self.nst = NS_ZERO;
                    self.num_digit(b, false);
                    1
                } else if dig {
                    self.nst = NS_INT;
                    self.num_digit(b, false);
                    1
                } else {
                    -1
                }
            }
            NS_ZERO => {
                if dig {
                    -1
                } else if b == b'.' {
                    self.nst = NS_DOT;
                    1
                } else if b == b'e' || b == b'E' {
                    self.nst = NS_E;
                    1
                } else {
                    0
                }
            }
            NS_INT => {
                if dig {
                    self.num_digit(b, false);
                    1
                } else if b == b'.' {
                    self.nst = NS_DOT;
                    1
                } else if b == b'e' || b == b'E' {
                    self.nst = NS_E;
                    1
                } else {
                    0
                }
            }
            NS_DOT => {
                if dig {
                    self.nst = NS_FRAC;
                    self.num_digit(b, true);
                    1
                } else {
                    -1
                }
            }
            NS_FRAC => {
                if dig {
                    self.num_digit(b, true);
                    1
                } else if b == b'e' || b == b'E' {
                    self.nst = NS_E;
                    1
                } else {
                    0
                }
            }
            NS_E => {
                if b == b'+' || b == b'-' {
                    self.n_exp_neg = b == b'-';
                    self.nst = NS_ESIGN;
                    1
                } else if dig {
                    self.nst = NS_EXP;
                    self.n_e = (b - b'0') as i64;
                    1
                } else {
                    -1
                }
            }
            NS_ESIGN => {
                if dig {
                    self.nst = NS_EXP;
                    self.n_e = (b - b'0') as i64;
                    1
                } else {
                    -1
                }
            }
            _ => {
                if dig {
                    if self.n_e < 1_000_000_000_000_000 {
                        self.n_e = self.n_e * 10 + (b - b'0') as i64;
                    }
                    1
                } else {
                    0
                }
            }
        }
    }

    fn num_hash(&self, s: &Schema) -> u64 {
        if !s.uses_unique {
            return 0; // el hash solo sirve a uniqueItems
        }
        let mut h = (FNV_BASIS ^ 0x4E).wrapping_mul(FNV_PRIME);
        if self.n_nd == 0 {
            h = (h ^ b'0' as u64).wrapping_mul(FNV_PRIME);
            return mix64(h);
        }
        if self.n_neg {
            h = (h ^ b'-' as u64).wrapping_mul(FNV_PRIME);
        }
        for &d in &self.n_digits[..self.n_nd as usize] {
            h = (h ^ d as u64).wrapping_mul(FNV_PRIME);
        }
        let mut tmp = Fixed::<32>::new();
        let _ = write!(tmp, "e{}", self.exp_norm());
        for &b in tmp.as_bytes() {
            h = (h ^ b as u64).wrapping_mul(FNV_PRIME);
        }
        mix64(h)
    }

    fn syn_token(&mut self, s: &Schema, tk: usize) {
        self.ntokens += 1;
        let a = SYN[self.syn as usize][tk];
        match a {
            A_ERR => {
                let off = self.tok_start;
                self.fatal(Status::Malformed, SYN_EXPECT[self.syn as usize], off);
            }
            A_VALUE => {
                self.begin_value(s, tk);
                if !self.running() {
                    return;
                }
                if tk == TK_STRING {
                    self.str_key = false;
                } else if tk == TK_NUMBER {
                    let h = self.num_hash(s);
                    self.end_scalar(s, tk, h);
                } else if tk == TK_TRUE || tk == TK_FALSE || tk == TK_NULL {
                    let h = if tk == TK_TRUE { mix64(2) } else if tk == TK_FALSE { mix64(3) } else { mix64(1) };
                    self.end_scalar(s, tk, h);
                }
            }
            A_KEY => self.str_key = true,
            A_COLON => self.syn = S_VALUE,
            A_OCOMMA => self.syn = S_OBJ_KEY,
            A_ACOMMA => self.syn = S_VALUE,
            _ => self.end_container(s),
        }
    }

    fn utf8_lead(&mut self, b: u8) -> bool {
        let (n, lo, hi) = match b {
            0xC2..=0xDF => (1, 0x80, 0xBF),
            0xE0 => (2, 0xA0, 0xBF),
            0xE1..=0xEC | 0xEE | 0xEF => (2, 0x80, 0xBF),
            0xED => (2, 0x80, 0x9F),
            0xF0 => (3, 0x90, 0xBF),
            0xF1..=0xF3 => (3, 0x80, 0xBF),
            0xF4 => (3, 0x80, 0x8F),
            _ => return false,
        };
        self.u8_left = n;
        self.u8_lo = lo;
        self.u8_hi = hi;
        true
    }

    fn start_token(&mut self, s: &Schema, b: u8) {
        if is_ws(b) {
            return;
        }
        self.tok_start = self.offset;
        if b == b'"' {
            self.syn_token(s, TK_STRING);
            if !self.running() {
                return;
            }
            self.str_begin(s);
            self.lx = LX_STR;
        } else if is_punct(b) {
            self.syn_token(s, punct_tok(b));
        } else if b == b'-' || b.is_ascii_digit() {
            self.num_begin(b);
            if self.n_lexlen < self.n_lex.len() {
                self.n_lex[self.n_lexlen] = b;
                self.n_lexlen += 1;
            }
            self.lx = LX_NUM;
        } else if b == b't' || b == b'f' || b == b'n' {
            self.lit_kind = if b == b't' { 0 } else if b == b'f' { 1 } else { 2 };
            self.lit_pos = 1;
            self.lx = LX_LIT;
        } else if b >= 0x80 {
            let off = self.offset;
            if !self.utf8_lead(b) {
                self.fatal(Status::Utf8, "byte UTF-8 inválido", off);
            } else {
                self.fatal(Status::Malformed, "byte inesperado fuera de cadena", off);
            }
        } else {
            let off = self.offset;
            self.fatal(Status::Malformed, "byte inesperado", off);
        }
    }

    fn end_number(&mut self, s: &Schema) {
        let acc = matches!(self.nst, NS_ZERO | NS_INT | NS_FRAC | NS_EXP);
        if !acc {
            let off = self.tok_start;
            self.fatal(Status::Malformed, "número incompleto", off);
            return;
        }
        self.syn_token(s, TK_NUMBER);
    }

    /// Alimenta un bloque de bytes (cualquier tamaño).
    pub fn feed(&mut self, s: &Schema, data: &[u8]) -> Status {
        for &b in data {
            if !self.running() {
                break;
            }
            match self.lx {
                LX_WS => self.start_token(s, b),
                LX_STR => {
                    if self.u8_left > 0 {
                        if b < self.u8_lo || b > self.u8_hi {
                            let off = self.offset;
                            self.fatal(Status::Utf8, "byte de continuación UTF-8 inválido", off);
                        } else {
                            self.u8_left -= 1;
                            self.u8_lo = 0x80;
                            self.u8_hi = 0xBF;
                            self.str_byte(s, b);
                        }
                    } else if b == b'"' {
                        self.lx = LX_WS;
                        self.str_end(s);
                    } else if b == b'\\' {
                        self.lx = LX_ESC;
                    } else if b < 0x20 {
                        let off = self.offset;
                        self.fatal(Status::Malformed, "carácter de control sin escapar", off);
                    } else if b < 0x80 {
                        self.str_byte(s, b);
                        self.ncp += 1;
                    } else if !self.utf8_lead(b) {
                        let off = self.offset;
                        self.fatal(Status::Utf8, "byte UTF-8 inválido", off);
                    } else {
                        self.str_byte(s, b);
                        self.ncp += 1;
                    }
                }
                LX_ESC => {
                    let v = match b {
                        b'"' => b'"',
                        b'\\' => b'\\',
                        b'/' => b'/',
                        b'b' => 0x08,
                        b'f' => 0x0C,
                        b'n' => b'\n',
                        b'r' => b'\r',
                        b't' => b'\t',
                        b'u' => {
                            self.hex_left = 4;
                            self.hex_val = 0;
                            self.sur_low = false;
                            self.lx = LX_HEX;
                            0
                        }
                        _ => {
                            let off = self.offset;
                            self.fatal(Status::Malformed, "escape inválido", off);
                            0
                        }
                    };
                    if b != b'u' && self.running() {
                        self.str_byte(s, v);
                        self.ncp += 1;
                        self.lx = LX_STR;
                    }
                }
                LX_HEX => {
                    let h = hexval(b);
                    if h < 0 {
                        let off = self.offset;
                        self.fatal(Status::Malformed, "escape \\u no hexadecimal", off);
                    } else {
                        self.hex_val = self.hex_val * 16 + h as u32;
                        self.hex_left -= 1;
                        if self.hex_left == 0 {
                            let cp = self.hex_val;
                            if self.sur_low {
                                if !(0xDC00..=0xDFFF).contains(&cp) {
                                    let off = self.offset;
                                    self.fatal(Status::Malformed, "sustituto alto sin sustituto bajo", off);
                                } else {
                                    let full = 0x10000 + ((self.hi_sur - 0xD800) << 10) + (cp - 0xDC00);
                                    self.str_cp(s, full);
                                    self.sur_low = false;
                                    self.lx = LX_STR;
                                }
                            } else if (0xD800..=0xDBFF).contains(&cp) {
                                self.hi_sur = cp;
                                self.lx = LX_SUR1;
                            } else if (0xDC00..=0xDFFF).contains(&cp) {
                                let off = self.offset;
                                self.fatal(Status::Malformed, "sustituto bajo suelto", off);
                            } else {
                                self.str_cp(s, cp);
                                self.lx = LX_STR;
                            }
                        }
                    }
                }
                LX_SUR1 => {
                    if b == b'\\' {
                        self.lx = LX_SUR2;
                    } else {
                        let off = self.offset;
                        self.fatal(Status::Malformed, "sustituto alto sin pareja", off);
                    }
                }
                LX_SUR2 => {
                    if b == b'u' {
                        self.hex_left = 4;
                        self.hex_val = 0;
                        self.sur_low = true;
                        self.lx = LX_HEX;
                    } else {
                        let off = self.offset;
                        self.fatal(Status::Malformed, "sustituto alto sin pareja", off);
                    }
                }
                LX_NUM => {
                    let r = self.num_byte(b);
                    if r > 0 {
                        if self.n_lexlen < self.n_lex.len() {
                            self.n_lex[self.n_lexlen] = b;
                            self.n_lexlen += 1;
                        }
                    } else if r < 0 {
                        let off = self.offset;
                        self.fatal(Status::Malformed, "número inválido", off);
                    } else {
                        self.lx = LX_WS;
                        self.end_number(s);
                        if self.running() {
                            self.start_token(s, b);
                        }
                    }
                }
                LX_LIT => {
                    let lit = LITS[self.lit_kind as usize];
                    if b == lit[self.lit_pos as usize] {
                        self.lit_pos += 1;
                        if self.lit_pos as usize == lit.len() {
                            self.lx = LX_LIT_END;
                        }
                    } else {
                        let off = self.tok_start;
                        self.fatal(Status::Malformed, "literal inválido", off);
                    }
                }
                _ => {
                    if is_ws(b) || is_punct(b) {
                        self.lx = LX_WS;
                        self.syn_token(s, TK_TRUE + self.lit_kind as usize);
                        if self.running() {
                            self.start_token(s, b);
                        }
                    } else {
                        let off = self.tok_start;
                        self.fatal(Status::Malformed, "literal inválido", off);
                    }
                }
            }
            self.offset += 1;
        }
        self.status
    }

    pub fn finish(&mut self, s: &Schema) -> Status {
        if !self.running() {
            return self.status;
        }
        match self.lx {
            LX_NUM => {
                self.lx = LX_WS;
                self.end_number(s);
            }
            LX_LIT_END => {
                self.lx = LX_WS;
                self.syn_token(s, TK_TRUE + self.lit_kind as usize);
            }
            LX_LIT => {
                let off = self.tok_start;
                self.fatal(Status::Malformed, "literal incompleto", off);
            }
            LX_WS => {}
            _ => {
                if self.u8_left > 0 {
                    let off = self.offset;
                    self.fatal(Status::Utf8, "secuencia UTF-8 incompleta", off);
                } else {
                    let off = self.tok_start;
                    self.fatal(Status::Malformed, "cadena sin cerrar", off);
                }
            }
        }
        if self.running() && self.syn != S_DONE {
            let off = self.offset;
            self.fatal(Status::Malformed, "entrada incompleta: el valor JSON no terminó", off);
        }
        if self.running() {
            self.status = if self.weight == 0 { Status::Valid } else { Status::Invalid };
        }
        self.status
    }
}
