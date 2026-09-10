// scaffold — health check only
(async () => {
  const el = document.getElementById("health");
  const status = document.getElementById("status");
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    const ok = j.status === "ok" || j.ok === true;
    if (el) el.textContent = ok ? "ok" : JSON.stringify(j);
    if (status) status.textContent = ok ? "health: ok" : "health: " + JSON.stringify(j);
  } catch (e) {
    if (el) el.textContent = "error";
    if (status) status.textContent = String(e);
  }
})();
