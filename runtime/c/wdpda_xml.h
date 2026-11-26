/* wdpda_xml.h — Runtime del WDPDA para XML (extensión XSD).
 *
 * Misma idea que el runtime JSON: AFD léxico byte a byte (etiquetas,
 * atributos, texto, entidades predefinidas; DOCTYPE rechazado), DPDA de
 * buena formación (pila de nombres acotada) y, por elemento abierto, un
 * marco con (declaración, estado del AFD de contenido de Glushkov,
 * máscara de atributos, verificador del texto). Sin malloc.
 * Especificación ejecutable equivalente: compiler/xml_engine.py.
 */
#ifndef WDPDA_XML_H
#define WDPDA_XML_H

#include "wdpda_rt.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifndef WX_NAME_MAX
#define WX_NAME_MAX 128         /* longitud máxima de un nombre */
#endif
#ifndef WX_NAME_POOL
#define WX_NAME_POOL 16384      /* nombres abiertos en la pila */
#endif
#ifndef WX_SIB_SLOTS
#define WX_SIB_SLOTS 32         /* contadores de hermanos por marco */
#endif

typedef struct {
    const char *name;
    int32_t node;
    uint8_t required;
} wx_attr_t;

typedef struct {
    const char *name;
    int32_t simple;           /* nodo del tipo simple o -1 */
    uint8_t complex, mixed;
    int32_t content_dfa;      /* -1 = contenido vacío */
    uint32_t nchild;
    const int32_t *child_elems;
    int32_t names_dfa;        /* trie de nombres de hijos -> símbolo */
    uint32_t nattrs;
    const wx_attr_t *attrs;
    int32_t attr_dfa;
    uint64_t attr_required;
    const char *content_src;
} wx_elem_t;

typedef struct {
    const char *name;
    uint32_t nelems;
    const wx_elem_t *elems;
    uint32_t nroots;
    const int32_t *roots;
    int32_t root_names_dfa;
    uint32_t nnodes;
    const wd_node_t *nodes;
    uint32_t ndfas;
    const wd_dfa_t *dfas;
    uint32_t nconsts;
    const wd_const_t *consts;
    uint32_t formal_states, formal_transitions;
} wx_schema_t;

/* Verificador de texto de un tipo simple (streaming). */
typedef struct {
    int32_t node;
    uint8_t kind;
    uint8_t bad, trailing, ws_pending;
    int32_t dst[1 + WD_MAX_PATS];
    uint32_t ncp;
    /* número XSD */
    uint8_t nst, n_neg, n_exp_neg, any_digit;
    char n_digits[WD_MAX_DIGITS];
    uint32_t n_nd;
    uint64_t n_zeros, n_dropped, n_frac;
    int64_t n_e;
    char lex[48];
    uint32_t lexlen;
} wx_text_t;

typedef struct {
    int32_t elem;
    int32_t dstate;
    uint32_t name_off, name_len;
    uint32_t path_start;
    uint8_t text_flag;
    uint64_t sib_hash[WX_SIB_SLOTS];
    uint32_t sib_count[WX_SIB_SLOTS];
    wx_text_t text;
} wx_frame_t;

typedef struct {
    const wx_schema_t *s;
    int fail_fast;
    uint32_t max_depth;
    wd_status_t status;
    char fatal_msg[WD_ERR_MSG];
    uint64_t fatal_offset;
    uint64_t offset, tok_start, ntokens, weight;
    wd_error_t errors[WD_MAX_ERRORS];
    uint32_t nerrors;
    uint64_t error_count;
    uint32_t lanes_peak;      /* siempre 0 (sin producto en XML) */
    /* lexer */
    uint8_t lx, quote, dashes, root_seen, done;
    uint8_t u8_left, u8_lo, u8_hi;
    uint8_t ent_attr;         /* la entidad está en un atributo */
    char ent[16];
    uint32_t ent_len;
    char name[WX_NAME_MAX];
    uint32_t name_len;
    uint8_t name_over;
    /* atributos del tag en curso */
    int32_t cur_attr;         /* índice declarado o -1 */
    uint64_t attr_seen;
    uint64_t attr_hashes[32];
    uint32_t nattr_hashes;
    uint32_t attr_path;       /* longitud de ruta antes de "/@nombre" */
    wx_text_t attr_text;
    uint8_t tag_skip;         /* elemento abierto sin declaración */
    /* pila */
    wx_frame_t frames[WD_MAX_DEPTH];
    uint32_t depth;
    uint32_t max_depth_seen;
    char names[WX_NAME_POOL];
    uint32_t names_top;
    char path[WD_PATH_BYTES];
    uint32_t path_len;
} wx_ctx_t;

void wx_init(wx_ctx_t *c, const wx_schema_t *s, uint32_t max_depth,
             int fail_fast);
wd_status_t wx_feed(wx_ctx_t *c, const uint8_t *p, size_t n);
wd_status_t wx_finish(wx_ctx_t *c);
void wx_abort(wx_ctx_t *c, wd_status_t st, const char *msg);
size_t wx_ctx_size(void);

#ifdef __cplusplus
}
#endif
#endif
