"""Versioned documents and lexical FTS5 search, with query-time access filtering."""
import json
import re
import sqlite3
from contextlib import contextmanager


class VersionConflict(Exception):
    pass


class MissingDocument(Exception):
    pass


def chunks(text, size=80, overlap=15):
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("require 0 <= overlap < size")
    words = text.split()
    if not words:
        return []
    out = []
    start = 0
    while start < len(words):
        out.append(" ".join(words[start:start + size]))
        if start + size >= len(words):
            break
        start += size - overlap
    return out


def fts_query(query):
    # Convert untrusted queries into quoted terms instead of accepting raw FTS operators.
    terms = re.findall(r"[^\W_]+", query.lower(), flags=re.UNICODE)[:16]
    return " OR ".join('"' + term + '"' for term in terms)


class Index:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents(
                  tenant TEXT NOT NULL, doc_id TEXT NOT NULL, title TEXT NOT NULL,
                  version INTEGER NOT NULL, roles TEXT NOT NULL,
                  PRIMARY KEY(tenant,doc_id));
                CREATE TABLE IF NOT EXISTS chunks(
                  chunk_id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, doc_id TEXT NOT NULL,
                  version INTEGER NOT NULL, position INTEGER NOT NULL, content TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content);
                CREATE INDEX IF NOT EXISTS chunk_doc ON chunks(tenant,doc_id);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    def upsert(self, tenant, doc_id, title, text, roles, expected_version):
        parts = chunks(text)
        if not parts:
            raise ValueError("document must contain text")
        roles = sorted(set(roles))
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT version FROM documents WHERE tenant=? AND doc_id=?",
                                  (tenant, doc_id)).fetchone()
                version = row["version"] if row else 0
                if expected_version != version:
                    raise VersionConflict()
                con.execute("DELETE FROM chunks_fts WHERE rowid IN "
                            "(SELECT chunk_id FROM chunks WHERE tenant=? AND doc_id=?)",
                            (tenant, doc_id))
                con.execute("DELETE FROM chunks WHERE tenant=? AND doc_id=?", (tenant, doc_id))
                con.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT(tenant,doc_id) DO UPDATE SET "
                            "title=excluded.title,version=excluded.version,roles=excluded.roles",
                            (tenant, doc_id, title, version + 1, json.dumps(roles)))
                for position, content in enumerate(parts):
                    cur = con.execute("INSERT INTO chunks(tenant,doc_id,version,position,content) "
                                      "VALUES (?,?,?,?,?)",
                                      (tenant, doc_id, version + 1, position, content))
                    con.execute("INSERT INTO chunks_fts(rowid,content) VALUES (?,?)",
                                (cur.lastrowid, content))
                con.execute("COMMIT")
                return {"id": doc_id, "version": version + 1, "chunks": len(parts)}
            except Exception:
                con.execute("ROLLBACK")
                raise

    def delete(self, tenant, doc_id, expected_version):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT version FROM documents WHERE tenant=? AND doc_id=?",
                                  (tenant, doc_id)).fetchone()
                if row is None:
                    raise MissingDocument()
                if row["version"] != expected_version:
                    raise VersionConflict()
                con.execute("DELETE FROM chunks_fts WHERE rowid IN "
                            "(SELECT chunk_id FROM chunks WHERE tenant=? AND doc_id=?)",
                            (tenant, doc_id))
                con.execute("DELETE FROM chunks WHERE tenant=? AND doc_id=?", (tenant, doc_id))
                con.execute("DELETE FROM documents WHERE tenant=? AND doc_id=?", (tenant, doc_id))
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise

    def search(self, tenant, roles, query, limit=5):
        fts = fts_query(query)
        if not fts:
            return []
        # roles=[] in the stored document means public within the provided tenant.
        # Caller roles are bound values. Filtering precedes SQL LIMIT.
        with self.connect() as con:
            rows = con.execute("""
                SELECT c.doc_id,c.version,c.position,c.content,d.title,bm25(chunks_fts) AS rank
                FROM chunks_fts JOIN chunks c ON c.chunk_id=chunks_fts.rowid
                JOIN documents d ON d.tenant=c.tenant AND d.doc_id=c.doc_id
                WHERE chunks_fts MATCH ? AND c.tenant=?
                  AND (json_array_length(d.roles)=0 OR EXISTS (
                    SELECT 1 FROM json_each(d.roles) allowed
                    JOIN json_each(?) caller ON caller.value=allowed.value))
                ORDER BY rank, c.doc_id, c.position LIMIT ?
            """, (fts, tenant, json.dumps(sorted(set(roles))), limit)).fetchall()
        return [{"document_id": r["doc_id"], "version": r["version"], "chunk": r["position"],
                 "title": r["title"], "text": r["content"], "score": r["rank"]} for r in rows]
