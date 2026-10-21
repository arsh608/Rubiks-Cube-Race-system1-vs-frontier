/* Rubik's Cube Race — frontend controller. Talks only to the local FastAPI backend;
 * the OpenRouter key never reaches the browser. */
(function () {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const LANE_COLOURS = ["#6ea8ff", "#34d399", "#fbbf24", "#c084fc", "#f472b6", "#22d3ee"];
  const SOLVED = "UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB";

  const app = {
    config: null,
    profile: null,
    activeRunId: null,     // run currently streaming
    viewRunId: null,       // run shown in lanes/dashboard
    runData: null,         // full JSON of viewRunId once finished
    puzzles: [],
    lanes: {},             // model_key -> lane object
    models: [],
    es: null,
    trialsDone: 0,
    trialsTotal: 0,
    lastStats: null,
    timer: null,
  };

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function fmtMs(ms) {
    if (ms == null || isNaN(ms)) return "—";
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
  }
  function fmtS(s) {
    if (s == null || isNaN(s)) return "—";
    if (s < 60) return `${s.toFixed(2)} s`;
    const m = Math.floor(s / 60);
    return `${m}m ${String(Math.round(s - m * 60)).padStart(2, "0")}s`;
  }
  const fmtGap = (g) => (g == null ? "—" : (g > 0 ? `+${g}` : String(g)));
  const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } };

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    let body = null;
    try { body = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) {
      const detail = body && body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText;
      throw new Error(detail);
    }
    return body;
  }

  function showAlert(msg, kind = "error") {
    const el = $("#alert");
    el.className = "alert" + (kind === "info" ? " info" : "");
    el.textContent = msg;
    if (kind === "info") setTimeout(() => { if (el.textContent === msg) el.classList.add("hidden"); }, 9000);
  }
  const hideAlert = () => $("#alert").classList.add("hidden");

  // ---------- student badge ----------
  function student() {
    const saved = lsGet("student", null);
    return {
      name: (saved && saved.name) || app.config.student_name || "",
      roll: (saved && saved.roll) || app.config.roll_number || "",
    };
  }
  function renderStudent() {
    const s = student();
    $("#sb-name").textContent = s.name || "Click to set name";
    $("#sb-roll").textContent = s.roll || "—";
    $("#slide-name").textContent = s.name || "Student name";
    $("#slide-roll").textContent = "Roll No: " + (s.roll || "—");
  }
  function editStudent() {
    const s = student();
    $("#in-name").value = s.name;
    $("#in-roll").value = s.roll;
    $("#student-dialog").showModal();
  }

  // ---------- cubes ----------
  let heroCube, heroNet;
  function setHero(state, face) {
    heroCube.setState(state, face);
    heroNet.setState(state, face);
  }

  // ---------- lanes ----------
  const limitOf = (m) => m.move_limit || Math.min(app.maxMoves(), m.max_moves || app.maxMoves());
  const SRC_LABEL = { live: "LIVE API", cache: "CACHE", sim: "SIMULATED" };

  function createLane(model, colour) {
    const el = document.createElement("div");
    el.className = "lane";
    el.style.setProperty("--lane-c", colour);
    const isFrontier = /frontier/i.test(model.category);
    el.innerHTML = `
      <div class="lane-head">
        <div><div class="lane-title">${esc(model.name)}</div><div class="lane-id">${esc(model.model_id)} · ${esc(model.interface)}</div></div>
        <span class="chip ${isFrontier ? "fr" : "s1"}">${isFrontier ? "Frontier LLM" : "System-1"}</span>
      </div>
      <div class="lane-body">
        <div class="lane-cubes"><div class="c3"></div><div class="cn"></div></div>
        <div class="lane-now">
          <div class="now-move thinking">Waiting…</div>
          <div class="lane-stats">
            <span>Moves</span><b class="s-moves">0 / ${limitOf(model)}</b>
            <span>Last latency</span><b class="s-last">—</b>
            <span>Avg latency</span><b class="s-avg">—</b>
            <span>Elapsed</span><b class="s-time">—</b>
            <span>Confidence</span><b class="s-conf">—</b>
            <span>Invalid / retries</span><b class="s-bad">0 / 0</b>
            <span>Last answer from</span><b class="s-src">—</b>
            <span>API calls / cache</span><b class="s-calls">0 / 0</b>
          </div>
          <div class="conf"><div></div></div>
        </div>
      </div>
      <div class="banner">Idle</div>
      <div class="history"></div>
      <div class="reasoning hidden"></div>
      <div class="lane-foot"></div>`;
    const lane = {
      el, model,
      cube: new CubeView.Cube3D($(".c3", el), { draggable: true }),
      net: new CubeView.CubeNet($(".cn", el)),
      now: $(".now-move", el), banner: $(".banner", el), history: $(".history", el),
      foot: $(".lane-foot", el), reasoning: $(".reasoning", el), conf: $(".conf > div", el),
      s: { moves: $(".s-moves", el), last: $(".s-last", el), avg: $(".s-avg", el), time: $(".s-time", el),
           conf: $(".s-conf", el), bad: $(".s-bad", el), src: $(".s-src", el), calls: $(".s-calls", el) },
      startedAt: null, finished: false, latencies: [], limit: limitOf(model),
    };
    lane.cube.setState(SOLVED);
    lane.net.setState(SOLVED);
    return lane;
  }

  function buildLanes(models) {
    app.models = models;
    const wrap = $("#lanes");
    wrap.innerHTML = "";
    app.lanes = {};
    models.forEach((m, i) => {
      const lane = createLane(m, LANE_COLOURS[i % LANE_COLOURS.length]);
      lane.colour = LANE_COLOURS[i % LANE_COLOURS.length];
      app.lanes[m.key] = lane;
      wrap.appendChild(lane.el);
    });
  }

  function resetLane(lane, state) {
    lane.cube.setState(state);
    lane.net.setState(state);
    lane.el.classList.remove("solved", "limit", "error");
    lane.now.className = "now-move thinking";
    lane.now.textContent = "Waiting…";
    lane.banner.className = "banner";
    lane.banner.textContent = "Queued";
    lane.history.innerHTML = "";
    lane.foot.textContent = "";
    lane.reasoning.classList.add("hidden");
    lane.reasoning.textContent = "";
    lane.conf.style.width = "0";
    lane.s.moves.textContent = `0 / ${lane.limit}`;
    lane.s.last.textContent = lane.s.avg.textContent = lane.s.time.textContent = lane.s.conf.textContent = "—";
    lane.s.bad.textContent = "0 / 0";
    lane.s.src.textContent = "—";
    lane.s.src.className = "s-src";
    lane.s.calls.textContent = "0 / 0";
    lane.startedAt = null;
    lane.finished = false;
    lane.latencies = [];
  }

  function setLaneStatus(lane, status, trial) {
    lane.el.classList.remove("solved", "limit", "error");
    lane.banner.className = "banner " + status;
    if (status === "running") lane.banner.textContent = "PLAYING…";
    else if (status === "solved") {
      lane.el.classList.add("solved");
      const gap = trial && trial.optimal_gap != null ? ` · gap ${fmtGap(trial.optimal_gap)}` : "";
      lane.banner.textContent = `✓ SOLVED in ${trial ? trial.move_count : "?"} moves${gap}`;
    } else if (status === "limit") {
      lane.el.classList.add("limit");
      lane.banner.textContent = `✗ MOVE LIMIT REACHED (${lane.limit}) — NOT SOLVED`;
    } else if (status === "budget") {
      lane.el.classList.add("error");
      lane.banner.className = "banner error";
      lane.banner.textContent = "⛔ API BUDGET STOP — NOT SOLVED";
    } else if (status === "error") {
      lane.el.classList.add("error");
      lane.banner.textContent = "⚠ ERROR — trial aborted";
    } else if (status === "cancelled") lane.banner.textContent = "Stopped";
    else lane.banner.textContent = status;
  }

  function addHistory(lane, rec) {
    const prev = $(".mv.last", lane.history);
    if (prev) prev.classList.remove("last");
    const chip = document.createElement("span");
    chip.className = "mv" + (rec.valid ? " last" : " bad");
    chip.textContent = rec.valid ? rec.move : "✗";
    chip.title = rec.valid ? `turn ${rec.turn}: ${rec.move} · ${fmtMs(rec.latency_ms)}` : (rec.error || "invalid");
    lane.history.appendChild(chip);
    lane.history.scrollTop = lane.history.scrollHeight;
  }

  function applyMove(lane, evt) {
    const rec = evt.record;
    const t = evt.trial;
    if (rec.valid) {
      lane.now.className = "now-move";
      lane.now.textContent = rec.move;
      lane.cube.setState(evt.state, rec.move[0]);
      lane.net.setState(evt.state, rec.move[0]);
      lane.latencies.push(rec.latency_ms);
      lane.s.last.textContent = fmtMs(rec.latency_ms);
    } else {
      lane.now.className = "now-move thinking";
      lane.now.textContent = rec.error ? "invalid output" : "—";
    }
    addHistory(lane, rec);
    lane.s.moves.textContent = `${t.move_count} / ${t.move_limit || lane.limit}` + (t.wasted_turns ? ` (+${t.wasted_turns} wasted)` : "");
    lane.s.avg.textContent = fmtMs(t.avg_latency_ms ?? avg(lane.latencies));
    lane.s.bad.textContent = `${t.invalid_outputs} / ${t.retries}`;
    setSource(lane, rec);
    lane.s.calls.textContent = `${t.live_calls ?? 0} / ${t.cache_hits ?? 0}`;
    if (typeof rec.confidence === "number") {
      lane.s.conf.textContent = `${(rec.confidence * 100).toFixed(0)}%`;
      lane.conf.style.width = `${Math.max(0, Math.min(1, rec.confidence)) * 100}%`;
    }
    if (rec.reasoning) {
      lane.reasoning.classList.remove("hidden");
      lane.reasoning.textContent = "Thinking: " + rec.reasoning;
    }
    if (rec.error) lane.foot.textContent = rec.error;
  }
  const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);

  function setSource(lane, rec) {
    if (!rec || !rec.source) return;
    lane.s.src.textContent = SRC_LABEL[rec.source] || rec.source;
    lane.s.src.className = "s-src src-" + rec.source;
    lane.s.src.title = rec.source === "cache"
      ? `served from cache in ${rec.cached_ms} ms; original live latency ${fmtMs(rec.latency_ms)}` : "";
  }

  function finishLane(lane, trial) {
    lane.finished = true;
    setLaneStatus(lane, trial.status, trial);
    if (trial.total_time_s != null) lane.s.time.textContent = fmtS(trial.total_time_s);
    if (trial.avg_latency_ms != null) lane.s.avg.textContent = fmtMs(trial.avg_latency_ms);
    if (trial.error) lane.foot.textContent = trial.error;
    if (trial.status !== "solved" && lane.now.textContent === "Waiting…") lane.now.textContent = "—";
  }

  function tickTimers() {
    const now = performance.now();
    for (const lane of Object.values(app.lanes)) {
      if (lane.startedAt && !lane.finished) lane.s.time.textContent = fmtS((now - lane.startedAt) / 1000);
    }
  }

  // ---------- puzzle panel ----------
  function showPuzzle(p, total) {
    $("#pz-index").textContent = `${p.index + 1} of ${total}`;
    $("#pz-scramble").textContent = p.scramble.join(" ") + `  (${p.scramble_length} turns)`;
    if (p.optimal_distance != null) {
      $("#pz-optimal").textContent = `${p.optimal_distance} moves (exact, HTM)`;
      $("#pz-solution").textContent = p.optimal.solution.join(" ") || "(already solved)";
    } else {
      $("#pz-optimal").textContent = `> ${p.optimal.max_depth} moves (≤ ${p.optimal_upper_bound}); exact value not computed`;
      $("#pz-solution").textContent = "inverse scramble is an upper bound";
    }
    setHero(p.state);
  }

  function setProgress() {
    const pct = app.trialsTotal ? (100 * app.trialsDone) / app.trialsTotal : 0;
    $("#progress-bar").style.width = pct + "%";
    $("#progress-text").textContent = app.trialsTotal ? `${app.trialsDone} / ${app.trialsTotal} model-puzzle trials complete` : "No run yet.";
  }

  function setRunStatus(status) {
    const el = $("#run-status");
    el.className = "status-chip " + status;
    el.textContent = { running: "Running", finished: "Finished", cancelled: "Stopped", error: "Error", idle: "Idle" }[status] || status;
    const running = status === "running";
    $("#run-btn").disabled = running;
    $("#stop-btn").disabled = !running;
  }

  // ---------- live stream ----------
  function attach(runId) {
    if (app.es) app.es.close();
    app.activeRunId = runId;
    app.viewRunId = runId;
    app.runData = null;
    const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
    app.es = es;
    es.onmessage = (m) => {
      let evt;
      try { evt = JSON.parse(m.data); } catch { return; }
      handleEvent(evt);
    };
    es.addEventListener("end", () => { es.close(); if (app.es === es) app.es = null; });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED && app.es === es) app.es = null;
    };
  }

  function handleEvent(evt) {
    switch (evt.type) {
      case "session_started": {
        const s = evt.session;
        app.puzzles = evt.puzzles;
        app.currentMax = s.max_moves;
        app.trialsDone = 0;
        app.trialsTotal = s.trials_total;
        buildLanes(s.models);
        $("#pz-run").textContent = `${s.id} · ${s.suite_label} · seed ${s.seed} · ${modeLabel(s.mode)}${s.replay_speed ? " · cache replay at original speed" : ""}`;
        app.runProvider = s.provider;
        setRunStatus("running");
        setProgress();
        hidePuzzlePicker();
        break;
      }
      case "puzzle_started": {
        showPuzzle(evt.puzzle, app.puzzles.length);
        for (const lane of Object.values(app.lanes)) resetLane(lane, evt.puzzle.state);
        break;
      }
      case "trial_started": {
        const lane = app.lanes[evt.model_key];
        if (lane) {
          lane.startedAt = performance.now();
          setLaneStatus(lane, "running");
          lane.now.className = "now-move thinking";
          lane.now.textContent = "Thinking…";
        }
        break;
      }
      case "move": {
        const lane = app.lanes[evt.model_key];
        if (lane) applyMove(lane, evt);
        break;
      }
      case "trial_finished": {
        const lane = app.lanes[evt.model_key];
        if (lane) finishLane(lane, evt.trial);
        if (["solved", "limit", "error", "budget"].includes(evt.trial.status)) app.trialsDone++;
        if (app.runProvider === "openrouter") refreshBudget();
        setProgress();
        break;
      }
      case "log":
        showAlert(evt.message, evt.level === "error" ? "error" : "info");
        break;
      case "session_finished": {
        setRunStatus(evt.session.status);
        app.activeRunId = null;
        refreshBudget();
        loadRuns();
        refreshDashboard();
        loadRunData(evt.session.id);
        break;
      }
      default:
        break;
    }
  }

  // ---------- viewing finished runs ----------
  async function loadRunData(id) {
    try {
      app.runData = await api(`/api/runs/${encodeURIComponent(id)}`);
      showPuzzlePicker();
    } catch (e) { /* ignore */ }
  }

  function hidePuzzlePicker() {
    const sel = $("#pz-picker");
    if (sel) sel.remove();
  }

  function showPuzzlePicker() {
    const d = app.runData;
    if (!d || !d.puzzles || !d.puzzles.length) return;
    hidePuzzlePicker();
    const sel = document.createElement("select");
    sel.id = "pz-picker";
    sel.style.width = "auto";
    sel.innerHTML = d.puzzles.map((p) => `<option value="${p.index}">Show puzzle ${p.index + 1}</option>`).join("");
    sel.value = String(d.puzzles.length - 1);
    sel.addEventListener("change", () => renderFinishedPuzzle(Number(sel.value)));
    $("#run-status").after(sel);
  }

  function renderFinishedPuzzle(idx) {
    const d = app.runData;
    const p = d.puzzles[idx];
    if (!p) return;
    showPuzzle(p, d.puzzles.length);
    for (const t of d.trials.filter((t) => t.puzzle_index === idx)) {
      const lane = app.lanes[t.model_key];
      if (!lane) continue;
      resetLane(lane, p.state);
      let last = null;
      for (const rec of t.moves) {
        addHistory(lane, rec);
        if (rec.valid) last = rec;
      }
      lane.cube.setState(t.final_state);
      lane.net.setState(t.final_state);
      lane.now.className = "now-move" + (last ? "" : " thinking");
      lane.now.textContent = last ? last.move : "—";
      lane.s.moves.textContent = `${t.move_count} / ${t.move_limit || lane.limit}` + (t.wasted_turns ? ` (+${t.wasted_turns} wasted)` : "");
      lane.s.last.textContent = last ? fmtMs(last.latency_ms) : "—";
      lane.s.bad.textContent = `${t.invalid_outputs} / ${t.retries}`;
      setSource(lane, last);
      lane.s.calls.textContent = `${t.live_calls ?? 0} / ${t.cache_hits ?? 0}`;
      if (last && typeof last.confidence === "number") {
        lane.s.conf.textContent = `${(last.confidence * 100).toFixed(0)}%`;
        lane.conf.style.width = `${last.confidence * 100}%`;
      }
      finishLane(lane, t);
    }
  }

  async function viewRun(id) {
    if (app.activeRunId && app.activeRunId !== id) {
      showAlert("A run is in progress — stop it or wait before viewing another run.", "info");
      return;
    }
    try {
      const d = await api(`/api/runs/${encodeURIComponent(id)}`);
      app.viewRunId = id;
      app.runData = d;
      app.currentMax = d.max_moves;
      buildLanes(d.models);
      app.puzzles = d.puzzles;
      app.trialsTotal = d.trials_total;
      app.trialsDone = d.trials_done;
      setProgress();
      setRunStatus(d.status === "running" ? "error" : d.status);
      $("#pz-run").textContent = `${d.id} · ${d.suite_label} · seed ${d.seed} · ${modeLabel(d.mode)}`;
      showPuzzlePicker();
      renderFinishedPuzzle(d.puzzles.length - 1);
      $("#dash-scope").value = "current";
      refreshDashboard();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { showAlert(e.message); }
  }

  // ---------- controls ----------
  app.maxMoves = () => app.currentMax || Number($("#max-moves").value) || 20;
  const modeLabel = (m) => ({ mock: "SIMULATOR", ollama: "OLLAMA (local)", live: "LIVE OpenRouter" }[m] || m);
  const modePill = (m) => m === "mock" ? '<span class="pill sim">SIM</span>'
    : m === "ollama" ? '<span class="pill local">OLLAMA</span>' : '<span class="pill live">OPENROUTER</span>';

  async function refreshBudget() {
    let b;
    try { b = await api("/api/budget"); } catch { return; }
    const o = b.openrouter;
    const claude = o.per_model["anthropic/claude-opus-5.5"] || { used: 0, cap: 5 };
    const el = $("#budget-pill");
    el.textContent = `OpenRouter budget: ${o.total_used}/${o.total_cap} requests · Claude ${claude.used}/${claude.cap} · $${o.cost_usd.toFixed(4)}`;
    el.className = "pill " + (o.total_used >= o.total_cap ? "bad" : o.total_used > 0 ? "warn" : "live");
    el.title = Object.entries(o.per_model).map(([m, v]) => `${m}: ${v.used}/${v.cap}`).join("\n") +
      `\ncache: ${b.cache.entries} responses stored, ${b.cache.hits} hits`;
    app.budget = o;
  }

  function renderProfile() {
    const prof = app.config.profiles[app.profile];
    const provider = prof.provider;
    const mp = $("#mode-pill");
    mp.className = "pill " + ({ openrouter: "live", ollama: "local", mock: "sim" }[provider]);
    mp.textContent = { openrouter: "LIVE · OpenRouter (budgeted)", ollama: "LOCAL · Ollama open-source models",
                       mock: "SIMULATOR · not real model output" }[provider];
    // Course rules for the single OpenRouter experiment are locked in the UI (and enforced by the backend).
    const locked = provider === "openrouter";
    if (locked) {
      $("#suite").value = "exact3";
      $("#puzzles").value = 1;
      $("#max-moves").value = 20;
      renderSuiteHint();
    }
    ["#suite", "#puzzles", "#max-moves"].forEach((sel) => ($(sel).disabled = locked));
    $("#parallel").disabled = provider === "ollama";
    if (provider === "ollama") $("#parallel").checked = false;
    $("#smoke-btn").classList.toggle("hidden", provider === "openrouter");
    const picks = $("#model-picks");
    const selected = lsGet("models_" + app.profile, null);
    picks.innerHTML = prof.models.map((m) => `
      <label title="${esc(m.model_id)}"><input type="checkbox" value="${esc(m.key)}" ${!selected || selected.includes(m.key) ? "checked" : ""}/>
      ${esc(m.name)} <span class="muted">(${esc(m.interface)})</span></label>`).join("");
    picks.querySelectorAll("input").forEach((i) => i.addEventListener("change", () => {
      lsSet("models_" + app.profile, selectedModels());
    }));
    if (!app.activeRunId && !app.runData) {
      app.currentMax = null;
      buildLanes(prof.models);
    }
  }
  const selectedModels = () => [...document.querySelectorAll("#model-picks input:checked")].map((i) => i.value);

  function renderSuiteHint() {
    const s = app.config.suites[$("#suite").value];
    const what = s.exact_distance
      ? `Start: a cube exactly ${s.exact_distance} moves from solved (verified by BFS). Move limit: 20 for decision models, 5 for Claude.`
      : `${s.label}: ${s.min_len}–${s.max_len} random turns. ${s.purpose}`;
    $("#suite-hint").textContent = `${what} Policy: timeout ${app.config.policy.request_timeout_s}s, ` +
      `${app.config.policy.max_retries} retries per turn, last ${app.config.policy.history_window} moves shown to every model.`;
  }

  async function runBenchmark() {
    hideAlert();
    const models = selectedModels();
    if (!models.length) return showAlert("Select at least one model.");
    const seedVal = $("#seed").value.trim();
    const body = {
      profile: app.profile,
      suite: $("#suite").value,
      puzzles: Number($("#puzzles").value) || 1,
      seed: seedVal === "" ? null : Number(seedVal),
      max_moves: Number($("#max-moves").value) || 20,
      parallel: $("#parallel").checked,
      models,
      scramble: $("#scramble").value.trim() || null,
      replay_speed: $("#replay").checked,
    };
    if (app.config.profiles[app.profile].provider === "openrouter") {
      await refreshBudget();
      const o = app.budget;
      const lines = Object.entries(o.per_model).map(([m, v]) => `  ${m}: ${v.remaining} left (cap ${v.cap})`).join("\n");
      if (!confirm(`This sends REAL OpenRouter requests (the cache is checked first; hits are free).\n\n` +
                   `Used so far: ${o.total_used}/${o.total_cap}\n${lines}\n\nStart the experiment?`)) return;
    }
    $("#run-btn").disabled = true;
    try {
      const run = await api("/api/runs", { method: "POST", body: JSON.stringify(body) });
      app.currentMax = body.max_moves;
      attach(run.id);
      setRunStatus("running");
    } catch (e) {
      $("#run-btn").disabled = false;
      showAlert(e.message);
    }
  }

  async function stopRun() {
    if (!app.activeRunId) return;
    try { await api(`/api/runs/${encodeURIComponent(app.activeRunId)}/cancel`, { method: "POST" }); showAlert("Stopping after the in-flight move(s)…", "info"); }
    catch (e) { showAlert(e.message); }
  }

  async function smokeTest() {
    const box = $("#smoke");
    box.classList.remove("hidden");
    box.innerHTML = `<h3>Connection test — ${esc(app.profile)}</h3><p class="muted">Calling each model once on a 1-move scramble (R)…</p>`;
    $("#smoke-btn").disabled = true;
    try {
      const d = await api("/api/smoke", { method: "POST", body: JSON.stringify({ profile: app.profile }) });
      box.innerHTML = `<div class="row-between"><h3>Connection test — ${esc(d.profile)} (scramble: R, optimal answer: R')</h3>
        <button class="btn ghost" id="smoke-close">✕</button></div>
        <div class="table-wrap"><table class="stats"><thead><tr><th>Model</th><th>Interface</th><th>Result</th><th>Move</th><th>Latency</th><th>Confidence</th><th>Details</th></tr></thead><tbody>` +
        d.results.map((r) => `<tr><td class="mono">${esc(r.model_id)}</td><td>${esc(r.interface)}</td>
          <td class="${r.ok ? "best" : ""}" style="${r.ok ? "" : "color:var(--bad)"}">${r.ok ? "✓ OK" : "✗ FAILED"}</td>
          <td class="mono">${esc(r.move ?? "—")}${r.solves_it ? " ✓" : ""}</td><td>${fmtMs(r.latency_ms)}</td>
          <td>${typeof r.confidence === "number" ? (r.confidence * 100).toFixed(0) + "%" : "—"}</td>
          <td class="small-text" style="white-space:normal">${esc([r.error, ...(r.notes || [])].filter(Boolean).join(" · ") || (r.malformed ? r.malformed + " malformed then OK" : ""))}</td></tr>`).join("") +
        `</tbody></table></div>`;
      $("#smoke-close").addEventListener("click", () => box.classList.add("hidden"));
    } catch (e) {
      box.innerHTML = `<h3>Connection test failed</h3><p style="color:var(--bad)">${esc(e.message)}</p>`;
    } finally {
      $("#smoke-btn").disabled = false;
    }
  }

  // ---------- run history ----------
  async function loadRuns() {
    let runs = [];
    try { runs = await api("/api/runs"); } catch (e) { return; }
    const t = $("#runs-table");
    if (!runs.length) { t.innerHTML = `<tr><td class="muted">No runs yet.</td></tr>`; return; }
    t.innerHTML = `<thead><tr><th>Started</th><th>Run id</th><th>Mode</th><th>Test set</th><th>Puzzles</th><th>Trials</th><th>Status</th><th></th></tr></thead><tbody>` +
      runs.map((r) => `<tr>
        <td>${esc((r.created_at || "").replace("T", " ").replace("+00:00", " UTC"))}</td>
        <td class="mono">${esc(r.id)}</td>
        <td>${modePill(r.mode)}</td>
        <td>${esc(r.suite_label || r.suite)}</td>
        <td>${r.puzzle_count ?? "—"}</td>
        <td>${r.trials_done ?? 0} / ${r.trials_total ?? "—"}</td>
        <td>${esc(r.status)}</td>
        <td><button class="btn ghost" data-view="${esc(r.id)}">View</button>
            <button class="btn ghost" data-del="${esc(r.id)}" ${r.status === "running" ? "disabled" : ""}>Delete</button></td>
      </tr>`).join("") + "</tbody>";
    t.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => viewRun(b.dataset.view)));
    t.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`Delete run ${b.dataset.del}? This removes its saved results file.`)) return;
      try { await api(`/api/runs/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); } catch (e) { showAlert(e.message); }
      if (app.viewRunId === b.dataset.del) { app.viewRunId = null; app.runData = null; }
      loadRuns(); refreshDashboard();
    }));
  }

  // ---------- dashboard ----------
  function colourFor(key) {
    const keys = app.models.map((m) => m.key);
    const i = keys.indexOf(key);
    return LANE_COLOURS[(i >= 0 ? i : 0) % LANE_COLOURS.length];
  }

  function barChart(el, rows, key, fmt, { lowerBetter = false, fixedMax = null } = {}) {
    const vals = rows.map((r) => r[key]).filter((v) => v != null);
    if (!vals.length) { el.innerHTML = `<div class="muted small-text">No data yet.</div>`; return; }
    const max = fixedMax ?? Math.max(...vals.map((v) => Math.abs(v)), 1e-9);
    const best = lowerBetter ? Math.min(...vals) : Math.max(...vals);
    el.innerHTML = `<div class="bars">` + rows.map((r) => {
      const v = r[key];
      const w = v == null ? 0 : Math.max(1, (Math.abs(v) / max) * 100);
      return `<div class="bar-row">
        <div class="bar-label" title="${esc(r.model_name)}">${v === best ? "★ " : ""}${esc(r.model_name)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${colourFor(r.model_key)}"></div></div>
        <div class="bar-val">${v == null ? "—" : fmt(v)}</div></div>`;
    }).join("") + `</div>`;
  }

  function statsTableHTML(stats) {
    const lat = stats.map((s) => s.avg_latency_ms).filter((v) => v != null);
    const sr = stats.map((s) => s.success_rate);
    const gap = stats.map((s) => s.avg_optimal_gap).filter((v) => v != null);
    const tt = stats.map((s) => s.avg_total_time_s).filter((v) => v != null);
    const minLat = Math.min(...lat), maxSr = Math.max(...sr), minGap = Math.min(...gap), minT = Math.min(...tt);
    return stats.map((s) => `<tr>
      <td><span class="dot" style="background:${colourFor(s.model_key)}"></span>${esc(s.model_name)}
          ${s.mode === "mock" ? '<span class="pill sim">SIM</span>' : ""}<div class="lane-id">${esc(s.model_id)}</div></td>
      <td>${esc(s.interface)}</td>
      <td class="${s.avg_latency_ms === minLat ? "best" : ""}">${fmtMs(s.avg_latency_ms)}</td>
      <td>${fmtMs(s.median_latency_ms)}</td>
      <td class="${s.avg_total_time_s === minT ? "best" : ""}">${fmtS(s.avg_total_time_s)}</td>
      <td>${s.total_moves} <span class="muted">(avg ${s.avg_moves})</span></td>
      <td class="${s.success_rate === maxSr ? "best" : ""}">${s.success_rate.toFixed(1)}% <span class="muted">(${s.solved}/${s.trials})</span></td>
      <td class="${s.avg_optimal_gap === minGap ? "best" : ""}">${s.avg_optimal_gap == null ? "—" : fmtGap(s.avg_optimal_gap)}
          <span class="muted">${s.gap_samples ? `(n=${s.gap_samples}, ${s.zero_gap_solves} optimal)` : ""}</span></td>
      <td>${s.invalid_outputs} / ${s.retries}${s.errored_trials ? ` <span class="muted">· ${s.errored_trials} err</span>` : ""}${s.budget_stops ? ` <span class="muted">· ${s.budget_stops} budget</span>` : ""}</td>
      <td>${s.live_calls ?? 0} / ${s.cache_hits ?? 0}</td>
      <td>${s.total_cost_usd ? "$" + s.total_cost_usd.toFixed(5) : "$0"}</td>
    </tr>`).join("");
  }

  function answersHTML(a, editable) {
    if (!a || !a.ready) return `<li class="muted">Run a benchmark to generate findings.</li>`;
    const q5saved = lsGet("q5_text", "");
    const q5 = q5saved || a.q5_recommendation;
    return `
      <li><b>Fastest decisions:</b> ${esc(a.q1_fastest)}</li>
      <li><b>Highest solve rate:</b> ${esc(a.q2_highest_solve_rate)}</li>
      <li><b>Shortest solutions:</b> ${esc(a.q3_shortest_solutions)}</li>
      <li><b>Fastest = best quality?</b> ${esc(a.q4_fastest_also_best)}</li>
      <li><b>Recommendation:</b> ${editable
        ? `<textarea id="q5-edit">${esc(q5)}</textarea><div class="muted small-text">Auto-drafted from the data — edit freely; the slide uses this text.</div>`
        : esc(q5)}</li>`;
  }

  async function refreshDashboard() {
    const scope = $("#dash-scope").value;
    const suite = $("#dash-suite").value;
    const incMock = $("#dash-mock").checked;
    let url;
    if (scope === "current") {
      if (!app.viewRunId) {
        app.lastStats = null;
        $("#dash-meta").textContent = "No run selected yet — start a benchmark or pick one from Run history.";
        $("#stats-table tbody").innerHTML = "";
        ["#ch-latency", "#ch-success", "#ch-gap", "#ch-time"].forEach((s) => ($(s).innerHTML = `<div class="muted small-text">No data yet.</div>`));
        $("#matrix").innerHTML = "";
        $("#answers").innerHTML = answersHTML(null);
        $("#csv-link").href = "/api/export.csv";
        return;
      }
      url = `/api/stats?ids=${encodeURIComponent(app.viewRunId)}&include_mock=true`;
      $("#csv-link").href = `/api/export.csv?ids=${encodeURIComponent(app.viewRunId)}&include_mock=true`;
    } else {
      url = `/api/stats?include_mock=${incMock}` + (suite ? `&suite=${encodeURIComponent(suite)}` : "");
      $("#csv-link").href = `/api/export.csv?include_mock=${incMock}`;
    }
    let data;
    try { data = await api(url); } catch (e) { return showAlert(e.message); }
    app.lastStats = data;
    app.lastScope = scope;
    if (!app.models.length && data.stats.length) app.models = data.stats.map((s) => ({ key: s.model_key }));
    $("#dash-meta").textContent = `${data.trials} trial(s) from ${data.runs.length} run(s)` +
      (scope === "current" ? ` · run ${app.viewRunId}` : (incMock ? " · simulator runs included" : " · live runs only"));
    $("#stats-table tbody").innerHTML = data.stats.length ? statsTableHTML(data.stats) : `<tr><td colspan="11" class="muted">No finished trials yet.</td></tr>`;
    barChart($("#ch-latency"), data.stats, "avg_latency_ms", fmtMs, { lowerBetter: true });
    barChart($("#ch-success"), data.stats, "success_rate", (v) => v.toFixed(0) + "%", { fixedMax: 100 });
    barChart($("#ch-gap"), data.stats, "avg_optimal_gap", (v) => fmtGap(v), { lowerBetter: true });
    barChart($("#ch-time"), data.stats, "avg_total_time_s", fmtS, { lowerBetter: true });
    $("#answers").innerHTML = answersHTML(data.answers, true);
    const q5 = $("#q5-edit");
    if (q5) q5.addEventListener("input", () => lsSet("q5_text", q5.value));
    renderMatrix(scope);
  }

  async function renderMatrix(scope) {
    const t = $("#matrix");
    if (scope !== "current" || !app.viewRunId) { t.innerHTML = `<tr><td class="muted">Select a single run to see per-puzzle results.</td></tr>`; return; }
    let d = app.runData && app.runData.id === app.viewRunId ? app.runData : null;
    if (!d) { try { d = await api(`/api/runs/${encodeURIComponent(app.viewRunId)}`); } catch { return; } }
    const models = d.models;
    t.innerHTML = `<thead><tr><th>#</th><th>Scramble</th><th>Optimal</th>${models.map((m) => `<th>${esc(m.name)}</th>`).join("")}</tr></thead><tbody>` +
      d.puzzles.map((p) => `<tr><td>${p.index + 1}</td><td class="mono">${esc(p.scramble.join(" "))}</td>
        <td>${p.optimal_distance ?? "> " + p.optimal.max_depth}</td>` +
        models.map((m) => {
          const tr = d.trials.find((x) => x.puzzle_index === p.index && x.model_key === m.key);
          if (!tr) return `<td class="muted">—</td>`;
          if (tr.status === "solved") return `<td class="ok">✓ ${tr.move_count} mv${tr.optimal_gap != null ? ` (gap ${fmtGap(tr.optimal_gap)})` : ""} · ${fmtMs(tr.avg_latency_ms)}</td>`;
          if (tr.status === "limit") return `<td class="no">✗ limit · ${fmtMs(tr.avg_latency_ms)}</td>`;
          if (tr.status === "error") return `<td class="err" title="${esc(tr.error)}">⚠ error</td>`;
          if (tr.status === "budget") return `<td class="err" title="${esc(tr.error)}">⛔ budget stop · ${tr.move_count} mv</td>`;
          return `<td class="muted">${esc(tr.status)}</td>`;
        }).join("") + `</tr>`).join("") + "</tbody>";
  }

  // ---------- slide ----------
  function openSlide() {
    const data = app.lastStats;
    renderStudent();
    if (!data || !data.stats.length) {
      showAlert("No benchmark results yet — run a benchmark (or pick a run in Run history) first.", "info");
      return;
    }
    const d = app.runData && app.lastScope === "current" ? app.runData : null;
    const meta = d
      ? `${d.suite_label} · ${d.puzzle_count} puzzle(s) × ${d.models.length} models · move limits ${Object.entries((d.settings || {}).move_limits || {}).map(([k, v]) => k + " " + v).join(", ") || d.max_moves} · seed ${d.seed} · ${d.mode === "mock" ? "SIMULATOR DATA" : modeLabel(d.mode)}`
      : `${data.trials} trials across ${data.runs.length} run(s)`;
    $("#slide-meta").textContent = meta;
    $("#slide-table").innerHTML = `<thead><tr><th>Model</th><th>Interface</th><th>Avg latency / move</th><th>Median</th><th>Avg total time</th><th>Total moves</th><th>Success rate</th><th>Avg optimal gap</th><th>Invalid / retries</th><th>API calls / cache</th><th>Cost</th></tr></thead><tbody>${statsTableHTML(data.stats)}</tbody>`;
    barChart($("#sl-latency"), data.stats, "avg_latency_ms", fmtMs, { lowerBetter: true });
    barChart($("#sl-success"), data.stats, "success_rate", (v) => v.toFixed(0) + "%", { fixedMax: 100 });
    barChart($("#sl-gap"), data.stats, "avg_optimal_gap", (v) => fmtGap(v), { lowerBetter: true });
    barChart($("#sl-time"), data.stats, "avg_total_time_s", fmtS, { lowerBetter: true });
    $("#slide-answers").innerHTML = answersHTML(data.answers, false);
    $("#slide").classList.remove("hidden");
  }
  const closeSlide = () => $("#slide").classList.add("hidden");

  // ---------- init ----------
  async function init() {
    heroCube = new CubeView.Cube3D($("#hero-cube"));
    heroNet = new CubeView.CubeNet($("#hero-net"));
    setHero(SOLVED);
    try {
      app.config = await api("/api/config");
    } catch (e) {
      showAlert("Backend not reachable: " + e.message);
      return;
    }
    const cfg = app.config;
    renderStudent();
    $("#key-pill").className = "pill " + (cfg.has_key ? "live" : "bad");
    $("#key-pill").textContent = cfg.has_key ? "API key: configured (server-side)" : "API key: not set";
    if (!cfg.has_key) showAlert("OPENROUTER_API_KEY is not set — use the Ollama or simulator profiles. Add the key to .env and restart for the single OpenRouter experiment.", "info");
    refreshBudget();

    const profSel = $("#profile");
    profSel.innerHTML = Object.entries(cfg.profiles).map(([k, p]) =>
      `<option value="${k}" ${p.requires_key && !cfg.has_key ? "disabled" : ""}>${esc(p.label)}${p.requires_key && !cfg.has_key ? " — needs key" : ""}</option>`).join("");
    let prof = lsGet("profile", cfg.default_profile);
    if (!cfg.profiles[prof] || (cfg.profiles[prof].requires_key && !cfg.has_key)) prof = cfg.default_profile;
    profSel.value = prof;
    app.profile = prof;

    $("#suite").innerHTML = Object.entries(cfg.suites).map(([k, s]) => `<option value="${k}">${esc(s.label)}${s.exact_distance ? "" : ` (${s.min_len}–${s.max_len} turns)`}</option>`).join("");
    $("#suite").value = "exact3";
    $("#dash-suite").innerHTML = `<option value="">All test sets</option>` + Object.entries(cfg.suites).map(([k, s]) => `<option value="${k}">${esc(s.label)}</option>`).join("");
    $("#max-moves").value = cfg.max_moves;
    renderSuiteHint();
    renderProfile();

    profSel.addEventListener("change", () => { app.profile = profSel.value; lsSet("profile", app.profile); app.runData = null; renderProfile(); });
    $("#suite").addEventListener("change", renderSuiteHint);
    $("#run-btn").addEventListener("click", runBenchmark);
    $("#stop-btn").addEventListener("click", stopRun);
    $("#slide-btn").addEventListener("click", openSlide);
    $("#smoke-btn").addEventListener("click", smokeTest);
    $("#slide-close").addEventListener("click", closeSlide);
    $("#refresh-runs").addEventListener("click", loadRuns);
    ["#dash-scope", "#dash-suite", "#dash-mock"].forEach((s) => $(s).addEventListener("change", refreshDashboard));
    $("#student-badge").addEventListener("click", editStudent);
    $("#save-student").addEventListener("click", () => {
      lsSet("student", { name: $("#in-name").value.trim(), roll: $("#in-roll").value.trim() });
      renderStudent();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSlide(); });
    app.timer = setInterval(tickTimers, 100);

    try {
      const c = await api("/api/contract");
      $("#contract").textContent = JSON.stringify(c, null, 2);
    } catch { /* optional */ }

    await loadRuns();
    // Re-attach to a run that is still in progress (e.g. after a page reload).
    try {
      const runs = await api("/api/runs");
      const running = runs.find((r) => r.status === "running" || r.status === "pending");
      if (running) attach(running.id);
      else refreshDashboard();
    } catch { refreshDashboard(); }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
