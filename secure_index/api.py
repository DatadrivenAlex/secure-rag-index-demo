"""Unauthenticated demo API; tenant/roles headers simulate verified claims only."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from secure_index.index import Index, MissingDocument, VersionConflict


class Document(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100000)
    roles: list[str] = Field(default_factory=list, max_length=20)
    expected_version: int = Field(ge=0)


def create_app(index=None):
    @asynccontextmanager
    async def lifespan(app):
        if index is None:
            path = os.getenv("INDEX_DB", "index.db")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            app.state.index = Index(path)
        else:
            app.state.index = index
        yield

    app = FastAPI(title="Secure RAG Index Demo", version="0.1.0", lifespan=lifespan)

    @app.put("/v1/documents/{doc_id}")
    def put(doc_id: str, body: Document, tenant: str = Header(alias="X-Tenant-ID",
            min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")):
        if not re_valid_id(doc_id) or any(not re_valid_id(role) for role in body.roles):
            raise HTTPException(422, "Invalid document ID or role")
        try:
            return app.state.index.upsert(tenant, doc_id, body.title, body.text,
                                          body.roles, body.expected_version)
        except VersionConflict:
            raise HTTPException(409, "Version conflict")
        except ValueError:
            raise HTTPException(422, "Document must contain text")

    @app.delete("/v1/documents/{doc_id}")
    def delete(doc_id: str, expected_version: int = Query(ge=1),
               tenant: str = Header(alias="X-Tenant-ID", min_length=1, max_length=80,
                                    pattern=r"^[a-zA-Z0-9_-]+$")):
        try:
            app.state.index.delete(tenant, doc_id, expected_version)
        except VersionConflict:
            raise HTTPException(409, "Version conflict")
        except MissingDocument:
            raise HTTPException(404, "Document not found")
        return {"deleted": True}

    @app.get("/v1/search")
    def search(q: str = Query(min_length=1, max_length=500), limit: int = Query(default=5, ge=1,
               le=50), tenant: str = Header(alias="X-Tenant-ID", min_length=1, max_length=80,
                                            pattern=r"^[a-zA-Z0-9_-]+$"),
               roles: str = Header(default="", alias="X-Roles", max_length=1000)):
        values = [x.strip() for x in roles.split(",") if x.strip()]
        if len(values) > 20 or any(not re_valid_id(role) for role in values):
            raise HTTPException(422, "Invalid roles")
        return {"results": app.state.index.search(tenant, values, q, limit)}

    return app


def re_valid_id(value):
    import re
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value))


app = create_app()
