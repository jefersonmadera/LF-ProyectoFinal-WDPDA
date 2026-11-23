/* wdpda_xml.c — Runtime del WDPDA para XML. Sin malloc.
 * Equivalente ejecutable: compiler/xml_engine.py (pruebas cruzadas). */
#include "wdpda_xml.h"

#include <stdio.h>
#include <string.h>

enum { X_CONTENT, X_LT, X_OPEN_NAME, X_ATTRS, X_ATTR_NAME, X_ATTR_EQ,
       X_ATTR_Q, X_ATTR_VAL, X_EMPTY_END, X_CLOSE_NAME, X_CLOSE_WS, X_PI,
       X_BANG1, X_BANG2, X_COMMENT, X_ENT };
enum { XK_STRING = 0, XK_BOOLEAN, XK_INTEGER, XK_DECIMAL, XK_DOUBLE };
enum { XN_START, XN_SIGN, XN_INT, XN_DOT, XN_FRAC, XN_E, XN_ESIGN, XN_EXP };

#define RUNNING(c) ((c)->status == WD_RUNNING)

static uint64_t mix64(uint64_t x) {
    x ^= x >> 30;
    x *= 0xBF58476D1CE4E5B9ULL;
    x ^= x >> 27;
    x *= 0x94D049BB133111EBULL;
    x ^= x >> 31;
    return x;
}

size_t wx_ctx_size(void) { return sizeof(wx_ctx_t); }

void wx_init(wx_ctx_t *c, const wx_schema_t *s, uint32_t max_depth,
             int fail_fast) {
    memset(c, 0, offsetof(wx_ctx_t, frames));
    c->depth = c->max_depth_seen = 0;
    c->names_top = 0;
    c->path_len = 0;
    c->s = s;
    c->fail_fast = fail_fast;
    c->max_depth = (max_depth == 0 || max_depth > WD_MAX_DEPTH)
                       ? WD_MAX_DEPTH : max_depth;
    c->status = WD_RUNNING;
    c->lx = X_CONTENT;
}

static void fatal(wx_ctx_t *c, wd_status_t st, const char *msg, uint64_t off) {
    if (!RUNNING(c)) return;
    c->status = st;
    c->fatal_offset = off;
    snprintf(c->fatal_msg, sizeof c->fatal_msg, "%s", msg);
}

void wx_abort(wx_ctx_t *c, wd_status_t st, const char *msg) {
    fatal(c, st, msg, c->offset);
}

static void violate(wx_ctx_t *c, int code, const char *msg, uint32_t plen) {
    if (!RUNNING(c)) return;
    c->weight++;
    c->error_count++;
    if (c->nerrors < WD_MAX_ERRORS) {
        wd_error_t *e = &c->errors[c->nerrors++];
        uint32_t n = plen < WD_ERR_PATH - 1 ? plen : WD_ERR_PATH - 1;
        e->code = (uint8_t)code;
        e->offset = c->tok_start;
        memcpy(e->path, c->path, n);
        e->path[n] = 0;
        snprintf(e->msg, sizeof e->msg, "%s", msg);
    }
    if (c->fail_fast) c->status = WD_INVALID;
}

static void path_str(wx_ctx_t *c, const char *s, uint32_t n) {
    for (uint32_t i = 0; i < n && c->path_len < WD_PATH_BYTES - 1; i++)
        c->path[c->path_len++] = s[i];
}

static int32_t dfa_run(const wd_dfa_t *d, const char *s, uint32_t n) {
    int32_t st = 0;
    for (uint32_t i = 0; i < n && st >= 0; i++)
        st = d->table[(uint32_t)st * d->ncls + d->classes[(uint8_t)s[i]]];
    return st >= 0 ? d->accept[st] : -1;
}

static inline void dfa_step(const wd_dfa_t *d, int32_t *st, uint8_t b) {
    if (*st >= 0) *st = d->table[(uint32_t)*st * d->ncls + d->classes[b]];
}

/* ---- Decimal exacto (igual que en wdpda_rt.c) --------------------------- */
static int cmp_text_num(const wx_text_t *t, const wd_const_t *b) {
    int az = t->n_nd == 0, bz = b->ndigits == 0;
    int neg = t->n_neg && !az;
    int64_t exp_norm = (t->n_exp_neg ? -t->n_e : t->n_e) - (int64_t)t->n_frac +
                       (int64_t)t->n_zeros;
    int64_t e = exp_norm + (int64_t)t->n_dropped;
    if (az && bz) return 0;
    if (az) return b->neg ? 1 : -1;
    if (bz) return neg ? -1 : 1;
    if (neg != b->neg) return neg ? -1 : 1;
    int64_t ma = (int64_t)t->n_nd + e, mb = (int64_t)b->ndigits + b->exp;
    int r = 0;
    if (ma != mb) {
        r = ma < mb ? -1 : 1;
    } else {
        uint32_t n = t->n_nd > b->ndigits ? t->n_nd : b->ndigits;
        for (uint32_t i = 0; i < n && r == 0; i++) {
            char x = i < t->n_nd ? t->n_digits[i] : '0';
            char y = i < b->ndigits ? b->digits[i] : '0';
            if (x != y) r = x < y ? -1 : 1;
        }
        if (r == 0 && t->n_dropped) r = 1;
    }
    return neg ? -r : r;
}

static void num_sig(wx_text_t *t, char d) {
    if (t->n_nd < WD_MAX_DIGITS && t->n_dropped == 0) t->n_digits[t->n_nd++] = d;
    else t->n_dropped++;
}

static void num_digit(wx_text_t *t, uint8_t b, int frac) {
    t->any_digit = 1;
    if (frac) t->n_frac++;
    if (b == '0') {
        if (t->n_nd == 0 && t->n_dropped == 0) return;
        t->n_zeros++;
        return;
    }
    while (t->n_zeros) { num_sig(t, '0'); t->n_zeros--; }
    num_sig(t, (char)b);
}

/* ---- Verificador de texto de un tipo simple ------------------------------- */
static void text_begin(const wx_schema_t *s, wx_text_t *t, int32_t node) {
    memset(t, 0, offsetof(wx_text_t, n_digits));
    t->n_nd = 0;
    t->n_zeros = t->n_dropped = t->n_frac = 0;
    t->n_e = 0;
    t->lexlen = 0;
    t->node = node;
    if (node < 0) return;
    const wd_node_t *N = &s->nodes[node];
    t->kind = N->xsd;
    t->dst[0] = (N->has_enum && N->enum_dfa >= 0) ? 0 : -1;
    for (uint32_t p = 0; p < WD_MAX_PATS; p++)
        t->dst[1 + p] = (N->has_str && p < N->npat) ? 0 : -1;
    t->nst = XN_START;
}

static int is_ws(uint8_t b) {
    return b == ' ' || b == '\t' || b == '\n' || b == '\r';
}

static void text_feed_dfas(const wx_schema_t *s, wx_text_t *t, uint8_t b) {
    const wd_node_t *N = &s->nodes[t->node];
    if (N->has_enum && N->enum_dfa >= 0) dfa_step(&s->dfas[N->enum_dfa], &t->dst[0], b);
    if (N->has_str)
        for (uint32_t p = 0; p < N->npat; p++)
            dfa_step(&s->dfas[N->pats[p]], &t->dst[1 + p], b);
    if ((b & 0xC0) != 0x80) t->ncp++;
}

static void text_byte(const wx_schema_t *s, wx_text_t *t, uint8_t b) {
    if (t->node < 0) return;
    if (t->kind == XK_STRING) {
        text_feed_dfas(s, t, b);
        return;
    }
    if (is_ws(b)) {
        if (t->lexlen || t->any_digit || t->nst != XN_START) t->trailing = 1;
        return;
    }
    if (t->trailing) t->bad = 1;
    if (t->bad) return;
    text_feed_dfas(s, t, b);
    if (t->lexlen < sizeof t->lex) t->lex[t->lexlen++] = (char)b;
    else if (t->kind == XK_BOOLEAN) t->bad = 1;
    if (t->kind == XK_BOOLEAN) return;
    int dig = b >= '0' && b <= '9';
    int k = t->kind;
    switch (t->nst) {
        case XN_START:
            if (b == '+' || b == '-') { t->n_neg = b == '-'; t->nst = XN_SIGN; }
            else if (dig) { t->nst = XN_INT; num_digit(t, b, 0); }
            else if (b == '.' && k != XK_INTEGER) t->nst = XN_DOT;
            else t->bad = 1;
            break;
        case XN_SIGN:
            if (dig) { t->nst = XN_INT; num_digit(t, b, 0); }
            else if (b == '.' && k != XK_INTEGER) t->nst = XN_DOT;
            else t->bad = 1;
            break;
        case XN_INT:
            if (dig) num_digit(t, b, 0);
            else if (b == '.' && k != XK_INTEGER) t->nst = XN_DOT;
            else if ((b == 'e' || b == 'E') && k == XK_DOUBLE) t->nst = XN_E;
            else t->bad = 1;
            break;
        case XN_DOT:
        case XN_FRAC:
            if (dig) { t->nst = XN_FRAC; num_digit(t, b, 1); }
            else if ((b == 'e' || b == 'E') && k == XK_DOUBLE && t->any_digit)
                t->nst = XN_E;
            else t->bad = 1;
            break;
        case XN_E:
            if (b == '+' || b == '-') { t->n_exp_neg = b == '-'; t->nst = XN_ESIGN; }
            else if (dig) { t->nst = XN_EXP; t->n_e = b - '0'; }
            else t->bad = 1;
            break;
        case XN_ESIGN:
            if (dig) { t->nst = XN_EXP; t->n_e = b - '0'; }
            else t->bad = 1;
            break;
        case XN_EXP:
            if (dig) {
                if (t->n_e < 1000000000000000LL) t->n_e = t->n_e * 10 + (b - '0');
            } else {
                t->bad = 1;
            }
            break;
    }
}

static const char *KIND_NAME[5] = {"string", "boolean", "integer", "decimal",
                                   "double"};

static void text_end(wx_ctx_t *c, wx_text_t *t, uint32_t plen) {
    if (t->node < 0 || !RUNNING(c)) return;
    const wx_schema_t *s = c->s;
    const wd_node_t *N = &s->nodes[t->node];
    char msg[WD_ERR_MSG];
    int numeric = t->kind >= XK_INTEGER;
    if (t->kind == XK_BOOLEAN) {
        int ok = !t->bad && ((t->lexlen == 4 && !memcmp(t->lex, "true", 4)) ||
                             (t->lexlen == 5 && !memcmp(t->lex, "false", 5)) ||
                             (t->lexlen == 1 && (t->lex[0] == '1' ||
                                                  t->lex[0] == '0')));
        if (!ok) {
            snprintf(msg, sizeof msg, "'%.*s' no es un xs:boolean",
                     (int)t->lexlen, t->lex);
            violate(c, WE_TYPE, msg, plen);
            return;
        }
    } else if (numeric) {
        int ok = !t->bad && t->any_digit && t->nst != XN_E &&
                 t->nst != XN_ESIGN;
        if (!ok) {
            snprintf(msg, sizeof msg, "'%.*s' no es un xs:%s", (int)t->lexlen,
                     t->lex, KIND_NAME[t->kind]);
            violate(c, WE_TYPE, msg, plen);
            return;
        }
    }
    if (N->has_str) {
        if (N->min_len >= 0 && t->ncp < (uint32_t)N->min_len) {
            snprintf(msg, sizeof msg, "longitud %u < %d", (unsigned)t->ncp,
                     N->min_len);
            violate(c, WE_MIN_LENGTH, msg, plen);
        }
        if (N->max_len >= 0 && t->ncp > (uint32_t)N->max_len) {
            snprintf(msg, sizeof msg, "longitud %u > %d", (unsigned)t->ncp,
                     N->max_len);
            violate(c, WE_MAX_LENGTH, msg, plen);
        }
    }
    if (N->has_enum) {
        int ok = 0;
        if (numeric) {
            for (uint32_t k = 0; k < N->enum_nnums && !ok; k++)
                ok = cmp_text_num(t, &s->consts[N->enum_nums[k]]) == 0;
        } else {
            ok = N->enum_dfa >= 0 && t->dst[0] >= 0 &&
                 s->dfas[N->enum_dfa].accept[t->dst[0]] >= 0;
        }
        if (!ok) violate(c, WE_ENUM, "valor fuera de la enumeración", plen);
    }
    if (N->has_str) {
        for (uint32_t p = 0; p < N->npat; p++) {
            int32_t st = t->dst[1 + p];
            if (!(st >= 0 && s->dfas[N->pats[p]].accept[st] >= 0)) {
                snprintf(msg, sizeof msg, "no coincide con '%s'", N->pat_src[p]);
                violate(c, WE_PATTERN, msg, plen);
            }
        }
    }
    if (numeric) {
        for (uint32_t k = 0; k < N->nnum; k++) {
            const wd_const_t *K = &s->consts[N->num_consts[k]];
            if (N->num_ops[k] == WD_OP_MUL) continue;
            int cmp = cmp_text_num(t, K), bad = 0, code = 0;
            switch (N->num_ops[k]) {
                case WD_OP_GE: bad = cmp < 0; code = WE_MINIMUM; break;
                case WD_OP_GT: bad = cmp <= 0; code = WE_EXCLUSIVE_MINIMUM; break;
                case WD_OP_LE: bad = cmp > 0; code = WE_MAXIMUM; break;
                case WD_OP_LT: bad = cmp >= 0; code = WE_EXCLUSIVE_MAXIMUM; break;
                default: break;
            }
            if (bad) {
                snprintf(msg, sizeof msg, "%.*s fuera de rango (%s)",
                         (int)t->lexlen, t->lex, K->lexeme);
                violate(c, code, msg, plen);
            }
        }
    }
}

/* ---- Elementos ------------------------------------------------------------ */
static uint32_t sibling_index(wx_frame_t *p, const char *name, uint32_t n) {
    uint64_t h = 0xCBF29CE484222325ULL;
    for (uint32_t i = 0; i < n; i++) h = (h ^ (uint8_t)name[i]) * 0x100000001B3ULL;
    h = mix64(h) | 1;
    uint32_t pos = (uint32_t)(h % WX_SIB_SLOTS);
    for (uint32_t k = 0; k < WX_SIB_SLOTS; k++) {
        uint32_t q = (pos + k) % WX_SIB_SLOTS;
        if (p->sib_hash[q] == h) return ++p->sib_count[q];
        if (p->sib_hash[q] == 0) {
            p->sib_hash[q] = h;
            p->sib_count[q] = 1;
            return 1;
        }
    }
    return 0;
}

static void push_frame(wx_ctx_t *c, int32_t elem, uint32_t path_start) {
    wx_frame_t *f = &c->frames[c->depth++];
    if (c->depth > c->max_depth_seen) c->max_depth_seen = c->depth;
    f->elem = elem;
    f->dstate = 0;
    f->path_start = path_start;
    f->text_flag = 0;
    memset(f->sib_hash, 0, sizeof f->sib_hash);
    f->name_off = c->names_top;
    f->name_len = c->name_len;
    memcpy(&c->names[c->names_top], c->name, c->name_len);
    c->names_top += c->name_len;
    int32_t simple = -1;
    if (elem >= 0 && !c->s->elems[elem].complex) simple = c->s->elems[elem].simple;
    text_begin(c->s, &f->text, simple);
}

/* Fin del nombre de una etiqueta de apertura. */
static void open_begin(wx_ctx_t *c) {
    char msg[WD_ERR_MSG];
    const wx_schema_t *s = c->s;
    c->attr_seen = 0;
    c->nattr_hashes = 0;
    c->tag_skip = 1;
    if (c->done) { fatal(c, WD_MALFORMED, "más de un elemento raíz", c->tok_start); return; }
    if (c->depth + 1 > c->max_depth) {
        fatal(c, WD_DEPTH, "profundidad máxima superada", c->tok_start);
        return;
    }
    if (c->names_top + c->name_len > WX_NAME_POOL) {
        fatal(c, WD_LIMIT, "pila de nombres llena", c->tok_start);
        return;
    }
    uint32_t start = c->path_len;
    int32_t child = -1;
    if (c->depth == 0) {
        c->root_seen = 1;
        path_str(c, "/", 1);
        path_str(c, c->name, c->name_len);
        int32_t r = dfa_run(&s->dfas[s->root_names_dfa], c->name, c->name_len);
        if (r < 0) {
            push_frame(c, -1, start);
            snprintf(msg, sizeof msg, "elemento raíz <%.*s> no declarado",
                     (int)c->name_len, c->name);
            violate(c, WE_XML_ROOT, msg, c->path_len);
            return;
        }
        child = s->roots[r];
    } else {
        wx_frame_t *p = &c->frames[c->depth - 1];
        uint32_t k = sibling_index(p, c->name, c->name_len);
        char seg[24];
        int sn = snprintf(seg, sizeof seg, "[%u]", (unsigned)k);
        path_str(c, "/", 1);
        path_str(c, c->name, c->name_len);
        path_str(c, seg, (uint32_t)sn);
        if (p->elem >= 0) {
            const wx_elem_t *P = &s->elems[p->elem];
            if (!P->complex) {
                snprintf(msg, sizeof msg, "<%s> es de tipo simple y no admite "
                         "el hijo <%.*s>", P->name, (int)c->name_len, c->name);
                violate(c, WE_XML_CONTENT, msg, c->path_len);
            } else {
                int32_t sym = P->names_dfa >= 0
                    ? dfa_run(&s->dfas[P->names_dfa], c->name, c->name_len) : -1;
                if (sym < 0) {
                    snprintf(msg, sizeof msg, "<%.*s> no está permitido en <%s>",
                             (int)c->name_len, c->name, P->name);
                    violate(c, WE_XML_UNDECLARED, msg, c->path_len);
                } else {
                    const wd_dfa_t *d = &s->dfas[P->content_dfa];
                    int32_t ns = d->table[(uint32_t)p->dstate * d->ncls +
                                          d->classes[(uint8_t)sym]];
                    if (ns < 0) {
                        snprintf(msg, sizeof msg, "<%.*s> no se esperaba aquí "
                                 "en <%s> (modelo %s)", (int)c->name_len,
                                 c->name, P->name, P->content_src);
                        violate(c, WE_XML_CONTENT, msg, c->path_len);
                    } else {
                        p->dstate = ns;
                    }
                    child = P->child_elems[sym];
                }
            }
        }
    }
    push_frame(c, child, start);
    c->tag_skip = child < 0;
}

static void close_elem(wx_ctx_t *c) {
    char msg[WD_ERR_MSG];
    wx_frame_t *f = &c->frames[c->depth - 1];
    if (f->elem >= 0) {
        const wx_elem_t *E = &c->s->elems[f->elem];
        if (E->complex) {
            if (E->content_dfa >= 0 &&
                c->s->dfas[E->content_dfa].accept[f->dstate] < 0) {
                snprintf(msg, sizeof msg, "contenido incompleto de <%s> "
                         "(modelo %s)", E->name, E->content_src);
                violate(c, WE_XML_INCOMPLETE, msg, c->path_len);
            }
        } else if (E->simple >= 0) {
            text_end(c, &f->text, c->path_len);
        }
    }
    if (!RUNNING(c)) return;
    c->names_top = f->name_off;
    c->path_len = f->path_start;
    c->depth--;
    if (c->depth == 0) c->done = 1;
}

static void open_end(wx_ctx_t *c, int empty) {
    char msg[WD_ERR_MSG];
    c->ntokens++;
    if (!c->tag_skip) {
        const wx_elem_t *E = &c->s->elems[c->frames[c->depth - 1].elem];
        uint64_t miss = E->attr_required & ~c->attr_seen;
        for (uint32_t i = 0; miss; i++, miss >>= 1) {
            if (miss & 1u) {
                snprintf(msg, sizeof msg, "falta el atributo requerido '%s'",
                         E->attrs[i].name);
                violate(c, WE_XML_ATTR_REQUIRED, msg, c->path_len);
            }
        }
    }
    if (empty && RUNNING(c)) close_elem(c);
}

static void close_tag(wx_ctx_t *c) {
    c->ntokens++;
    if (c->depth == 0) {
        fatal(c, WD_MALFORMED, "etiqueta de cierre sin apertura", c->tok_start);
        return;
    }
    wx_frame_t *f = &c->frames[c->depth - 1];
    if (f->name_len != c->name_len ||
        memcmp(&c->names[f->name_off], c->name, c->name_len)) {
        char msg[WD_ERR_MSG];
        snprintf(msg, sizeof msg, "</%.*s> no cierra <%.*s>", (int)c->name_len,
                 c->name, (int)f->name_len, &c->names[f->name_off]);
        fatal(c, WD_MALFORMED, msg, c->tok_start);
        return;
    }
    close_elem(c);
}

/* ---- Atributos ----------------------------------------------------------- */
static void attr_begin(wx_ctx_t *c) {
    char msg[WD_ERR_MSG];
    uint64_t h = 0xCBF29CE484222325ULL;
    for (uint32_t i = 0; i < c->name_len; i++)
        h = (h ^ (uint8_t)c->name[i]) * 0x100000001B3ULL;
    h = mix64(h);
    for (uint32_t i = 0; i < c->nattr_hashes; i++) {
        if (c->attr_hashes[i] == h) {
            fatal(c, WD_MALFORMED, "atributo duplicado", c->tok_start);
            return;
        }
    }
    if (c->nattr_hashes < 32) c->attr_hashes[c->nattr_hashes++] = h;
    c->cur_attr = -2;
    c->attr_text.node = -1;
    if (c->tag_skip) return;
    const wx_elem_t *E = &c->s->elems[c->frames[c->depth - 1].elem];
    int32_t idx = E->attr_dfa >= 0
        ? dfa_run(&c->s->dfas[E->attr_dfa], c->name, c->name_len) : -1;
    c->attr_path = c->path_len;
    path_str(c, "/@", 2);
    path_str(c, c->name, c->name_len);
    if (idx < 0) {
        snprintf(msg, sizeof msg, "atributo '%.*s' no declarado en <%s>",
                 (int)c->name_len, c->name, E->name);
        violate(c, WE_XML_ATTR, msg, c->path_len);
        c->path_len = c->attr_path;
        c->cur_attr = -1;
        return;
    }
    c->attr_seen |= (uint64_t)1 << idx;
    c->cur_attr = idx;
    text_begin(c->s, &c->attr_text, E->attrs[idx].node);
}

static void attr_end(wx_ctx_t *c) {
    if (c->cur_attr >= 0) {
        text_end(c, &c->attr_text, c->path_len);
        c->path_len = c->attr_path;
    }
    c->cur_attr = -2;
}

/* ---- Texto ---------------------------------------------------------------- */
static void content_byte(wx_ctx_t *c, uint8_t b) {
    if (c->depth == 0) {
        if (!is_ws(b)) fatal(c, WD_MALFORMED, "texto fuera del elemento raíz",
                             c->offset);
        return;
    }
    wx_frame_t *f = &c->frames[c->depth - 1];
    if (f->elem < 0) return;
    const wx_elem_t *E = &c->s->elems[f->elem];
    if (E->complex) {
        if (!E->mixed && !is_ws(b) && !f->text_flag) {
            char msg[WD_ERR_MSG];
            f->text_flag = 1;
            c->tok_start = c->offset;
            snprintf(msg, sizeof msg, "<%s> no admite texto (contenido solo "
                     "de elementos)", E->name);
            violate(c, WE_XML_TEXT, msg, c->path_len);
        }
        return;
    }
    text_byte(c->s, &f->text, b);
}

static void emit_cp(wx_ctx_t *c, uint32_t cp) {
    uint8_t buf[4];
    int n;
    if (cp < 0x80) { buf[0] = (uint8_t)cp; n = 1; }
    else if (cp < 0x800) {
        buf[0] = (uint8_t)(0xC0 | (cp >> 6));
        buf[1] = (uint8_t)(0x80 | (cp & 0x3F)); n = 2;
    } else if (cp < 0x10000) {
        buf[0] = (uint8_t)(0xE0 | (cp >> 12));
        buf[1] = (uint8_t)(0x80 | ((cp >> 6) & 0x3F));
        buf[2] = (uint8_t)(0x80 | (cp & 0x3F)); n = 3;
    } else {
        buf[0] = (uint8_t)(0xF0 | (cp >> 18));
        buf[1] = (uint8_t)(0x80 | ((cp >> 12) & 0x3F));
        buf[2] = (uint8_t)(0x80 | ((cp >> 6) & 0x3F));
        buf[3] = (uint8_t)(0x80 | (cp & 0x3F)); n = 4;
    }
    for (int i = 0; i < n; i++) {
        if (c->ent_attr) text_byte(c->s, &c->attr_text, buf[i]);
        else content_byte(c, buf[i]);
    }
}

static void entity_end(wx_ctx_t *c) {
    const char *e = c->ent;
    uint32_t n = c->ent_len;
    uint32_t cp = 0;
    if (n == 2 && !memcmp(e, "lt", 2)) cp = '<';
    else if (n == 2 && !memcmp(e, "gt", 2)) cp = '>';
    else if (n == 3 && !memcmp(e, "amp", 3)) cp = '&';
    else if (n == 4 && !memcmp(e, "quot", 4)) cp = '"';
    else if (n == 4 && !memcmp(e, "apos", 4)) cp = '\'';
    else if (n >= 2 && e[0] == '#') {
        int hex = e[1] == 'x';
        uint32_t i = hex ? 2 : 1;
        if (i >= n) { fatal(c, WD_MALFORMED, "referencia de carácter inválida", c->offset); return; }
        for (; i < n; i++) {
            char ch = e[i];
            uint32_t d;
            if (ch >= '0' && ch <= '9') d = (uint32_t)(ch - '0');
            else if (hex && ch >= 'a' && ch <= 'f') d = (uint32_t)(ch - 'a' + 10);
            else if (hex && ch >= 'A' && ch <= 'F') d = (uint32_t)(ch - 'A' + 10);
            else { fatal(c, WD_MALFORMED, "referencia de carácter inválida", c->offset); return; }
            cp = cp * (hex ? 16u : 10u) + d;
            if (cp > 0x10FFFF) break;
        }
        if (cp == 0 || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
            fatal(c, WD_MALFORMED, "referencia de carácter inválida", c->offset);
            return;
        }
    } else {
        fatal(c, WD_MALFORMED, "entidad no definida (sin DTD no hay entidades: "
              "mitigación XXE)", c->offset);
        return;
    }
    emit_cp(c, cp);
}

static int name_start(uint8_t b) {
    return (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') || b == '_';
}
static int name_char(uint8_t b) {
    return name_start(b) || (b >= '0' && b <= '9') || b == '-' || b == '.' ||
           b == ':';
}

static void name_push(wx_ctx_t *c, uint8_t b) {
    if (c->name_len < WX_NAME_MAX) c->name[c->name_len++] = (char)b;
    else fatal(c, WD_LIMIT, "nombre demasiado largo", c->tok_start);
}

/* ---- Bucle principal -------------------------------------------------------- */
static void step(wx_ctx_t *c, uint8_t b) {
    switch (c->lx) {
        case X_CONTENT:
            if (b == '<') { c->tok_start = c->offset; c->lx = X_LT; }
            else if (b == '&') { c->ent_attr = 0; c->ent_len = 0; c->lx = X_ENT; }
            else content_byte(c, b);
            break;
        case X_ENT:
            if (b == ';') {
                c->lx = c->ent_attr ? X_ATTR_VAL : X_CONTENT;
                entity_end(c);
            } else if (c->ent_len < 11 && b != '&' && b != '<' && !is_ws(b)) {
                c->ent[c->ent_len++] = (char)b;
            } else {
                fatal(c, WD_MALFORMED, "'&' sin referencia de entidad válida",
                      c->offset);
            }
            break;
        case X_LT:
            if (b == '/') { c->name_len = 0; c->lx = X_CLOSE_NAME; }
            else if (b == '?') { c->dashes = 0; c->lx = X_PI; }
            else if (b == '!') c->lx = X_BANG1;
            else if (name_start(b)) { c->name_len = 0; name_push(c, b); c->lx = X_OPEN_NAME; }
            else fatal(c, WD_MALFORMED, "etiqueta inválida", c->tok_start);
            break;
        case X_OPEN_NAME:
            if (name_char(b)) name_push(c, b);
            else if (is_ws(b)) { open_begin(c); c->lx = X_ATTRS; }
            else if (b == '/') { open_begin(c); c->lx = X_EMPTY_END; }
            else if (b == '>') { open_begin(c); c->lx = X_CONTENT; open_end(c, 0); }
            else fatal(c, WD_MALFORMED, "etiqueta inválida", c->tok_start);
            break;
        case X_ATTRS:
            if (is_ws(b)) break;
            if (b == '/') c->lx = X_EMPTY_END;
            else if (b == '>') { c->lx = X_CONTENT; open_end(c, 0); }
            else if (name_start(b)) { c->name_len = 0; name_push(c, b); c->lx = X_ATTR_NAME; }
            else fatal(c, WD_MALFORMED, "atributo inválido", c->tok_start);
            break;
        case X_ATTR_NAME:
            if (name_char(b)) name_push(c, b);
            else if (is_ws(b)) c->lx = X_ATTR_EQ;
            else if (b == '=') c->lx = X_ATTR_Q;
            else fatal(c, WD_MALFORMED, "atributo sin =", c->tok_start);
            break;
        case X_ATTR_EQ:
            if (is_ws(b)) break;
            if (b == '=') c->lx = X_ATTR_Q;
            else fatal(c, WD_MALFORMED, "atributo sin =", c->tok_start);
            break;
        case X_ATTR_Q:
            if (is_ws(b)) break;
            if (b == '"' || b == '\'') {
                c->quote = b;
                attr_begin(c);
                c->lx = X_ATTR_VAL;
            } else {
                fatal(c, WD_MALFORMED, "valor de atributo sin comillas", c->tok_start);
            }
            break;
        case X_ATTR_VAL:
            if (b == c->quote) { attr_end(c); c->lx = X_ATTRS; }
            else if (b == '<') fatal(c, WD_MALFORMED, "'<' no permitido en un "
                                     "valor de atributo", c->tok_start);
            else if (b == '&') { c->ent_attr = 1; c->ent_len = 0; c->lx = X_ENT; }
            else if (c->cur_attr >= 0) text_byte(c->s, &c->attr_text, b);
            break;
        case X_EMPTY_END:
            if (is_ws(b)) break;
            if (b == '>') { c->lx = X_CONTENT; open_end(c, 1); }
            else fatal(c, WD_MALFORMED, "etiqueta inválida", c->tok_start);
            break;
        case X_CLOSE_NAME:
            if (c->name_len == 0 ? name_start(b) : name_char(b)) name_push(c, b);
            else if (c->name_len && is_ws(b)) c->lx = X_CLOSE_WS;
            else if (c->name_len && b == '>') { c->lx = X_CONTENT; close_tag(c); }
            else fatal(c, WD_MALFORMED, "etiqueta de cierre inválida", c->tok_start);
            break;
        case X_CLOSE_WS:
            if (is_ws(b)) break;
            if (b == '>') { c->lx = X_CONTENT; close_tag(c); }
            else fatal(c, WD_MALFORMED, "etiqueta de cierre inválida", c->tok_start);
            break;
        case X_PI:
            if (b == '>' && c->dashes) c->lx = X_CONTENT;
            c->dashes = (b == '?');
            break;
        case X_BANG1:
            if (b == '-') c->lx = X_BANG2;
            else if (b == 'D' || b == 'd')
                fatal(c, WD_MALFORMED, "DOCTYPE rechazado (mitigación XXE)", c->tok_start);
            else fatal(c, WD_MALFORMED, "declaración <! no soportada", c->tok_start);
            break;
        case X_BANG2:
            if (b == '-') { c->dashes = 0; c->lx = X_COMMENT; }
            else fatal(c, WD_MALFORMED, "declaración <! no soportada", c->tok_start);
            break;
        case X_COMMENT:
            if (b == '>' && c->dashes >= 2) c->lx = X_CONTENT;
            else if (b == '-') c->dashes++;
            else c->dashes = 0;
            break;
    }
}

wd_status_t wx_feed(wx_ctx_t *c, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n && RUNNING(c); i++, c->offset++) {
        uint8_t b = p[i];
        /* AFD UTF-8 sobre todos los bytes (como compiler/utf8.py) */
        if (c->u8_left) {
            if (b < c->u8_lo || b > c->u8_hi) {
                fatal(c, WD_UTF8, "byte de continuación UTF-8 inválido", c->offset);
                break;
            }
            c->u8_left--;
            c->u8_lo = 0x80;
            c->u8_hi = 0xBF;
        } else if (b >= 0x80) {
            if (b >= 0xC2 && b <= 0xDF) { c->u8_left = 1; c->u8_lo = 0x80; c->u8_hi = 0xBF; }
            else if (b == 0xE0) { c->u8_left = 2; c->u8_lo = 0xA0; c->u8_hi = 0xBF; }
            else if ((b >= 0xE1 && b <= 0xEC) || b == 0xEE || b == 0xEF) { c->u8_left = 2; c->u8_lo = 0x80; c->u8_hi = 0xBF; }
            else if (b == 0xED) { c->u8_left = 2; c->u8_lo = 0x80; c->u8_hi = 0x9F; }
            else if (b == 0xF0) { c->u8_left = 3; c->u8_lo = 0x90; c->u8_hi = 0xBF; }
            else if (b >= 0xF1 && b <= 0xF3) { c->u8_left = 3; c->u8_lo = 0x80; c->u8_hi = 0xBF; }
            else if (b == 0xF4) { c->u8_left = 3; c->u8_lo = 0x80; c->u8_hi = 0x8F; }
            else { fatal(c, WD_UTF8, "byte UTF-8 inválido", c->offset); break; }
            if (c->lx != X_CONTENT && c->lx != X_ATTR_VAL && c->lx != X_COMMENT &&
                c->lx != X_PI) {
                fatal(c, WD_MALFORMED, "byte no ASCII en una etiqueta", c->offset);
                break;
            }
        }
        step(c, b);
    }
    return c->status;
}

wd_status_t wx_finish(wx_ctx_t *c) {
    if (!RUNNING(c)) return c->status;
    if (c->u8_left) fatal(c, WD_UTF8, "secuencia UTF-8 incompleta", c->offset);
    else if (c->lx != X_CONTENT) fatal(c, WD_MALFORMED, "etiqueta, comentario o "
                                       "entidad sin cerrar", c->tok_start);
    else if (c->depth) fatal(c, WD_MALFORMED, "etiqueta sin cerrar", c->offset);
    else if (!c->root_seen) fatal(c, WD_MALFORMED, "documento sin elemento raíz",
                                  c->offset);
    if (RUNNING(c)) c->status = c->weight == 0 ? WD_VALID : WD_INVALID;
    return c->status;
}
