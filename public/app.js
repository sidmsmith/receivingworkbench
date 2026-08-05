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
    actionStatus: document.getElementById("actionStatus"),
    partialLineInfo: document.getElementById("partialLineInfo"),
    partialQtyInput: document.getElementById("partialQtyInput"),
    partialQtyUom: document.getElementById("partialQtyUom"),
    partialQtyHint: document.getElementById("partialQtyHint"),
    partialLineConfirmBtn: document.getElementById("partialLineConfirmBtn"),
    allLinesList: document.getElementById("allLinesList"),
    allLinesConfirmBtn: document.getElementById("allLinesConfirmBtn"),
    busyOverlay: document.getElementById("busyOverlay"),
    themeLogo: document.getElementById("themeLogo"),
    themeSelectorBtn: document.getElementById("themeSelectorBtn"),
    themeList: document.getElementById("themeList"),
  };

  /** Case-insensitive query params: org/organization, theme, asn/asnid/asn_id/asn-id */
  function parseUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const ci = {};
    for (const [key, value] of params.entries()) {
      ci[String(key).toLowerCase()] = value;
    }
    return {
      org: String(ci.org || ci.organization || "").trim(),
      theme: String(ci.theme || "").trim(),
      asn: String(ci.asn || ci.asnid || ci.asn_id || ci["asn-id"] || "").trim(),
    };
  }

  const urlParams = parseUrlParams();

  function setBusy(on, label) {
    el.busyOverlay.classList.toggle("visible", !!on);
    el.busyOverlay.textContent = label || "Working…";
  }

  function setStatus(msg, kind) {
    el.status.textContent = msg || "";
    el.status.className = "status-line flex-grow-1" + (kind ? " " + kind : "");
  }

  function setActionStatus(msg, kind) {
    el.actionStatus.textContent = msg || "";
    el.actionStatus.className = "status-line mb-2" + (kind ? " " + kind : "");
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
      await applyUrlAsnBoot();
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
    setActionStatus("");
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

  async function fetchAndRenderAsn(asnId) {
    const data = await api("load_asn", {
      org: state.org,
      token: state.token,
      location: state.facility,
      asnId,
    });
    if (!data.success) {
      setStatus(data.error || "Load failed", "error");
      return false;
    }
    state.asn = data;
    renderLines(data.lines);
    el.asnMeta.innerHTML = `
      <span><strong>ASN</strong> ${escapeHtml(data.asnId)}</span>
      <span><strong>Status</strong> ${escapeHtml(data.asnStatusLabel || data.asnStatus)}</span>
      <span><strong>Vendor</strong> ${escapeHtml(data.vendorId || "")}</span>
    `;
    el.resultsStatus.textContent = fmtCount(data.lineCount || 0, "line");
    return true;
  }

  async function loadAsn() {
    const asnId = el.asnScan.value.trim();
    if (!asnId || !state.validAsnIds.has(asnId)) return;
    setBusy(true, "Loading ASN…");
    try {
      const ok = await fetchAndRenderAsn(asnId);
      if (ok) showResults();
    } catch (e) {
      setStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let urlAsnBootApplied = false;
  async function applyUrlAsnBoot() {
    if (urlAsnBootApplied) return;
    urlAsnBootApplied = true;
    if (!urlParams.asn) return;
    el.asnScan.value = urlParams.asn.toUpperCase();
    updateLoadButton();
    if (!el.loadAsnBtn.disabled) await loadAsn();
  }

  function getSelectedLine() {
    if (state.selectedLineNumber === null || !state.asn) return null;
    return (state.asn.lines || []).find(
      (l) => String(l.lineNumber) === String(state.selectedLineNumber)
    ) || null;
  }

  function remainingQty(line) {
    const rem = Number(line.shippedQuantity || 0) - Number(line.receivedQuantity || 0);
    return rem > 0 ? rem : 0;
  }

  async function callReceiveLine(asnLineId, mode, quantity) {
    return api("receive_line", {
      org: state.org,
      token: state.token,
      location: state.facility,
      asnId: state.asn.asnId,
      asnLineId,
      mode,
      quantity,
    });
  }

  async function receiveFullLine() {
    const line = getSelectedLine();
    if (!line) return;
    const remaining = remainingQty(line);
    if (remaining <= 0) {
      setActionStatus("Line " + line.lineNumber + " is already fully received.", "error");
      return;
    }
    setBusy(true, "Receiving line " + line.lineNumber + "…");
    try {
      const result = await callReceiveLine(line.asnLineId, "full");
      if (!result.success) {
        setActionStatus(result.error || "Receive failed", "error");
        return;
      }
      setActionStatus(
        "Received " + result.quantityDisplay + " " + result.displayUom +
          " on line " + line.lineNumber + " (LPN " + result.lpnId + ").",
        "success"
      );
      await fetchAndRenderAsn(state.asn.asnId);
    } catch (e) {
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  function openPartialModal() {
    const line = getSelectedLine();
    if (!line) return;
    const remaining = remainingQty(line);
    if (remaining <= 0) {
      setActionStatus("Line " + line.lineNumber + " is already fully received.", "error");
      return;
    }
    el.partialLineInfo.innerHTML =
      "<strong>Line " + escapeHtml(line.lineNumber) + "</strong> — " +
      escapeHtml(line.itemId) + " " + escapeHtml(line.description) +
      "<br/>Remaining: " + remaining + " " + escapeHtml(line.quantityUomId);
    el.partialQtyInput.value = remaining;
    el.partialQtyInput.max = remaining;
    el.partialQtyInput.min = 0;
    el.partialQtyUom.textContent = line.quantityUomId;
    el.partialQtyHint.textContent = "";
    partialLineModal.show();
  }

  async function confirmPartialLine() {
    const line = getSelectedLine();
    if (!line) {
      partialLineModal.hide();
      return;
    }
    const remaining = remainingQty(line);
    const qty = Number(el.partialQtyInput.value);
    if (!qty || qty <= 0) {
      el.partialQtyHint.textContent = "Enter a quantity greater than 0.";
      return;
    }
    if (qty > remaining) {
      el.partialQtyHint.textContent = "Cannot exceed remaining quantity (" + remaining + ").";
      return;
    }
    partialLineModal.hide();
    setBusy(true, "Receiving line " + line.lineNumber + "…");
    try {
      const result = await callReceiveLine(line.asnLineId, "partial", qty);
      if (!result.success) {
        setActionStatus(result.error || "Receive failed", "error");
        return;
      }
      setActionStatus(
        "Received " + result.quantityDisplay + " " + result.displayUom +
          " on line " + line.lineNumber + " (LPN " + result.lpnId + ").",
        "success"
      );
      await fetchAndRenderAsn(state.asn.asnId);
    } catch (e) {
      setActionStatus(e.message || String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  let allLinesPending = [];

  function openAllLinesModal() {
    if (!state.asn) return;
    allLinesPending = (state.asn.lines || []).filter((l) => remainingQty(l) > 0);
    if (!allLinesPending.length) {
      setActionStatus("No outstanding lines to receive.", "");
      return;
    }
    el.allLinesList.innerHTML = allLinesPending
      .map(
        (l) =>
          "<li>Line " + escapeHtml(l.lineNumber) + " — " + escapeHtml(l.itemId) + " " +
          escapeHtml(l.description) + ": " + remainingQty(l) + " " + escapeHtml(l.quantityUomId) + "</li>"
      )
      .join("");
    allLinesModal.show();
  }

  async function confirmAllLines() {
    allLinesModal.hide();
    const total = allLinesPending.length;
    let succeeded = 0;
    const failures = [];
    for (let i = 0; i < total; i++) {
      const line = allLinesPending[i];
      setBusy(true, "Receiving line " + (i + 1) + " of " + total + "…");
      try {
        const result = await callReceiveLine(line.asnLineId, "full");
        if (result.success) {
          succeeded++;
        } else {
          failures.push("Line " + line.lineNumber + ": " + (result.error || "failed"));
        }
      } catch (e) {
        failures.push("Line " + line.lineNumber + ": " + (e.message || String(e)));
      }
    }
    setBusy(false);
    await fetchAndRenderAsn(state.asn.asnId);
    if (!failures.length) {
      setActionStatus("Received " + fmtCount(succeeded, "line") + ".", "success");
    } else {
      setActionStatus(
        "Received " + succeeded + " of " + total + " lines. Failures: " + failures.join("; "),
        "error"
      );
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

  const partialLineModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("partialLineModal"))
    : null;
  const allLinesModal = window.bootstrap
    ? new window.bootstrap.Modal(document.getElementById("allLinesModal"))
    : null;

  el.fullLineBtn.addEventListener("click", receiveFullLine);
  el.partialLineBtn.addEventListener("click", openPartialModal);
  el.partialLineConfirmBtn.addEventListener("click", confirmPartialLine);
  el.allLinesBtn.addEventListener("click", openAllLinesModal);
  el.allLinesConfirmBtn.addEventListener("click", confirmAllLines);

  if (window.InspectionThemes) {
    // Theme=N hides the picker; Theme=<key> (case-insensitive) pre-selects a theme.
    if (urlParams.theme && urlParams.theme.toUpperCase() === "N") {
      el.themeSelectorBtn.style.display = "none";
    } else if (urlParams.theme) {
      const themes = window.InspectionThemes.themes;
      const themeKey = themes[urlParams.theme]
        ? urlParams.theme
        : themes[urlParams.theme.toLowerCase()]
          ? urlParams.theme.toLowerCase()
          : null;
      if (themeKey) localStorage.setItem("selectedTheme", themeKey);
    }
    const themeModalEl = document.getElementById("themeModal");
    const themeModal = window.bootstrap ? new window.bootstrap.Modal(themeModalEl) : null;
    window.InspectionThemes.wireThemePicker({
      themeSelectorBtn: el.themeSelectorBtn,
      themeModal,
      themeList: el.themeList,
      themeLogo: el.themeLogo,
    });
  }

  // URL boot: Organization/org auto-authenticates (ASN deep-link is applied
  // inside authenticate() once preload completes, see applyUrlAsnBoot()).
  if (urlParams.org) {
    el.org.value = urlParams.org.toUpperCase();
    authenticate(urlParams.org);
  } else {
    el.org.focus();
  }
})();
