// Combined 186 (key/providers) + 187 (export dry-run 5 states)
(function () {
  // --- 186: key + providers ---
  const healthEl = document.getElementById("health");
  const statusEl = document.getElementById("status");
  const keyInput = document.getElementById("key-input");
  const eyeBtn = document.getElementById("eye-btn");
  const saveBtn = document.getElementById("save-btn");
  const clearBtn = document.getElementById("clear-btn");
  const badge = document.getElementById("key-badge");
  const errEl = document.getElementById("key-error");
  const filterEl = document.getElementById("provider-filter");
  const selectEl = document.getElementById("provider-select");
  const countEl = document.getElementById("providers-count");
  // --- 187: export ---
  const dryBtn = document.getElementById("dryBtn");
  const applyBtn = document.getElementById("applyBtn");
  const cancelBtn = document.getElementById("cancelExportBtn");
  const logEl = document.getElementById("exportLog");
  const dotEl = document.getElementById("exportDot");
  const stateEl = document.getElementById("exportState");
  const autoScroll = document.getElementById("exportAutoScroll");
  const clearLink = document.getElementById("exportClear");
  // --- 188: gateway + apply confirm ---
  const gatewayInput = document.getElementById("gateway-input");
  const gatewayError = document.getElementById("gateway-error");
  const applyModal = document.getElementById("apply-modal");
  const applyModalUrl = document.getElementById("apply-modal-url");
  const applyModalBackdrop = document.getElementById("apply-modal-backdrop");
  const applyCancelBtn = document.getElementById("apply-cancel-btn");
  const applyConfirmBtn = document.getElementById("apply-confirm-btn");

  let providersAll = [];
  let toastTimer = null;
  let currentJobId = null;
  let es = null;

  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  function toast(msg, isError) {
    const c = document.getElementById("toast");
    if (!c) return;
    const bg = isError ? "#7f1d1d" : "#0f172a";
    c.innerHTML = '<span class="toast" style="background:' + bg + '">' + esc(msg) + '</span>';
    c.style.display = "block";
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { c.style.display = "none"; }, 3000);
  }

  function setBadge(hasKey, hint) {
    if (!badge) return;
    if (hasKey) {
      badge.textContent = hint || "***";
      badge.className = "badge badge-green";
    } else {
      badge.textContent = "Not set";
      badge.className = "badge badge-gray";
    }
  }

  function setInputPlaceholder(hint) {
    if (!keyInput) return;
    if (hint) {
      keyInput.placeholder = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" + hint;
    } else {
      keyInput.placeholder = "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022***";
    }
  }

  function showError(msg) {
    if (!errEl) return;
    if (msg) {
      errEl.textContent = msg;
      errEl.style.display = "block";
    } else {
      errEl.textContent = "";
      errEl.style.display = "none";
    }
  }

  async function refreshStatus() {
    try {
      const r = await fetch("/api/config/status");
      const j = await r.json();
      const hasKey = !!j.hasKey;
      const hint = j.hint || "";
      setBadge(hasKey, hint);
      setInputPlaceholder(hint);
      if (keyInput) keyInput.value = "";
    } catch (e) {
      setBadge(false, "");
    }
  }

  async function doSave() {
    showError("");
    const raw = keyInput ? keyInput.value : "";
    try {
      const r = await fetch("/api/config/omniroute-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: raw })
      });
      if (!r.ok) {
        let detail = "key required";
        try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
        showError(detail);
        toast(detail, true);
        return;
      }
      const j = await r.json();
      setBadge(!!j.hasKey, j.hint || "");
      setInputPlaceholder(j.hint || "");
      if (keyInput) keyInput.value = "";
      toast("Saved");
    } catch (e) {
      const msg = String(e);
      showError(msg);
      toast(msg, true);
    }
  }

  async function doClear() {
    showError("");
    try {
      const r = await fetch("/api/config/omniroute-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "" })
      });
      if (!r.ok) {
        let detail = "clear failed";
        try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
        showError(detail);
        toast(detail, true);
        return;
      }
      const j = await r.json();
      setBadge(false, "");
      setInputPlaceholder("");
      if (keyInput) keyInput.value = "";
      toast("Cleared");
    } catch (e) {
      const msg = String(e);
      showError(msg);
      toast(msg, true);
    }
  }

  function renderProviders(filter) {
    if (!selectEl) return;
    const q = (filter || "").toLowerCase();
    const list = q ? providersAll.filter(function (n) { return n.toLowerCase().indexOf(q) !== -1; }) : providersAll.slice();
    selectEl.innerHTML = "";
    list.forEach(function (name) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      selectEl.appendChild(opt);
    });
    if (countEl) countEl.textContent = String(list.length) + " / " + String(providersAll.length);
  }

  async function loadProviders() {
    try {
      const r = await fetch("/api/providers");
      const j = await r.json();
      providersAll = Array.isArray(j.providers) ? j.providers.slice().sort() : [];
      renderProviders(filterEl ? filterEl.value : "");
    } catch (e) {
      if (countEl) countEl.textContent = "error";
    }
  }

  // --- 187: export 5 states ---
  function setState(state, extra) {
    if (!dotEl || !stateEl || !logEl) return;
    if (state === "idle") {
      dotEl.className = "dot";
      stateEl.textContent = "idle \u2014 no logs yet";
      logEl.className = "log empty";
      logEl.textContent = "No logs yet. Run Dry run (safe) to see streaming output. Placeholder apiKey = env:SECRET on dry-run; resolved only on apply.";
      if (dryBtn) dryBtn.disabled = false;
      if (applyBtn) applyBtn.disabled = !isGatewayValid(gatewayInput ? gatewayInput.value : "http://localhost:20128");
      if (cancelBtn) cancelBtn.disabled = true;
    } else if (state === "running") {
      dotEl.className = "dot running";
      stateEl.textContent = "running \u2014 streaming logs\u2026";
      logEl.className = "log";
      if (dryBtn) dryBtn.disabled = true;
      if (applyBtn) applyBtn.disabled = true;
      if (cancelBtn) cancelBtn.disabled = false;
    } else if (state === "done") {
      dotEl.className = "dot ok";
      stateEl.textContent = "done \u2014 exit 0";
      if (dryBtn) dryBtn.disabled = false;
      if (applyBtn) applyBtn.disabled = !isGatewayValid(gatewayInput ? gatewayInput.value : "http://localhost:20128");
      if (cancelBtn) cancelBtn.disabled = true;
    } else if (state === "error") {
      dotEl.className = "dot err";
      stateEl.textContent = "error \u2014 exit " + (extra && extra.exitCode != null ? extra.exitCode : "1");
      if (dryBtn) dryBtn.disabled = false;
      if (applyBtn) applyBtn.disabled = !isGatewayValid(gatewayInput ? gatewayInput.value : "http://localhost:20128");
      if (cancelBtn) cancelBtn.disabled = true;
      toast("Export failed (exit " + (extra && extra.exitCode != null ? extra.exitCode : 1) + ")", true);
    } else if (state === "killed") {
      dotEl.className = "dot err";
      stateEl.textContent = "killed \u2014 canceled";
      if (dryBtn) dryBtn.disabled = false;
      if (applyBtn) applyBtn.disabled = !isGatewayValid(gatewayInput ? gatewayInput.value : "http://localhost:20128");
      if (cancelBtn) cancelBtn.disabled = true;
      toast("Canceled export (killed)", true);
    }
  }

  function appendLine(entry) {
    if (!logEl) return;
    var html = "";
    if (entry.type === "stdout") html = "<span>" + esc(entry.line) + "</span>";
    else if (entry.type === "stderr") html = '<span class="stderr">' + esc(entry.line) + "</span>";
    var line = document.createElement("div");
    line.innerHTML = html;
    if (logEl.classList.contains("empty")) { logEl.textContent = ""; logEl.className = "log"; }
    logEl.appendChild(line);
    while (logEl.children.length > 500) { logEl.removeChild(logEl.firstChild); }
    if (autoScroll && autoScroll.checked) { logEl.scrollTop = logEl.scrollHeight; }
  }

  function appendTerminal(entry) {
    if (!logEl) return;
    var d = document.createElement("div");
    if (entry.type === "done") {
      if (entry.exitCode === 0) { d.innerHTML = '<span class="ok">\u2713 done exitCode:0</span>'; setState("done"); }
      else { d.innerHTML = '<span class="stderr">exit ' + esc(String(entry.exitCode)) + "</span>"; setState("error", entry); }
    } else if (entry.type === "killed") { d.innerHTML = '<span class="killed">killed</span>'; setState("killed"); }
    logEl.appendChild(d);
    if (autoScroll && autoScroll.checked) { logEl.scrollTop = logEl.scrollHeight; }
    if (es) { es.close(); es = null; }
    currentJobId = null;
  }

  // --- 188 helpers ---
  function isGatewayValid(v) {
    return /^https?:\/\//.test(String(v || "").trim());
  }
  function showGatewayError(msg) {
    if (!gatewayError) return;
    if (msg) { gatewayError.textContent = msg; gatewayError.style.display = "block"; }
    else { gatewayError.textContent = ""; gatewayError.style.display = "none"; }
  }
  function validateGateway() {
    if (!gatewayInput) return true;
    const v = gatewayInput.value.trim();
    if (!v) { showGatewayError(""); if (applyBtn) applyBtn.disabled = true; return false; }
    if (!isGatewayValid(v)) {
      showGatewayError("gatewayUrl must be http(s)://");
      if (applyBtn) applyBtn.disabled = true;
      return false;
    }
    showGatewayError("");
    // re-enable only if not running
    if (applyBtn && dotEl && !dotEl.classList.contains("running")) applyBtn.disabled = false;
    return true;
  }
  function openApplyModal() {
    if (!applyModal) return;
    const v = gatewayInput ? gatewayInput.value.trim() : "http://localhost:20128";
    if (applyModalUrl) applyModalUrl.textContent = v || "http://localhost:20128";
    applyModal.style.display = "flex";
  }
  function closeApplyModal() {
    if (!applyModal) return;
    applyModal.style.display = "none";
  }
  async function runApply(gatewayUrl) {
    if (es) { es.close(); es = null; }
    if (logEl) { logEl.textContent = ""; logEl.className = "log"; }
    setState("running");
    // dry-run needs no URL: this path always posts to /api/export/apply
    try {
      const r = await fetch("/api/export/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gatewayUrl: gatewayUrl })
      });
      if (!r.ok) {
        let detail = "gatewayUrl must be http(s)://";
        try { const j = await r.json(); detail = j.detail || detail; } catch (_) {}
        showGatewayError(detail);
        toast(detail, true);
        setState("error", { exitCode: 1 });
        var d2 = document.createElement("div"); d2.innerHTML = '<span class="stderr">' + esc(detail) + "</span>"; if (logEl) logEl.appendChild(d2);
        return;
      }
      const j = await r.json();
      const jobId = j.jobId;
      currentJobId = jobId;
      es = new EventSource("/api/jobs/" + jobId + "/logs");
      es.onmessage = function (e) {
        try {
          var data = JSON.parse(e.data);
          if (data.type === "stdout" || data.type === "stderr") appendLine(data);
          else if (data.type === "done" || data.type === "killed") appendTerminal(data);
        } catch (err) {}
      };
      es.onerror = function () {};
    } catch (e) {
      setState("error", { exitCode: 1 });
      var d = document.createElement("div"); d.innerHTML = '<span class="stderr">' + esc(String(e)) + "</span>"; if (logEl) logEl.appendChild(d);
    }
  }

  if (eyeBtn && keyInput) {
    eyeBtn.addEventListener("click", function () {
      keyInput.type = keyInput.type === "password" ? "text" : "password";
    });
  }
  if (saveBtn) saveBtn.addEventListener("click", doSave);
  if (clearBtn) clearBtn.addEventListener("click", doClear);
  if (filterEl) filterEl.addEventListener("input", function () { renderProviders(filterEl.value); });

  if (dryBtn) {
    dryBtn.addEventListener("click", async function () {
      if (es) { es.close(); es = null; }
      if (logEl) { logEl.textContent = ""; logEl.className = "log"; }
      setState("running");
      try {
        const r = await fetch("/api/export/dry-run", { method: "POST" });
        const j = await r.json();
        const jobId = j.jobId;
        currentJobId = jobId;
        es = new EventSource("/api/jobs/" + jobId + "/logs");
        es.onmessage = function (e) {
          try {
            var data = JSON.parse(e.data);
            if (data.type === "stdout" || data.type === "stderr") appendLine(data);
            else if (data.type === "done" || data.type === "killed") appendTerminal(data);
          } catch (err) {}
        };
        es.onerror = function () {};
      } catch (e) {
        setState("error", { exitCode: 1 });
        var d = document.createElement("div"); d.innerHTML = '<span class="stderr">' + esc(String(e)) + "</span>"; logEl.appendChild(d);
      }
    });
  }
  if (cancelBtn) {
    cancelBtn.addEventListener("click", async function () {
      if (!currentJobId) return;
      try { await fetch("/api/jobs/" + currentJobId + "/cancel", { method: "POST" }); } catch (e) {}
    });
  }
  if (clearLink) {
    clearLink.addEventListener("click", function (e) {
      e.preventDefault();
      if (es) { es.close(); es = null; }
      currentJobId = null;
      setState("idle");
    });
  }
  // --- 188 wiring ---
  if (gatewayInput) {
    gatewayInput.addEventListener("input", validateGateway);
    gatewayInput.addEventListener("change", validateGateway);
  }
  if (applyBtn) {
    applyBtn.addEventListener("click", function () {
      if (!validateGateway()) return;
      openApplyModal();
    });
  }
  if (applyCancelBtn) applyCancelBtn.addEventListener("click", closeApplyModal);
  if (applyModalBackdrop) applyModalBackdrop.addEventListener("click", closeApplyModal);
  if (applyConfirmBtn) {
    applyConfirmBtn.addEventListener("click", function () {
      closeApplyModal();
      const v = gatewayInput ? gatewayInput.value.trim() : "http://localhost:20128";
      runApply(v);
    });
  }
  // ESC closes modal
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && applyModal && applyModal.style.display !== "none") closeApplyModal();
  });

  // Init
  (async function () {
    validateGateway();
    try {
      const r = await fetch("/api/health");
      const j = await r.json();
      const ok = j.status === "ok" || j.ok === true;
      if (healthEl) healthEl.textContent = ok ? "ok" : JSON.stringify(j);
      if (statusEl) statusEl.textContent = ok ? "health: ok" : "health: " + JSON.stringify(j);
    } catch (e) {
      if (healthEl) healthEl.textContent = "error";
      if (statusEl) statusEl.textContent = String(e);
    }
    await refreshStatus();
    await loadProviders();
    if (dotEl && stateEl && logEl) setState("idle");
  })();
})();