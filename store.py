"""Capa de datos de Libreta.

Un solo archivo SQLite guarda cuadernos, secciones, páginas, bloques e imágenes.
Lo usan a la vez la aplicación web (server.py) y el servidor MCP (mcp_server.py),
por eso todo pasa por esta clase y la base trabaja en modo WAL.
"""

import base64
import datetime
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_data_dir():
    # En Linux (Mint) los datos van a ~/.local/share/libreta, separados del código.
    if os.name == "posix":
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(xdg, "libreta")
    return os.path.join(BASE_DIR, "datos")


DATA_DIR = os.environ.get("LIBRETA_DATOS") or _default_data_dir()
DB_PATH = os.path.join(DATA_DIR, "libreta.db")
BACKUP_DIR = os.path.join(DATA_DIR, "respaldos")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

BACKUP_FORMAT = "libreta-respaldo"
BACKUP_VERSION = 1

BLOCK_TYPES = (
    "paragraph", "heading1", "heading2", "heading3", "bullet", "numbered",
    "todo", "quote", "callout", "code", "divider", "image",
)

BLOCK_TYPE_NAMES = {
    "paragraph": "texto", "heading1": "título 1", "heading2": "título 2",
    "heading3": "título 3", "bullet": "viñeta", "numbered": "lista numerada",
    "todo": "tarea", "quote": "cita", "callout": "nota destacada",
    "code": "código", "divider": "separador", "image": "imagen",
}

NOTEBOOK_COLORS = ["#7719aa", "#0f6cbd", "#107c41", "#c50f1f", "#ca5010", "#8764b8", "#038387", "#986f0b"]

DEFAULT_CONFIG = {
    "horas_entre_respaldos": 12,
    "respaldos_a_conservar": 30,
    "carpeta_respaldo_extra": "",
    "puerto": 8765,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    color TEXT NOT NULL,
    position REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
    id TEXT PRIMARY KEY,
    notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    color TEXT NOT NULL,
    position REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    position REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    rid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    props TEXT NOT NULL DEFAULT '{}',
    tags TEXT NOT NULL DEFAULT '[]',
    position REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    page_id TEXT REFERENCES pages(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_sections_nb ON sections(notebook_id, position);
CREATE INDEX IF NOT EXISTS ix_pages_sec ON pages(section_id, position);
CREATE INDEX IF NOT EXISTS ix_blocks_page ON blocks(page_id, position);
CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
    content, tags, content='blocks', content_rowid='rid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
    INSERT INTO blocks_fts(rowid, content, tags) VALUES (new.rid, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, content, tags) VALUES ('delete', old.rid, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS blocks_au AFTER UPDATE ON blocks BEGIN
    INSERT INTO blocks_fts(blocks_fts, rowid, content, tags) VALUES ('delete', old.rid, old.content, old.tags);
    INSERT INTO blocks_fts(rowid, content, tags) VALUES (new.rid, new.content, new.tags);
END;
"""


class NotFound(Exception):
    pass


def now():
    # Milisegundos: la interfaz los usa para detectar si Antigravity cambió la página mientras se editaba.
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def new_id():
    return uuid.uuid4().hex[:16]


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _ext_for(mime):
    return {
        "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
        "image/webp": "webp", "image/svg+xml": "svg", "image/bmp": "bmp",
    }.get(mime, "bin")


def _safe_filename(name, fallback="sin-titulo"):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "").strip(" .")
    return (name or fallback)[:80]


def normalize_block(raw):
    """Valida un bloque recibido de la interfaz, de un respaldo o de Antigravity."""
    btype = raw.get("type") or "paragraph"
    if btype not in BLOCK_TYPES:
        raise ValueError(f"Tipo de bloque desconocido: {btype}. Tipos válidos: {', '.join(BLOCK_TYPES)}")
    props = raw.get("props") or {}
    if not isinstance(props, dict):
        raise ValueError("props debe ser un objeto")
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    tags = [str(t).lstrip("#").strip() for t in tags if str(t).strip()]
    return {
        "id": raw.get("id") or new_id(),
        "type": btype,
        "content": "" if btype == "divider" else str(raw.get("content") or ""),
        "props": dict(props),
        "tags": tags,
    }


# ---------- Markdown ----------

def blocks_to_markdown(blocks, with_ids=False, asset_link=None):
    """Convierte bloques a Markdown. asset_link(asset_id, props) devuelve la ruta de la imagen."""
    out = []
    number = 0
    for b in blocks:
        t, c, p = b["type"], b["content"], b.get("props") or {}
        number = number + 1 if t == "numbered" else 0
        if with_ids:
            meta = f"<!-- bloque:{b['id']} tipo:{t}"
            if b.get("tags"):
                meta += " etiquetas:" + ",".join(b["tags"])
            out.append(meta + " -->")
        if t == "heading1":
            text = "## " + c
        elif t == "heading2":
            text = "### " + c
        elif t == "heading3":
            text = "#### " + c
        elif t == "bullet":
            text = "- " + c.replace("\n", "\n  ")
        elif t == "numbered":
            text = f"{number}. " + c.replace("\n", "\n   ")
        elif t == "todo":
            text = ("- [x] " if p.get("checked") else "- [ ] ") + c.replace("\n", "\n  ")
        elif t == "quote":
            text = "\n".join("> " + line for line in c.split("\n"))
        elif t == "callout":
            text = "> [!NOTE]\n" + "\n".join("> " + line for line in c.split("\n"))
        elif t == "code":
            text = f"```{p.get('language', '')}\n{c}\n```"
        elif t == "divider":
            text = "---"
        elif t == "image":
            link = asset_link(p.get("asset_id"), p) if asset_link else f"libreta-recurso:{p.get('asset_id')}"
            text = f"![{c or p.get('name') or 'imagen'}]({link})"
        else:
            text = c
        if b.get("tags") and not with_ids:
            text += "  " + " ".join("#" + tag for tag in b["tags"])
        out.append(text)
    return "\n\n".join(out) + ("\n" if out else "")


def markdown_to_blocks(text):
    """Convierte Markdown sencillo a bloques (lo usa Antigravity para escribir)."""
    blocks = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    para = []

    def flush():
        if para:
            blocks.append({"type": "paragraph", "content": "\n".join(para).strip()})
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            lang = s[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append({"type": "code", "content": "\n".join(body), "props": {"language": lang}})
        elif not s:
            flush()
        elif s.startswith("<!--") and s.endswith("-->"):
            pass
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush()
            blocks.append({"type": "divider"})
        elif m := re.match(r"^(#{1,6})\s+(.*)$", s):
            flush()
            level = min(len(m.group(1)), 3)
            blocks.append({"type": f"heading{level}", "content": m.group(2)})
        elif m := re.match(r"^[-*+]\s+\[([ xX])\]\s+(.*)$", s):
            flush()
            blocks.append({"type": "todo", "content": m.group(2), "props": {"checked": m.group(1) != " "}})
        elif m := re.match(r"^[-*+]\s+(.*)$", s):
            flush()
            blocks.append({"type": "bullet", "content": m.group(1)})
        elif m := re.match(r"^\d+[.)]\s+(.*)$", s):
            flush()
            blocks.append({"type": "numbered", "content": m.group(1)})
        elif s.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            i -= 1
            if quote and quote[0].strip().upper().startswith("[!"):
                blocks.append({"type": "callout", "content": "\n".join(quote[1:])})
            else:
                blocks.append({"type": "quote", "content": "\n".join(quote)})
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


# ---------- Almacén ----------

class Store:
    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.makedirs(BACKUP_DIR, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    # --- utilidades ---
    def _one(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _next_pos(self, table, col, val):
        r = self._one(f"SELECT COALESCE(MAX(position), 0) + 1 AS p FROM {table} WHERE {col} = ?", (val,))
        return r["p"]

    def _touch_page(self, page_id):
        self.db.execute("UPDATE pages SET updated_at = ? WHERE id = ?", (now(), page_id))

    @staticmethod
    def _block_row(r):
        d = dict(r)
        d.pop("rid", None)
        d["props"] = json.loads(d["props"] or "{}")
        d["tags"] = json.loads(d["tags"] or "[]")
        return d

    def is_empty(self):
        return self._one("SELECT COUNT(*) AS n FROM notebooks")["n"] == 0

    # --- estructura ---
    def tree(self):
        with self.lock:
            nbs = self._all("SELECT * FROM notebooks ORDER BY position, created_at")
            secs = self._all("SELECT * FROM sections ORDER BY position, created_at")
            pages = self._all(
                "SELECT p.id, p.section_id, p.title, p.position, p.created_at, p.updated_at,"
                " (SELECT COUNT(*) FROM blocks b WHERE b.page_id = p.id) AS block_count"
                " FROM pages p ORDER BY p.position, p.created_at"
            )
        by_sec = {}
        for p in pages:
            by_sec.setdefault(p["section_id"], []).append(p)
        by_nb = {}
        for s in secs:
            s["pages"] = by_sec.get(s["id"], [])
            by_nb.setdefault(s["notebook_id"], []).append(s)
        for nb in nbs:
            nb["sections"] = by_nb.get(nb["id"], [])
        return nbs

    def stats(self):
        with self.lock:
            c = lambda t: self._one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
            return {
                "cuadernos": c("notebooks"), "secciones": c("sections"), "paginas": c("pages"),
                "bloques": c("blocks"), "imagenes": c("assets"),
                "ultima_modificacion": self._one("SELECT MAX(updated_at) AS u FROM pages")["u"],
                "base_de_datos": self.path,
            }

    # --- cuadernos ---
    def create_notebook(self, title, color=None):
        with self.lock:
            n = self._one("SELECT COUNT(*) AS n FROM notebooks")["n"]
            nid, ts = new_id(), now()
            self.db.execute(
                "INSERT INTO notebooks VALUES (?,?,?,?,?,?)",
                (nid, title or "Cuaderno nuevo", color or NOTEBOOK_COLORS[n % len(NOTEBOOK_COLORS)],
                 self._next_pos("notebooks", "1", 1), ts, ts),
            )
            self.db.commit()
            return self.get_notebook(nid)

    def get_notebook(self, nid):
        r = self._one("SELECT * FROM notebooks WHERE id = ?", (nid,))
        if not r:
            raise NotFound(f"No existe el cuaderno {nid}")
        return dict(r)

    def update_notebook(self, nid, **f):
        return self._update("notebooks", nid, f, ("title", "color", "position"))

    def delete_notebook(self, nid):
        return self._delete("notebooks", nid)

    # --- secciones ---
    def create_section(self, notebook_id, title, color=None):
        with self.lock:
            self.get_notebook(notebook_id)
            n = self._one("SELECT COUNT(*) AS n FROM sections WHERE notebook_id = ?", (notebook_id,))["n"]
            sid, ts = new_id(), now()
            self.db.execute(
                "INSERT INTO sections VALUES (?,?,?,?,?,?,?)",
                (sid, notebook_id, title or "Sección nueva",
                 color or NOTEBOOK_COLORS[(n + 1) % len(NOTEBOOK_COLORS)],
                 self._next_pos("sections", "notebook_id", notebook_id), ts, ts),
            )
            self.db.commit()
            return self.get_section(sid)

    def get_section(self, sid):
        r = self._one("SELECT * FROM sections WHERE id = ?", (sid,))
        if not r:
            raise NotFound(f"No existe la sección {sid}")
        return dict(r)

    def update_section(self, sid, **f):
        if "notebook_id" in f:
            self.get_notebook(f["notebook_id"])
        return self._update("sections", sid, f, ("title", "color", "position", "notebook_id"))

    def delete_section(self, sid):
        return self._delete("sections", sid)

    # --- páginas ---
    def create_page(self, section_id, title="", blocks=None):
        with self.lock:
            self.get_section(section_id)
            pid, ts = new_id(), now()
            self.db.execute(
                "INSERT INTO pages VALUES (?,?,?,?,?,?)",
                (pid, section_id, title or "", self._next_pos("pages", "section_id", section_id), ts, ts),
            )
            for i, raw in enumerate(blocks or []):
                b = normalize_block(raw)
                self._insert_block(pid, b, i + 1, ts)
            self.db.commit()
            return self.get_page(pid)

    def _insert_block(self, page_id, b, position, ts, created_at=None):
        self.db.execute(
            "INSERT INTO blocks (id, page_id, type, content, props, tags, position, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (b["id"], page_id, b["type"], b["content"], json.dumps(b["props"], ensure_ascii=False),
             json.dumps(b["tags"], ensure_ascii=False), position, created_at or ts, ts),
        )

    def get_page(self, pid, with_blocks=True):
        with self.lock:
            r = self._one(
                "SELECT p.*, s.title AS section_title, s.notebook_id, n.title AS notebook_title"
                " FROM pages p JOIN sections s ON s.id = p.section_id JOIN notebooks n ON n.id = s.notebook_id"
                " WHERE p.id = ?", (pid,),
            )
            if not r:
                raise NotFound(f"No existe la página {pid}")
            page = dict(r)
            if with_blocks:
                page["blocks"] = [self._block_row(b) for b in self.db.execute(
                    "SELECT * FROM blocks WHERE page_id = ? ORDER BY position, rid", (pid,))]
            return page

    def update_page(self, pid, **f):
        if "section_id" in f:
            self.get_section(f["section_id"])
            if "position" not in f:
                f["position"] = self._next_pos("pages", "section_id", f["section_id"])
        return self._update("pages", pid, f, ("title", "position", "section_id"))

    def delete_page(self, pid):
        return self._delete("pages", pid)

    def save_blocks(self, page_id, blocks):
        """Guarda la lista completa de bloques de una página (lo que ve el editor)."""
        with self.lock:
            self.get_page(page_id, with_blocks=False)
            ts = now()
            existing = {r["id"]: dict(r) for r in self.db.execute(
                "SELECT id, page_id, type, content, props, tags, position FROM blocks WHERE page_id = ?", (page_id,))}
            seen = set()
            for i, raw in enumerate(blocks):
                b = normalize_block(raw)
                pos = i + 1
                seen.add(b["id"])
                old = existing.get(b["id"])
                props, tags = json.dumps(b["props"], ensure_ascii=False), json.dumps(b["tags"], ensure_ascii=False)
                if old is None:
                    other = self._one("SELECT page_id FROM blocks WHERE id = ?", (b["id"],))
                    if other:
                        b["id"] = new_id()
                        seen.add(b["id"])
                    self._insert_block(page_id, b, pos, ts)
                elif (old["type"], old["content"], old["props"], old["tags"], old["position"]) != (b["type"], b["content"], props, tags, pos):
                    changed = (old["type"], old["content"], old["props"], old["tags"]) != (b["type"], b["content"], props, tags)
                    self.db.execute(
                        "UPDATE blocks SET type=?, content=?, props=?, tags=?, position=?, updated_at=? WHERE id=?",
                        (b["type"], b["content"], props, tags, pos, ts if changed else self._one(
                            "SELECT updated_at FROM blocks WHERE id=?", (b["id"],))["updated_at"], b["id"]),
                    )
            for bid in set(existing) - seen:
                self.db.execute("DELETE FROM blocks WHERE id = ?", (bid,))
            self._touch_page(page_id)
            self.db.commit()
            return self.get_page(page_id)

    def add_blocks(self, page_id, blocks, after_block_id=None):
        with self.lock:
            page = self.get_page(page_id)
            current = page["blocks"]
            idx = len(current)
            if after_block_id:
                ids = [b["id"] for b in current]
                if after_block_id not in ids:
                    raise NotFound(f"El bloque {after_block_id} no está en la página {page_id}")
                idx = ids.index(after_block_id) + 1
            new = [normalize_block(b) for b in blocks]
            for b in new:
                b["id"] = new_id()
            self.save_blocks(page_id, current[:idx] + new + current[idx:])
            return new

    def get_block(self, bid):
        with self.lock:
            r = self._one("SELECT * FROM blocks WHERE id = ?", (bid,))
            if not r:
                raise NotFound(f"No existe el bloque {bid}")
            b = self._block_row(r)
            page = self.get_page(b["page_id"], with_blocks=False)
            b["ruta"] = {"cuaderno": page["notebook_title"], "seccion": page["section_title"], "pagina": page["title"],
                         "notebook_id": page["notebook_id"], "section_id": page["section_id"]}
            return b

    def update_block(self, bid, content=None, type=None, props=None, tags=None):
        with self.lock:
            b = self.get_block(bid)
            merged = {
                "id": bid,
                "type": type or b["type"],
                "content": b["content"] if content is None else content,
                "props": {**b["props"], **(props or {})},
                "tags": b["tags"] if tags is None else tags,
            }
            n = normalize_block(merged)
            self.db.execute(
                "UPDATE blocks SET type=?, content=?, props=?, tags=?, updated_at=? WHERE id=?",
                (n["type"], n["content"], json.dumps(n["props"], ensure_ascii=False),
                 json.dumps(n["tags"], ensure_ascii=False), now(), bid),
            )
            self._touch_page(b["page_id"])
            self.db.commit()
            return self.get_block(bid)

    def delete_block(self, bid):
        with self.lock:
            b = self.get_block(bid)
            self.db.execute("DELETE FROM blocks WHERE id = ?", (bid,))
            self._touch_page(b["page_id"])
            self.db.commit()

    # --- imágenes ---
    def add_asset(self, page_id, name, mime, data):
        with self.lock:
            aid = new_id()
            self.db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?)",
                            (aid, page_id, name or "", mime, len(data), data, now()))
            self.db.commit()
            return {"id": aid, "name": name, "mime": mime, "size": len(data)}

    def get_asset(self, aid):
        with self.lock:
            r = self._one("SELECT * FROM assets WHERE id = ?", (aid,))
            if not r:
                raise NotFound(f"No existe la imagen {aid}")
            return dict(r)

    # --- genérico ---
    def _update(self, table, rid, fields, allowed):
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        with self.lock:
            if not self._one(f"SELECT id FROM {table} WHERE id = ?", (rid,)):
                raise NotFound(f"No existe {rid}")
            if sets:
                sets["updated_at"] = now()
                cols = ", ".join(f"{k} = ?" for k in sets)
                self.db.execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*sets.values(), rid))
                self.db.commit()
            return dict(self._one(f"SELECT * FROM {table} WHERE id = ?", (rid,)))

    def _delete(self, table, rid):
        with self.lock:
            cur = self.db.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))
            if table in ("notebooks", "sections", "pages"):
                self.db.execute("DELETE FROM assets WHERE page_id IS NULL")
            self.db.commit()
            if not cur.rowcount:
                raise NotFound(f"No existe {rid}")

    # --- búsqueda ---
    def search(self, query, limit=30):
        query = (query or "").strip()
        if not query:
            return []
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        results = []
        with self.lock:
            if tokens:
                fts = " ".join(f'"{t}"*' for t in tokens)
                rows = self._all(
                    "SELECT b.id, b.type, b.page_id, b.content,"
                    " snippet(blocks_fts, 0, '«', '»', '…', 14) AS fragmento,"
                    " p.title AS pagina, s.title AS seccion, n.title AS cuaderno, s.id AS section_id, n.id AS notebook_id"
                    " FROM blocks_fts f JOIN blocks b ON b.rid = f.rowid"
                    " JOIN pages p ON p.id = b.page_id JOIN sections s ON s.id = p.section_id"
                    " JOIN notebooks n ON n.id = s.notebook_id"
                    " WHERE blocks_fts MATCH ? ORDER BY bm25(blocks_fts) LIMIT ?", (fts, limit))
                for r in rows:
                    r["coincide_en"] = "bloque"
                    r["contenido"] = r.pop("content")
                    results.append(r)
            like = f"%{query}%"
            for r in self._all(
                    "SELECT p.id AS page_id, p.title AS pagina, s.title AS seccion, n.title AS cuaderno,"
                    " s.id AS section_id, n.id AS notebook_id"
                    " FROM pages p JOIN sections s ON s.id = p.section_id JOIN notebooks n ON n.id = s.notebook_id"
                    " WHERE p.title LIKE ? LIMIT ?", (like, limit)):
                r.update({"id": None, "type": "page", "fragmento": r["pagina"], "coincide_en": "titulo_pagina"})
                results.insert(0, r)
        return results[:limit]

    _BLOCK_PATH_SQL = (
        "SELECT b.*, p.title AS pagina, s.title AS seccion, n.title AS cuaderno,"
        " s.id AS section_id, n.id AS notebook_id"
        " FROM blocks b JOIN pages p ON p.id = b.page_id JOIN sections s ON s.id = p.section_id"
        " JOIN notebooks n ON n.id = s.notebook_id"
    )

    def blocks_by_tag(self, tag, limit=200):
        tag = tag.lstrip("#").strip().lower()
        with self.lock:
            rows = self.db.execute(self._BLOCK_PATH_SQL + " ORDER BY n.position, s.position, p.position, b.position").fetchall()
        out = []
        for r in rows:
            b = self._block_row(r)
            if tag in [t.lower() for t in b["tags"]]:
                out.append(b)
                if len(out) >= limit:
                    break
        return out

    def todos(self, state="pendientes", notebook_id=None, limit=500):
        sql = self._BLOCK_PATH_SQL + " WHERE b.type = 'todo'"
        args = []
        if notebook_id:
            sql += " AND n.id = ?"
            args.append(notebook_id)
        sql += " ORDER BY n.position, s.position, p.position, b.position"
        with self.lock:
            rows = [self._block_row(r) for r in self.db.execute(sql, args)]
        if state == "pendientes":
            rows = [b for b in rows if not b["props"].get("checked")]
        elif state == "hechas":
            rows = [b for b in rows if b["props"].get("checked")]
        return rows[:limit]

    def recent_pages(self, limit=20, since=None):
        sql = ("SELECT p.id, p.title, p.updated_at, p.created_at, s.title AS seccion, n.title AS cuaderno,"
               " (SELECT COUNT(*) FROM blocks b WHERE b.page_id = p.id) AS bloques"
               " FROM pages p JOIN sections s ON s.id = p.section_id JOIN notebooks n ON n.id = s.notebook_id")
        args = []
        if since:
            sql += " WHERE p.updated_at >= ?"
            args.append(since)
        sql += " ORDER BY p.updated_at DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return self._all(sql, args)

    # --- Markdown ---
    def page_markdown(self, pid, with_ids=False, asset_link=None):
        page = self.get_page(pid)
        head = f"# {page['title'] or 'Sin título'}\n\n"
        if with_ids:
            head += (f"<!-- pagina:{page['id']} cuaderno:{page['notebook_title']} seccion:{page['section_title']}"
                     f" modificada:{page['updated_at']} -->\n\n")
        return head + blocks_to_markdown(page["blocks"], with_ids=with_ids, asset_link=asset_link)

    # --- exportación / respaldo ---
    def export_data(self, include_assets=True, notebook_id=None):
        with self.lock:
            tree = self.tree()
            if notebook_id:
                tree = [nb for nb in tree if nb["id"] == notebook_id]
                if not tree:
                    raise NotFound(f"No existe el cuaderno {notebook_id}")
            page_ids = []
            for nb in tree:
                for s in nb["sections"]:
                    full = []
                    for p in s["pages"]:
                        page = self.get_page(p["id"])
                        page_ids.append(p["id"])
                        full.append({k: page[k] for k in ("id", "title", "position", "created_at", "updated_at")}
                                    | {"blocks": page["blocks"]})
                    s["pages"] = full
            for nb in tree:
                for s in nb["sections"]:
                    for p in s["pages"]:
                        for b in p["blocks"]:
                            b.pop("page_id", None)
            assets = []
            if include_assets and page_ids:
                marks = ",".join("?" * len(page_ids))
                for a in self._all(f"SELECT * FROM assets WHERE page_id IN ({marks})", page_ids):
                    a["data"] = base64.b64encode(a["data"]).decode("ascii")
                    assets.append(a)
        return {
            "format": BACKUP_FORMAT, "version": BACKUP_VERSION, "exported_at": now(), "app": "Libreta",
            "stats": {"cuadernos": len(tree), "paginas": len(page_ids), "imagenes": len(assets)},
            "notebooks": tree, "assets": assets,
        }

    def import_data(self, data, mode="merge"):
        """mode='merge' añade copias con IDs nuevos; mode='replace' sustituye todo (hace respaldo antes)."""
        if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
            raise ValueError("El archivo no es un respaldo de Libreta")
        if data.get("version", 0) > BACKUP_VERSION:
            raise ValueError("El respaldo es de una versión más nueva de Libreta")
        safety = None
        if mode == "replace":
            safety = self.create_backup("antes-de-restaurar")
        remap = (lambda old: new_id()) if mode == "merge" else (lambda old: old)
        ids = {}

        def mid(old):
            if old not in ids:
                ids[old] = remap(old)
            return ids[old]

        counts = {"cuadernos": 0, "secciones": 0, "paginas": 0, "bloques": 0, "imagenes": 0}
        with self.lock:
            try:
                if mode == "replace":
                    self.db.execute("DELETE FROM notebooks")
                    self.db.execute("DELETE FROM assets")
                base = self._one("SELECT COALESCE(MAX(position), 0) AS p FROM notebooks")["p"]
                for a in data.get("assets", []):
                    mid(a["id"])
                for nb in data.get("notebooks", []):
                    title = nb["title"] + (" (importado)" if mode == "merge" else "")
                    self.db.execute("INSERT INTO notebooks VALUES (?,?,?,?,?,?)",
                                    (mid(nb["id"]), title, nb.get("color") or NOTEBOOK_COLORS[0],
                                     base + nb.get("position", 0), nb.get("created_at") or now(), nb.get("updated_at") or now()))
                    counts["cuadernos"] += 1
                    for s in nb.get("sections", []):
                        self.db.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?)",
                                        (mid(s["id"]), mid(nb["id"]), s["title"], s.get("color") or NOTEBOOK_COLORS[1],
                                         s.get("position", 0), s.get("created_at") or now(), s.get("updated_at") or now()))
                        counts["secciones"] += 1
                        for p in s.get("pages", []):
                            self.db.execute("INSERT INTO pages VALUES (?,?,?,?,?,?)",
                                            (mid(p["id"]), mid(s["id"]), p.get("title", ""), p.get("position", 0),
                                             p.get("created_at") or now(), p.get("updated_at") or now()))
                            counts["paginas"] += 1
                            for i, raw in enumerate(p.get("blocks", [])):
                                b = normalize_block(raw)
                                b["id"] = mid(b["id"])
                                if b["props"].get("asset_id"):
                                    b["props"]["asset_id"] = mid(b["props"]["asset_id"])
                                self._insert_block(mid(p["id"]), b, raw.get("position", i + 1),
                                                   raw.get("updated_at") or now(), raw.get("created_at"))
                                counts["bloques"] += 1
                for a in data.get("assets", []):
                    page = ids.get(a.get("page_id")) if a.get("page_id") else None
                    blob = base64.b64decode(a["data"])
                    self.db.execute("INSERT INTO assets VALUES (?,?,?,?,?,?,?)",
                                    (mid(a["id"]), page, a.get("name", ""), a["mime"], len(blob), blob,
                                     a.get("created_at") or now()))
                    counts["imagenes"] += 1
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return {"modo": mode, "importado": counts, "respaldo_previo": safety}

    def export_markdown_zip(self, notebook_id=None):
        data = self.export_data(include_assets=True, notebook_id=notebook_id)
        buf = io.BytesIO()
        assets = {a["id"]: a for a in data["assets"]}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            index = ["# Índice de Libreta", "", f"Exportado: {data['exported_at']}", ""]
            for nb in data["notebooks"]:
                nb_dir = _safe_filename(nb["title"], "Cuaderno")
                index.append(f"## {nb['title']}")
                for s in nb["sections"]:
                    s_dir = f"{nb_dir}/{_safe_filename(s['title'], 'Seccion')}"
                    index.append(f"### {s['title']}")
                    used = set()
                    for p in s["pages"]:
                        base = _safe_filename(p["title"])
                        name = base
                        n = 2
                        while name.lower() in used:
                            name, n = f"{base} ({n})", n + 1
                        used.add(name.lower())

                        def link(aid, props):
                            a = assets.get(aid)
                            return f"../../imagenes/{aid}.{_ext_for(a['mime'])}" if a else "imagen-no-encontrada"

                        md = f"# {p['title'] or 'Sin título'}\n\n" + blocks_to_markdown(p["blocks"], asset_link=link)
                        z.writestr(f"{s_dir}/{name}.md", md)
                        index.append(f"- [{p['title'] or 'Sin título'}](<{s_dir}/{name}.md>)")
                index.append("")
            for a in data["assets"]:
                z.writestr(f"imagenes/{a['id']}.{_ext_for(a['mime'])}", base64.b64decode(a["data"]))
            z.writestr("INDICE.md", "\n".join(index))
        return buf.getvalue()

    def export_sqlite(self, dest):
        with self.lock:
            target = sqlite3.connect(dest)
            try:
                self.db.backup(target)
            finally:
                target.close()
        return dest

    def create_backup(self, reason="manual"):
        cfg = load_config()
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"libreta_{stamp}_{_safe_filename(reason)}.json"
        path = os.path.join(BACKUP_DIR, name)
        data = self.export_data(include_assets=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        extra = os.path.expanduser((cfg.get("carpeta_respaldo_extra") or "").strip())
        extra_path = None
        if extra:
            try:
                os.makedirs(extra, exist_ok=True)
                extra_path = shutil.copy2(path, os.path.join(extra, name))
            except OSError as e:
                extra_path = f"ERROR: {e}"
        self._prune_backups(int(cfg.get("respaldos_a_conservar") or 30))
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('ultimo_respaldo', ?)", (now(),))
            self.db.commit()
        return {"archivo": path, "copia_extra": extra_path, "bytes": os.path.getsize(path), "estadisticas": data["stats"]}

    def _prune_backups(self, keep):
        files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("libreta_") and f.endswith(".json"))
        for f in files[:-keep] if keep > 0 else []:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except OSError:
                pass

    def list_backups(self):
        out = []
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if f.startswith("libreta_") and f.endswith(".json"):
                full = os.path.join(BACKUP_DIR, f)
                out.append({"nombre": f, "ruta": full, "bytes": os.path.getsize(full),
                            "fecha": datetime.datetime.fromtimestamp(os.path.getmtime(full)).isoformat(timespec="seconds")})
        return out

    def last_backup(self):
        r = self._one("SELECT value FROM meta WHERE key = 'ultimo_respaldo'")
        return r["value"] if r else None

    def restore_backup_file(self, filename, mode="replace"):
        path = os.path.join(BACKUP_DIR, os.path.basename(filename))
        with open(path, encoding="utf-8") as f:
            return self.import_data(json.load(f), mode=mode)

    # --- contenido inicial ---
    def seed(self):
        if not self.is_empty():
            return
        nb = self.create_notebook("Mi cuaderno")
        sec = self.create_section(nb["id"], "Inicio")
        self.create_page(sec["id"], "Bienvenida a Libreta", markdown_to_blocks(WELCOME_MD))


WELCOME_MD = """# Qué es Libreta
Libreta funciona como OneNote: cuadernos que contienen secciones, secciones que contienen páginas, y páginas hechas de bloques.

## Cómo escribir
- Escribe y pulsa Enter para crear un bloque nuevo.
- Escribe / al inicio de un bloque vacío para cambiar su tipo (título, tarea, código, cita...).
- Pega una imagen con Ctrl+V y queda guardada dentro de la página.
- [ ] Prueba a marcar esta tarea
- [x] Esta ya está hecha

## Respaldos
> [!NOTE]
> Libreta guarda un respaldo automático en ~/.local/share/libreta/respaldos. Desde el menú Respaldo puedes exportar todo a JSON, a Markdown (ZIP) o una copia de la base SQLite, y restaurar un respaldo.

## Antigravity
Antigravity puede leer todo lo que hay aquí mediante el servidor MCP de Libreta: la estructura completa, cada página en Markdown con los IDs de sus bloques, y una búsqueda de texto completo.

```
python3 mcp_server.py
```
"""
