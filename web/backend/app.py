"""Fase 7 — Backend web: API REST + WebSocket.

Ejecutar en local (desde la raíz del repo):

    python -m uvicorn web.backend.app:app --reload --port 8000

REST
  GET  /api/health                     estado, almacén NoSQL, herramientas
  GET  /api/examples                   esquemas y payloads de ejemplo
  POST /api/compile                    esquema -> IR, δ, AFD, código C/Rust
  GET  /api/download/{id}.zip          proyecto C (+ Rust) generado
  POST /api/validate                   valida (motor Python con traza, o C)
  POST /api/tree                       árbol de derivación (DOT)
  GET  /api/cases[?categoria=]         casos de prueba (NoSQL)
  POST /api/cases                      crea / actualiza un caso
  POST /api/cases/{id}/run             ejecuta un caso y guarda métricas
  POST /api/cases/run-all              ejecuta todos los casos
WebSocket
  /ws/stream   validación en tiempo real por bloques (estado tras cada bloque)
  /ws/bench    comparación en vivo WDPDA vs DOM vs jsonschema
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
import os
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from compiler import export  # noqa: E402
from compiler.codegen_c import generate_c, write_c_project  # noqa: E402
from compiler.engine import Validator  # noqa: E402
from compiler.formal import build_formal  # noqa: E402
from compiler.grammar import LL1Parser  # noqa: E402
from compiler.wdpda import compile_schema  # noqa: E402
from compiler.xml_engine import XmlValidator  # noqa: E402
from compiler.xsd import compile_xsd  # noqa: E402

from . import sandbox  # noqa: E402
from .store import open_store  # noqa: E402

app = FastAPI(title="WDPDA — Validación de esquemas JSON/XML con autómatas "
              "de pila ponderados", version="1.0")

# El frontend puede servirse desde otro origen (bucket S3 en AWS): se
# permiten solo los orígenes listados en CORS_ORIGINS (separados por
# comas). Sin la variable, solo el mismo origen (desarrollo local).
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",")
            if o.strip()]
if _origins:
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=_origins,
                       allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])
STORE = open_store()
CACHE: dict[str, dict] = {}  # id -> {"ir":..., "schema":..., "format":...}
FRONT = ROOT / "web" / "frontend"


class CompileReq(BaseModel):
    schema_text: str
    format: str = "json"  # json | xsd
    name: str = "schema"


class ValidateReq(BaseModel):
    id: str
    payload: str
    engine: str = "py"  # py | c
    chunk: int | None = None
    trace: bool = False
    fail_fast: bool = False


class TreeReq(BaseModel):
    payload: str
    format: str = "json"


class CaseReq(BaseModel):
    id: str
    categoria: str
    formato: str = "json"
    nombre: str = ""
    esquema: str
    payload: str
    esperado: dict = {}


# ---------------------------------------------------------------- utilidades
def compile_any(text: str, fmt: str) -> dict:
    data = text.encode("utf-8")
    if len(data) > sandbox.MAX_SCHEMA:
        raise HTTPException(413, "esquema demasiado grande")
    try:
        return compile_xsd(data) if fmt == "xsd" else compile_schema(data)
    except ValueError as exc:  # SchemaError, XsdError, CompileError...
        raise HTTPException(422, str(exc)) from exc


def get_entry(cid: str) -> dict:
    if cid not in CACHE:
        raise HTTPException(404, "esquema no compilado (compílalo primero)")
    return CACHE[cid]


def make_validator(ir: dict, trace: bool = False, fail_fast: bool = False):
    if ir.get("format") == "xml":
        return XmlValidator(ir, fail_fast=fail_fast, trace=trace)
    return Validator(ir, fail_fast=fail_fast, trace=trace)


def tools() -> dict:
    from compiler.codegen_c import gcc_env
    return {"gcc": shutil.which("gcc", path=gcc_env()["PATH"]) is not None,
            "cargo": shutil.which("cargo") is not None}


# ---------------------------------------------------------------- REST
@app.get("/api/health")
def health():
    return {"ok": True, "store": STORE.kind, "cases": STORE.count(),
            **tools()}


@app.get("/api/examples")
def examples():
    out = []
    for f in sorted((ROOT / "tests" / "schemas").glob("*.json")):
        out.append({"name": f.stem, "format": "json",
                    "schema": f.read_text(encoding="utf-8")})
    for f in sorted((ROOT / "tests" / "schemas_xsd").glob("*.xsd")):
        out.append({"name": f.stem + " (XSD)", "format": "xsd",
                    "schema": f.read_text(encoding="utf-8")})
    ex = ROOT / "avance_mvp" / "example_schema.json"
    out.insert(0, {"name": "ejemplo MVP", "format": "json",
                   "schema": ex.read_text(encoding="utf-8")})
    return out


@app.post("/api/compile")
def compile_endpoint(req: CompileReq):
    t0 = time.perf_counter()
    ir = compile_any(req.schema_text, req.format)
    ms = (time.perf_counter() - t0) * 1000
    cid = sandbox.ir_hash(ir)
    CACHE[cid] = {"ir": ir, "schema": req.schema_text, "format": req.format,
                  "name": req.name}
    resp = {"id": cid, "format": ir.get("format", "json"),
            "compile_ms": round(ms, 2), "stats": ir["stats"],
            "formal": ir["formal"], "classification": ir["classification"],
            "warnings": ir.get("warnings", []), "limits": ir["limits"],
            "c_code": generate_c(ir, req.name)[:200000]}
    dfas = []
    for d in ir["dfas"]:
        au = export.dfa_auto(d, f"AFD {d['id']} ({d['kind']})",
                             d.get("symbols"))
        dfas.append({"id": d["id"], "kind": d["kind"],
                     "source": d["source"] if isinstance(d["source"], str)
                     else ", ".join(map(str, d["source"])),
                     "states": d["states"],
                     "states_before_min": d["states_before_min"],
                     "dot": export.to_dot(au) if d["states"] <= 80 else None})
    resp["dfas"] = dfas
    if ir.get("format") == "xml":
        resp["elements"] = [{k: e[k] for k in ("id", "name", "complex",
                                               "content_src", "child_names")}
                            for e in ir["elements"]]
        resp["upa"] = ir.get("upa", [])
    else:
        f = build_formal(ir)
        view = export.formal_auto(f, "WDPDA (⊤ colapsado)", collapse_top=True,
                                  drop_weighted=False)
        resp["dot_wdpda"] = export.to_dot(view)
        resp["dot_wdpda_simple"] = export.to_dot(export.formal_auto(
            f, "WDPDA (peso 0)", collapse_top=True))
        resp["delta"] = [{"src": t.src, "sym": t.sym, "pop": t.pop,
                          "push": list(t.push), "dst": t.dst,
                          "weight": t.weight, "guard": t.guard,
                          "action": t.action} for t in f.trans]
        resp["nodes"] = [{"id": n["id"], "ptr": n["ptr"], "desc": n["desc"],
                          "checks": n["checks"]} for n in ir["nodes"]]
        if ir["formal"]["product_nodes"]:
            resp["product_note"] = (
                "Los nodos " + ", ".join(f"n{i}" for i in
                                         ir["formal"]["product_nodes"]) +
                " usan combinadores: en ejecución se instancian varios "
                "carriles (producto de autómatas de pila visibles).")
    return resp


@app.get("/api/download/{cid}.zip")
def download(cid: str):
    e = get_entry(cid)
    ir = e["ir"]
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        write_c_project(ir, Path(tmp) / "c", e["name"])
        if ir.get("format") != "xml":
            from compiler.codegen_rust import write_rust_project
            write_rust_project(ir, Path(tmp) / "rust", e["name"])
        (Path(tmp) / f"{e['name']}.ir.json").write_text(
            json.dumps(ir, ensure_ascii=False, indent=1), encoding="utf-8")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in Path(tmp).rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(tmp))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{e["name"]}_wdpda.zip"'})


@app.post("/api/validate")
def validate_endpoint(req: ValidateReq):
    e = get_entry(req.id)
    ir = e["ir"]
    data = req.payload.encode("utf-8")
    if len(data) > sandbox.MAX_PAYLOAD:
        raise HTTPException(413, "payload demasiado grande")
    if req.engine == "c":
        if not tools()["gcc"]:
            raise HTTPException(503, "gcc no disponible en el servidor")
        return {"engine": "c", **sandbox.run_validator(
            ir, data, req.chunk, fail_fast=req.fail_fast)}
    t0 = time.perf_counter()
    v = make_validator(ir, trace=req.trace and len(data) <= 20000,
                       fail_fast=req.fail_fast)
    r = v.validate(data, req.chunk)
    out = {"engine": "py", "seconds": time.perf_counter() - t0,
           **r.to_dict()}
    if v.trace is not None:
        out["trace"] = v.trace[:2000]
    return out


class GenReq(BaseModel):
    id: str
    invalid: bool = False
    seed: int = 0


@app.post("/api/generate")
def generate(req: GenReq):
    """Fase 5: payload válido (o mutado) generado desde el autómata."""
    ir = get_entry(req.id)["ir"]
    if ir.get("format") == "xml":
        from compiler.xml_gen import gen_xml, mutate_xml
        x = gen_xml(ir, req.seed)
        return {"payload": mutate_xml(x, req.seed) if req.invalid else x}
    from compiler.payload_gen import gen_valid, mutate, to_json
    v = gen_valid(ir, req.seed)
    if req.invalid:
        v, desc = mutate(ir, v, req.seed)
        return {"payload": to_json(v, indent=2), "mutation": desc}
    return {"payload": to_json(v, indent=2)}


@app.post("/api/tree")
def tree_endpoint(req: TreeReq):
    data = req.payload.encode("utf-8")
    if len(data) > 4000:
        raise HTTPException(413, "para el árbol de derivación use un payload "
                            "de hasta 4000 bytes")
    try:
        if req.format == "xml":
            from compiler.grammars import XML_GRAMMAR
            from compiler.xsd import xml_tokens_for_parse
            p = LL1Parser(XML_GRAMMAR)
            tree = p.parse(xml_tokens_for_parse(data))
        else:
            from compiler.grammars import JSON_GRAMMAR
            from compiler.json_lexer import JsonTokenizer
            p = LL1Parser(JSON_GRAMMAR)
            tree = p.parse(JsonTokenizer().tokenize(data))
    except ValueError as exc:
        raise HTTPException(422, f"no se pudo derivar: {exc}") from exc
    return {"dot": export.tree_to_dot(tree), "text": tree.to_text(),
            "nodes": tree.count(), "steps": p.steps}


# ---------------------------------------------------------------- casos
def _jsonable(doc: dict) -> dict:
    return json.loads(json.dumps(doc, default=str))


@app.get("/api/cases")
def list_cases(categoria: str | None = None):
    return [_jsonable(d) for d in STORE.list(categoria)]


@app.post("/api/cases")
def put_case(req: CaseReq):
    doc = {"_id": req.id, "categoria": req.categoria, "formato": req.formato,
           "nombre": req.nombre, "esquema": req.esquema,
           "payload": req.payload, "esperado": req.esperado}
    return _jsonable(STORE.upsert(doc))


def run_case(doc: dict) -> dict:
    fmt = "xsd" if doc.get("formato") in ("xml", "xsd") else "json"
    ir = compile_any(doc["esquema"], fmt)
    from .seed import payload_of  # inline, base64 o receta (> 400 KB)
    data = payload_of(doc)
    metr = {}
    t0 = time.perf_counter()
    r = make_validator(ir).validate(data)
    metr["py"] = {"status": r.status, "weight": None if r.weight ==
                  float("inf") else r.weight, "seconds":
                  time.perf_counter() - t0,
                  "errors": [e.to_dict() for e in r.errors][:5]}
    if tools()["gcc"]:
        c = sandbox.run_validator(ir, data)
        metr["c"] = {k: c.get(k) for k in ("status", "weight", "mb_s",
                                           "cycles_per_byte", "max_depth",
                                           "ctx_bytes", "seconds")}
        metr["c"]["errors"] = c.get("errors", [])[:5]
    exp = doc.get("esperado", {}).get("status")
    metr["coincide"] = exp is None or metr["py"]["status"] == exp and (
        "c" not in metr or metr["c"]["status"] == exp)
    STORE.update_metrics(doc["_id"], metr)
    return metr


@app.post("/api/cases/{cid}/run")
def run_one(cid: str):
    doc = STORE.get(cid)
    if not doc:
        raise HTTPException(404, "caso no encontrado")
    return run_case(doc)


@app.post("/api/cases/run-all")
def run_all(categoria: str | None = None):
    out = {}
    for d in STORE.list(categoria):
        try:
            out[d["_id"]] = run_case(d)
        except HTTPException as exc:
            out[d["_id"]] = {"error": exc.detail}
    ok = sum(1 for v in out.values() if v.get("coincide"))
    return {"total": len(out), "coinciden": ok, "resultados": out}


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket):
    """Validación en tiempo real: el cliente envía {"id":...} y luego
    bloques de texto; tras cada bloque se responde el estado del WDPDA."""
    await ws.accept()
    try:
        hello = json.loads(await ws.receive_text())
        e = get_entry(hello["id"])
        v = make_validator(e["ir"])
        while True:
            msg = await ws.receive_text()
            m = json.loads(msg)
            if m.get("end"):
                r = v.finish()
                await ws.send_text(json.dumps({"final": True, **r.to_dict()},
                                              ensure_ascii=False))
                break
            v.feed(m["chunk"].encode("utf-8"))
            levels = getattr(v, "levels", None)
            frames = getattr(v, "frames", None)
            depth = len(levels) if levels is not None else len(frames or [])
            if levels is not None:  # peso en curso: raíz + violaciones en pila
                weight = v.doc_weight + sum(ln.viol for lv in levels
                                            for ln in lv.lanes)
            else:
                weight = v.weight
            await ws.send_text(json.dumps({
                "bytes": v.nbytes, "depth": depth, "weight": weight,
                "error_count": len(v.errors),
                "errors": [x.to_dict() for x in v.errors][-5:],
                "status": v.status or "RUNNING",
                "fatal": v.fatal_message}, ensure_ascii=False))
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await ws.send_text(json.dumps({"error": exc.detail}))
    await ws.close()


@app.websocket("/ws/bench")
async def ws_bench(ws: WebSocket):
    """Comparación en vivo: para cada tamaño genera un payload válido del
    esquema y mide WDPDA-C, DOM-C y jsonschema; envía cada métrica en
    cuanto está lista."""
    await ws.accept()
    try:
        req = json.loads(await ws.receive_text())
        e = get_entry(req["id"])
        ir = e["ir"]
        if ir.get("format") == "xml":
            await ws.send_text(json.dumps({"error": "la comparación en vivo "
                                           "usa esquemas JSON"}))
            return
        from compiler.payload_gen import gen_valid, to_json
        docs = [to_json(gen_valid(ir, s)) for s in range(200)]
        loop = asyncio.get_running_loop()
        for kb in req.get("sizes", [64, 512, 2048]):
            parts, total, i = [], 0, 0
            while total < kb * 1024:
                parts.append(docs[i % len(docs)])
                total += len(parts[-1]) + 1
                i += 1
            payload = ("[" + ",".join(parts) + "]").encode()
            # El esquema raíz valida cada documento: se envuelve en un
            # arreglo con items = esquema para medir sobre un lote (los
            # $ref locales "#/..." pasan a "#/items/...").
            lote = compile_schema(json.dumps(
                {"type": "array", "items": _rebase_refs(
                    json.loads(e["schema"]))},
                ensure_ascii=False).encode())
            await ws.send_text(json.dumps({"size_kb": kb, "bytes":
                                           len(payload), "phase": "start"}))
            c = await loop.run_in_executor(None, lambda: sandbox.run_validator(
                lote, payload, bench=5))
            await ws.send_text(json.dumps({"size_kb": kb, "impl": "WDPDA (C)",
                                           "mb_s": c["mb_s"],
                                           "cycles_per_byte":
                                           c["cycles_per_byte"],
                                           "allocations": 0,
                                           "memory": c["ctx_bytes"],
                                           "status": c["status"]}))
            b = await loop.run_in_executor(None, lambda: sandbox.run_baseline(
                lote, payload, 5))
            await ws.send_text(json.dumps({"size_kb": kb, "impl": "DOM (C)",
                                           "mb_s": b["mb_s"],
                                           "allocations": b["allocations"],
                                           "memory": b["peak_bytes"],
                                           "status": b["status"]}))

            def js():
                import tracemalloc

                import jsonschema
                v = jsonschema.Draft202012Validator(json.loads(e["schema"]))
                t0 = time.perf_counter()
                arr = json.loads(payload)
                bad = sum(1 for x in arr for _ in v.iter_errors(x))
                t = time.perf_counter() - t0
                tracemalloc.start()
                [list(v.iter_errors(x)) for x in json.loads(payload)]
                peak = tracemalloc.get_traced_memory()[1]
                tracemalloc.stop()
                return {"mb_s": len(payload) / t / 1e6, "memory": peak,
                        "status": "VALID" if not bad else "INVALID"}

            if kb <= 4096:
                j = await loop.run_in_executor(None, js)
                await ws.send_text(json.dumps({"size_kb": kb, "impl":
                                               "jsonschema (Python)", **j}))
        await ws.send_text(json.dumps({"done": True}))
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await ws.send_text(json.dumps({"error": exc.detail}))
    await ws.close()


def _rebase_refs(v):
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k == "$ref" and isinstance(x, str) and x.startswith("#"):
                out[k] = "#/items" + x[1:]
            else:
                out[k] = _rebase_refs(x)
        return out
    if isinstance(v, list):
        return [_rebase_refs(x) for x in v]
    return v


# ---------------------------------------------------------------- frontend
@app.get("/")
def index():
    return FileResponse(FRONT / "index.html")


app.mount("/static", StaticFiles(directory=FRONT), name="static")


@app.exception_handler(ValueError)
def value_error(_, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=422)
