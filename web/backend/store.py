"""Persistencia NoSQL de los casos de prueba.

- ``WDPDA_STORE=dynamodb``: Amazon DynamoDB (despliegue en AWS, ver
  ``docs/despliegue-aws.md``).
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


class DynamoStore:
    """Amazon DynamoDB (capa gratuita de AWS).

    Tabla con clave de partición ``id`` (cadena). Cada caso es un ítem
    con sus atributos anidados (mapas y listas nativos de DynamoDB); los
    números se guardan como ``Decimal`` porque DynamoDB no acepta float.
    Límite de un ítem: 400 KB, por eso los payloads grandes del corpus
    se guardan como receta (``payload_gen``) y se regeneran al ejecutar.
    Credenciales: el rol IAM de la instancia (en AWS) o las del perfil
    local; la aplicación nunca guarda claves.
    """

    kind = "Amazon DynamoDB"

    def __init__(self, table: str, region: str | None = None):
        import boto3
        self.table = boto3.resource("dynamodb", region_name=region).Table(
            table)
        self.table.load()  # falla temprano si la tabla no existe

    @staticmethod
    def _to_db(doc: dict) -> dict:
        from decimal import Decimal
        clean = json.loads(json.dumps(doc, default=str), parse_float=Decimal)
        clean["id"] = clean.pop("_id")
        return clean

    @staticmethod
    def _from_db(item: dict | None) -> dict | None:
        if item is None:
            return None
        from decimal import Decimal

        def conv(v):
            if isinstance(v, Decimal):
                return int(v) if v == v.to_integral_value() else float(v)
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v

        doc = conv(item)
        doc["_id"] = doc.pop("id")
        return doc

    def _scan(self, **kw) -> list[dict]:
        items, start = [], None
        while True:
            if start:
                kw["ExclusiveStartKey"] = start
            r = self.table.scan(**kw)
            items += r.get("Items", [])
            start = r.get("LastEvaluatedKey")
            if not start:
                return items

    def list(self, categoria: str | None = None) -> list[dict]:
        kw = {}
        if categoria:
            from boto3.dynamodb.conditions import Attr
            kw["FilterExpression"] = Attr("categoria").eq(categoria)
        docs = [self._from_db(i) for i in self._scan(**kw)]
        return sorted(docs, key=lambda d: d["_id"])

    def get(self, cid: str) -> dict | None:
        return self._from_db(self.table.get_item(Key={"id": cid}).get("Item"))

    def upsert(self, doc: dict) -> dict:
        doc["actualizado"] = datetime.now(timezone.utc).isoformat()
        self.table.put_item(Item=self._to_db(dict(doc)))
        return doc

    def update_metrics(self, cid: str, metricas: dict) -> None:
        from decimal import Decimal
        self.table.update_item(
            Key={"id": cid},
            UpdateExpression="SET metricas = :m, actualizado = :t",
            ExpressionAttributeValues={
                ":m": json.loads(json.dumps(metricas, default=str),
                                 parse_float=Decimal),
                ":t": datetime.now(timezone.utc).isoformat()})

    def delete(self, cid: str) -> bool:
        r = self.table.delete_item(Key={"id": cid}, ReturnValues="ALL_OLD")
        return "Attributes" in r

    def count(self) -> int:
        return sum(1 for _ in self._scan(ProjectionExpression="id"))


def open_store():
    """Elige el almacén según el entorno:

    - ``WDPDA_STORE=dynamodb`` (+ ``DYNAMODB_TABLE``, ``AWS_REGION``):
      Amazon DynamoDB, el que usa el despliegue en AWS;
    - ``MONGODB_URI``: MongoDB (Atlas, Cosmos DB o docker-compose);
    - si no: archivo JSON local (desarrollo).
    """
    if os.environ.get("WDPDA_STORE") == "dynamodb":
        return DynamoStore(os.environ.get("DYNAMODB_TABLE", "wdpda-casos"),
                           os.environ.get("AWS_REGION"))
    uri = os.environ.get("MONGODB_URI")
    if uri:
        return MongoStore(uri, os.environ.get("WDPDA_DB", "wdpda"))
    return FileStore()
