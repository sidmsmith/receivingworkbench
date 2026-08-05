/* Receiving Workbench — full-screen UI */
(function () {
  const state = {
    org: "",
    token: "",
    facility: "",
    validAsnIds: new Set(),
    asn: null, // last loaded ASN payload from /api/load_asn
    selectedLineNumber: null,
    stagingLocations: [], // [{locationId, displayLocation}]
    stagingLocationId: "", // resolved LocationId, "" if none/invalid
    stagingLocationValid: true, // blank counts as valid (optional field)
    transactions: [], // [{transactionId, strategyId, description}]
    selectedTransactionId: "",
    selectedStrategyId: "",
  };

  const STAGING_LOCATION_STORAGE_KEY = "rw_staging_location";
  const TRANSACTION_STORAGE_KEY = "rw_transaction_id";
  // The value this app used to hardcode before the Transaction ID picker
  // existed — still the default selection when an org's list contains it.
  const DEFAULT_TRANSACTION_ID = "Receiving";

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
    lpnCount: document.getElementById("lpnCount"),
    lpnBody: document.getElementById("lpnBody"),
    partialLineBtn: document.getElementById("partialLineBtn"),
    fullLineBtn: document.getElementById("fullLineBtn"),
    allLinesBtn: document.getElementById("allLinesBtn"),
    transactionIdSelect: document.getElementById("transactionIdSelect"),
    transactionIdHint: document.getElementById("transactionIdHint"),
    stagingLocationInput: document.getElementById("stagingLocationInput"),
    stagingLocationSearchBtn: document.getElementById("stagingLocationSearchBtn"),
    stagingLocationDropdown: document.getElementById("stagingLocationDropdown"),
    stagingLocationList: document.getElementById("stagingLocationList"),
    stagingLocationHint: document.getElementById("stagingLocationHint"),
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

  /** Case-insensitive query params: org/organization, theme, asn/asnid/asn_id/asn-id,
   *  staginglocation/staging_location/staging-location,
   *  transactionid/transaction_id/transaction-id */
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
      stagingLocation: String(
        ci.staginglocation || ci.staging_location || ci["staging-location"] || ""
      ).trim(),
      transactionId: String(
        ci.transactionid || ci.transaction_id || ci["transaction-id"] || ""
      ).trim(),
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
      await preloadTransactions();
      await preloadStagingLocations();
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
          <td class="col-qty-wide">${escapeHtml(line.shippedQuantityLabel)}</td>
          <td class="col-qty-wide">${escapeHtml(line.receivedQuantityLabel)}</td>
        </tr>`
      )
      .join("");
  }

  function renderLpns(lpns) {
    el.lpnCount.textContent = (lpns || []).length;
    el.lpnBody.innerHTML = (lpns || [])
      .map(
        (lpn) => `
        <tr>
          <td>${escapeHtml(lpn.lpnId)}</td>
          <td>${escapeHtml(lpn.statusLabel)}</td>
          <td>${escapeHtml(lpn.location)}</td>
          <td>${escapeHtml(lpn.itemId)}</td>
          <td>${escapeHtml(lpn.description)}</td>
          <td class="col-qty-wide">${escapeHtml(lpn.qty)}</td>
          <td>${escapeHtml(lpn.uom)}</td>
          <td>${escapeHtml(lpn.conditionCode)}</td>
        </tr>`
      )
      .join("");
  }

  // --- Transaction ID ---

  function populateTransactionSelect() {
    const options = ['<option value="">-- Select --</option>'].concat(
      state.transactions.map(
        (t) => `<option value="${escapeAttr(t.transactionId)}">${escapeHtml(t.transactionId)}</option>`
      )
    );
    el.transactionIdSelect.innerHTML = options.join("");
  }

  function evaluateTransactionSelection() {
    const value = el.transactionIdSelect.value;
    const match = state.transactions.find((t) => t.transactionId === value);
    state.selectedTransactionId = match ? match.transactionId : "";
    state.selectedStrategyId = match ? match.strategyId : "";
    const valid = !!match;
    el.transactionIdSelect.classList.toggle("invalid", !valid);
    el.transactionIdHint.textContent = valid ? "" : "Required.";
    if (valid) {
      localStorage.setItem(TRANSACTION_STORAGE_KEY, match.transactionId);
    }
    updateLineActionButtons();
  }

  async function preloadTransactions() {
    try {
      const data = await api("preload_transactions", {
        org: state.org,
        token: state.token,
        location: state.facility,
      });
      if (data.success) {
        state.transactions = data.entries || [];
      }
    } catch (e) {
      // Non-fatal here — evaluateTransactionSelection() will still correctly
      // leave the field blank/required if nothing loaded.
    }
    populateTransactionSelect();
    applyTransactionIdBoot();
  }

  function applyTransactionIdBoot() {
    const known = new Set(state.transactions.map((t) => t.transactionId));
    let value = "";
    if (urlParams.transactionId && known.has(urlParams.transactionId)) {
      value = urlParams.transactionId;
    } else {
      const saved = localStorage.getItem(TRANSACTION_STORAGE_KEY);
      if (saved && known.has(saved)) {
        value = saved;
      } else if (known.has(DEFAULT_TRANSACTION_ID)) {
        value = DEFAULT_TRANSACTION_ID;
      }
    }
    el.transactionIdSelect.value = value;
    evaluateTransactionSelection();
  }

  // --- Staging location ---

  function resolveStagingLocationText(text) {
    const t = (text || "").trim();
    if (!t) return { valid: true, locationId: "" };
    const lower = t.toLowerCase();
    const match = state.stagingLocations.find(
      (l) => l.locationId.toLowerCase() === lower || l.displayLocation.toLowerCase() === lower
    );
    return match ? { valid: true, locationId: match.locationId } : { valid: false, locationId: "" };
  }

  function evaluateStagingLocation() {
    const raw = el.stagingLocationInput.value;
    const result = resolveStagingLocationText(raw);
    state.stagingLocationId = result.locationId;
    state.stagingLocationValid = result.valid;
    el.stagingLocationInput.classList.toggle("invalid", !result.valid);
    el.stagingLocationHint.textContent = result.valid ? "" : "Staging location not found.";
    if (result.valid) {
      if (raw.trim()) {
        localStorage.setItem(
          STAGING_LOCATION_STORAGE_KEY,
          JSON.stringify({ text: raw.trim(), locationId: result.locationId })
        );
      } else {
        localStorage.removeItem(STAGING_LOCATION_STORAGE_KEY);
      }
    }
    updateLineActionButtons();
  }

  function renderStagingLocationList(filterText) {
    const filter = (filterText || "").trim().toLowerCase();
    const matches = state.stagingLocations.filter(
      (l) =>
        !filter ||
        l.displayLocation.toLowerCase().includes(filter) ||
        l.locationId.toLowerCase().includes(filter)
    );
    const shown = matches.slice(0, 100);
    if (!shown.length) {
      el.stagingLocationList.innerHTML = '<li class="empty">No matching locations</li>';
      return;
    }
    el.stagingLocationList.innerHTML = shown
      .map((l) => `<li data-display="${escapeAttr(l.displayLocation)}">${escapeHtml(l.displayLocation)}</li>`)
      .join("");
    if (matches.length > shown.length) {
      el.stagingLocationList.innerHTML += `<li class="empty">${matches.length - shown.length} more — keep typing to narrow</li>`;
    }
  }

  function openStagingLocationDropdown() {
    renderStagingLocationList(el.stagingLocationInput.value);
    el.stagingLocationDropdown.classList.add("visible");
  }

  function closeStagingLocationDropdown() {
    el.stagingLocationDropdown.classList.remove("visible");
  }

  async function preloadStagingLocations() {
    try {
      const data = await api("preload_staging_locations", {
        org: state.org,
        token: state.token,
        location: state.facility,
      });
      if (data.success) {
        state.stagingLocations = data.entries || [];
      }
    } catch (e) {
      // Non-fatal — staging location is optional; leave the field usable-but-empty.
    }
    applyStagingLocationBoot();
  }

  function applyStagingLocationBoot() {
    let text = "";
    if (urlParams.stagingLocation) {
      text = urlParams.stagingLocation;
    } else {
      try {
        const saved = JSON.parse(localStorage.getItem(STAGING_LOCATION_STORAGE_KEY) || "null");
        if (saved && saved.text) text = saved.text;
      } catch (e) {
        // ignore malformed storage
      }
    }
    el.stagingLocationInput.value = text;
    evaluateStagingLocation();
  }

  function updateLineActionButtons() {
    const hasSelection = state.selectedLineNumber !== null;
    const stagingOk = state.stagingLocationValid;
    const transactionOk = !!state.selectedTransactionId;
    el.partialLineBtn.disabled = !hasSelection || !stagingOk || !transactionOk;
    el.fullLineBtn.disabled = !hasSelection || !stagingOk || !transactionOk;
    el.allLinesBtn.disabled = !stagingOk || !transactionOk;
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
    renderLpns(data.lpns);
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
      stagingLocationId: state.stagingLocationId || "",
      transactionId: state.selectedTransactionId || "",
      receivingStrategy: state.selectedStrategyId || "",
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
      "<br/>Remaining: " + escapeHtml(line.remainingQuantityLabel);
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
          escapeHtml(l.description) + ": " + escapeHtml(l.remainingQuantityLabel) + "</li>"
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

  el.transactionIdSelect.addEventListener("change", evaluateTransactionSelection);

  el.stagingLocationInput.addEventListener("input", () => {
    evaluateStagingLocation();
    if (el.stagingLocationDropdown.classList.contains("visible")) {
      renderStagingLocationList(el.stagingLocationInput.value);
    }
  });
  el.stagingLocationSearchBtn.addEventListener("click", () => {
    if (el.stagingLocationDropdown.classList.contains("visible")) {
      closeStagingLocationDropdown();
    } else {
      openStagingLocationDropdown();
    }
  });
  el.stagingLocationList.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-display]");
    if (!li) return;
    el.stagingLocationInput.value = li.dataset.display;
    evaluateStagingLocation();
    closeStagingLocationDropdown();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".staging-location-input-row")) closeStagingLocationDropdown();
  });

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
