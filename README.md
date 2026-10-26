# Libreta

Cuadernos de notas al estilo OneNote para Linux Mint. Tiene tres partes:

- La **aplicación** se abre en una ventana del navegador y guarda todo en tu equipo, sin nube.
- Los **respaldos** se exportan a JSON, Markdown o SQLite, se hacen automáticamente y se pueden restaurar.
- El **servidor MCP** permite que Antigravity lea todo lo que hay en tus cuadernos, bloque por bloque.

Solo necesita Python 3.9 o superior, que Linux Mint 21 y 22 ya traen. No instala nada de internet.

## Instalar en Linux Mint

Abre una terminal y pega esto:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/pedromogollon3-coder/libreta/main/install.sh)"
```

**Para actualizar** a la última versión, ejecuta el mismo comando. Tus notas no se tocan.

Si no tienes `curl`: `sudo apt install curl`. También puedes clonar el repositorio y ejecutar `bash instalar.sh` dentro de la carpeta.

El instalador hace lo siguiente:

1. Comprueba que tu Python sirve (3.9+ con búsqueda FTS5 de SQLite).
2. Copia la app a `~/.local/share/libreta/app`.
3. Crea el comando `libreta` y la entrada **Libreta** en el menú de Mint, en Oficina.
4. Deja el servidor activo en segundo plano y hace que arranque solo al iniciar sesión, mediante un servicio de usuario de systemd.
5. Pregunta si quieres conectarlo con Antigravity. Si dices que sí, añade la entrada `libreta` a `~/.gemini/antigravity/mcp_config.json` y guarda antes una copia del archivo.

Si tienes Chromium, Chrome o Brave, Libreta se abre en su propia ventana, sin barra de direcciones. Si no, se abre en tu navegador por defecto en `http://127.0.0.1:8765`.

Para desinstalar, desde una copia del repositorio:

```bash
bash desinstalar.sh
```

Tus notas se conservan salvo que añadas `--borrar-datos`.

## Dónde están tus datos

| Qué | Ruta |
|---|---|
| Base de datos | `~/.local/share/libreta/libreta.db` |
| Respaldos automáticos | `~/.local/share/libreta/respaldos/` |
| Ajustes | `~/.local/share/libreta/config.json` |

## Cómo se usa

- **Cuadernos → Secciones → Páginas**, igual que en OneNote. Con el botón `⋯` de cada uno puedes renombrar, cambiar el color, mover, exportar, copiar su ID o eliminar.
- Cada página está hecha de **bloques**. Pulsa Enter para crear uno nuevo y escribe `/` para elegir su tipo.
- **Atajos al escribir:**

  | Escribe | Obtienes |
  |---|---|
  | `# ` `## ` `### ` | Títulos |
  | `- ` | Viñeta |
  | `1. ` | Lista numerada |
  | `[] ` | Tarea |
  | `> ` | Cita |
  | `!> ` | Nota destacada |
  | ` ``` ` | Código |
  | `---` | Separador |

- **Imágenes:** pégalas con Ctrl+V o arrástralas a la página. Se guardan dentro de la base de datos.
- **Asa `⋮⋮`** de cada bloque: arrástrala para reordenar, o haz clic para convertir el bloque, ponerle etiquetas, duplicarlo, copiar su ID o eliminarlo.
- **Ctrl+K** abre la búsqueda en todos los cuadernos. No distingue acentos ni mayúsculas.
- Se guarda solo mientras escribes; Ctrl+S fuerza el guardado.
- Arrastra una página a otra sección para moverla.

## Respaldo y exportación

Abre el botón **⤓ Respaldo**:

- **JSON completo:** incluye todo, imágenes también, y se puede restaurar. Es el formato de respaldo.
- **Markdown en ZIP:** una carpeta por cuaderno y sección, un `.md` por página y las imágenes aparte. Se lee con cualquier editor.
- **Base de datos SQLite:** una copia exacta de `libreta.db`.
- **Respaldo automático:** cada 12 horas por defecto, y siempre antes de borrar o restaurar algo. Se conservan los 30 últimos.
- **Copiar también a otra carpeta:** por ejemplo `~/Nextcloud/Libreta` o un disco USB, para tener una copia fuera del equipo.
- **Importar:** carga un JSON. Puedes añadirlo como cuadernos nuevos o sustituir todo el contenido actual; en ese caso antes se hace un respaldo.

También puedes respaldar copiando la carpeta `~/.local/share/libreta` entera.

## Antigravity

Antigravity arranca `mcp_server.py` por su cuenta y lee la misma base de datos. No hace falta tener Libreta abierta. Si no lo conectaste durante la instalación, abre **⚡ Antigravity** en la app, copia la configuración y pégala en `~/.gemini/antigravity/mcp_config.json`. En Antigravity también se llega desde `…` › MCP Servers › Manage MCP Servers › View raw config.

```json
{
  "mcpServers": {
    "libreta": {
      "command": "/usr/bin/python3",
      "args": ["/home/TU_USUARIO/.local/share/libreta/app/mcp_server.py"]
    }
  }
}
```

Reinicia Antigravity y pregúntale cosas como «usa libreta y dime qué cuadernos tengo» o «lista mis tareas pendientes de libreta».

### Herramientas de lectura (siempre activas)

| Herramienta | Qué devuelve |
|---|---|
| `estructura` | Árbol completo con IDs, número de bloques y fechas |
| `leer_pagina` | La página entera en Markdown, con el ID, tipo y etiquetas de cada bloque (o en JSON) |
| `leer_seccion`, `leer_cuaderno` | Todas las páginas de una sección o cuaderno |
| `volcar_todo` | Todo el contenido, paginado para textos grandes |
| `leer_bloque` | Un bloque con todos sus campos y su ruta |
| `buscar` | Búsqueda de texto completo con ruta e IDs |
| `buscar_por_etiqueta` | Bloques con una etiqueta |
| `listar_tareas` | Tareas pendientes, hechas o todas |
| `cambios_recientes` | Páginas ordenadas por última modificación |
| `ver_imagen` | La imagen de un bloque |
| `crear_respaldo` | Crea un respaldo JSON en ese momento |

### Herramientas de escritura (opcionales)

Si añades `"env": {"LIBRETA_MCP_ESCRITURA": "1"}` a la entrada de `libreta`, Antigravity también puede usar `crear_cuaderno`, `crear_seccion`, `crear_pagina` (desde Markdown), `agregar_bloques`, `editar_bloque`, `eliminar_bloque` y `renombrar_pagina`.

Si Antigravity cambia una página que tienes abierta, Libreta muestra el cambio en unos segundos. Si en ese momento estabas escribiendo en ella, combina los dos cambios en lugar de pisar uno con otro.

## Otros detalles

- El servidor solo escucha en `127.0.0.1` y rechaza peticiones que vengan de otras páginas web.
- Puedes cambiar el puerto con `"puerto"` en `config.json`, o al instalar con `LIBRETA_PUERTO=9000 bash instalar.sh`.
- Puedes usar otra carpeta de datos con la variable de entorno `LIBRETA_DATOS=/ruta`.
- Si quieres arrancarlo a mano: `python3 server.py --abrir`.
