"use strict";
// ------------------------------------------------------------------ utilidades
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
};
const enc = encodeURIComponent;
const base = (p) => (p || "").split("/").pop();
const fmt = (s) => (s == null ? "" : `${Number(s).toFixed(2)}s`);
const round = (x, n = 3) => Math.round(x * 10 ** n) / 10 ** n;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && typeof opts.body === "string" ? { "Content-Type": "application/json" } : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body) });

function toast(msg, err = false) {
  const t = h("div", { class: "toast" + (err ? " err" : "") }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), err ? 7000 : 3000);
}

function watchJob(id, box, onDone) {
  box.innerHTML = "";
  const bar = h("div", { class: "bar" }, h("div", { style: "width:0%" }));
  const pre = h("pre");
  box.append(bar, pre);
  const tick = async () => {
    let j;
    try { j = await api(`/api/jobs/${id}`); } catch (e) { pre.textContent = e.message; return; }
    bar.firstChild.style.width = `${Math.round(j.progress * 100)}%`;
    pre.textContent = j.log.filter((l) => !/^(W0000|I0000|INFO:|WARNING: All log)/.test(l)).join("\n");
    pre.scrollTop = pre.scrollHeight;
    if (j.status === "running") return setTimeout(tick, 700);
    if (j.status === "error") { box.append(h("div", { class: "err" }, "Error: " + j.error)); return; }
    onDone && onDone(j.result);
  };
  tick();
}

// ------------------------------------------------------------------ estado
const S = { meta: null, lib: { clips: [], music: [], references: [], renders: [] }, styles: [],
  project: null, pname: null, sel: -1, dirty: false, order: [] };

function setStatus() {
  const m = S.meta;
  $("#status").textContent = m ? (m.segmentation ? "Recorte de personas: activo" : "Recorte de personas: no disponible (pip install mediapipe)") : "";
}

// ------------------------------------------------------------------ pestañas
function show(view) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  if (view === "editor") loadProjects();
  if (view === "create") renderCreate();
  if (view === "styles") renderStyles();
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

// ------------------------------------------------------------------ biblioteca
async function loadLibrary() {
  S.lib = await api("/api/library");
  for (const panel of $$("#view-library .panel[data-kind]")) {
    const kind = panel.dataset.kind;
    const ul = $(".items", panel);
    ul.innerHTML = "";
    for (const it of S.lib[kind]) ul.append(libItem(kind, it));
    if (!S.lib[kind].length) ul.append(h("li", { class: "muted" }, "Vacío"));
  }
  const rl = $("#renders-list");
  rl.innerHTML = "";
  for (const it of S.lib.renders) rl.append(libItem("renders", it, true));
  if (!S.lib.renders.length) rl.append(h("li", { class: "muted" }, "Todavía no hay renders"));
}

const SEC_LABEL = { intro: "intro", build: "subida", drop: "drop", break: "bajada", verse: "parte media", outro: "final" };

function musicMapView(m, from = 0, to = null) {
  to = to ?? m.duration;
  const span = Math.max(0.1, to - from);
  const bars = h("div", { class: "bars" });
  m.beats.forEach((b, i) => {
    if (b < from || b >= to) return;
    const e = m.beat_energy[i] ?? 0;
    bars.append(h("i", { style: `height:${8 + 92 * e}%;background:hsl(${220 - 200 * e},70%,55%)`, title: `${b.toFixed(2)}s · energía ${e}` }));
  });
  const secs = h("div", { class: "secs" });
  for (const s of m.sections) {
    if (s.end <= from || s.start >= to) continue;
    const a = Math.max(s.start, from), b = Math.min(s.end, to);
    secs.append(h("div", { class: `sec-${s.label}`, style: `left:${100 * (a - from) / span}%;width:${100 * (b - a) / span}%`,
      title: `${SEC_LABEL[s.label] || s.label} ${s.start.toFixed(1)}–${s.end.toFixed(1)}s` }, SEC_LABEL[s.label] || s.label));
  }
  const wrap = h("div", { class: "mmap" }, h("div", { class: "muted" },
    `${Math.round(m.bpm)} BPM · ${m.sections.length} secciones · ${m.hits.length} golpes fuertes` + (m.drop != null ? ` · drop principal en ${m.drop.toFixed(2)}s` : "")),
    bars, secs);
  if (m.drop != null && m.drop >= from && m.drop < to) bars.append(h("div", { class: "drop", style: `left:${100 * (m.drop - from) / span}%` }));
  return wrap;
}

async function showMusicMap(name, box) {
  box.innerHTML = "Analizando la canción…";
  try { const m = await api(`/api/music/${enc(name)}`); box.innerHTML = ""; box.append(musicMapView(m)); }
  catch (e) { box.textContent = e.message; }
}

function libItem(kind, it, link = false) {
  const isVideo = it.width != null;
  if (kind === "music") {
    const box = h("div", { style: "width:100%" });
    return h("li", { style: "flex-wrap:wrap" }, h("span", {}, "♪"),
      h("div", { class: "grow" }, it.name, h("div", { class: "muted" }, fmt(it.duration))),
      h("button", { onclick: () => showMusicMap(it.name, box) }, "Analizar"),
      h("button", { class: "danger", title: "Borrar", onclick: async () => {
        if (!confirm(`¿Borrar ${it.name}?`)) return;
        await api(`/api/library/${kind}/${enc(it.name)}`, { method: "DELETE" });
        loadLibrary();
      } }, "✕"), box);
  }
  return h("li", {},
    isVideo ? h("img", { src: `/api/thumb/${kind}/${enc(it.name)}?t=${Math.min(1, (it.duration || 2) / 2)}`, loading: "lazy" }) : h("span", {}, "♪"),
    h("div", { class: "grow" },
      link ? h("a", { href: `/media/${kind}/${enc(it.name)}`, target: "_blank" }, it.name) : it.name,
      h("div", { class: "muted" }, [it.duration != null ? fmt(it.duration) : "", isVideo ? ` · ${it.width}×${it.height}` : "", it.error ? ` · ${it.error}` : ""].join(""))),
    h("button", { class: "danger", title: "Borrar", onclick: async () => {
      if (!confirm(`¿Borrar ${it.name}?`)) return;
      await api(`/api/library/${kind}/${enc(it.name)}`, { method: "DELETE" });
      loadLibrary();
    } }, "✕"));
}

function upload(kind, files) {
  return Promise.all([...files].map((f) => new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    const note = h("div", { class: "toast" }, `Subiendo ${f.name}… 0%`);
    document.body.append(note);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) note.textContent = `Subiendo ${f.name}… ${Math.round(100 * e.loaded / e.total)}%`; };
    xhr.onloadend = () => { note.remove(); if (xhr.status !== 200) toast(`Error subiendo ${f.name}`, true); resolve(); };
    xhr.open("PUT", `/api/upload/${kind}/${enc(f.name)}`);
    xhr.send(f);
  }))).then(loadLibrary);
}

for (const panel of $$("#view-library .panel[data-kind]")) {
  const kind = panel.dataset.kind, drop = $(".drop", panel), input = $("input", drop);
  input.addEventListener("change", () => upload(kind, input.files));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload(kind, e.dataTransfer.files); });
}

// ------------------------------------------------------------------ estilos
const TYPE_COLOR = { clip: "var(--c-clip)", stack: "var(--c-stack)", black: "var(--c-black)", color: "var(--c-color)" };
const TR_LABEL = { cut: "corte", fade: "disolución", fadeblack: "fundido a negro", flash: "flash",
  subject_drop: "persona entra", slidedown: "slide ↓", slideup: "slide ↑", slideleft: "slide ←", slideright: "slide →" };

function recipeBar(sections) {
  const total = sections.reduce((a, s) => a + (s.beats || 1), 0);
  return h("div", { class: "recipe" }, sections.map((s) => {
    const b = s.beats || 1;
    const tr = s.transition;
    const label = s.type === "stack" ? `${s.panels.length} paneles` : s.type === "clip" ? `${b}b` : s.until ? "negro" : `${b}b`;
    const fx = (s.effects || []).map((e) => e.type).join(", ");
    return h("div", { style: `flex:${b / total};background:${TYPE_COLOR[s.type]}`,
      title: `${s.type} · ${b} beats${tr ? " · entra con " + (TR_LABEL[tr.type] || tr.type) : ""}${fx ? " · " + fx : ""}${s.motion != null ? " · movimiento " + s.motion : ""}` },
      tr ? h("div", { class: "tr", style: `width:${Math.min(100, 100 * tr.beats / b)}%` }) : null,
      h("span", { style: "position:relative" }, label + (fx ? " ✦" : "")));
  }));
}

async function renderStyles() {
  [S.styles, S.lib] = await Promise.all([api("/api/styles"), api("/api/library")]);
  const refs = $("#learn-refs");
  refs.innerHTML = "";
  if (!S.lib.references.length) refs.append(h("span", { class: "muted" }, "Sube videos a «Referencias» en la Biblioteca."));
  for (const r of S.lib.references) refs.append(h("label", {}, h("input", { type: "checkbox", value: r.name }), " ", r.name));
  const list = $("#styles-list");
  list.innerHTML = "";
  for (const st of S.styles) {
    const stats = st.stats || {};
    list.append(h("div", { class: "panel style-card" },
      h("div", { class: "row" }, h("h2", {}, st.name), st.builtin ? h("span", { class: "muted" }, "(incluido)") : null,
        st.builtin ? null : h("button", { class: "danger", onclick: async () => {
          if (!confirm(`¿Borrar el estilo ${st.name}?`)) return;
          await api(`/api/styles/${enc(st.id)}`, { method: "DELETE" }); renderStyles();
        } }, "Borrar")),
      h("div", { class: "meta" },
        h("span", {}, `Aprendido de: ${(st.sources || []).join(", ")}`),
        h("span", {}, `Plano medio: ${stats.avg_clip_beats ?? "-"} beats`),
        h("span", {}, `Transiciones: ${Object.entries(stats.transitions || {}).map(([k, v]) => `${TR_LABEL[k] || k} ×${v}`).join(", ") || "-"}`),
        h("span", {}, `Efectos: ${Object.entries(stats.effects || {}).map(([k, v]) => `${k} ×${v}`).join(", ") || "-"}`),
        st.letterbox ? h("span", {}, `Barras de cine ${Math.round(st.letterbox * 100)}%`) : null),
      (st.variants || []).map((v) => h("div", {},
        h("div", { class: "muted" }, `${v.source} · ${v.bpm ? Math.round(v.bpm) + " BPM" : "sin música"}`),
        recipeBar(v.sections))),
      h("div", { class: "legend" }, ...Object.entries({ clip: "plano", stack: "paneles", black: "negro" })
        .map(([k, l]) => h("span", {}, h("i", { style: `background:${TYPE_COLOR[k]}` }), l)),
        h("span", {}, "rayado = transición de entrada · ✦ = efectos"))));
  }
}

$("#learn-btn").addEventListener("click", async () => {
  const name = $("#learn-name").value.trim();
  const refs = $$("#learn-refs input:checked").map((i) => i.value);
  if (!name || !refs.length) return toast("Pon un nombre y elige al menos una referencia", true);
  try {
    const { job } = await post("/api/learn", { name, references: refs });
    watchJob(job, $("#learn-job"), () => { toast("Estilo aprendido"); renderStyles(); });
  } catch (e) { toast(e.message, true); }
});

// ------------------------------------------------------------------ nuevo edit
async function renderCreate() {
  [S.styles, S.lib] = await Promise.all([api("/api/styles"), api("/api/library")]);
  const sel = $("#create-style");
  const prev = sel.value;
  sel.innerHTML = "";
  const g1 = h("optgroup", { label: "Estilos aprendidos" }, S.styles.map((s) => h("option", { value: "style:" + s.id }, s.name)));
  const g2 = h("optgroup", { label: "Plantillas fijas" }, S.meta.templates.map((t) => h("option", { value: "tpl:" + t }, t)));
  sel.append(g1, g2);
  if (prev) sel.value = prev;
  const mus = $("#create-music");
  mus.innerHTML = "";
  for (const m of S.lib.music) mus.append(h("option", { value: m.name }, `${m.name} (${fmt(m.duration)})`));
  if (!S.lib.music.length) mus.append(h("option", { value: "" }, "Sube una canción en la Biblioteca"));
  S.order = S.order.filter((n) => S.lib.clips.some((c) => c.name === n));
  drawClipPicker();
  if (mus.value) showMusicMap(mus.value, $("#create-music-map"));
}
$("#create-music").addEventListener("change", (e) => e.target.value && showMusicMap(e.target.value, $("#create-music-map")));

function drawClipPicker() {
  const box = $("#create-clips");
  box.innerHTML = "";
  if (!S.lib.clips.length) box.append(h("p", { class: "muted" }, "Sube clips en la Biblioteca."));
  for (const c of S.lib.clips) {
    const idx = S.order.indexOf(c.name);
    box.append(h("div", { class: "thumb" + (idx >= 0 ? " sel" : ""), onclick: () => {
      const i = S.order.indexOf(c.name);
      if (i >= 0) S.order.splice(i, 1); else S.order.push(c.name);
      drawClipPicker();
    } },
      h("img", { src: `/api/thumb/clips/${enc(c.name)}?t=${Math.min(1, (c.duration || 2) / 2)}`, loading: "lazy" }),
      idx >= 0 ? h("div", { class: "n" }, idx + 1) : null,
      h("div", { class: "cap" }, `${c.name} · ${fmt(c.duration)}`)));
  }
}

$("#create-btn").addEventListener("click", async () => {
  const sv = $("#create-style").value;
  const body = {
    clips: S.order, music: $("#create-music").value,
    music_start: $("#create-start").value === "" ? null : parseFloat($("#create-start").value),
    follow_music: $("#create-follow").checked,
    accents: $("#create-accents").checked,
    duration: parseFloat($("#create-duration").value) || null,
    color_strength: parseFloat($("#create-color").value),
    keep_order: $("#create-order").checked,
    name: $("#create-name").value.trim() || undefined,
  };
  if (sv.startsWith("style:")) body.style = sv.slice(6); else body.template = sv.slice(4);
  try {
    const { job } = await post("/api/create", body);
    watchJob(job, $("#create-job"), async (r) => {
      toast("Edit creado. Abriendo el editor…");
      S.pname = r.project;
      show("editor");
    });
  } catch (e) { toast(e.message, true); }
});

// ------------------------------------------------------------------ editor
async function loadProjects() {
  const [list, lib] = await Promise.all([api("/api/projects"), api("/api/library")]);
  S.lib = lib;
  const sel = $("#project-select");
  sel.innerHTML = "";
  if (!list.length) sel.append(h("option", { value: "" }, "No hay proyectos: crea uno en «Nuevo edit»"));
  for (const p of list) sel.append(h("option", { value: p.name }, p.name));
  if (S.pname && list.some((p) => p.name === S.pname)) sel.value = S.pname;
  if (sel.value) openProject(sel.value);
}

$("#project-select").addEventListener("change", (e) => {
  if (S.dirty && !confirm("Hay cambios sin guardar. ¿Descartarlos?")) { e.target.value = S.pname; return; }
  openProject(e.target.value);
});

async function openProject(name) {
  S.pname = name;
  S.project = await api(`/api/projects/${enc(name)}`);
  S.sel = -1;
  setDirty(false);
  if (!S.project.info?.beats && S.project.music?.src) {
    try {
      const b = await api(`/api/beats/${enc(base(S.project.music.src))}?start=${S.project.music.start || 0}`);
      S.project.info = { ...(S.project.info || {}), bpm: b.bpm, beats: b.beats };
    } catch { /* sin beats */ }
  }
  loadPlayer();
  drawProjectForm();
  drawTimeline();
  drawInspector();
}

function loadPlayer(file) {
  const v = $("#player");
  const cands = file ? [file] : [`${S.pname}.mp4`, `${S.pname}_preview.mp4`];
  const found = cands.find((f) => S.lib.renders.some((r) => r.name === f));
  if (found) { v.src = `/media/renders/${enc(found)}?v=${Date.now()}`; } else { v.removeAttribute("src"); v.load(); }
}

function setDirty(d) { S.dirty = d; $("#dirty").textContent = d ? "● cambios sin guardar" : ""; }
function changed() { setDirty(true); drawTimeline(); }

const segs = () => S.project.segments;
const starts = () => { let t = 0; return segs().map((s) => { const a = t; t += Number(s.duration) || 0; return a; }); };
const clipOptions = () => S.lib.clips.map((c) => `../clips/${c.name}`);

// Campo de formulario enlazado a obj[key]
function field(label, obj, key, type = "number", opts = {}) {
  let input;
  if (type === "select") {
    input = h("select", {}, (opts.options || []).map((o) => {
      const [v, l] = Array.isArray(o) ? o : [o, opts.labels ? opts.labels(o) : base(o) || o];
      return h("option", { value: v }, l);
    }));
    if (opts.empty) input.prepend(h("option", { value: "" }, opts.empty));
    input.value = obj[key] ?? (opts.empty ? "" : opts.def ?? "");
  } else if (type === "checkbox") {
    input = h("input", { type: "checkbox" });
    input.checked = !!obj[key];
    input.addEventListener("change", () => { obj[key] = input.checked; changed(); if (opts.redraw) drawInspector(); });
    return h("label", { class: "inline" }, input, label);
  } else {
    input = h("input", { type, step: opts.step || "0.05", min: opts.min, max: opts.max, placeholder: opts.placeholder || "" });
    input.value = obj[key] ?? "";
  }
  input.addEventListener("change", () => {
    let v = input.value;
    if (type === "number" || type === "range") v = v === "" ? undefined : parseFloat(v);
    if (v === "" || v === undefined) delete obj[key]; else obj[key] = v;
    changed();
    if (opts.redraw) drawInspector();
  });
  return h("label", {}, label, input);
}

function drawProjectForm() {
  const p = S.project, f = $("#project-form");
  f.innerHTML = "";
  p.output = p.output || {};
  p.music = p.music || {};
  f.append(
    field("Look de color", p, "grade", "select", { options: S.meta.grades }),
    p.color_match ? field("Copiar color aprendido (0-1)", p, "color_strength", "number", { step: "0.05", min: 0, max: 1 }) : null,
    field("Canción", p.music, "src", "select", { options: S.lib.music.map((m) => `../music/${m.name}`), empty: "(sin música)" }),
    field("Canción: empieza en (s)", p.music, "start", "number", { step: "0.1" }),
    field("Volumen", p.music, "volume", "number", { step: "0.1", min: 0 }),
    field("Ancho", p.output, "width", "number", { step: "2" }),
    field("Alto", p.output, "height", "number", { step: "2" }),
    field("FPS", p.output, "fps", "number", { step: "1" }),
  );
}

// ---------------- línea de tiempo
function drawTimeline() {
  const tl = $("#timeline");
  const pps = Number($("#tl-zoom").value);
  const st = starts();
  const total = st.length ? st[st.length - 1] + Number(segs()[segs().length - 1].duration) : 0;
  const inner = h("div", { class: "tl-inner", style: `width:${Math.max(total * pps + 40, tl.clientWidth)}px` });
  const ruler = h("div", { class: "tl-ruler" });
  const beats = S.project.info?.beats || [];
  beats.forEach((b, i) => { if (b <= total + 0.01) ruler.append(h("div", { class: "tl-beat" + (i % 4 === 0 ? " bar" : ""), style: `left:${b * pps}px` })); });
  for (let s = 0; s <= total; s++) ruler.append(h("div", { class: "tl-sec", style: `left:${s * pps}px` }, `${s}s`));
  inner.append(ruler);
  const info = S.project.info || {};
  if (info.sections) {
    const band = h("div", { class: "tl-secs" });
    for (const sec of info.sections) band.append(h("div", { class: `sec-${sec.label}`, style: `left:${sec.start * pps}px;width:${Math.max(2, (sec.end - sec.start) * pps)}px`,
      title: `${SEC_LABEL[sec.label] || sec.label}` }, SEC_LABEL[sec.label] || sec.label));
    inner.append(band);
  }
  if (info.drop != null) inner.append(h("div", { class: "tl-drop", style: `left:${info.drop * pps}px`, title: "drop" }));
  segs().forEach((s, i) => {
    const w = Math.max(6, s.duration * pps - 2);
    const src = s.type === "clip" ? s.src : s.type === "stack" ? s.panels?.[0]?.src : null;
    const inpt = s.type === "clip" ? s.in || 0 : s.panels?.[0]?.in || 0;
    const kind = src && src.includes("/clips/") ? "clips" : null;
    const tr = typeof s.transition === "string" ? { type: s.transition } : s.transition;
    const label = s.type === "clip" ? base(s.src) : s.type === "stack" ? `${s.panels.length} paneles` : s.type === "black" ? "negro" : s.color || "color";
    const el = h("div", { class: "tl-seg" + (i === S.sel ? " sel" : ""), style: `left:${st[i] * pps}px;width:${w}px;background:${TYPE_COLOR[s.type]}`,
      title: `${label} · ${fmt(s.duration)}`, onclick: () => { S.sel = i; drawTimeline(); drawInspector(); seek(st[i]); } },
      kind ? h("img", { src: `/api/thumb/${kind}/${enc(base(src))}?t=${round(inpt + 0.2, 2)}`, loading: "lazy" }) : null,
      tr && tr.type && tr.type !== "cut" && i > 0 ? h("div", { class: "tr", style: `width:${(tr.duration || 0.3) * pps}px`, title: TR_LABEL[tr.type] || tr.type }) : null,
      h("div", { class: "lbl" }, label),
      h("div", { class: "lbl muted" }, fmt(s.duration)),
      (s.effects || []).length ? h("div", { class: "fx" }, s.effects.map((e) => (typeof e === "string" ? e : e.type)).join(" · ")) : null);
    inner.append(el);
  });
  inner.append(h("div", { class: "tl-playhead", id: "playhead", style: "left:0" }));
  inner.addEventListener("click", (e) => {
    if (e.target === inner || e.target.classList.contains("tl-ruler")) seek(e.offsetX / pps);
  });
  tl.innerHTML = "";
  tl.append(inner);
  $("#tl-info").textContent = `${segs().length} segmentos · ${fmt(total)}${S.project.info?.bpm ? ` · ${Math.round(S.project.info.bpm)} BPM` : ""}`;
  movePlayhead();
}

function seek(t) { const v = $("#player"); if (v.src) v.currentTime = t; }
function movePlayhead() {
  const ph = $("#playhead");
  if (ph) ph.style.left = `${$("#player").currentTime * Number($("#tl-zoom").value)}px`;
}
$("#player").addEventListener("timeupdate", movePlayhead);
$("#tl-zoom").addEventListener("input", drawTimeline);

// ---------------- inspector
const EFFECT_DEFAULTS = {
  glitch: { at: "end", duration: 0.6 }, punch: { times: [0], amount: 0.1 }, shake: { at: 0, duration: 0.5, amount: 0.5 },
  flash: { times: [0], amount: 0.7 }, rgb_split: { amount: 8 }, fade_in: { duration: 0.4 }, fade_out: { duration: 0.4 },
  blur: { amount: 12 }, grain: { amount: 10 }, sharpen: { amount: 0.8 }, letterbox: { size: 0.08 },
};

function drawInspector() {
  const box = $("#inspector");
  box.innerHTML = "";
  const i = S.sel, s = segs()[i];
  $("#seg-index").textContent = s ? `#${i + 1}` : "";
  if (!s) { box.append(h("p", { class: "muted" }, "Selecciona un segmento en la línea de tiempo.")); return; }
  if (typeof s.transition === "string") s.transition = { type: s.transition };
  const main = h("div", { class: "form" },
    field("Tipo", s, "type", "select", { options: ["clip", "stack", "black", "color"], redraw: true }),
    field("Duración (s)", s, "duration", "number", { step: "0.01", min: 0.05 }),
    field("Look propio", s, "grade", "select", { options: S.meta.grades, empty: "(el del proyecto)" }));
  box.append(main);

  if (s.type === "clip") {
    if (!s.src) s.src = clipOptions()[0];
    const img = h("img", { class: "preview", src: `/api/thumb/clips/${enc(base(s.src))}?t=${round((s.in || 0) + 0.1, 2)}` });
    box.append(h("div", { class: "form" },
      field("Clip", s, "src", "select", { options: clipOptions(), redraw: true }),
      field("Empieza en (s)", s, "in", "number", { step: "0.05", min: 0, redraw: true }),
      field("Velocidad", s, "speed", "number", { step: "0.05", min: 0.05, placeholder: "1" }),
      field("Zoom", s, "zoom", "number", { step: "0.05", min: 1, placeholder: "1" }),
      field("Espejo", s, "mirror", "checkbox"),
      field("Al revés", s, "reverse", "checkbox")), img);
  }
  if (s.type === "stack") {
    s.panels = s.panels || [];
    const pbox = h("div", { class: "sub" }, h("h3", {}, "Paneles (de arriba a abajo)"));
    s.panels.forEach((p, k) => pbox.append(h("div", { class: "row" },
      h("img", { class: "preview", style: "height:60px", src: `/api/thumb/clips/${enc(base(p.src))}?t=${round((p.in || 0) + 0.1, 2)}` }),
      h("div", { class: "form", style: "flex:1" },
        field("Clip", p, "src", "select", { options: clipOptions(), redraw: true }),
        field("Empieza en", p, "in", "number", { min: 0, redraw: true }),
        field("Aparece en", p, "appear", "number", { min: 0 })),
      h("button", { class: "danger", onclick: () => { s.panels.splice(k, 1); changed(); drawInspector(); } }, "✕"))));
    pbox.append(h("button", { onclick: () => { s.panels.push({ src: clipOptions()[0], in: 0, appear: 0 }); changed(); drawInspector(); } }, "+ Panel"));
    box.append(h("div", { class: "form" }, field("Separación (px)", s, "gap", "number", { step: "1", min: 0 })), pbox);
  }
  if (s.type === "color") box.append(h("div", { class: "form" }, field("Color", s, "color", "color")));

  if (i > 0) {
    s.transition = s.transition || { type: "cut" };
    const t = s.transition;
    box.append(h("div", { class: "sub" }, h("h3", {}, "Transición de entrada"), h("div", { class: "form" },
      field("Tipo", t, "type", "select", { options: S.meta.transitions, labels: (o) => TR_LABEL[o] ? `${TR_LABEL[o]} (${o})` : o, redraw: true }),
      t.type !== "cut" ? field("Duración (s)", t, "duration", "number", { step: "0.05", min: 0.05, placeholder: "0.3" }) : null,
      t.type === "subject_drop" ? field("Desde", t, "direction", "select", { options: S.meta.directions }) : null),
      t.type === "subject_drop" && !S.meta.segmentation ? h("p", { class: "muted" }, "Sin MediaPipe se usará un slide normal.") : null));
  }

  // efectos
  s.effects = (s.effects || []).map((e) => (typeof e === "string" ? { type: e } : e));
  const ebox = h("div", { class: "sub" }, h("h3", {}, "Efectos"));
  s.effects.forEach((e, k) => {
    const params = { ...e }; delete params.type;
    const ta = h("input", { value: JSON.stringify(params), style: "flex:1;min-width:160px" });
    ta.addEventListener("change", () => {
      try { const v = JSON.parse(ta.value || "{}"); s.effects[k] = { type: e.type, ...v }; changed(); ta.style.borderColor = ""; }
      catch { ta.style.borderColor = "var(--danger)"; }
    });
    ebox.append(h("div", { class: "row" }, h("b", {}, e.type), ta,
      h("button", { class: "danger", onclick: () => { s.effects.splice(k, 1); changed(); drawInspector(); } }, "✕")));
  });
  const add = h("select", {}, h("option", { value: "" }, "+ Añadir efecto"), S.meta.effects.map((e) => h("option", { value: e }, e)));
  add.addEventListener("change", () => { if (!add.value) return; s.effects.push({ type: add.value, ...(EFFECT_DEFAULTS[add.value] || {}) }); changed(); drawInspector(); });
  ebox.append(add, h("p", { class: "muted" }, "Parámetros en JSON. Tiempos relativos al inicio del segmento; \"at\": \"end\" | \"start\" | segundos."));
  box.append(ebox);

  // textos
  s.texts = s.texts || [];
  const tbox = h("div", { class: "sub" }, h("h3", {}, "Textos"));
  s.texts.forEach((t, k) => tbox.append(h("div", { class: "form" },
    field("Texto", t, "text", "text"), field("Desde (s)", t, "start", "number"), field("Hasta (s)", t, "end", "number"),
    field("Altura (0-1)", t, "y", "number", { step: "0.01" }), field("Tamaño", t, "size", "number", { step: "1" }),
    field("Color", t, "color", "text", { placeholder: "white" }),
    h("button", { class: "danger", onclick: () => { s.texts.splice(k, 1); changed(); drawInspector(); } }, "Quitar texto"))));
  tbox.append(h("button", { onclick: () => { s.texts.push({ text: "TEXTO", y: 0.15, size: 90, start: 0 }); changed(); drawInspector(); } }, "+ Texto"));
  box.append(tbox);
}

// ---------------- operaciones con segmentos
function selOr(msg) { if (S.sel < 0) { toast(msg || "Selecciona un segmento", true); return false; } return true; }
$("#add-seg").addEventListener("click", () => {
  if (!S.project) return;
  const at = S.sel >= 0 ? S.sel + 1 : segs().length;
  segs().splice(at, 0, { type: "clip", src: clipOptions()[0], in: 0, duration: 2, transition: { type: "cut" } });
  S.sel = at; changed(); drawInspector();
});
$("#dup-seg").addEventListener("click", () => { if (!selOr()) return; segs().splice(S.sel + 1, 0, JSON.parse(JSON.stringify(segs()[S.sel]))); S.sel++; changed(); drawInspector(); });
$("#del-seg").addEventListener("click", () => { if (!selOr()) return; segs().splice(S.sel, 1); S.sel = Math.min(S.sel, segs().length - 1); changed(); drawInspector(); });
const move = (d) => { if (!selOr()) return; const j = S.sel + d; if (j < 0 || j >= segs().length) return; const a = segs(); [a[S.sel], a[j]] = [a[j], a[S.sel]]; S.sel = j; changed(); drawInspector(); };
$("#left-seg").addEventListener("click", () => move(-1));
$("#right-seg").addEventListener("click", () => move(1));
$("#snap-seg").addEventListener("click", () => {
  if (!selOr()) return;
  const beats = S.project.info?.beats || [];
  if (!beats.length) return toast("Este proyecto no tiene beats (añade una canción)", true);
  const st = starts(), s = segs()[S.sel], end = st[S.sel] + Number(s.duration);
  const nb = beats.filter((b) => b > st[S.sel] + 0.05).reduce((a, b) => (Math.abs(b - end) < Math.abs(a - end) ? b : a), Infinity);
  if (!isFinite(nb)) return;
  s.duration = round(nb - st[S.sel]); changed(); drawInspector();
});

// ---------------- guardar y renderizar
async function save() {
  try {
    await api(`/api/projects/${enc(S.pname)}`, { method: "PUT", body: JSON.stringify(S.project) });
    setDirty(false); toast("Guardado"); return true;
  } catch (e) { toast("No se pudo guardar: " + e.message, true); return false; }
}
$("#save-btn").addEventListener("click", () => S.pname && save());
async function doRender(preview) {
  if (!S.pname || !(await save())) return;
  try {
    const { job } = await post("/api/render", { project: S.pname, preview });
    watchJob(job, $("#render-job"), async (r) => {
      S.lib = await api("/api/library");
      loadPlayer(r.output);
      $("#player").play().catch(() => {});
      toast(`Render listo: ${r.output}`);
    });
  } catch (e) { toast(e.message, true); }
}
$("#preview-btn").addEventListener("click", () => doRender(true));
$("#render-btn").addEventListener("click", () => doRender(false));
window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });

// ------------------------------------------------------------------ inicio
(async () => {
  try {
    S.meta = await api("/api/meta");
    setStatus();
    await loadLibrary();
  } catch (e) { toast("No se pudo conectar con el servidor: " + e.message, true); }
})();
