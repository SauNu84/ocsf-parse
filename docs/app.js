// Pulls catalog.json from the main branch + renders + filters the table.
// Single-file dependency-free; runs fine off GitHub Pages.

const CATALOG_URL =
  "https://raw.githubusercontent.com/SauNu84/ocsf-parse/main/catalog.json";

const $ = (sel) => document.querySelector(sel);

(async function () {
  let entries = [];
  try {
    const resp = await fetch(CATALOG_URL, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    entries = data.entries || [];
  } catch (err) {
    console.error("could not load catalog.json:", err);
    const tbody = $("#catalog-table tbody");
    tbody.innerHTML =
      `<tr><td colspan="5" class="muted">could not load catalog.json — ` +
      `view it on <a href="https://github.com/SauNu84/ocsf-parse/blob/main/catalog.json">GitHub</a>.</td></tr>`;
    return;
  }

  // Update headline stats from the catalog data.
  $("#stat-mappings").textContent = entries.length;
  const uniqClasses = new Set(entries.map((e) => e.ocsf.class_name));
  const uniqCats    = new Set(entries.map((e) => e.ocsf.category_name));
  $("#stat-classes").textContent    = uniqClasses.size;
  $("#stat-categories").textContent = uniqCats.size;

  const filterPri  = $("#filter-priority");
  const filterText = $("#filter-text");

  function render() {
    const text = filterText.value.toLowerCase().trim();
    const pri  = filterPri.value;
    const priOrder = { critical: 0, high: 1, medium: 2, low: 3 };

    const filtered = entries
      .filter((e) => !pri || e.priority === pri)
      .filter((e) => {
        if (!text) return true;
        const blob = [
          e.source, e.display_name, e.vendor, e.description,
          e.ocsf.category_name, e.ocsf.class_name,
        ].join(" ").toLowerCase();
        return blob.includes(text);
      })
      .sort((a, b) => {
        const p = (priOrder[a.priority] ?? 99) - (priOrder[b.priority] ?? 99);
        return p !== 0 ? p : a.display_name.localeCompare(b.display_name);
      });

    const tbody = $("#catalog-table tbody");
    tbody.innerHTML = filtered.map((e) => `
      <tr>
        <td>
          <strong>${escapeHtml(e.display_name)}</strong><br>
          <code>${escapeHtml(e.source)}</code>
        </td>
        <td>${escapeHtml(e.vendor)}</td>
        <td>${escapeHtml(e.ocsf.category_name)}</td>
        <td>
          ${escapeHtml(e.ocsf.class_name)}
          <span class="muted">(${e.ocsf.class_uid})</span>
        </td>
        <td><span class="badge pri-${e.priority}">${e.priority}</span></td>
      </tr>
    `).join("");
    $("#catalog-empty").hidden = filtered.length > 0;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  filterPri.addEventListener("change", render);
  filterText.addEventListener("input", render);
  render();
})();
