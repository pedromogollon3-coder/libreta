'use strict';

// ---------- Estado ----------
const S = {
  tree: [],
  stats: null,
  sectionId: null,
  page: null,
  dirty: false,
  saving: false,
  timer: null,
  slash: null,
  drag: null,
  open: new Set(load('libreta-open', [])),
};

const TYPES = [
  { type: 'paragraph', label: 'Texto', icon: '¶', hint: '' },
  { type: 'heading1', label: 'Título 1', icon: 'H1', hint: '#' },
  { type: 'heading2', label: 'Título 2', icon: 'H2', hint: '##' },
  { type: 'heading3', label: 'Título 3', icon: 'H3', hint: '###' },
  { type: 'bullet', label: 'Lista con viñetas', icon: '•', hint: '-' },
  { type: 'numbered', label: 'Lista numerada', icon: '1.', hint: '1.' },
  { type: 'todo', label: 'Tarea', icon: '☐', hint: '[]' },
  { type: 'quote', label: 'Cita', icon: '❝', hint: '>' },
  { type: 'callout', label: 'Nota destacada', icon: '💡', hint: '' },
  { type: 'code', label: 'Código', icon: '</>', hint: '```' },
  { type: 'divider', label: 'Separador', icon: '—', hint: '---' },
];
const LIST_TYPES = ['bullet', 'numbered', 'todo'];
const COLORS = ['#7719aa', '#0f6cbd', '#107c41', '#c50f1f', '#ca5010', '#8764b8', '#038387', '#986f0b'];
const PLACEHOLDER = {
  paragraph: 'Escribe algo, o «/» para elegir el tipo de bloque',
  heading1: 'Título 1', heading2: 'Título 2', heading3: 'Título 3',
  bullet: 'Elemento de lista', numbered: 'Elemento de lista', todo: 'Tarea',
  quote: 'Cita', callout: 'Nota destacada', code: 'Código', image: 'Pie de imagen (opcional)',
};

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null) n.append(kid);
  return n;
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString('es', { dateStyle: 'medium', timeStyle: 'short' }) : '');
const CE_MODE = (() => { const d = document.createElement('div'); d.contentEditable = 'plaintext-only'; return d.contentEditable === 'plaintext-only' ? 'plaintext-only' : 'true'; })();

function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* sin almacenamiento local */ }
}

async function api(method, url, body, rawType) {
  const opts = { method, headers: {} };
  if (rawType) { opts.body = body; opts.headers['Content-Type'] = rawType; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Error ${res.status}`), { status: res.status, data });
  return data;
}

function toast(msg, isErr = false) {
  const t = el('div', { class: 'toast' + (isErr ? ' err' : ''), text: msg });
  document.body.append(t);
  setTimeout(() => t.remove(), isErr ? 5000 : 2600);
}

async function copy(text, what = 'Copiado') {
  try { await navigator.clipboard.writeText(text); toast(what); }
  catch { await ask({ title: 'Copia este texto', value: text, ok: 'Cerrar' }); }
}

// ---------- Menús y diálogos ----------
let openMenuEl = null;
function closeMenu() { if (openMenuEl) { openMenuEl.remove(); openMenuEl = null; } }

function openMenu(items, x, y) {
  closeMenu();
  const m = el('div', { class: 'menu' });
  for (const it of items) {
    if (it === 'sep') { m.append(el('div', { class: 'sep' })); continue; }
    if (it.title) { m.append(el('div', { class: 'title', text: it.title })); continue; }
    if (it.swatches) {
      m.append(el('div', { class: 'swatches' }, it.swatches.map((c) =>
        el('span', { class: 'swatch', style: `background:${c}`, title: c, onclick: () => { closeMenu(); it.action(c); } }))));
      continue;
    }
    m.append(el('div', {
      class: 'item' + (it.danger ? ' danger' : ''),
      onclick: (e) => { e.stopPropagation(); closeMenu(); it.action(); },
    }, el('span', { class: 'ic', text: it.icon || '' }), el('span', { text: it.label }), it.hint ? el('span', { class: 'hint', text: it.hint }) : null));
  }
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
  openMenuEl = m;
  return m;
}
function menuAt(btn, items) {
  const r = btn.getBoundingClientRect();
  openMenu(items, r.left, r.bottom + 4);
}
document.addEventListener('mousedown', (e) => {
  if (openMenuEl && !openMenuEl.contains(e.target)) { closeMenu(); closeSlash(); }
});

function dialog(build) {
  return new Promise((resolve) => {
    const ov = el('div', { class: 'overlay' });
    const box = el('div', { class: 'dialog' });
    const done = (v) => { ov.remove(); document.removeEventListener('keydown', onKey, true); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
    document.addEventListener('keydown', onKey, true);
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) done(null); });
    build(box, done);
    ov.append(box);
    document.body.append(ov);
    const f = box.querySelector('[autofocus]') || box.querySelector('input,button.primary');
    if (f) { f.focus(); if (f.select) f.select(); }
  });
}

function ask({ title, label, value = '', ok = 'Aceptar', placeholder = '' }) {
  return dialog((box, done) => {
    const inp = el('input', { type: 'text', value, placeholder, autofocus: true });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(inp.value.trim()); });
    box.append(el('h2', { text: title }), label ? el('p', { text: label }) : null, inp,
      el('div', { class: 'actions' },
        el('button', { class: 'btn', text: 'Cancelar', onclick: () => done(null) }),
        el('button', { class: 'btn primary', text: ok, onclick: () => done(inp.value.trim()) })));
  });
}

function confirmBox({ title, message, ok = 'Aceptar', danger = false }) {
  return dialog((box, done) => {
    box.append(el('h2', { text: title }), el('p', { text: message }),
      el('div', { class: 'actions' },
        el('button', { class: 'btn', text: 'Cancelar', onclick: () => done(false) }),
        el('button', { class: 'btn primary' + (danger ? ' danger' : ''), text: ok, onclick: () => done(true) })));
  }).then((v) => !!v);
}

// ---------- Árbol ----------
function findSection(id) {
  for (const nb of S.tree) for (const s of nb.sections) if (s.id === id) return { nb, s };
  return null;
}
function findPageMeta(id) {
  for (const nb of S.tree) for (const s of nb.sections) for (const p of s.pages) if (p.id === id) return { nb, s, p };
  return null;
}

async function loadTree() {
  const data = await api('GET', '/api/tree');
  S.tree = data.notebooks;
  S.stats = data.stats;
  if (S.sectionId && !findSection(S.sectionId)) S.sectionId = null;
  if (!S.sectionId) {
    const first = S.tree.find((nb) => nb.sections.length);
    if (first) { S.sectionId = first.sections[0].id; S.open.add(first.id); }
  }
  renderNotebooks();
  renderPages();
}

function renderNotebooks() {
  const root = $('#notebooks');
  root.replaceChildren();
  if (!S.tree.length) root.append(el('div', { class: 'pages-empty', text: 'Crea tu primer cuaderno con «+ Cuaderno».' }));
  for (const nb of S.tree) {
    const open = S.open.has(nb.id);
    const more = el('button', { class: 'more', text: '⋯', title: 'Opciones del cuaderno' });
    more.addEventListener('click', (e) => { e.stopPropagation(); menuAt(more, notebookMenu(nb)); });
    const row = el('div', { class: 'row nb-row', title: nb.title },
      el('span', { class: 'chev', text: '▶' }),
      el('span', { class: 'nb-icon', style: `background:${nb.color}` }),
      el('span', { class: 'label', text: nb.title }), more);
    row.addEventListener('click', () => {
      if (S.open.has(nb.id)) S.open.delete(nb.id); else S.open.add(nb.id);
      store('libreta-open', [...S.open]);
      renderNotebooks();
    });
    const secs = el('div', { class: 'sections' });
    for (const s of nb.sections) {
      const smore = el('button', { class: 'more', text: '⋯', title: 'Opciones de la sección' });
      smore.addEventListener('click', (e) => { e.stopPropagation(); menuAt(smore, sectionMenu(nb, s)); });
      const srow = el('div', { class: 'row' + (s.id === S.sectionId ? ' active' : ''), title: s.title },
        el('span', { class: 'sec-dot', style: `background:${s.color}` }),
        el('span', { class: 'label', text: s.title }), smore);
      srow.addEventListener('click', () => selectSection(s.id));
      // Soltar una página aquí la mueve a esta sección
      srow.addEventListener('dragover', (e) => { if (S.drag?.kind === 'page') { e.preventDefault(); srow.classList.add('active'); } });
      srow.addEventListener('dragleave', () => { if (s.id !== S.sectionId) srow.classList.remove('active'); });
      srow.addEventListener('drop', (e) => { e.preventDefault(); if (S.drag?.kind === 'page') movePage(S.drag.id, s.id); });
      secs.append(srow);
    }
    secs.append(el('div', { class: 'row add-sec', text: '+ Sección', onclick: () => addSection(nb) }));
    root.append(el('div', { class: 'nb' + (open ? ' open' : '') }, row, secs));
  }
}

function notebookMenu(nb) {
  return [
    { icon: '✎', label: 'Cambiar nombre', action: async () => {
      const t = await ask({ title: 'Cambiar nombre del cuaderno', value: nb.title });
      if (t) { await api('PATCH', `/api/notebooks/${nb.id}`, { title: t }); loadTree(); }
    } },
    { title: 'Color' },
    { swatches: COLORS, action: async (c) => { await api('PATCH', `/api/notebooks/${nb.id}`, { color: c }); loadTree(); } },
    'sep',
    { icon: '+', label: 'Nueva sección', action: () => addSection(nb) },
    { icon: '⤓', label: 'Exportar cuaderno (JSON)', action: () => download(`/api/export/json?notebook_id=${nb.id}`) },
    { icon: '⤓', label: 'Exportar cuaderno (Markdown)', action: () => download(`/api/export/markdown?notebook_id=${nb.id}`) },
    { icon: '#', label: 'Copiar ID del cuaderno', action: () => copy(nb.id, 'ID del cuaderno copiado') },
    'sep',
    { icon: '🗑', label: 'Eliminar cuaderno', danger: true, action: async () => {
      const ok = await confirmBox({ title: 'Eliminar cuaderno', danger: true, ok: 'Eliminar',
        message: `Se eliminará «${nb.title}» con todas sus secciones y páginas. Antes se guarda un respaldo automático por si necesitas recuperarlo.` });
      if (!ok) return;
      await api('POST', '/api/backups');
      await api('DELETE', `/api/notebooks/${nb.id}`);
      if (S.page && findPageMeta(S.page.id)?.nb.id === nb.id) closePage();
      S.sectionId = null;
      loadTree();
    } },
  ];
}

function sectionMenu(nb, s) {
  return [
    { icon: '✎', label: 'Cambiar nombre', action: async () => {
      const t = await ask({ title: 'Cambiar nombre de la sección', value: s.title });
      if (t) { await api('PATCH', `/api/sections/${s.id}`, { title: t }); loadTree(); refreshCrumbs(); }
    } },
    { title: 'Color' },
    { swatches: COLORS, action: async (c) => { await api('PATCH', `/api/sections/${s.id}`, { color: c }); loadTree(); } },
    'sep',
    { icon: '↦', label: 'Mover a otro cuaderno…', action: () => {
      const r = $('#notebooks').getBoundingClientRect();
      openMenu([{ title: 'Mover a' }, ...S.tree.filter((n) => n.id !== nb.id).map((n) => ({
        icon: '▮', label: n.title, action: async () => {
          await api('PATCH', `/api/sections/${s.id}`, { notebook_id: n.id }); S.open.add(n.id); loadTree();
        } }))], r.right - 20, 120);
    } },
    { icon: '#', label: 'Copiar ID de la sección', action: () => copy(s.id, 'ID de la sección copiado') },
    'sep',
    { icon: '🗑', label: 'Eliminar sección', danger: true, action: async () => {
      const ok = await confirmBox({ title: 'Eliminar sección', danger: true, ok: 'Eliminar',
        message: `Se eliminará «${s.title}» y sus ${s.pages.length} página(s). Antes se guarda un respaldo automático.` });
      if (!ok) return;
      await api('POST', '/api/backups');
      await api('DELETE', `/api/sections/${s.id}`);
      if (S.sectionId === s.id) { S.sectionId = null; closePage(); }
      loadTree();
    } },
  ];
}

async function addNotebook() {
  const t = await ask({ title: 'Nuevo cuaderno', placeholder: 'Nombre del cuaderno', ok: 'Crear' });
  if (!t) return;
  const nb = await api('POST', '/api/notebooks', { title: t });
  const s = await api('POST', '/api/sections', { notebook_id: nb.id, title: 'Sección nueva' });
  S.open.add(nb.id);
  store('libreta-open', [...S.open]);
  await loadTree();
  await selectSection(s.id);
  addPage();
}

async function addSection(nb) {
  const t = await ask({ title: 'Nueva sección', label: `En «${nb.title}»`, placeholder: 'Nombre de la sección', ok: 'Crear' });
  if (!t) return;
  const s = await api('POST', '/api/sections', { notebook_id: nb.id, title: t });
  S.open.add(nb.id);
  await loadTree();
  await selectSection(s.id);
  addPage();
}

async function selectSection(id) {
  S.sectionId = id;
  renderNotebooks();
  renderPages();
  const f = findSection(id);
  const last = load('libreta-last-page', {})[id];
  const target = f?.s.pages.find((p) => p.id === last) || f?.s.pages[0];
  if (target) openPage(target.id); else closePage();
}

// ---------- Páginas ----------
function renderPages() {
  const root = $('#pages');
  root.replaceChildren();
  const f = findSection(S.sectionId);
  $('#pages-title').textContent = f ? f.s.title : 'Páginas';
  $('#add-page').disabled = !f;
  if (!f) { root.append(el('div', { class: 'pages-empty', text: 'Elige una sección.' })); return; }
  if (!f.s.pages.length) root.append(el('div', { class: 'pages-empty', text: 'Sin páginas. Pulsa «+ Página».' }));
  for (const p of f.s.pages) {
    const more = el('button', { class: 'more', text: '⋯', title: 'Opciones de la página' });
    more.addEventListener('click', (e) => { e.stopPropagation(); menuAt(more, pageMenu(p)); });
    const title = S.page?.id === p.id ? S.page.title : p.title;
    const row = el('div', { class: 'row page-row' + (S.page?.id === p.id ? ' active' : ''), draggable: 'true' },
      el('span', { class: 'label', text: title || 'Sin título' }),
      el('span', { class: 'date', text: fmtDate(p.updated_at) }), more);
    row.addEventListener('click', () => openPage(p.id));
    row.addEventListener('dragstart', (e) => { S.drag = { kind: 'page', id: p.id }; row.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; });
    row.addEventListener('dragend', () => { S.drag = null; row.classList.remove('dragging'); renderNotebooks(); });
    row.addEventListener('dragover', (e) => { if (S.drag?.kind === 'page' && S.drag.id !== p.id) { e.preventDefault(); row.classList.add('drop-target'); } });
    row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
    row.addEventListener('drop', (e) => { e.preventDefault(); row.classList.remove('drop-target'); reorderPage(S.drag.id, p.id); });
    root.append(row);
  }
}

function pageMenu(p) {
  return [
    { icon: '↦', label: 'Mover a otra sección…', action: () => {
      const items = [{ title: 'Mover a' }];
      for (const nb of S.tree) {
        items.push({ title: nb.title });
        for (const s of nb.sections) if (s.id !== S.sectionId) items.push({ icon: '▪', label: s.title, action: () => movePage(p.id, s.id) });
      }
      const r = $('#pages').getBoundingClientRect();
      openMenu(items, r.left + 40, 120);
    } },
    { icon: '⧉', label: 'Duplicar página', action: async () => {
      await saveNow();
      const full = await api('GET', `/api/pages/${p.id}`);
      const copyPage = await api('POST', '/api/pages', {
        section_id: full.section_id, title: (full.title || 'Sin título') + ' (copia)',
        blocks: full.blocks.map(({ type, content, props, tags }) => ({ type, content, props, tags })) });
      await loadTree(); openPage(copyPage.id);
    } },
    { icon: '⤓', label: 'Descargar como Markdown', action: async () => { await saveNow(); download(`/api/export/page/${p.id}`); } },
    { icon: '#', label: 'Copiar ID de la página', action: () => copy(p.id, 'ID de la página copiado') },
    'sep',
    { icon: '🗑', label: 'Eliminar página', danger: true, action: async () => {
      const ok = await confirmBox({ title: 'Eliminar página', danger: true, ok: 'Eliminar',
        message: `Se eliminará «${p.title || 'Sin título'}». Antes se guarda un respaldo automático.` });
      if (!ok) return;
      if (S.page?.id === p.id) { await saveNow(); closePage(); }
      await api('POST', '/api/backups');
      await api('DELETE', `/api/pages/${p.id}`);
      await loadTree();
      const f = findSection(S.sectionId);
      if (f?.s.pages[0] && !S.page) openPage(f.s.pages[0].id);
    } },
  ];
}

async function addPage() {
  if (!S.sectionId) return;
  await saveNow();
  const p = await api('POST', '/api/pages', { section_id: S.sectionId, title: '' });
  await loadTree();
  await openPage(p.id);
  $('.page-title')?.focus();
}

async function movePage(pageId, sectionId) {
  S.drag = null;
  await saveNow();
  await api('PATCH', `/api/pages/${pageId}`, { section_id: sectionId });
  await loadTree();
  toast('Página movida');
  if (S.page?.id === pageId) { S.sectionId = sectionId; renderNotebooks(); renderPages(); refreshCrumbs(); }
}

async function reorderPage(dragId, targetId) {
  const f = findSection(S.sectionId);
  if (!f || !dragId || dragId === targetId) return;
  const ids = f.s.pages.map((p) => p.id).filter((id) => id !== dragId);
  ids.splice(ids.indexOf(targetId), 0, dragId);
  await Promise.all(ids.map((id, i) => api('PATCH', `/api/pages/${id}`, { position: i + 1 })));
  loadTree();
}

async function openPage(id, highlightBlock) {
  await saveNow();
  try {
    S.page = await api('GET', `/api/pages/${id}`);
  } catch (e) { toast(e.message, true); return; }
  markSynced(S.page);
  if (!S.page.blocks.length) S.page.blocks.push({ id: newId(), type: 'paragraph', content: '', props: {}, tags: [] });
  if (S.sectionId !== S.page.section_id || !S.open.has(S.page.notebook_id)) {
    S.sectionId = S.page.section_id;
    S.open.add(S.page.notebook_id);
    store('libreta-open', [...S.open]);
    renderNotebooks();
  }
  const last = load('libreta-last-page', {});
  last[S.sectionId] = id;
  store('libreta-last-page', last);
  store('libreta-last', { sectionId: S.sectionId, pageId: id });
  renderPages();
  renderEditor();
  if (highlightBlock) {
    const b = document.querySelector(`.block[data-id="${highlightBlock}"]`);
    if (b) { b.scrollIntoView({ block: 'center' }); b.classList.add('flash'); }
  }
}

function closePage() {
  S.page = null;
  renderEditor();
  renderPages();
}

// ---------- Editor ----------
function refreshCrumbs() {
  const c = $('.crumbs');
  if (!c || !S.page) return;
  const f = findPageMeta(S.page.id);
  if (f) c.textContent = `${f.nb.title}  ›  ${f.s.title}`;
}

function renderEditor() {
  const ed = $('#editor');
  ed.replaceChildren();
  if (!S.page) {
    ed.append(el('div', { class: 'editor-empty', text: S.sectionId ? 'Selecciona o crea una página.' : 'Selecciona una sección para ver sus páginas.' }));
    return;
  }
  const title = el('input', { class: 'page-title', type: 'text', placeholder: 'Título de la página', value: S.page.title });
  title.addEventListener('input', () => { S.page.title = title.value; markDirty(); renderPages(); });
  title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === 'ArrowDown') { e.preventDefault(); focusBlock(S.page.blocks[0].id, 0); }
  });
  ed.append(
    el('div', { class: 'editor-meta' }, el('span', { class: 'crumbs' }), el('span', { class: 'save-state', id: 'save-state', text: 'Guardado' })),
    title,
    el('div', { class: 'page-date', text: `Creada ${fmtDate(S.page.created_at)}  ·  modificada ${fmtDate(S.page.updated_at)}` }),
    el('div', { id: 'blocks' }));
  refreshCrumbs();
  renderBlocks();
}

function renderBlocks() {
  const root = $('#blocks');
  if (!root) return;
  root.replaceChildren();
  let n = 0;
  const only = S.page.blocks.length === 1 && S.page.blocks[0].type === 'paragraph' && !S.page.blocks[0].content;
  for (const b of S.page.blocks) {
    n = b.type === 'numbered' ? n + 1 : 0;
    root.append(renderBlock(b, n, only));
  }
}

function renderBlock(b, number, only) {
  const wrap = el('div', { class: `block t-${b.type}` + (b.props?.checked ? ' checked' : '') + (only ? ' only' : ''), 'data-id': b.id });
  const handle = el('button', { class: 'handle', text: '⋮⋮', title: 'Arrastra para mover · clic para opciones', draggable: 'true', tabindex: '-1' });
  handle.addEventListener('click', (e) => { e.stopPropagation(); menuAt(handle, blockMenu(b)); });
  handle.addEventListener('dragstart', (e) => {
    S.drag = { kind: 'block', id: b.id };
    wrap.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setDragImage(wrap, 10, 10);
  });
  handle.addEventListener('dragend', () => { S.drag = null; wrap.classList.remove('dragging'); });
  wrap.append(handle);

  const content = () => {
    const c = el('div', { class: 'content', contenteditable: CE_MODE, spellcheck: 'true', 'data-ph': PLACEHOLDER[b.type] || '' });
    c.textContent = b.content;
    return c;
  };

  if (b.type === 'bullet') wrap.append(el('span', { class: 'prefix', text: '•' }), content());
  else if (b.type === 'numbered') wrap.append(el('span', { class: 'prefix', text: number + '.' }), content());
  else if (b.type === 'todo') {
    const cb = el('input', { type: 'checkbox' });
    cb.checked = !!b.props?.checked;
    cb.addEventListener('change', () => { b.props = { ...b.props, checked: cb.checked }; wrap.classList.toggle('checked', cb.checked); markDirty(); });
    wrap.append(el('span', { class: 'prefix' }, cb), content());
  } else if (b.type === 'callout') wrap.append(el('div', { class: 'body' }, el('span', { text: '💡' }), content()));
  else if (b.type === 'code') {
    const lang = el('input', { class: 'lang', placeholder: 'lenguaje', value: b.props?.language || '' });
    lang.addEventListener('input', () => { b.props = { ...b.props, language: lang.value.trim() }; markDirty(); });
    wrap.append(el('div', { class: 'body' }, lang, content()));
  } else if (b.type === 'divider') {
    const body = el('div', { class: 'body', tabindex: '0' }, el('hr'));
    body.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' || e.key === 'Delete') { e.preventDefault(); removeBlock(b.id, 'prev'); }
      else if (e.key === 'Enter') { e.preventDefault(); insertAfter(b.id, { type: 'paragraph', content: '' }); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); focusNeighbor(b.id, -1); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); focusNeighbor(b.id, 1); }
    });
    wrap.append(body);
  } else if (b.type === 'image') {
    wrap.append(el('div', { class: 'body' },
      el('img', { src: `/api/assets/${b.props?.asset_id}`, alt: b.content || b.props?.name || 'imagen', loading: 'lazy' }), content()));
  } else wrap.append(content());

  if (b.tags?.length) wrap.append(el('div', { class: 'tags' }, b.tags.map((t) => el('span', { class: 'tag', text: '#' + t }))));

  wrap.addEventListener('dragover', (e) => {
    if (S.drag?.kind !== 'block' || S.drag.id === b.id) return;
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
    wrap.classList.toggle('drop-after', after);
    wrap.classList.toggle('drop-before', !after);
  });
  wrap.addEventListener('dragleave', () => wrap.classList.remove('drop-after', 'drop-before'));
  wrap.addEventListener('drop', (e) => {
    if (S.drag?.kind !== 'block') return;
    e.preventDefault();
    const after = wrap.classList.contains('drop-after');
    wrap.classList.remove('drop-after', 'drop-before');
    moveBlockTo(S.drag.id, b.id, after);
  });
  return wrap;
}

function blockMenu(b) {
  const conv = TYPES.filter((t) => t.type !== b.type && b.type !== 'image')
    .map((t) => ({ icon: t.icon, label: t.label, action: () => setType(b.id, t.type) }));
  return [
    ...(conv.length ? [{ title: 'Convertir en' }, ...conv, 'sep'] : []),
    { icon: '#', label: 'Etiquetas…', action: async () => {
      const t = await ask({ title: 'Etiquetas del bloque', label: 'Separadas por comas o espacios. Antigravity puede buscar por ellas.', value: (b.tags || []).join(', '), ok: 'Guardar' });
      if (t === null) return;
      b.tags = t.split(/[,\s]+/).map((x) => x.replace(/^#/, '').trim()).filter(Boolean);
      renderBlocks(); markDirty();
    } },
    { icon: '↑', label: 'Subir', action: () => shiftBlock(b.id, -1) },
    { icon: '↓', label: 'Bajar', action: () => shiftBlock(b.id, 1) },
    { icon: '⧉', label: 'Duplicar', action: () => insertAfter(b.id, { type: b.type, content: b.content, props: { ...b.props }, tags: [...(b.tags || [])] }) },
    { icon: '⌗', label: 'Copiar ID del bloque', hint: b.id.slice(0, 6), action: () => copy(b.id, 'ID del bloque copiado') },
    'sep',
    { icon: '🗑', label: 'Eliminar bloque', danger: true, action: () => removeBlock(b.id, 'prev') },
  ];
}

// --- operaciones sobre bloques ---
const idx = (id) => S.page.blocks.findIndex((b) => b.id === id);
const blockById = (id) => S.page.blocks.find((b) => b.id === id);

function getText(c) {
  return c.querySelector('br,div,p') ? c.innerText.replace(/\n$/, '') : c.textContent;
}

function caretOffset(c) {
  const sel = getSelection();
  if (!sel.rangeCount || !c.contains(sel.anchorNode)) return 0;
  const r = sel.getRangeAt(0);
  const pre = r.cloneRange();
  pre.selectNodeContents(c);
  pre.setEnd(r.startContainer, r.startOffset);
  return pre.toString().length;
}

function setCaret(c, off) {
  c.focus();
  const sel = getSelection();
  const r = document.createRange();
  if (off !== 'end') {
    let rem = off;
    const w = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = w.nextNode())) {
      if (rem <= node.length) { r.setStart(node, rem); r.collapse(true); sel.removeAllRanges(); sel.addRange(r); return; }
      rem -= node.length;
    }
  }
  r.selectNodeContents(c);
  r.collapse(false);
  sel.removeAllRanges();
  sel.addRange(r);
}

function focusBlock(id, off = 'end') {
  const w = document.querySelector(`.block[data-id="${id}"]`);
  if (!w) return;
  const c = w.querySelector('.content');
  if (c) setCaret(c, off);
  else w.querySelector('.body[tabindex]')?.focus();
}

function focusNeighbor(id, dir, off) {
  const i = idx(id) + dir;
  if (i < 0) { $('.page-title')?.focus(); return; }
  if (i >= S.page.blocks.length) return;
  focusBlock(S.page.blocks[i].id, off ?? (dir < 0 ? 'end' : 0));
}

function insertAfter(id, data, focusOff = 0) {
  const b = { id: newId(), type: 'paragraph', content: '', props: {}, tags: [], ...data };
  S.page.blocks.splice(idx(id) + 1, 0, b);
  renderBlocks();
  markDirty();
  focusBlock(b.id, focusOff);
  return b;
}

function removeBlock(id, focus) {
  const i = idx(id);
  if (i < 0) return;
  S.page.blocks.splice(i, 1);
  if (!S.page.blocks.length) S.page.blocks.push({ id: newId(), type: 'paragraph', content: '', props: {}, tags: [] });
  renderBlocks();
  markDirty();
  if (focus) focusBlock(S.page.blocks[Math.max(0, focus === 'prev' ? i - 1 : i)].id);
}

function setType(id, type) {
  const b = blockById(id);
  b.type = type;
  if (type === 'divider') b.content = '';
  if (type !== 'todo') delete b.props.checked;
  renderBlocks();
  markDirty();
  if (type === 'divider' && idx(id) === S.page.blocks.length - 1) insertAfter(id, { type: 'paragraph' });
  else focusBlock(id);
}

function shiftBlock(id, dir) {
  const i = idx(id);
  const j = i + dir;
  if (j < 0 || j >= S.page.blocks.length) return;
  const arr = S.page.blocks;
  [arr[i], arr[j]] = [arr[j], arr[i]];
  renderBlocks();
  markDirty();
  focusBlock(id);
}

function moveBlockTo(dragId, targetId, after) {
  if (dragId === targetId) return;
  const arr = S.page.blocks;
  const [moved] = arr.splice(idx(dragId), 1);
  arr.splice(idx(targetId) + (after ? 1 : 0), 0, moved);
  renderBlocks();
  markDirty();
}

// --- atajos tipo Markdown ---
const SHORTCUTS = [
  [/^###\s/, 'heading3'], [/^##\s/, 'heading2'], [/^#\s/, 'heading1'],
  [/^\[(\s|x)?\]\s/i, 'todo'], [/^[-*+]\s/, 'bullet'], [/^1[.)]\s/, 'numbered'],
  [/^>\s/, 'quote'], [/^!>\s/, 'callout'], [/^```/, 'code'], [/^---$/, 'divider'],
];

function onBlockInput(e) {
  const c = e.target.closest('.content');
  if (!c || !S.page) return;
  const w = c.closest('.block');
  const b = blockById(w.dataset.id);
  if (!b) return;
  b.content = getText(c);
  w.classList.remove('only');
  // «- [ ] » primero se convierte en viñeta; el «[ ] » siguiente la vuelve tarea.
  const rules = b.type === 'paragraph' ? SHORTCUTS : b.type === 'bullet' ? SHORTCUTS.filter(([, t]) => t === 'todo') : [];
  if (rules.length) {
    for (const [rx, type] of rules) {
      if (!rx.test(b.content)) continue;
      // Se aplica después de que el navegador termine de procesar la tecla; si se
      // redibuja durante el evento, Chrome deja el cursor en el nodo viejo.
      setTimeout(() => {
        const cur = getText(c);
        const m = cur.match(rx);
        if (!m || !c.isConnected) return;
        const off = Math.max(0, caretOffset(c) - m[0].length);
        b.content = cur.slice(m[0].length);
        if (type === 'todo') b.props = { ...b.props, checked: /x/i.test(m[1] || '') };
        setType(b.id, type);
        if (type !== 'divider') focusBlock(b.id, off);
      }, 0);
      markDirty();
      return;
    }
  }
  if (b.content.startsWith('/') && b.type !== 'code') openSlash(b, c);
  else closeSlash();
  markDirty();
}

function onBlockKeydown(e) {
  const c = e.target.closest('.content');
  if (!c) return;
  const w = c.closest('.block');
  const b = blockById(w.dataset.id);
  if (!b) return;

  if (S.slash) {
    if (['ArrowDown', 'ArrowUp', 'Enter', 'Escape', 'Tab'].includes(e.key)) {
      e.preventDefault();
      slashKey(e.key);
      return;
    }
  }
  const text = getText(c);
  const off = caretOffset(c);
  const sel = getSelection();
  const collapsed = sel.isCollapsed;

  if (e.key === 'Enter' && b.type === 'code' && !(e.ctrlKey || e.metaKey)) return; // salto de línea normal
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (LIST_TYPES.includes(b.type) && !text) { setType(b.id, 'paragraph'); return; }
    if (b.type === 'code' || b.type === 'image') { insertAfter(b.id, { type: 'paragraph' }); return; }
    b.content = text.slice(0, off);
    c.textContent = b.content;
    const nextType = LIST_TYPES.includes(b.type) ? b.type : 'paragraph';
    insertAfter(b.id, { type: nextType, content: text.slice(off), props: nextType === 'todo' ? { checked: false } : {} });
    return;
  }
  if (e.key === 'Tab' && b.type === 'code') {
    e.preventDefault();
    document.execCommand('insertText', false, '    ');
    return;
  }
  if (e.key === 'Backspace' && collapsed && off === 0) {
    const i = idx(b.id);
    if (b.type === 'image') { if (!text) { e.preventDefault(); removeBlock(b.id, 'prev'); } return; }
    e.preventDefault();
    if (b.type !== 'paragraph') { setType(b.id, 'paragraph'); focusBlock(b.id, 0); return; }
    if (i === 0) return;
    const prev = S.page.blocks[i - 1];
    if (prev.type === 'divider' || prev.type === 'image') { S.page.blocks.splice(i - 1, 1); renderBlocks(); markDirty(); focusBlock(b.id, 0); return; }
    const join = prev.content.length;
    prev.content += text;
    S.page.blocks.splice(i, 1);
    renderBlocks();
    markDirty();
    focusBlock(prev.id, join);
    return;
  }
  if (e.key === 'ArrowUp' && collapsed && off === 0) { e.preventDefault(); focusNeighbor(b.id, -1); return; }
  if (e.key === 'ArrowDown' && collapsed && off === text.length) { e.preventDefault(); focusNeighbor(b.id, 1); }
}

async function onBlockPaste(e) {
  const c = e.target.closest('.content');
  if (!c || !S.page) return;
  const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
  e.preventDefault();
  if (files.length) {
    let after = c.closest('.block').dataset.id;
    for (const f of files) after = (await uploadImage(f, after))?.id || after;
    return;
  }
  const text = e.clipboardData.getData('text/plain');
  if (text) document.execCommand('insertText', false, text);
}

async function uploadImage(file, afterId) {
  try {
    const name = file.name || 'imagen-pegada';
    const a = await api('POST', `/api/assets?page_id=${S.page.id}&name=${encodeURIComponent(name)}`, file, file.type);
    const ref = afterId || S.page.blocks[S.page.blocks.length - 1].id;
    const cur = blockById(ref);
    // Si el bloque actual está vacío, la imagen lo sustituye.
    if (cur && cur.type === 'paragraph' && !cur.content) {
      Object.assign(cur, { type: 'image', content: '', props: { asset_id: a.id, name, mime: a.mime } });
      renderBlocks(); markDirty();
      insertAfter(cur.id, { type: 'paragraph' });
      return cur;
    }
    return insertAfter(ref, { type: 'image', content: '', props: { asset_id: a.id, name, mime: a.mime } });
  } catch (err) { toast('No se pudo subir la imagen: ' + err.message, true); return null; }
}

// --- menú «/» ---
function openSlash(b, c) {
  const q = b.content.slice(1).toLowerCase();
  const items = TYPES.filter((t) => !q || t.label.toLowerCase().includes(q) || t.type.includes(q));
  if (!items.length) { closeSlash(); return; }
  const prevSel = S.slash?.blockId === b.id ? S.slash.sel : 0;
  S.slash = { blockId: b.id, items, sel: Math.min(prevSel, items.length - 1) };
  const r = c.getBoundingClientRect();
  const m = openMenu([{ title: 'Tipo de bloque' }, ...items.map((t) => ({ icon: t.icon, label: t.label, hint: t.hint, action: () => applySlash(t.type) }))], r.left, r.bottom + 4);
  highlightSlash(m);
  setTimeout(() => c.focus(), 0);
}
function highlightSlash(m = openMenuEl) {
  if (!m || !S.slash) return;
  m.querySelectorAll('.item').forEach((n, i) => n.classList.toggle('sel', i === S.slash.sel));
  m.querySelectorAll('.item')[S.slash.sel]?.scrollIntoView({ block: 'nearest' });
}
function slashKey(key) {
  const s = S.slash;
  if (key === 'Escape') { closeSlash(); return; }
  if (key === 'ArrowDown') s.sel = (s.sel + 1) % s.items.length;
  else if (key === 'ArrowUp') s.sel = (s.sel - 1 + s.items.length) % s.items.length;
  else if (key === 'Enter' || key === 'Tab') { applySlash(s.items[s.sel].type); return; }
  highlightSlash();
}
function applySlash(type) {
  const id = S.slash?.blockId;
  closeSlash();
  if (!id) return;
  const b = blockById(id);
  b.content = '';
  setType(id, type);
}
function closeSlash() {
  if (S.slash) { S.slash = null; closeMenu(); }
}

// ---------- Guardado ----------
function setSaveState(text, err = false) {
  const s = $('#save-state');
  if (s) { s.textContent = text; s.classList.toggle('err', err); }
}

function markDirty() {
  S.dirty = true;
  setSaveState('Guardando…');
  clearTimeout(S.timer);
  S.timer = setTimeout(save, 700);
}

function payload() {
  return { base: S.page.updated_at, title: S.page.title, blocks: S.page.blocks.map(({ id, type, content, props, tags }) => ({ id, type, content, props, tags })) };
}

// Lo que el servidor tenía la última vez que sincronizamos; sirve para fusionar
// si Antigravity cambia la página mientras se está editando.
const blockKey = (b) => JSON.stringify([b.type, b.content, b.props || {}, b.tags || []]);
function markSynced(page) {
  S.synced = new Map(page.blocks.map((b) => [b.id, blockKey(b)]));
  S.syncedTitle = page.title;
}

function mergeRemote(remote) {
  const local = S.page.blocks;
  const changed = new Map(local.filter((b) => S.synced.get(b.id) !== blockKey(b)).map((b) => [b.id, b]));
  const removed = new Set([...S.synced.keys()].filter((id) => !local.some((b) => b.id === id)));
  const result = remote.blocks.filter((b) => !removed.has(b.id)).map((b) => changed.get(b.id) || b);
  local.forEach((b, i) => {
    if (changed.has(b.id) && !result.some((r) => r.id === b.id)) {
      const prev = local[i - 1];
      const at = prev ? result.findIndex((r) => r.id === prev.id) : -1;
      result.splice(at + 1, 0, b);
    }
  });
  const title = S.page.title !== S.syncedTitle ? S.page.title : remote.title;
  S.page = { ...remote, title, blocks: result.length ? result : [{ id: newId(), type: 'paragraph', content: '', props: {}, tags: [] }] };
  markSynced(remote);
  rerenderKeepFocus();
}

function rerenderKeepFocus() {
  const active = document.activeElement;
  const w = active?.closest?.('.block');
  const id = w?.dataset.id;
  const off = active?.classList?.contains('content') ? caretOffset(active) : null;
  const t = $('.page-title');
  if (t && t !== active && t.value !== S.page.title) t.value = S.page.title;
  renderBlocks();
  if (id && off !== null && blockById(id)) focusBlock(id, off);
}

async function save() {
  if (!S.page || !S.dirty) return;
  if (S.saving) { clearTimeout(S.timer); S.timer = setTimeout(save, 300); return; }
  S.saving = true;
  S.dirty = false;
  const pid = S.page.id;
  try {
    const res = await api('PUT', `/api/pages/${pid}/blocks`, payload());
    if (S.page?.id === pid) { S.page.updated_at = res.updated_at; markSynced(res); }
    const meta = findPageMeta(pid);
    if (meta) { meta.p.title = res.title; meta.p.updated_at = res.updated_at; }
    if (!S.dirty) setSaveState('Guardado ✓');
    renderPages();
  } catch (e) {
    if (e.status === 409 && S.page?.id === pid) {
      // Antigravity cambió la página: fusionamos sus cambios con los nuestros y guardamos otra vez.
      mergeRemote(e.data.page);
      S.dirty = true;
      toast('Antigravity modificó esta página; se combinaron los cambios');
      clearTimeout(S.timer);
      S.timer = setTimeout(save, 50);
      return;
    }
    S.dirty = true;
    setSaveState('Error al guardar: ' + e.message, true);
    clearTimeout(S.timer);
    S.timer = setTimeout(save, 3000);
  } finally {
    S.saving = false;
  }
}

async function saveNow() {
  clearTimeout(S.timer);
  while (S.saving) await sleep(40);
  if (S.dirty) await save();
}

addEventListener('beforeunload', () => {
  if (S.page && S.dirty) {
    fetch(`/api/pages/${S.page.id}/blocks`, { method: 'PUT', keepalive: true, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()) });
  }
});

// ---------- Búsqueda ----------
let searchTimer = null;
let searchSel = -1;
function renderHits(results) {
  const box = $('#search-results');
  box.replaceChildren();
  box.hidden = false;
  searchSel = -1;
  if (!results.length) { box.append(el('div', { class: 'empty', text: 'Sin resultados' })); return; }
  for (const r of results) {
    const frag = el('div');
    frag.innerHTML = esc(r.fragmento || '').replace(/«/g, '<mark>').replace(/»/g, '</mark>');
    const hit = el('div', { class: 'hit' },
      el('div', { class: 'path', text: `${r.cuaderno} › ${r.seccion} › ${r.pagina || 'Sin título'}` + (r.coincide_en === 'titulo_pagina' ? '  (título)' : '') }),
      frag);
    hit.addEventListener('mousedown', (e) => { e.preventDefault(); goHit(r); });
    hit._r = r;
    box.append(hit);
  }
}
function goHit(r) {
  $('#search-results').hidden = true;
  $('#search').blur();
  openPage(r.page_id, r.id);
}
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q) { $('#search-results').hidden = true; return; }
  searchTimer = setTimeout(async () => {
    await saveNow();
    try { renderHits((await api('GET', `/api/search?q=${encodeURIComponent(q)}`)).results); } catch (err) { toast(err.message, true); }
  }, 220);
});
$('#search').addEventListener('keydown', (e) => {
  const hits = [...document.querySelectorAll('#search-results .hit')];
  if (e.key === 'Escape') { $('#search-results').hidden = true; e.target.blur(); return; }
  if (!hits.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    searchSel = (searchSel + (e.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
    hits.forEach((h, i) => h.classList.toggle('sel', i === searchSel));
    hits[searchSel].scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    goHit(hits[Math.max(0, searchSel)]._r);
  }
});
$('#search').addEventListener('blur', () => setTimeout(() => { $('#search-results').hidden = true; }, 150));
$('#search').addEventListener('focus', (e) => { if (e.target.value.trim()) e.target.dispatchEvent(new Event('input')); });

// ---------- Respaldo ----------
function download(url) {
  const a = el('a', { href: url, download: '' });
  document.body.append(a);
  a.click();
  a.remove();
}

async function openBackup() {
  await saveNow();
  let info, tree;
  try { [info, tree] = await Promise.all([api('GET', '/api/backups'), api('GET', '/api/tree')]); }
  catch (e) { toast(e.message, true); return; }
  const st = tree.stats;
  await dialog((box, done) => {
    box.classList.add('wide');
    const list = el('div', { class: 'backup-list' });
    if (!info.respaldos.length) list.append(el('div', { class: 'bk' }, el('span', { class: 'n s', text: 'Todavía no hay respaldos.' })));
    for (const r of info.respaldos) {
      list.append(el('div', { class: 'bk' },
        el('span', { class: 'n', text: r.nombre, title: r.ruta }),
        el('span', { class: 's', text: (r.bytes / 1024).toFixed(0) + ' KB' }),
        el('button', { class: 'btn', text: 'Descargar', onclick: () => download(`/api/backups/file?nombre=${encodeURIComponent(r.nombre)}`) }),
        el('button', { class: 'btn', text: 'Restaurar', onclick: async () => {
          const ok = await confirmBox({ title: 'Restaurar respaldo', danger: true, ok: 'Restaurar',
            message: `Todo el contenido actual se sustituirá por el de «${r.nombre}». Antes se guarda un respaldo del estado actual.` });
          if (!ok) return;
          try { await api('POST', '/api/backups/restore', { nombre: r.nombre, modo: 'replace' }); done(); afterRestore('Respaldo restaurado'); }
          catch (e) { toast(e.message, true); }
        } })));
    }
    const file = el('input', { type: 'file', accept: '.json,application/json' });
    const mode = el('select', {}, el('option', { value: 'merge', text: 'Añadir como cuadernos nuevos (no borra nada)' }), el('option', { value: 'replace', text: 'Sustituir todo el contenido actual' }));
    const cfg = info.config;
    const hours = el('input', { type: 'number', min: '0', step: '1', value: cfg.horas_entre_respaldos });
    const keep = el('input', { type: 'number', min: '1', step: '1', value: cfg.respaldos_a_conservar });
    const extra = el('input', { type: 'text', value: cfg.carpeta_respaldo_extra || '', placeholder: 'p. ej. ~/Nextcloud/Libreta o un disco externo' });

    box.append(
      el('h2', { text: 'Exportar y respaldar' }),
      el('div', { class: 'stats' },
        ...[['cuadernos', 'Cuadernos'], ['secciones', 'Secciones'], ['paginas', 'Páginas'], ['bloques', 'Bloques'], ['imagenes', 'Imágenes']]
          .map(([k, l]) => el('div', { class: 'stat' }, el('b', { text: st[k] }), el('span', { text: l })))),
      el('p', { text: `Tus datos están en ${info.carpeta.replace(/[\\/]respaldos$/, '')}. Último respaldo: ${info.ultimo ? fmtDate(info.ultimo) : 'nunca'}.` }),

      el('h3', { text: 'Exportar todo' }),
      el('div', { class: 'actions', style: 'justify-content:flex-start;margin-top:4px' },
        el('button', { class: 'btn primary', text: 'JSON completo (restaurable)', onclick: () => download('/api/export/json') }),
        el('button', { class: 'btn', text: 'Markdown en ZIP', onclick: () => download('/api/export/markdown') }),
        el('button', { class: 'btn', text: 'Base de datos SQLite', onclick: () => download('/api/export/sqlite') })),
      el('p', { text: 'El JSON incluye las imágenes y se puede restaurar aquí. El ZIP de Markdown sirve para leer tus notas con cualquier editor. La copia SQLite es la base de datos tal cual.' }),

      el('h3', { text: 'Respaldos automáticos' }),
      list,
      el('div', { class: 'actions', style: 'justify-content:flex-start' },
        el('button', { class: 'btn primary', text: 'Crear respaldo ahora', onclick: async () => {
          try { const r = await api('POST', '/api/backups'); toast('Respaldo creado'); done(); openBackup(); if (r.copia_extra?.startsWith('ERROR')) toast(r.copia_extra, true); }
          catch (e) { toast(e.message, true); }
        } })),
      el('div', { class: 'grid2' },
        el('div', { class: 'field' }, el('label', { text: 'Horas entre respaldos (0 = desactivado)' }), hours),
        el('div', { class: 'field' }, el('label', { text: 'Respaldos a conservar' }), keep)),
      el('div', { class: 'field' }, el('label', { text: 'Copiar cada respaldo también a esta carpeta' }), extra),
      el('div', { class: 'actions', style: 'justify-content:flex-start' },
        el('button', { class: 'btn', text: 'Guardar ajustes', onclick: async () => {
          try {
            await api('PUT', '/api/config', { horas_entre_respaldos: Number(hours.value), respaldos_a_conservar: Number(keep.value), carpeta_respaldo_extra: extra.value.trim() });
            toast('Ajustes guardados');
          } catch (e) { toast(e.message, true); }
        } })),

      el('h3', { text: 'Importar un respaldo JSON' }),
      el('div', { class: 'field' }, file),
      el('div', { class: 'field' }, mode),
      el('div', { class: 'actions' },
        el('button', { class: 'btn', text: 'Cerrar', onclick: () => done() }),
        el('button', { class: 'btn primary', text: 'Importar', onclick: async () => {
          const f = file.files[0];
          if (!f) { toast('Elige un archivo .json', true); return; }
          if (mode.value === 'replace' && !(await confirmBox({ title: 'Sustituir todo', danger: true, ok: 'Sustituir',
            message: 'Se borrará el contenido actual y se cargará el del archivo. Antes se guarda un respaldo del estado actual.' }))) return;
          try {
            const r = await api('POST', `/api/import?mode=${mode.value}`, f, 'application/json');
            done();
            afterRestore(`Importado: ${r.importado.paginas} páginas, ${r.importado.bloques} bloques`);
          } catch (e) { toast(e.message, true); }
        } })));
  });
}

async function afterRestore(msg) {
  S.page = null;
  S.sectionId = null;
  S.dirty = false;
  await loadTree();
  const f = findSection(S.sectionId);
  if (f?.s.pages[0]) openPage(f.s.pages[0].id); else renderEditor();
  toast(msg);
}

// ---------- Antigravity ----------
async function openAntigravity() {
  let info;
  try { info = await api('GET', '/api/info'); } catch (e) { toast(e.message, true); return; }
  const snippet = JSON.stringify({ mcpServers: { libreta: { command: info.python, args: [info.mcp_script] } } }, null, 2);
  const snippetW = JSON.stringify({ mcpServers: { libreta: { command: info.python, args: [info.mcp_script], env: { LIBRETA_MCP_ESCRITURA: '1' } } } }, null, 2);
  await dialog((box, done) => {
    box.classList.add('wide');
    box.append(
      el('h2', { text: 'Conectar Libreta con Antigravity' }),
      el('p', { text: 'Libreta incluye un servidor MCP. Antigravity lo arranca por su cuenta y lee la misma base de datos que esta ventana, así que ve tus cuadernos, secciones, páginas y cada bloque con su ID, tipo, etiquetas y fecha. No hace falta que esta ventana esté abierta.' }),
      el('h3', { text: '1. Añade esto a la configuración MCP de Antigravity' }),
      el('p', { text: `Archivo: ${info.antigravity_config}. En Antigravity también puedes abrirlo desde «…» › MCP Servers › Manage MCP Servers › View raw config. Si ya tienes otros servidores, añade solo la entrada «libreta» dentro de «mcpServers».` }),
      el('div', { class: 'mono', text: snippet }),
      el('div', { class: 'actions', style: 'justify-content:flex-start' },
        el('button', { class: 'btn primary', text: 'Copiar configuración (solo lectura)', onclick: () => copy(snippet, 'Configuración copiada') }),
        el('button', { class: 'btn', text: 'Copiar con permiso de escritura', onclick: () => copy(snippetW, 'Configuración con escritura copiada') })),
      el('p', { text: 'Con permiso de escritura, Antigravity además puede crear páginas, añadir bloques y editarlos. Sin él solo puede leer.' }),
      el('h3', { text: '2. Reinicia Antigravity y pregúntale' }),
      el('div', { class: 'mono', text: '«Usa libreta y dime qué cuadernos tengo»\n«Lee la página Bienvenida a Libreta con los IDs de sus bloques»\n«Busca en libreta todo lo que diga presupuesto»\n«Lista mis tareas pendientes de libreta»' }),
      el('h3', { text: 'Herramientas que verá Antigravity' }),
      el('div', { class: 'mono', text: info.tools.join('\n') }),
      el('div', { class: 'actions' }, el('button', { class: 'btn primary', text: 'Cerrar', onclick: () => done() })));
  });
}

// ---------- Tema ----------
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  store('libreta-theme', t);
}
applyTheme(load('libreta-theme', matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

// ---------- Eventos globales ----------
$('#add-notebook').addEventListener('click', addNotebook);
$('#add-page').addEventListener('click', addPage);
$('#btn-backup').addEventListener('click', openBackup);
$('#btn-antigravity').addEventListener('click', openAntigravity);
$('#btn-theme').addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

const edCol = $('#editor-col');
edCol.addEventListener('input', onBlockInput);
edCol.addEventListener('keydown', onBlockKeydown);
edCol.addEventListener('paste', onBlockPaste);
edCol.addEventListener('mousedown', (e) => {
  // Clic en el espacio vacío bajo los bloques: seguir escribiendo al final.
  if (!S.page || (e.target !== edCol && e.target !== $('#editor'))) return;
  const blocksEl = $('#blocks');
  if (!blocksEl || e.clientY < blocksEl.getBoundingClientRect().bottom) return;
  e.preventDefault();
  const last = S.page.blocks[S.page.blocks.length - 1];
  if (last.type === 'paragraph' && !last.content) focusBlock(last.id);
  else insertAfter(last.id, { type: 'paragraph' });
});
edCol.addEventListener('dragover', (e) => { if (S.page && e.dataTransfer.types.includes('Files')) e.preventDefault(); });
edCol.addEventListener('drop', async (e) => {
  if (!S.page || !e.dataTransfer.files.length) return;
  e.preventDefault();
  let after = e.target.closest?.('.block')?.dataset.id;
  for (const f of [...e.dataTransfer.files].filter((x) => x.type.startsWith('image/'))) after = (await uploadImage(f, after))?.id || after;
});

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#search').focus(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveNow().then(() => setSaveState('Guardado ✓')); }
  if (e.key === 'Escape') closeMenu();
});

// ---------- Cambios hechos desde Antigravity ----------
// Cada pocos segundos se mira si otro proceso añadió páginas o editó la abierta.
let lastTreeKey = '';
async function pollExternal() {
  if (document.hidden || S.saving || S.drag || openMenuEl || $('.overlay')) return;
  let data;
  try { data = await api('GET', '/api/tree'); } catch { return; }
  const key = JSON.stringify(data.notebooks);
  if (key !== lastTreeKey) {
    lastTreeKey = key;
    S.tree = data.notebooks;
    S.stats = data.stats;
    renderNotebooks();
    renderPages();
  }
  if (!S.page) return;
  const meta = findPageMeta(S.page.id);
  if (!meta) { if (!S.dirty) { toast('La página abierta se eliminó desde fuera'); closePage(); } return; }
  if (meta.p.updated_at !== S.page.updated_at && !S.dirty && !S.saving) {
    try {
      const remote = await api('GET', `/api/pages/${S.page.id}`);
      if (S.dirty || S.saving || remote.id !== S.page.id) return;
      mergeRemote(remote);
      renderPages();
    } catch { /* se reintenta en la próxima vuelta */ }
  }
}
setInterval(pollExternal, 4000);

// ---------- Inicio ----------
(async function init() {
  const last = load('libreta-last', {});
  S.sectionId = last.sectionId || null;
  try { await loadTree(); } catch (e) { toast('No se pudo conectar con el servidor de Libreta: ' + e.message, true); return; }
  const meta = last.pageId && findPageMeta(last.pageId);
  if (meta) openPage(last.pageId);
  else if (S.sectionId) selectSection(S.sectionId);
  else renderEditor();
})();
