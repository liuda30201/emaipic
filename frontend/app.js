const baseUrl = (window.APP_CONFIG && window.APP_CONFIG.ORCHESTRATOR_BASE_URL) || "http://localhost:3001";

const state = {
  batchId: null,
  pollTimer: null,
  latestSearch: []
};

const sourceInput = document.getElementById("source");
const profileInput = document.getElementById("profile");
const modeInput = document.getElementById("mode");
const uploadFilesInput = document.getElementById("uploadFiles");
const runButton = document.getElementById("runButton");
const refreshButton = document.getElementById("refreshButton");
const gatewayUrl = document.getElementById("gatewayUrl");
const currentBatch = document.getElementById("currentBatch");
const statusList = document.getElementById("statusList");
const statusError = document.getElementById("statusError");
const pagesContainer = document.getElementById("pagesContainer");
const searchResults = document.getElementById("searchResults");
const searchMeta = document.getElementById("searchMeta");

gatewayUrl.textContent = baseUrl;

function badge(status) {
  const normalized = status || "pending";
  return `<span class="badge ${normalized}">${normalized}</span>`;
}

function setBusy(isBusy) {
  runButton.disabled = isBusy;
  refreshButton.disabled = isBusy;
}

function getJson(url, options) {
  return fetch(url, options).then(async (response) => {
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || !payload.success) {
      const message = payload && payload.error ? payload.error.message : `HTTP ${response.status}`;
      throw new Error(message);
    }
    return payload.data;
  });
}

function renderStatus(data) {
  currentBatch.textContent = `当前批次：${data.batch_id || "无"}`;
  statusError.textContent = data.error ? `${data.error.step}: ${data.error.message}` : "";
  statusList.innerHTML = (data.steps || [])
    .map(
      (step) => `
        <div class="status-item">
          <div>
            <strong>${step.name}</strong>
            <div class="hint">${step.detail || ""}</div>
          </div>
          ${badge(step.status)}
        </div>
      `
    )
    .join("");
}

function findResultMap(results) {
  const map = new Map();
  (results || []).forEach((item) => map.set(item.page_id, item));
  return map;
}

function renderPages(data) {
  const resultMap = findResultMap(data.results || []);
  const pages = data.pages || [];
  if (!pages.length) {
    pagesContainer.innerHTML = `<p class="hint">暂无页数据。</p>`;
    return;
  }
  pagesContainer.innerHTML = pages
    .map((page) => {
      const result = resultMap.get(page.page_id);
      const normalized = result ? result.normalized : {};
      return `
        <div class="page-card">
          <a href="${baseUrl}/api/assets/image/${page.batch_id}/${page.page_id}" target="_blank" rel="noreferrer">
            <img src="${baseUrl}/api/assets/thumb/${page.batch_id}/${page.page_id}" alt="${page.page_id}" />
          </a>
          <div class="body">
            <div style="display:flex; justify-content:space-between; gap:8px; align-items:center;">
              <strong>${page.page_id}</strong>
              ${badge(page.status)}
            </div>
            <div class="hint" style="margin: 8px 0 10px;">doc=${page.doc_id} file=${page.file_id}</div>
            <pre>${escapeHtml(JSON.stringify(normalized || {}, null, 2))}</pre>
          </div>
        </div>
      `;
    })
    .join("");
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    if (state.batchId) {
      loadBatchStatus(state.batchId, true);
    }
  }, 2000);
}

async function loadBatchStatus(batchId, silent) {
  try {
    const data = await getJson(`${baseUrl}/api/batches/${encodeURIComponent(batchId)}/status`);
    state.batchId = batchId;
    renderStatus(data);
    renderPages(data);
    if (data.status === "done" || data.status === "failed") {
      stopPolling();
    }
  } catch (error) {
    if (!silent) {
      statusError.textContent = error.message;
    }
  }
}

async function runPipeline() {
  setBusy(true);
  statusError.textContent = "";
  const source = sourceInput.value;
  const profile = profileInput.value;
  const mode = modeInput.value;

  try {
    let data;
    if (source === "upload") {
      const selected = Array.from(uploadFilesInput.files || []);
      if (!selected.length) {
        throw new Error("upload 模式需要先选择文件");
      }
      const formData = new FormData();
      selected.forEach((file) => formData.append("files", file));
      data = await getJson(`${baseUrl}/api/run/upload?profile=${encodeURIComponent(profile)}&mode=${encodeURIComponent(mode)}`, {
        method: "POST",
        body: formData
      });
    } else {
      data = await getJson(
        `${baseUrl}/api/run/full?source=${encodeURIComponent(source)}&profile=${encodeURIComponent(profile)}&mode=${encodeURIComponent(mode)}`,
        { method: "POST" }
      );
    }
    renderStatus(data);
    renderPages(data);
    state.batchId = data.batch_id;
    currentBatch.textContent = `当前批次：${state.batchId}`;
    document.getElementById("searchBatchId").value = state.batchId;
    startPolling();
  } catch (error) {
    statusError.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

function collectFilters() {
  const filters = {
    start_date: document.getElementById("startDate").value,
    end_date: document.getElementById("endDate").value,
    invoice_no: document.getElementById("invoiceNo").value.trim(),
    buyer: document.getElementById("buyer").value.trim(),
    seller: document.getElementById("seller").value.trim(),
    batch_id: document.getElementById("searchBatchId").value.trim()
  };
  Object.keys(filters).forEach((key) => {
    if (!filters[key]) {
      delete filters[key];
    }
  });
  return filters;
}

function renderSearch(items) {
  state.latestSearch = items;
  searchMeta.textContent = `命中 ${items.length} 条`;
  if (!items.length) {
    searchResults.innerHTML = `<tr><td colspan="8" class="hint">无结果</td></tr>`;
    return;
  }
  searchResults.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td>
            <a href="${baseUrl}${item.image_url}" target="_blank" rel="noreferrer">
              <img class="thumb-mini" src="${baseUrl}${item.thumb_url}" alt="${item.page_id}" />
            </a>
          </td>
          <td>${item.invoice_no || ""}</td>
          <td>${item.invoice_date || ""}</td>
          <td>${item.buyer_name || ""}</td>
          <td>${item.seller_name || ""}</td>
          <td>${item.amount || ""}</td>
          <td>${item.tax || ""}</td>
          <td>${item.total || ""}</td>
        </tr>
      `
    )
    .join("");
}

async function searchInvoices() {
  try {
    const filters = collectFilters();
    const url = new URL(`${baseUrl}/api/invoices/search`);
    Object.entries(filters).forEach(([key, value]) => url.searchParams.set(key, value));
    const data = await getJson(url.toString());
    renderSearch(data.items || []);
  } catch (error) {
    searchMeta.textContent = `搜索失败：${error.message}`;
  }
}

async function createExport(kind) {
  try {
    const filters = collectFilters();
    const data = await getJson(`${baseUrl}/api/exports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filters)
    });
    window.open(kind === "csv" ? `${baseUrl}${data.csv_url}` : `${baseUrl}${data.zip_url}`, "_blank", "noopener,noreferrer");
  } catch (error) {
    searchMeta.textContent = `导出失败：${error.message}`;
  }
}

runButton.addEventListener("click", runPipeline);
refreshButton.addEventListener("click", () => {
  if (state.batchId) {
    loadBatchStatus(state.batchId, false);
  }
});
document.getElementById("searchButton").addEventListener("click", searchInvoices);
document.getElementById("exportCsvButton").addEventListener("click", () => createExport("csv"));
document.getElementById("exportZipButton").addEventListener("click", () => createExport("zip"));

renderStatus({ steps: [] });
renderPages({ pages: [] });
renderSearch([]);
