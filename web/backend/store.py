"""Persistencia NoSQL de los casos de prueba.

- Si existe la variable de entorno ``MONGODB_URI`` se usa MongoDB
  (MongoDB Atlas, Azure Cosmos DB for MongoDB o un ``mongo`` local de
  docker-compose): base ``WDPDA_DB`` (por defecto ``wdpda``), colección
  ``casos``.
- Si no, un almacén documental en un archivo JSON (``web/data/casos.json``)
  con la misma interfaz, para desarrollo sin red.

Documento de un caso::

    {"_id": "persona-valido-1", "categoria": "validos", "formato": "json",
     "nombre": "...", "esquema": "<texto>", "payload": "<texto>",
     "esperado": {"status": "VALID", "errores": [...]},
     "metricas": {...último resultado por motor...}, "actualizado": "..."}
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


class FileStore:
    kind = "archivo JSON (desarrollo)"

    def __init__(self, path: Path = DATA / "casos.json"):
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def list(self, categoria: str | None = None) -> list[dict]:
        with self.lock:
            docs = list(self._load().values())
        if categoria:
            docs = [d for d in docs if d.get("categoria") == categoria]
        return sorted(docs, key=lambda d: d["_id"])

    def get(self, cid: str) -> dict | None:
        with self.lock:
            return self._load().get(cid)

    def upsert(self, doc: dict) -> dict:
        doc["actualizado"] = datetime.now(timezone.utc).isoformat()
        with self.lock:
            d = self._load()
            d[doc["_id"]] = doc
            self._save(d)
        return doc

    def update_metrics(self, cid: str, metricas: dict) -> None:
        with self.lock:
            d = self._load()
            if cid in d:
                d[cid]["metricas"] = metricas
                d[cid]["actualizado"] = datetime.now(timezone.utc).isoformat()
                self._save(d)

    def delete(self, cid: str) -> bool:
        with self.lock:
            d = self._load()
            ok = d.pop(cid, None) is not None
            self._save(d)
        return ok

    def count(self) -> int:
        return len(self._load())


class MongoStore:
    kind = "MongoDB"

    def __init__(self, uri: str, db: str = "wdpda"):
        from pymongo import MongoClient
        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000,
                                  appname="wdpda-web")
        self.col = self.client[db]["casos"]
        self.col.create_index("categoria")

    def list(self, categoria: str | None = None) -> list[dict]:
        q = {"categoria": categoria} if categoria else {}
        return list(self.col.find(q).sort("_id", 1))

    def get(self, cid: str) -> dict | None:
        return self.col.find_one({"_id": cid})

    def upsert(self, doc: dict) -> dict:
        doc["actualizado"] = datetime.now(timezone.utc).isoformat()
        self.col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return doc

    def update_metrics(self, cid: str, metricas: dict) -> None:
        self.col.update_one({"_id": cid}, {"$set": {
            "metricas": metricas,
            "actualizado": datetime.now(timezone.utc).isoformat()}})

    def delete(self, cid: str) -> bool:
        return self.col.delete_one({"_id": cid}).deleted_count == 1

    def count(self) -> int:
        return self.col.count_documents({})


def open_store():
    uri = os.environ.get("MONGODB_URI")
    if uri:
        return MongoStore(uri, os.environ.get("WDPDA_DB", "wdpda"))
    return FileStore()
