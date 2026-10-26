"""Servidor MCP de Libreta para Antigravity (o cualquier cliente MCP por stdio).

Lee la misma base de datos que la aplicación. Por defecto solo lectura; con la
variable de entorno LIBRETA_MCP_ESCRITURA=1 también puede crear y editar.

Protocolo: JSON-RPC 2.0, un mensaje por línea en stdin/stdout. Todo lo que no sea
protocolo se escribe en stderr.
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store as st  # noqa: E402

SERVER_INFO = {"name": "libreta", "title": "Libreta (cuadernos de notas)", "version": "1.0.0"}
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
WRITE_ENABLED = os.environ.get("LIBRETA_MCP_ESCRITURA", "").strip().lower() in ("1", "true", "si", "sí", "yes")
DEFAULT_MAX_CHARS = 60000

INSTRUCTIONS = """Libreta es la aplicación de notas del usuario, organizada como OneNote:
cuadernos > secciones > páginas > bloques. Cada bloque tiene id, tipo (paragraph, heading1-3,
bullet, numbered, todo, quote, callout, code, divider, image), contenido de texto, etiquetas y fechas.
Para responder sobre su contenido: empieza con `estructura` para ver todo el árbol con IDs,
usa `buscar` para localizar texto y `leer_pagina` para leer una página completa con los IDs de sus bloques.
`volcar_todo` devuelve el contenido entero paginado. Cita los nombres de cuaderno, sección y página
cuando respondas. Las herramientas de escritura solo existen si el usuario las activó."""


def _s(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


FORMATO = {"type": "string", "enum": ["markdown", "json"], "description": "markdown (por defecto) o json con todos los campos"}
PAGINACION = {
    "desde": {"type": "integer", "minimum": 0, "description": "Carácter desde el que continuar (para textos largos)"},
    "max_caracteres": {"type": "integer", "minimum": 1000, "description": f"Tamaño máximo de la respuesta (por defecto {DEFAULT_MAX_CHARS})"},
}

READ_TOOLS = [
    {
        "name": "estructura",
        "description": "Árbol completo de Libreta: cuadernos, secciones y páginas con sus IDs, número de bloques y fechas, más estadísticas generales. Úsalo primero.",
        "inputSchema": _s({}),
    },
    {
        "name": "leer_pagina",
        "description": "Lee una página completa: título, ruta y todos sus bloques en orden. En markdown cada bloque va precedido de un comentario con su id, tipo y etiquetas.",
        "inputSchema": _s({"page_id": {"type": "string"}, "formato": FORMATO,
                           "incluir_ids": {"type": "boolean", "description": "Incluir IDs de bloque (por defecto sí)"}}, ["page_id"]),
    },
    {
        "name": "leer_seccion",
        "description": "Lee todas las páginas de una sección, completas y en orden.",
        "inputSchema": _s({"section_id": {"type": "string"}, "formato": FORMATO, **PAGINACION}, ["section_id"]),
    },
    {
        "name": "leer_cuaderno",
        "description": "Lee un cuaderno entero: todas sus secciones y páginas con su contenido.",
        "inputSchema": _s({"notebook_id": {"type": "string"}, "formato": FORMATO, **PAGINACION}, ["notebook_id"]),
    },
    {
        "name": "volcar_todo",
        "description": "Devuelve TODO el contenido de Libreta (todos los cuadernos) en markdown con IDs, o en json. Paginado: si se corta, indica el valor de 'desde' para continuar.",
        "inputSchema": _s({"formato": FORMATO, **PAGINACION}),
    },
    {
        "name": "leer_bloque",
        "description": "Devuelve un bloque concreto con todos sus campos y su ruta (cuaderno, sección, página).",
        "inputSchema": _s({"block_id": {"type": "string"}}, ["block_id"]),
    },
    {
        "name": "buscar",
        "description": "Búsqueda de texto completo en todos los bloques y títulos de página (ignora acentos y mayúsculas, admite prefijos). Devuelve fragmentos con la ruta y los IDs.",
        "inputSchema": _s({"texto": {"type": "string"}, "limite": {"type": "integer", "minimum": 1, "maximum": 200}}, ["texto"]),
    },
    {
        "name": "buscar_por_etiqueta",
        "description": "Lista los bloques que tienen una etiqueta (por ejemplo «urgente» o «#idea»).",
        "inputSchema": _s({"etiqueta": {"type": "string"}}, ["etiqueta"]),
    },
    {
        "name": "listar_tareas",
        "description": "Lista los bloques de tipo tarea (casillas) con su estado y su ruta.",
        "inputSchema": _s({"estado": {"type": "string", "enum": ["pendientes", "hechas", "todas"]},
                           "notebook_id": {"type": "string", "description": "Limitar a un cuaderno"}}),
    },
    {
        "name": "cambios_recientes",
        "description": "Páginas ordenadas por última modificación, opcionalmente desde una fecha ISO 8601.",
        "inputSchema": _s({"limite": {"type": "integer", "minimum": 1, "maximum": 200},
                           "desde_fecha": {"type": "string", "description": "p. ej. 2026-10-01"}}),
    },
    {
        "name": "ver_imagen",
        "description": "Devuelve una imagen guardada en un bloque de tipo image (usa el asset_id que aparece en el bloque).",
        "inputSchema": _s({"asset_id": {"type": "string"}}, ["asset_id"]),
    },
    {
        "name": "crear_respaldo",
        "description": "Crea ahora un respaldo JSON completo de Libreta (con imágenes) y devuelve la ruta del archivo.",
        "inputSchema": _s({}),
    },
]

BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(st.BLOCK_TYPES)},
        "content": {"type": "string"},
        "props": {"type": "object", "description": "todo: {checked}; code: {language}"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type"],
}

WRITE_TOOLS = [
    {
        "name": "crear_cuaderno",
        "description": "Crea un cuaderno nuevo con una primera sección.",
        "inputSchema": _s({"titulo": {"type": "string"}, "primera_seccion": {"type": "string"}}, ["titulo"]),
    },
    {
        "name": "crear_seccion",
        "description": "Crea una sección dentro de un cuaderno.",
        "inputSchema": _s({"notebook_id": {"type": "string"}, "titulo": {"type": "string"}}, ["notebook_id", "titulo"]),
    },
    {
        "name": "crear_pagina",
        "description": "Crea una página en una sección. El contenido se da en markdown (# título 1, - viñeta, 1. numerada, - [ ] tarea, > cita, > [!NOTE] nota, ``` código, --- separador).",
        "inputSchema": _s({"section_id": {"type": "string"}, "titulo": {"type": "string"},
                           "markdown": {"type": "string"}}, ["section_id", "titulo"]),
    },
    {
        "name": "agregar_bloques",
        "description": "Añade bloques a una página, al final o después de un bloque concreto. Pasa 'markdown' o una lista 'bloques'.",
        "inputSchema": _s({"page_id": {"type": "string"}, "markdown": {"type": "string"},
                           "bloques": {"type": "array", "items": BLOCK_SCHEMA},
                           "despues_de_bloque": {"type": "string", "description": "ID del bloque tras el que insertar"}}, ["page_id"]),
    },
    {
        "name": "editar_bloque",
        "description": "Cambia el contenido, el tipo, las etiquetas o el estado de tarea de un bloque.",
        "inputSchema": _s({"block_id": {"type": "string"}, "contenido": {"type": "string"},
                           "tipo": {"type": "string", "enum": list(st.BLOCK_TYPES)},
                           "marcada": {"type": "boolean", "description": "Solo para tareas"},
                           "etiquetas": {"type": "array", "items": {"type": "string"}}}, ["block_id"]),
    },
    {
        "name": "eliminar_bloque",
        "description": "Elimina un bloque de una página.",
        "inputSchema": _s({"block_id": {"type": "string"}}, ["block_id"]),
    },
    {
        "name": "renombrar_pagina",
        "description": "Cambia el título de una página.",
        "inputSchema": _s({"page_id": {"type": "string"}, "titulo": {"type": "string"}}, ["page_id", "titulo"]),
    },
]

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
for _t in READ_TOOLS:
    _t["annotations"] = dict(READ_ONLY)
READ_TOOLS[-1]["annotations"] = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
for _t in WRITE_TOOLS:
    _t["annotations"] = {"readOnlyHint": False, "destructiveHint": _t["name"] == "eliminar_bloque", "openWorldHint": False}


def all_tools(write=None):
    return READ_TOOLS + (WRITE_TOOLS if (WRITE_ENABLED if write is None else write) else [])


# ---------- implementación ----------

class ToolError(Exception):
    pass


STORE = None


def store():
    global STORE
    if STORE is None:
        STORE = st.Store()
    return STORE


def _paginate(text, args):
    start = int(args.get("desde") or 0)
    size = int(args.get("max_caracteres") or DEFAULT_MAX_CHARS)
    chunk = text[start:start + size]
    end = start + len(chunk)
    if end < len(text):
        chunk += (f"\n\n[… contenido cortado: se mostraron los caracteres {start}–{end} de {len(text)}. "
                  f"Llama otra vez con desde={end} para continuar.]")
    return chunk


def _tree_markdown(tree, stats):
    lines = [f"Libreta: {stats['cuadernos']} cuadernos, {stats['secciones']} secciones, {stats['paginas']} páginas, "
             f"{stats['bloques']} bloques, {stats['imagenes']} imágenes. Última modificación: {stats['ultima_modificacion']}.", ""]
    for nb in tree:
        lines.append(f"📒 {nb['title']}  [notebook_id={nb['id']}]")
        for s in nb["sections"]:
            lines.append(f"   📑 {s['title']}  [section_id={s['id']}]")
            for p in s["pages"]:
                lines.append(f"      📄 {p['title'] or 'Sin título'}  [page_id={p['id']}] · {p['block_count']} bloques · modificada {p['updated_at']}")
            if not s["pages"]:
                lines.append("      (sin páginas)")
    return "\n".join(lines)


def _pages_markdown(notebooks, with_ids=True):
    db = store()
    out = []
    for nb in notebooks:
        out.append(f"# 📒 Cuaderno: {nb['title']}\n<!-- notebook:{nb['id']} -->\n")
        for s in nb["sections"]:
            out.append(f"## 📑 Sección: {s['title']}\n<!-- section:{s['id']} -->\n")
            for p in s["pages"]:
                md = db.page_markdown(p["id"], with_ids=with_ids)
                out.append("### 📄 Página: " + md[2:])  # page_markdown empieza con "# título"
    return "\n".join(out)


def _block_line(b):
    estado = ""
    if b["type"] == "todo":
        estado = "[x] " if b["props"].get("checked") else "[ ] "
    tags = (" #" + " #".join(b["tags"])) if b["tags"] else ""
    return (f"- {estado}{b['content']}{tags}\n  ↳ {b['cuaderno']} › {b['seccion']} › {b['pagina'] or 'Sin título'}"
            f"  [page_id={b['page_id']} block_id={b['id']}]")


def call_tool(name, args):
    db = store()
    fmt = args.get("formato") or "markdown"

    if name == "estructura":
        tree, stats = db.tree(), db.stats()
        return _tree_markdown(tree, stats)

    if name == "leer_pagina":
        if fmt == "json":
            return json.dumps(db.get_page(args["page_id"]), ensure_ascii=False, indent=1)
        page = db.get_page(args["page_id"], with_blocks=False)
        md = db.page_markdown(args["page_id"], with_ids=args.get("incluir_ids", True))
        return f"Ruta: {page['notebook_title']} › {page['section_title']}\n\n{md}"

    if name in ("leer_seccion", "leer_cuaderno", "volcar_todo"):
        tree = db.tree()
        if name == "leer_seccion":
            found = [(nb, s) for nb in tree for s in nb["sections"] if s["id"] == args["section_id"]]
            if not found:
                raise ToolError(f"No existe la sección {args['section_id']}. Usa 'estructura' para ver los IDs.")
            nb, s = found[0]
            tree = [dict(nb, sections=[s])]
        elif name == "leer_cuaderno":
            tree = [nb for nb in tree if nb["id"] == args["notebook_id"]]
            if not tree:
                raise ToolError(f"No existe el cuaderno {args['notebook_id']}. Usa 'estructura' para ver los IDs.")
        if fmt == "json":
            data = db.export_data(include_assets=False)
            wanted = {p["id"] for nb in tree for s in nb["sections"] for p in s["pages"]}
            for nb in data["notebooks"]:
                for s in nb["sections"]:
                    s["pages"] = [p for p in s["pages"] if p["id"] in wanted]
                nb["sections"] = [s for s in nb["sections"] if s["pages"] or any(s["id"] == x["id"] for t in tree for x in t["sections"])]
            data["notebooks"] = [nb for nb in data["notebooks"] if any(nb["id"] == t["id"] for t in tree)]
            text = json.dumps(data, ensure_ascii=False, indent=1)
        else:
            text = _pages_markdown(tree)
        return _paginate(text, args)

    if name == "leer_bloque":
        return json.dumps(db.get_block(args["block_id"]), ensure_ascii=False, indent=1)

    if name == "buscar":
        res = db.search(args["texto"], int(args.get("limite") or 30))
        if not res:
            return f"Sin resultados para «{args['texto']}»."
        lines = [f"{len(res)} resultado(s) para «{args['texto']}»:"]
        for r in res:
            path = f"- {r['cuaderno']} › {r['seccion']} › {r['pagina'] or 'Sin título'}"
            if r["coincide_en"] == "titulo_pagina":
                lines.append(f"{path} (coincide el título)\n  [page_id={r['page_id']}]")
                continue
            text = r["contenido"] if len(r["contenido"]) <= 600 else r["fragmento"].replace("«", "**").replace("»", "**")
            lines.append(f"{path} (bloque {r['type']})\n  {text}\n  [page_id={r['page_id']} block_id={r['id']}]")
        return "\n".join(lines)

    if name == "buscar_por_etiqueta":
        res = db.blocks_by_tag(args["etiqueta"])
        if not res:
            return f"Ningún bloque tiene la etiqueta «{args['etiqueta']}»."
        return f"{len(res)} bloque(s) con #{args['etiqueta'].lstrip('#')}:\n" + "\n".join(_block_line(b) for b in res)

    if name == "listar_tareas":
        estado = args.get("estado") or "pendientes"
        res = db.todos(estado, args.get("notebook_id"))
        if not res:
            return f"No hay tareas ({estado})."
        return f"{len(res)} tarea(s) {estado}:\n" + "\n".join(_block_line(b) for b in res)

    if name == "cambios_recientes":
        res = db.recent_pages(int(args.get("limite") or 20), args.get("desde_fecha"))
        if not res:
            return "No hay páginas modificadas en ese periodo."
        return "\n".join(f"- {r['updated_at']} · {r['cuaderno']} › {r['seccion']} › {r['title'] or 'Sin título'}"
                         f" ({r['bloques']} bloques) [page_id={r['id']}]" for r in res)

    if name == "ver_imagen":
        a = db.get_asset(args["asset_id"])
        return [{"type": "image", "data": base64.b64encode(a["data"]).decode("ascii"), "mimeType": a["mime"]},
                {"type": "text", "text": f"Imagen «{a['name']}» ({a['mime']}, {a['size']} bytes)"}]

    if name == "crear_respaldo":
        info = db.create_backup("antigravity")
        return f"Respaldo creado: {info['archivo']} ({info['bytes']} bytes). Contiene: {info['estadisticas']}"

    # --- escritura ---
    if name in {t["name"] for t in WRITE_TOOLS}:
        if not WRITE_ENABLED:
            raise ToolError("La escritura está desactivada. El usuario debe añadir LIBRETA_MCP_ESCRITURA=1 en la configuración MCP.")
        if name == "crear_cuaderno":
            nb = db.create_notebook(args["titulo"])
            s = db.create_section(nb["id"], args.get("primera_seccion") or "General")
            return f"Cuaderno creado [notebook_id={nb['id']}] con la sección «{s['title']}» [section_id={s['id']}]."
        if name == "crear_seccion":
            s = db.create_section(args["notebook_id"], args["titulo"])
            return f"Sección creada [section_id={s['id']}]."
        if name == "crear_pagina":
            p = db.create_page(args["section_id"], args["titulo"], st.markdown_to_blocks(args.get("markdown") or ""))
            return f"Página creada [page_id={p['id']}] con {len(p['blocks'])} bloque(s):\n\n" + db.page_markdown(p["id"], with_ids=True)
        if name == "agregar_bloques":
            blocks = args.get("bloques") or st.markdown_to_blocks(args.get("markdown") or "")
            if not blocks:
                raise ToolError("Indica 'markdown' o 'bloques' con contenido.")
            new = db.add_blocks(args["page_id"], blocks, args.get("despues_de_bloque"))
            return f"Añadidos {len(new)} bloque(s): " + ", ".join(b["id"] for b in new)
        if name == "editar_bloque":
            props = {"checked": bool(args["marcada"])} if "marcada" in args else None
            b = db.update_block(args["block_id"], content=args.get("contenido"), type=args.get("tipo"),
                                props=props, tags=args.get("etiquetas"))
            return "Bloque actualizado:\n" + json.dumps(b, ensure_ascii=False, indent=1)
        if name == "eliminar_bloque":
            db.delete_block(args["block_id"])
            return f"Bloque {args['block_id']} eliminado."
        if name == "renombrar_pagina":
            db.update_page(args["page_id"], title=args["titulo"])
            return "Título cambiado."

    raise ToolError(f"Herramienta desconocida: {name}")


# ---------- JSON-RPC ----------

def handle(msg):
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS + ("\nEscritura ACTIVADA." if WRITE_ENABLED else "\nModo solo lectura."),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": all_tools()}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = call_tool(name, args)
            content = result if isinstance(result, list) else [{"type": "text", "text": result}]
            return {"content": content, "isError": False}
        except (ToolError, st.NotFound, ValueError, KeyError) as e:
            text = f"Falta el parámetro {e}" if isinstance(e, KeyError) else str(e)
            return {"content": [{"type": "text", "text": text}], "isError": True}
    if method in ("resources/list", "resources/templates/list"):
        return {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}
    raise LookupError(method)


def send(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main():
    sys.stderr.write(f"Libreta MCP listo (datos: {st.DATA_DIR}, escritura: {'sí' if WRITE_ENABLED else 'no'})\n")
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip().lstrip("﻿")
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON no válido"}})
            continue
        batch = msg if isinstance(msg, list) else [msg]
        for m in batch:
            if not isinstance(m, dict) or "method" not in m:
                continue  # respuestas del cliente u objetos extraños
            mid = m.get("id")
            try:
                result = handle(m)
                if mid is not None:
                    send({"jsonrpc": "2.0", "id": mid, "result": result})
            except LookupError:
                if mid is not None:
                    send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Método no soportado: {m.get('method')}"}})
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"Error en {m.get('method')}: {e!r}\n")
                if mid is not None:
                    send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
