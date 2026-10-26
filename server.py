"""Servidor local de Libreta: sirve la interfaz web y la API REST en 127.0.0.1.

Uso:  python3 server.py [--puerto 8765] [--abrir]
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import store as st

STATIC_DIR = os.path.join(st.BASE_DIR, "static")
MAX_BODY = 200 * 1024 * 1024
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}

STORE = None
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")


# ---------- estructura ----------

@route("GET", "/api/tree")
def api_tree(h, q, body):
    return {"notebooks": STORE.tree(), "stats": STORE.stats(), "ultimo_respaldo": STORE.last_backup()}


@route("POST", "/api/notebooks")
def api_nb_create(h, q, body):
    return STORE.create_notebook(body.get("title"), body.get("color"))


@route("PATCH", "/api/notebooks/(?P<id>\\w+)")
def api_nb_update(h, q, body, id):
    return STORE.update_notebook(id, **body)


@route("DELETE", "/api/notebooks/(?P<id>\\w+)")
def api_nb_delete(h, q, body, id):
    STORE.delete_notebook(id)
    return {"ok": True}


@route("POST", "/api/sections")
def api_sec_create(h, q, body):
    return STORE.create_section(body["notebook_id"], body.get("title"), body.get("color"))


@route("PATCH", "/api/sections/(?P<id>\\w+)")
def api_sec_update(h, q, body, id):
    return STORE.update_section(id, **body)


@route("DELETE", "/api/sections/(?P<id>\\w+)")
def api_sec_delete(h, q, body, id):
    STORE.delete_section(id)
    return {"ok": True}


@route("POST", "/api/pages")
def api_page_create(h, q, body):
    blocks = body.get("blocks") or [{"type": "paragraph", "content": ""}]
    return STORE.create_page(body["section_id"], body.get("title", ""), blocks)


@route("GET", "/api/pages/(?P<id>\\w+)")
def api_page_get(h, q, body, id):
    return STORE.get_page(id)


@route("PATCH", "/api/pages/(?P<id>\\w+)")
def api_page_update(h, q, body, id):
    return STORE.update_page(id, **body)


@route("DELETE", "/api/pages/(?P<id>\\w+)")
def api_page_delete(h, q, body, id):
    STORE.delete_page(id)
    return {"ok": True}


@route("PUT", "/api/pages/(?P<id>\\w+)/blocks")
def api_blocks_save(h, q, body, id):
    base = body.get("base")
    if base:
        current = STORE.get_page(id)
        if current["updated_at"] != base:
            # Otro proceso (Antigravity) cambió la página: la interfaz fusiona y reintenta.
            h.send_json({"error": "conflicto", "page": current}, HTTPStatus.CONFLICT)
            return
    if "title" in body:
        STORE.update_page(id, title=body["title"])
    return STORE.save_blocks(id, body.get("blocks", []))


@route("GET", "/api/pages/(?P<id>\\w+)/markdown")
def api_page_md(h, q, body, id):
    return {"markdown": STORE.page_markdown(id)}


@route("GET", "/api/search")
def api_search(h, q, body):
    return {"results": STORE.search(q.get("q", [""])[0], int(q.get("limit", ["40"])[0]))}


# ---------- imágenes ----------

@route("POST", "/api/assets")
def api_asset_upload(h, q, body):
    mime = (h.headers.get("Content-Type") or "").split(";")[0].strip()
    if not mime.startswith("image/"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "Solo se admiten imágenes")
    return STORE.add_asset(q.get("page_id", [None])[0], q.get("name", [""])[0], mime, body)


@route("GET", "/api/assets/(?P<id>\\w+)")
def api_asset_get(h, q, body, id):
    a = STORE.get_asset(id)
    h.send_bytes(a["data"], a["mime"], cache=True)


# ---------- exportación y respaldos ----------

@route("GET", "/api/export/json")
def api_export_json(h, q, body):
    nb = q.get("notebook_id", [None])[0]
    data = json.dumps(STORE.export_data(notebook_id=nb), ensure_ascii=False, indent=1).encode("utf-8")
    h.send_bytes(data, "application/json", filename=f"libreta_{_stamp()}.json")


@route("GET", "/api/export/markdown")
def api_export_md(h, q, body):
    nb = q.get("notebook_id", [None])[0]
    h.send_bytes(STORE.export_markdown_zip(nb), "application/zip", filename=f"libreta_markdown_{_stamp()}.zip")


@route("GET", "/api/export/sqlite")
def api_export_sqlite(h, q, body):
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        STORE.export_sqlite(tmp)
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        os.remove(tmp)
    h.send_bytes(data, "application/vnd.sqlite3", filename=f"libreta_{_stamp()}.db")


@route("GET", "/api/export/page/(?P<id>\\w+)")
def api_export_page(h, q, body, id):
    page = STORE.get_page(id, with_blocks=False)
    md = STORE.page_markdown(id, asset_link=lambda aid, p: f"http://{h.headers.get('Host')}/api/assets/{aid}")
    h.send_bytes(md.encode("utf-8"), "text/markdown; charset=utf-8",
                 filename=st._safe_filename(page["title"]) + ".md")


@route("POST", "/api/import")
def api_import(h, q, body):
    mode = q.get("mode", ["merge"])[0]
    if mode not in ("merge", "replace"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "mode debe ser merge o replace")
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except ValueError:
        raise ApiError(HTTPStatus.BAD_REQUEST, "El archivo no es JSON válido")
    return STORE.import_data(data, mode=mode)


@route("GET", "/api/backups")
def api_backups(h, q, body):
    return {"respaldos": STORE.list_backups(), "carpeta": st.BACKUP_DIR,
            "ultimo": STORE.last_backup(), "config": st.load_config()}


@route("POST", "/api/backups")
def api_backup_now(h, q, body):
    return STORE.create_backup("manual")


@route("POST", "/api/backups/restore")
def api_backup_restore(h, q, body):
    return STORE.restore_backup_file(body["nombre"], mode=body.get("modo", "replace"))


@route("GET", "/api/backups/file")
def api_backup_file(h, q, body):
    name = os.path.basename(q.get("nombre", [""])[0])
    path = os.path.join(st.BACKUP_DIR, name)
    if not name.endswith(".json") or not os.path.isfile(path):
        raise st.NotFound("No existe ese respaldo")
    with open(path, "rb") as f:
        h.send_bytes(f.read(), "application/json", filename=name)


@route("GET", "/api/info")
def api_info(h, q, body):
    import mcp_server
    return {
        "app_dir": st.BASE_DIR,
        "data_dir": st.DATA_DIR,
        "python": sys.executable,
        "mcp_script": os.path.join(st.BASE_DIR, "mcp_server.py"),
        "antigravity_config": os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "mcp_config.json"),
        "tools": [f"{t['name']} — {t['description'].split('.')[0]}" for t in mcp_server.all_tools()],
    }


@route("PUT", "/api/config")
def api_config(h, q, body):
    cfg = st.load_config()
    for k in ("horas_entre_respaldos", "respaldos_a_conservar", "carpeta_respaldo_extra"):
        if k in body:
            cfg[k] = body[k]
    st.save_config(cfg)
    return cfg


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Libreta/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("LIBRETA_LOG"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _allowed_host(self):
        # Evita que una página web externa use la API mediante DNS rebinding.
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]")

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).hostname in ("127.0.0.1", "localhost", "::1")

    def send_json(self, obj, status=HTTPStatus.OK):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_bytes(data, "application/json; charset=utf-8", status=status)

    def send_bytes(self, data, ctype, status=HTTPStatus.OK, filename=None, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=31536000, immutable" if cache else "no-store")
        if filename:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        self.wfile.write(data)
        self._responded = True

    def _dispatch(self, method):
        self._responded = False
        if not self._allowed_host():
            return self.send_json({"error": "Host no permitido"}, HTTPStatus.FORBIDDEN)
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if method != "GET":
                return self.send_json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)
            return self._static(url.path)
        if method != "GET" and not self._same_origin():
            return self.send_json({"error": "Origen no permitido"}, HTTPStatus.FORBIDDEN)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.send_json({"error": "Archivo demasiado grande"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        raw = self.rfile.read(length) if length else b""
        for m, rx, fn in ROUTES:
            match = rx.match(url.path)
            if m == method and match:
                try:
                    body = raw
                    ctype = (self.headers.get("Content-Type") or "")
                    if "json" in ctype and url.path != "/api/import":
                        body = json.loads(raw.decode("utf-8") or "{}")
                    elif not raw and url.path != "/api/import":
                        body = {}
                    result = fn(self, parse_qs(url.query), body, **match.groupdict())
                    if not self._responded:
                        self.send_json(result if result is not None else {"ok": True})
                except st.NotFound as e:
                    self.send_json({"error": str(e)}, HTTPStatus.NOT_FOUND)
                except ApiError as e:
                    self.send_json({"error": str(e)}, e.status)
                except (ValueError, KeyError, TypeError) as e:
                    self.send_json({"error": f"Petición no válida: {e}"}, HTTPStatus.BAD_REQUEST)
                except Exception as e:  # noqa: BLE001
                    sys.stderr.write(f"Error en {method} {url.path}: {e!r}\n")
                    self.send_json({"error": f"Error interno: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
        self.send_json({"error": "Ruta no encontrada"}, HTTPStatus.NOT_FOUND)

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            full = os.path.join(STATIC_DIR, "index.html")
        with open(full, "rb") as f:
            data = f.read()
        self.send_bytes(data, STATIC_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


def backup_loop():
    """Respaldo automático: al arrancar si toca, y luego cada N horas."""
    while True:
        try:
            hours = float(st.load_config().get("horas_entre_respaldos") or 0)
            if hours > 0:
                last = STORE.last_backup()
                due = True
                if last:
                    age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(last)
                    due = age.total_seconds() >= hours * 3600
                if due:
                    info = STORE.create_backup("automatico")
                    sys.stderr.write(f"Respaldo automático: {info['archivo']}\n")
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"Fallo el respaldo automático: {e!r}\n")
        time.sleep(600)


def main():
    global STORE
    cfg = st.load_config()
    ap = argparse.ArgumentParser(description="Libreta: cuadernos de notas locales")
    ap.add_argument("--puerto", type=int, default=int(cfg.get("puerto") or 8765))
    ap.add_argument("--abrir", action="store_true", help="abre el navegador al iniciar")
    args = ap.parse_args()

    STORE = st.Store()
    STORE.seed()
    threading.Thread(target=backup_loop, daemon=True).start()

    url = f"http://127.0.0.1:{args.puerto}/"
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.puerto), Handler)
    except OSError:
        # Ya hay una Libreta abierta en ese puerto: solo abrimos el navegador.
        print(f"Libreta ya está en marcha en {url}")
        if args.abrir:
            webbrowser.open(url)
        return
    print(f"Libreta en {url}  (datos: {st.DATA_DIR})")
    if args.abrir:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
