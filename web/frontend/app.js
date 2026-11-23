// Frontend del proyecto WDPDA (JavaScript sin framework).
"use strict";

const $ = (s) => document.querySelector(s);
const state = { id: null, compiled: null, examples: [], seed: 1, charts: {} };

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const kb = (b) => b == null ? "—" : (b / 1024).toFixed(0) + " KB";

// ---------------------------------------------------------------- Graphviz
let vizPromise = null;
async function renderDot(dot, el) {
  if (!dot) { el.innerHTML = "<p class='hint'>Autómata demasiado grande para dibujarlo.</p>"; return; }
  try {
    if (!window.Viz) throw new Error("sin viz.js");
    vizPromise = vizPromise || Viz.instance();
    const viz = await vizPromise;
    el.innerHTML = "";
    el.appendChild(viz.renderSVGElement(dot));
  } catch (e) {
    el.innerHTML = "<p class='hint'>No se pudo cargar viz.js (sin red): se muestra el DOT.</p><pre class='code'>" + esc(dot) + "</pre>";
  }
}

// ---------------------------------------------------------------- pestañas
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.remove("active"));
  document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  $("#" + b.dataset.tab).classList.add("active");
});

// ---------------------------------------------------------------- arranque
async function init() {
  try {
    const h = await api("/api/health");
    $("#health").textContent = `NoSQL: ${h.store} · ${h.cases} casos · gcc ${h.gcc ? "✓" : "✗"} · cargo ${h.cargo ? "✓" : "✗"}`;
    $("#health").className = "pill ok";
  } catch (e) { $("#health").textContent = "servidor no disponible"; $("#health").className = "pill bad"; }
  state.examples = await api("/api/examples");
  $("#examples").innerHTML = state.examples.map((e, i) => `<option value="${i}">${esc(e.name)}</option>`).join("");
  $("#examples").onchange = loadExample;
  loadExample();
  await compile();
}

function loadExample() {
  const e = state.examples[+$("#examples").value];
  $("#schema").value = e.schema;
  $("#format").value = e.format;
}

// ---------------------------------------------------------------- compilar
async function compile() {
  const msg = $("#compile-msg");
  msg.className = "msg"; msg.textContent = "compilando…";
  try {
    const r = await api("/api/compile", { schema_text: $("#schema").value, format: $("#format").value, name: "schema" });
    state.id = r.id; state.compiled = r;
    msg.className = "msg ok";
    msg.textContent = `Compilado en ${r.compile_ms} ms · id ${r.id}` + (r.warnings.length ? " · avisos: " + r.warnings.join("; ") : "");
    const st = r.stats, fo = r.formal;
    const cards = [
      [fo.states, "estados de δ"], [fo.transitions, "transiciones"],
      [fo.stack_symbols, "símbolos de pila Γ"],
      [st.nodes_after_minimization ?? st.elements, r.format === "xml" ? "elementos" : "nodos (tras minimizar)"],
      [st.dfa_count, "AFD"], [`${st.dfa_states} / ${st.dfa_states_before_min}`, "estados AFD (mín./antes)"],
      [fo.deterministic ? "sí" : "NO", "δ determinista"], [st.max_lane_width, "carriles máx. (producto)"],
    ];
    if (st.reachable_nodes) cards.splice(4, 0, [st.reachable_nodes, "nodos antes de minimizar"]);
    $("#stats").innerHTML = cards.map(([v, l]) => `<div class="card"><b>${esc(v)}</b><span>${l}</span></div>`).join("");
    $("#classif tbody").innerHTML = r.classification.map((c) =>
      `<tr><td><code>${esc(c.keyword)}</code></td><td>${esc(c.category)}</td><td>${esc(c.strategy)}</td><td>${c.count}</td></tr>`).join("");
    $("#btn-download").href = `/api/download/${r.id}.zip`;
    $("#ccode").textContent = r.c_code;
    // autómata
    $("#det").textContent = fo.deterministic ? "δ determinista ✓" : "δ NO determinista";
    $("#det").className = "pill " + (fo.deterministic ? "ok" : "bad");
    $("#dfa-select").innerHTML = r.dfas.map((d) =>
      `<option value="${d.id}">${d.id}: ${esc(d.kind)} — ${esc(String(d.source).slice(0, 40))} (${d.states} est.)</option>`).join("");
    $("#delta").innerHTML = r.delta ? "<table><tr><th>q</th><th>entrada</th><th>tope</th><th>apila</th><th>q'</th><th>w</th><th>guarda</th><th>registros</th></tr>" +
      r.delta.map((t) => `<tr><td>${esc(t.src)}</td><td>${esc(t.sym)}</td><td>${esc(t.pop ?? "λ")}</td><td>${esc(t.push.join("") || "λ")}</td><td>${esc(t.dst)}</td><td>${esc(t.weight)}</td><td>${esc(t.guard)}</td><td>${esc(t.action)}</td></tr>`).join("") + "</table>"
      : "<p class='hint'>XML: δ por elemento = AFD de contenido de Glushkov (ver lista de AFD).</p>";
    drawAutomaton(); drawDfa();
    $("#payload").value = (await api("/api/generate", { id: r.id, seed: 0 })).payload;
  } catch (e) {
    msg.className = "msg err"; msg.textContent = "Error de compilación: " + e.message;
  }
}

function drawAutomaton() {
  const r = state.compiled; if (!r) return;
  if (r.format === "xml") {
    $("#graph").innerHTML = "<table><tr><th>elemento</th><th>modelo de contenido</th><th>hijos</th></tr>" +
      r.elements.map((e) => `<tr><td>&lt;${esc(e.name)}&gt;</td><td><code>${esc(e.content_src || (e.complex ? "ε" : "texto"))}</code></td><td>${esc(e.child_names.join(", "))}</td></tr>`).join("") +
      "</table><p class='hint'>Cada modelo de contenido es una expresión regular sobre nombres de hijos, compilada con la construcción de Glushkov; UPA ⇔ AFD de Glushkov determinista.</p>";
    return;
  }
  renderDot($("#auto-view").value === "full" ? r.dot_wdpda : r.dot_wdpda_simple, $("#graph"));
  if (r.product_note) $("#graph").insertAdjacentHTML("afterbegin", `<p class="hint">${esc(r.product_note)}</p>`);
}
function drawDfa() {
  const r = state.compiled; if (!r || !r.dfas.length) { $("#dfa-graph").innerHTML = "<p class='hint'>Este esquema no usa AFD auxiliares.</p>"; return; }
  const d = r.dfas.find((x) => x.id == $("#dfa-select").value) || r.dfas[0];
  renderDot(d.dot, $("#dfa-graph"));
}
$("#auto-view").onchange = drawAutomaton;
$("#dfa-select").onchange = drawDfa;
$("#btn-compile").onclick = compile;
$("#btn-showc").onclick = () => $("#ccode").classList.toggle("hidden");

// ---------------------------------------------------------------- validar
function showResult(r) {
  const v = $("#verdict");
  const st = r.status;
  v.className = "verdict " + (st === "VALID" ? "ok" : st === "INVALID" ? "bad" : "mal");
  v.textContent = st === "VALID" ? "VÁLIDO · peso 0" : st === "INVALID" ? `INVÁLIDO · peso ${r.weight} (violaciones)` :
    `${st} · peso +∞ — ${r.fatal_message || ""} (byte ${r.fatal_offset})`;
  $("#errors tbody").innerHTML = (r.errors || []).map((e) =>
    `<tr><td><code>${esc(e.code)}</code></td><td><code>${esc(e.path || "(raíz)")}</code></td><td>${e.offset}</td><td>${esc(e.message)}</td></tr>`).join("");
  const m = [[r.bytes, "bytes"], [r.tokens, "tokens"], [r.max_depth, "profundidad máx. de pila"], [r.lanes_peak, "carriles (pico)"]];
  if (r.mb_s !== undefined) m.push([r.mb_s, "MB/s"], [r.cycles_per_byte ?? "—", "ciclos/byte"], [r.runtime_allocations, "asignaciones"], [kb(r.ctx_bytes), "memoria fija"]);
  if (r.seconds !== undefined) m.push([(r.seconds * 1000).toFixed(3) + " ms", "tiempo"]);
  $("#metrics").innerHTML = m.map(([a, b]) => `<div class="card"><b>${esc(a)}</b><span>${b}</span></div>`).join("");
  $("#trace").innerHTML = r.trace ? "<table><tr><th>#</th><th>byte</th><th>token</th><th>valor</th><th>estado</th><th>pila</th><th>peso</th><th>ruta</th></tr>" +
    r.trace.map((t, i) => `<tr><td>${i}</td><td>${t.offset}</td><td><code>${esc(t.token)}</code></td><td>${esc(t.value ?? "")}</td><td>${esc(t.state ?? "")}</td><td><code>${esc(t.stack.join(" "))}</code></td><td>${t.weight}</td><td><code>${esc(t.path)}</code></td></tr>`).join("") + "</table>"
    : "<p class='hint'>La traza la produce el motor de referencia (Python).</p>";
}
$("#btn-validate").onclick = async () => {
  if (!state.id) return;
  try {
    const r = await api("/api/validate", { id: state.id, payload: $("#payload").value, engine: $("#engine").value,
      chunk: +$("#chunk").value || null, trace: true, fail_fast: $("#failfast").checked });
    showResult(r);
  } catch (e) { $("#verdict").className = "verdict mal"; $("#verdict").textContent = e.message; }
};
async function gen(invalid) {
  const r = await api("/api/generate", { id: state.id, invalid, seed: state.seed++ });
  $("#payload").value = r.payload;
  if (r.mutation) $("#verdict").textContent = "mutación aplicada: " + r.mutation;
}
$("#btn-gen").onclick = () => gen(false);
$("#btn-gen-bad").onclick = () => gen(true);
$("#btn-tree").onclick = async () => {
  try {
    const fmt = state.compiled && state.compiled.format === "xml" ? "xml" : "json";
    const r = await api("/api/tree", { payload: $("#payload").value, format: fmt });
    renderDot(r.dot, $("#tree"));
    $("#tree").insertAdjacentHTML("afterbegin", `<p class="hint">${r.nodes} nodos · ${r.steps} pasos del analizador LL(1) (pila explícita)</p>`);
  } catch (e) { $("#tree").innerHTML = `<p class="msg err">${esc(e.message)}</p>`; }
};

// ---------------------------------------------------------------- streaming
$("#btn-stream").onclick = () => {
  if (!state.id) return;
  const text = $("#payload").value, size = Math.max(1, +$("#s-chunk").value), delay = +$("#s-delay").value;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/stream`);
  let pos = 0, logged = 0;
  $("#s-log").innerHTML = "";
  const paint = (bad) => { $("#s-text").innerHTML = `<span class="${bad ? "bad" : "done"}">${esc(text.slice(0, pos))}</span>${esc(text.slice(pos))}`; };
  paint(false);
  ws.onopen = () => { ws.send(JSON.stringify({ id: state.id })); next(); };
  function next() {
    if (pos >= text.length) { ws.send(JSON.stringify({ end: true })); return; }
    const chunk = text.slice(pos, pos + size); pos += chunk.length;
    ws.send(JSON.stringify({ chunk }));
  }
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.error) { $("#s-log").textContent = m.error; return; }
    const bad = m.status && !["RUNNING", "VALID"].includes(m.status);
    paint(bad);
    $("#s-state").innerHTML = [[m.bytes, "bytes consumidos"], [m.depth ?? m.max_depth, "profundidad de pila"],
      [m.weight === null ? "+∞" : m.weight, "peso tropical"], [m.status, "estado"]]
      .map(([a, b]) => `<div class="card"><b class="status-${esc(a)}">${esc(a)}</b><span>${b}</span></div>`).join("");
    if (m.final) { showResult(m); ws.close(); return; }
    // solo los errores nuevos desde el último bloque
    const errs = m.errors || [], total = m.error_count ?? errs.length;
    errs.slice(Math.max(0, errs.length - (total - logged))).forEach((e) =>
      $("#s-log").insertAdjacentHTML("beforeend", `<div>byte ${m.bytes}: <code>${esc(e.code)}</code> ${esc(e.path || "(raíz)")} — ${esc(e.message)}</div>`));
    logged = total;
    if (m.status !== "RUNNING") { ws.send(JSON.stringify({ end: true })); return; }
    setTimeout(next, delay);
  };
};

// ---------------------------------------------------------------- bench
function chart(id, label) {
  if (!window.Chart) return null;
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart($(id), { type: "bar", data: { labels: [], datasets: [] },
    options: { responsive: true, plugins: { title: { display: true, text: label } }, scales: { y: { beginAtZero: true } } } });
  return state.charts[id];
}
$("#btn-bench").onclick = () => {
  if (!state.id) return;
  const sizes = $("#b-sizes").value.split(",").map((x) => +x.trim()).filter(Boolean);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/bench`);
  $("#bench-table tbody").innerHTML = "";
  const c1 = chart("#chart-mbs", "Rendimiento (MB/s, más es mejor)"), c2 = chart("#chart-mem", "Memoria (KB, menos es mejor)");
  const impls = ["WDPDA (C)", "DOM (C)", "jsonschema (Python)"], colors = ["#1f6feb", "#d29922", "#8957e5"];
  [c1, c2].forEach((c) => { if (c) { c.data.labels = sizes.map((s) => s + " KB"); c.data.datasets = impls.map((l, i) => ({ label: l, data: sizes.map(() => 0), backgroundColor: colors[i] })); } });
  ws.onopen = () => ws.send(JSON.stringify({ id: state.id, sizes }));
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.error) { $("#bench-table tbody").innerHTML = `<tr><td colspan=7>${esc(m.error)}</td></tr>`; return; }
    if (!m.impl) return;
    $("#bench-table tbody").insertAdjacentHTML("beforeend", `<tr><td>${m.size_kb}</td><td>${esc(m.impl)}</td><td>${m.mb_s.toFixed(1)}</td><td>${m.cycles_per_byte ?? "—"}</td><td>${m.allocations ?? "—"}</td><td>${kb(m.memory)}</td><td class="status-${esc(m.status)}">${esc(m.status)}</td></tr>`);
    const i = impls.indexOf(m.impl), j = sizes.indexOf(m.size_kb);
    if (c1 && i >= 0) { c1.data.datasets[i].data[j] = m.mb_s; c1.update(); }
    if (c2 && i >= 0) { c2.data.datasets[i].data[j] = (m.memory || 0) / 1024; c2.update(); }
  };
};

// ---------------------------------------------------------------- casos
function caseRow(c) {
  const m = c.metricas || {};
  const py = m.py ? m.py.status : "", cc = m.c ? m.c.status : "";
  const exp = (c.esperado || {}).status || "";
  const bad = (py && py !== exp) || (cc && cc !== exp);
  return `<tr class="${bad ? "mismatch" : ""}" title="${esc(c.nota || "")}"><td><code>${esc(c._id)}</code></td><td>${esc(c.categoria)}</td><td>${esc(c.formato)}</td>
    <td>${esc(c.nombre)}${c.nota ? " ⓘ" : ""}</td><td class="status-${esc(exp)}">${esc(exp)}</td><td>${esc(c.oraculo || "—")}</td>
    <td class="status-${esc(py)}">${esc(py)}</td><td class="status-${esc(cc)}">${esc(cc)}</td><td>${m.c && m.c.mb_s != null ? m.c.mb_s : ""}</td>
    <td><button data-run="${esc(c._id)}">ejecutar</button></td></tr>`;
}
async function loadCases() {
  const cat = $("#c-cat").value;
  const cs = await api("/api/cases" + (cat ? "?categoria=" + cat : ""));
  $("#cases tbody").innerHTML = cs.map(caseRow).join("");
  $("#c-summary").textContent = `${cs.length} casos`;
  document.querySelectorAll("[data-run]").forEach((b) => b.onclick = async () => {
    b.textContent = "…"; await api(`/api/cases/${encodeURIComponent(b.dataset.run)}/run`, {}); loadCases();
  });
}
$("#btn-cases").onclick = loadCases;
$("#btn-runall").onclick = async () => {
  $("#c-summary").textContent = "ejecutando…";
  const cat = $("#c-cat").value;
  const r = await api("/api/cases/run-all" + (cat ? "?categoria=" + cat : ""), {});
  await loadCases();
  $("#c-summary").textContent = `${r.coinciden}/${r.total} coinciden con lo esperado`;
  $("#c-summary").className = "pill " + (r.coinciden === r.total ? "ok" : "bad");
};

init();
