/* Receiving Workbench — full-screen UI */
(function () {
  const state = {
    org: "",
    token: "",
    facility: "",
    validAsnIds: new Set(),
    asn: null, // last loaded ASN payload from /api/load_asn
    selectedLineNumber: null,
  };

  const el = {
    filtersScreen: document.getElementById("filtersScreen"),
    resultsScreen: document.getElementById("resultsScreen"),
    orgSection: document.getElementById("orgSection"),
    mainUI: document.getElementById("mainUI"),
    org: document.getElementById("org"),
    authBtn: document.getElementById("authBtn"),
    asnScan: document.getElementById("asnScan"),
    matchHint: document.getElementById("matchHint"),
    status: document.getElementById("status"),
    loadAsnBtn: document.getElementById("loadAsnBtn"),
    backToFilters: document.getElementById("backToFilters"),
    resultsStatus: document.getElementById("resultsStatus"),
    asnMeta: document.getElementById("asnMeta"),
    linesBody: document.getElementById("linesBody"),
    partialLineBtn: document.getElementById("partialLineBtn"),
    fullLineBtn: document.getElementById("fullLineBtn"),
    allLinesBtn: document.getElementById("allLinesBtn"),
    busyOverlay: document.getElementById("busyOverlay"),
    themeLogo: document.getElementById("themeLogo"),
    themeSelectorBtn: document.getElementById("themeSelectorBtn"),
    themeList: document.getElementById("themeList"),
  };

  function setBusy(on, label) {
    el.busyOverlay.classList.toggle("visible", !!on);
    el.busyOverlay.textContent = label || "Working…";
  }

  function setStatus(msg, kind) {
    el.status.textContent = msg || "";
    el.status.className = "status-line flex-grow-1" + (kind ? " " + kind : "");
  }

  async function api(action, data) {
    const res = await fetch("/api/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
    let body = {};
    try {
      body = await res.json();
    } catch (_) {
      body = { success: false, error: "Invalid JSON response (" + res.status + ")" };
    }
    if (!res.ok && body && !body.error) {
      body.error = "Request failed (" + res.status + ")";
      body.success = false;
    }
    return body;
  }

  function fmtQty(v) {
    if (v === null || v === undefined || v === "") return "0";
    const n = Number(v);
    return Number.isNaN(n) ? String(v) : String(n);
  }

  function fmtCount(n, singular, plural) {
    const count = Number(n) || 0;
    const word = count === 1 ? singular : plural || singular + "s";
    return count + " " + word;
  }

  function updateLoadButton() {
    const value = el.asnScan.value.trim();
    const valid = !!value && state.validAsnIds.has(value);
    el.loadAsnBtn.disabled = !valid;
    if (!state.token) {
      el.matchHint.textContent = "Authenticate to preload receivable ASNs.";
    } else if (!value) {
      el.matchHint.textContent =
        "Preloaded " + fmtCount(state.validAsnIds.size, "receivable ASN") + ". Scan or type an ASN number.";
    } else if (valid) {
      el.matchHint.textContent = "ASN " + value + " is ready to load.";
    } else {
      el.matchHint.textContent = "ASN not found or not eligible for receiving.";
    }
  }

  async function authenticate(org, options) {
    options = options || {};
    org = (org || "").trim().toUpperCase();
    if (!org) {
      setStatus("ORG is required", "error");
      return false;
    }
    if (!options.quiet) {
      setBusy(true, "Authenticating…");
      setStatus("Authenticating…");
    }
    try {
      const data = await api("auth", { org });
      if (!data.success) {
        setStatus(data.error || "Auth failed", "error");
        return false;
      }
      state.org = data.org || org;
      state.token = data.token;
      state.facility = state.org + "-DM1";
      el.org.value = state.org;
      el.orgSection.style.display = "none";
      el.mainUI.style.display = "block";
      el.asnScan.disabled = false;
      el.asnScan.focus();
      const via =
        data.source === "token-file"
          ? "via .token"
          : data.source === "oauth"
            ? "via OAuth"
            : "";
      setStatus("Authenticated " + via + ". Preloading ASNs…", "success");
      await preload();
      return true;
    } catch (e) {
      setStatus(e.message || String(e), "error");
      return false;
    } finally {
      if (!options.quiet) setBusy(false);
    }
  }

  async function preload() {
    setBusy(true, "Preloading ASNs…");
    try {
      const data = await api("preload_asns", {
        org: state.org,
        token: state.token,
        location: state.facility,
      });
      if (!data.success) {
        setStatus(data.error || "Preload failed", "error");
        return;
      }
      const entries = data.entries || [];
      state.validAsnIds = new Set(entries.map((e) => e.asnId));
      setStatus("Ready — " + fmtCount(state.validAsnIds.size, "receivable ASN") + " indexed", "success");
      updateLoadButton();
    } catch (e) {
      setStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  function itemImageCellHtml(imageUrl) {
    if (imageUrl) {
      return (
        '<span class="item-image-wrap item-image-wrap--inline" data-image-url="' +
        escapeAttr(imageUrl) +
        '"><img class="item-image-thumb" src="' +
        escapeAttr(imageUrl) +
        '" alt="" loading="lazy" /></span>'
      );
    }
    return '<span class="item-image-wrap item-image-wrap--empty">—</span>';
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  function renderLines(lines) {
    state.selectedLineNumber = null;
    updateLineActionButtons();
    el.linesBody.innerHTML = (lines || [])
      .map(
        (line) => `
        <tr class="line-row" data-line-number="${escapeAttr(line.lineNumber)}">
          <td>${escapeHtml(line.lineNumber)}</td>
          <td>
            <span class="item-cell">
              ${itemImageCellHtml(line.itemImageUrl)}
              <span>${escapeHtml(line.itemId)}</span>
            </span>
          </td>
          <td>${escapeHtml(line.description)}</td>
          <td class="col-qty">${fmtQty(line.shippedQuantity)}</td>
          <td class="col-qty">${fmtQty(line.receivedQuantity)}</td>
          <td class="col-uom">${escapeHtml(line.quantityUomId)}</td>
        </tr>`
      )
      .join("");
  }

  function updateLineActionButtons() {
    const hasSelection = state.selectedLineNumber !== null;
    el.partialLineBtn.disabled = !hasSelection;
    el.fullLineBtn.disabled = !hasSelection;
  }

  function selectLine(lineNumber) {
    state.selectedLineNumber = lineNumber;
    el.linesBody.querySelectorAll("tr.line-row").forEach((row) => {
      row.classList.toggle("selected", row.dataset.lineNumber === String(lineNumber));
    });
    updateLineActionButtons();
  }

  function showResults() {
    el.filtersScreen.classList.remove("active");
    el.resultsScreen.classList.add("active");
  }

  function showFilters() {
    el.resultsScreen.classList.remove("active");
    el.filtersScreen.classList.add("active");
    el.asnScan.value = "";
    updateLoadButton();
    el.asnScan.focus();
  }

  async function loadAsn() {
    const asnId = el.asnScan.value.trim();
    if (!asnId || !state.validAsnIds.has(asnId)) return;
    setBusy(true, "Loading ASN…");
    try {
      const data = await api("load_asn", {
        org: state.org,
        token: state.token,
        location: state.facility,
        asnId,
      });
      if (!data.success) {
        setStatus(data.error || "Load failed", "error");
        return;
      }
      state.asn = data;
      renderLines(data.lines);
      el.asnMeta.innerHTML = `
        <span><strong>ASN</strong> ${escapeHtml(data.asnId)}</span>
        <span><strong>Status</strong> ${escapeHtml(data.asnStatusLabel || data.asnStatus)}</span>
        <span><strong>Vendor</strong> ${escapeHtml(data.vendorId || "")}</span>
      `;
      el.resultsStatus.textContent = fmtCount(data.lineCount || 0, "line");
      showResults();
    } catch (e) {
      setStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  // --- Wiring ---
  if (el.authBtn) {
    el.authBtn.addEventListener("click", () => authenticate(el.org.value));
  }
  el.org.addEventListener("keypress", (e) => {
    if (e.key === "Enter") authenticate(el.org.value);
  });
  el.asnScan.addEventListener("input", updateLoadButton);
  el.asnScan.addEventListener("keypress", (e) => {
    if (e.key === "Enter" && !el.loadAsnBtn.disabled) loadAsn();
  });
  el.loadAsnBtn.addEventListener("click", loadAsn);
  el.backToFilters.addEventListener("click", showFilters);
  el.linesBody.addEventListener("click", (e) => {
    const row = e.target.closest("tr.line-row");
    if (!row) return;
    selectLine(row.dataset.lineNumber);
  });

  bindItemImagePreview(el.linesBody);

  if (window.InspectionThemes) {
    const themeModalEl = document.getElementById("themeModal");
    const themeModal = window.bootstrap ? new window.bootstrap.Modal(themeModalEl) : null;
    window.InspectionThemes.wireThemePicker({
      themeSelectorBtn: el.themeSelectorBtn,
      themeModal,
      themeList: el.themeList,
      themeLogo: el.themeLogo,
    });
  }

  el.org.focus();
})();
