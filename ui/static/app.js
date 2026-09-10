// Key persist + live providers dropdown (issue 186)
(function () {
  const healthEl = document.getElementById("health");
  const keyInput = document.getElementById("key-input");
  const eyeBtn = document.getElementById("eye-btn");
  const saveBtn = document.getElementById("save-btn");
  const clearBtn = document.getElementById("clear-btn");
  const badge = document.getElementById("key-badge");
  const errEl = document.getElementById("key-error");
  const filterEl = document.getElementById("provider-filter");
  const selectEl = document.getElementById("provider-select");
  const countEl = document.getElementById("providers-count");

  let providersAll = [];
  let toastTimer = null;

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

  // Eye toggle — local only, no fetch
  if (eyeBtn && keyInput) {
    eyeBtn.addEventListener("click", function () {
      keyInput.type = keyInput.type === "password" ? "text" : "password";
    });
  }
  if (saveBtn) saveBtn.addEventListener("click", doSave);
  if (clearBtn) clearBtn.addEventListener("click", doClear);
  if (filterEl) filterEl.addEventListener("input", function () { renderProviders(filterEl.value); });

  // Init
  (async function () {
    // health
    try {
      const r = await fetch("/api/health");
      const j = await r.json();
      const ok = j.status === "ok" || j.ok === true;
      if (healthEl) healthEl.textContent = ok ? "ok" : JSON.stringify(j);
    } catch (e) {
      if (healthEl) healthEl.textContent = "error";
    }
    await refreshStatus();
    await loadProviders();
  })();
})();
