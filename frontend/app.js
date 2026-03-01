const baseUrl =
  (window.APP_CONFIG && window.APP_CONFIG.ORCHESTRATOR_BASE_URL) ||
  `${window.location.protocol}//${window.location.hostname}:3001`;

const state = {
  batchId: null,
  pollTimer: null,
  modelsCatalog: [],
  latestSearch: [],
  pageResultsByPage: new Map(),
  pageModelSelections: new Map(),
  previewScale: 1,
  hasManualModelSelection: false
};

const sourceInput = document.getElementById("source");
const profileInput = document.getElementById("profile");
const promptVersionInput = document.getElementById("promptVersion");
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
const modelList = document.getElementById("modelList");
const modelSelectionHint = document.getElementById("modelSelectionHint");
const searchModelKey = document.getElementById("searchModelKey");
const includeFailed = document.getElementById("includeFailed");
const exportScope = document.getElementById("exportScope");
const previewModal = document.getElementById("previewModal");
const previewCloseButton = document.getElementById("previewCloseButton");
const previewZoomOut = document.getElementById("previewZoomOut");
const previewZoomReset = document.getElementById("previewZoomReset");
const previewZoomIn = document.getElementById("previewZoomIn");
const previewImage = document.getElementById("previewImage");
const previewImageViewport = document.getElementById("previewImageViewport");
const previewBatchId = document.getElementById("previewBatchId");
const previewPageId = document.getElementById("previewPageId");
const previewModelKey = document.getElementById("previewModelKey");
const previewJson = document.getElementById("previewJson");

gatewayUrl.textContent = baseUrl;

function badge(status) {
  const normalized = status || "pending";
  const safeStatus = ["pending", "processing", "done", "failed"].includes(normalized) ? normalized : "pending";
  return `<span class="badge ${safeStatus}">${normalized}</span>`;
}

function setBusy(isBusy) {
  runButton.disabled = isBusy;
  refreshButton.disabled = isBusy;
}

function setPreviewScale(scale) {
  state.previewScale = Math.min(4, Math.max(0.5, scale));
  previewImage.style.transform = `scale(${state.previewScale})`;
}

function closePreviewModal() {
  previewModal.hidden = true;
  previewImage.removeAttribute("src");
  previewImage.alt = "AI 输入图";
  previewJson.textContent = "{}";
  previewBatchId.textContent = "-";
  previewPageId.textContent = "-";
  previewModelKey.textContent = "-";
  setPreviewScale(1);
}

closePreviewModal();

function openPreviewModal({ batch_id, page_id, model_key, record }) {
  if (!batch_id || !page_id) {
    statusError.textContent = "缺少 batch_id 或 page_id，无法预览 AI 输入图";
    return;
  }
  const imageUrl = `${baseUrl}/api/batches/${encodeURIComponent(batch_id)}/pages/${encodeURIComponent(page_id)}/image`;
  previewBatchId.textContent = batch_id;
  previewPageId.textContent = page_id;
  previewModelKey.textContent = model_key || "-";
  previewJson.textContent = JSON.stringify(record || {}, null, 2);
  previewImage.src = imageUrl;
  previewImage.alt = `AI 输入图 ${page_id}`;
  previewModal.hidden = false;
  setPreviewScale(1);
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function selectedModels() {
  return Array.from(document.querySelectorAll(".model-checkbox:checked")).map((input) => input.value);
}

function modelLabel(modelKey) {
  const match = state.modelsCatalog.find((item) => item.key === modelKey);
  return match ? match.label : modelKey;
}

function updateSearchModelOptions(modelKeys) {
  const uniqueKeys = [];
  (modelKeys || []).forEach((key) => {
    const normalized = String(key || "").trim();
    if (normalized && !uniqueKeys.includes(normalized)) {
      uniqueKeys.push(normalized);
    }
  });

  const previousValue = searchModelKey.value || "__all__";
  searchModelKey.innerHTML = `<option value="__all__">全部模型（默认）</option>`;

  uniqueKeys.forEach((key) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = modelLabel(key);
    searchModelKey.appendChild(option);
  });

  searchModelKey.value = uniqueKeys.includes(previousValue) || previousValue === "__all__" ? previousValue : "__all__";
}

function updateModelSelectionHint() {
  const checked = selectedModels();
  modelSelectionHint.textContent = `已选 ${checked.length} / 3`;
  if (!state.batchId) {
    updateSearchModelOptions(state.hasManualModelSelection ? checked : []);
  }
}

function renderModelCatalog(items) {
  state.modelsCatalog = items;
  modelList.innerHTML = items
    .map(
      (item) => `
        <label class="model-option ${item.available ? "" : "unavailable"}">
          <span class="title">
            <input
              class="model-checkbox"
              type="checkbox"
              value="${escapeHtml(item.key)}"
              ${item.key === "mock" ? "checked" : ""}
              ${item.available ? "" : "disabled"}
            />
            <span>${escapeHtml(item.label)}</span>
          </span>
          <div class="meta">
            <div>${escapeHtml(item.provider)} / ${escapeHtml(item.purpose)}</div>
            <div>${item.available ? "Available" : escapeHtml(item.reason || "Unavailable")}</div>
          </div>
        </label>
      `
    )
    .join("");

  modelList.querySelectorAll(".model-checkbox").forEach((input) => {
    input.addEventListener("change", (event) => {
      state.hasManualModelSelection = true;
      const checked = selectedModels();
      if (checked.length > 3) {
        event.target.checked = false;
        statusError.textContent = "最多选择 3 个模型";
      } else {
        statusError.textContent = "";
      }
      updateModelSelectionHint();
    });
  });

  updateModelSelectionHint();
}

async function loadModels() {
  try {
    const data = await getJson(`${baseUrl}/api/models`);
    renderModelCatalog(data.items || []);
  } catch (error) {
    statusError.textContent = `模型列表加载失败：${error.message}`;
  }
}

function renderStatus(data) {
  currentBatch.textContent = `当前批次：${data.batch_id || "无"}`;
  statusError.textContent = data.error ? `${data.error.step}: ${data.error.message}` : "";
  updateSearchModelOptions(data.models || []);
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

function buildPageResultMap(results) {
  const pageMap = new Map();
  (results || []).forEach((pageResult) => {
    const modelMap = new Map();
    (pageResult.models || []).forEach((item) => modelMap.set(item.model_key, item));
    pageMap.set(pageResult.page_id, modelMap);
  });
  state.pageResultsByPage = pageMap;
}

function resultPreview(pageId, modelKey) {
  const modelMap = state.pageResultsByPage.get(pageId);
  if (!modelMap || !modelMap.get(modelKey)) {
    return "{}";
  }
  const modelResult = modelMap.get(modelKey);
  return JSON.stringify(
    {
      model_key: modelResult.model_key,
      run_id: modelResult.run_id,
      status: modelResult.status,
      records: modelResult.records || [],
      issues: modelResult.issues || []
    },
    null,
    2
  );
}

function attachPageModelListeners() {
  pagesContainer.querySelectorAll(".page-model-select").forEach((select) => {
    select.addEventListener("change", (event) => {
      const pageId = event.target.dataset.pageId;
      const modelKey = event.target.value;
      state.pageModelSelections.set(pageId, modelKey);
      const pre = document.getElementById(`result-${pageId}`);
      if (pre) {
        pre.textContent = resultPreview(pageId, modelKey);
      }
    });
  });
}

function renderPages(data) {
  buildPageResultMap(data.results || []);
  const pages = data.pages || [];
  if (!pages.length) {
    pagesContainer.innerHTML = `<p class="hint">暂无页数据。</p>`;
    return;
  }

  pagesContainer.innerHTML = pages
    .map((page) => {
      const modelMap = state.pageResultsByPage.get(page.page_id) || new Map();
      const modelKeys = Array.from(modelMap.keys());
      const selectedModel = state.pageModelSelections.get(page.page_id) || modelKeys[0] || "";
      if (selectedModel) {
        state.pageModelSelections.set(page.page_id, selectedModel);
      }
      const modelOptions = modelKeys.length
        ? modelKeys
            .map((modelKey) => `<option value="${escapeHtml(modelKey)}" ${modelKey === selectedModel ? "selected" : ""}>${escapeHtml(modelKey)}</option>`)
            .join("")
        : `<option value="">暂无模型结果</option>`;

      return `
        <div class="page-card">
          ${page.thumb_relpath ? `<a href="${baseUrl}/api/assets/image/${page.batch_id}/${page.page_id}" target="_blank" rel="noreferrer">
            <img src="${baseUrl}/api/assets/thumb/${page.batch_id}/${page.page_id}" alt="${escapeHtml(page.page_id)}" />
          </a>` : `<div style="height:180px; display:flex; align-items:center; justify-content:center; background:#eef2f8;">无缩略图</div>`}
          <div class="body">
            <div style="display:flex; justify-content:space-between; gap:8px; align-items:center;">
              <strong>${escapeHtml(page.page_id)}</strong>
              ${badge(page.status)}
            </div>
            <div class="hint" style="margin: 8px 0 10px;">doc=${escapeHtml(page.doc_id)} file=${escapeHtml(page.file_id)}</div>
            <div class="result-toolbar">
              <span class="hint">模型结果</span>
              <select class="page-model-select" data-page-id="${escapeHtml(page.page_id)}">${modelOptions}</select>
            </div>
            <pre id="result-${escapeHtml(page.page_id)}">${escapeHtml(resultPreview(page.page_id, selectedModel))}</pre>
          </div>
        </div>
      `;
    })
    .join("");

  attachPageModelListeners();
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
  const promptVersion = promptVersionInput.value;
  const models = selectedModels();

  try {
    if (!models.length) {
      throw new Error("至少选择 1 个模型");
    }
    if (models.length > 3) {
      throw new Error("最多选择 3 个模型");
    }

    let data;
    if (source === "upload") {
      const selectedFiles = Array.from(uploadFilesInput.files || []);
      if (!selectedFiles.length) {
        throw new Error("upload 模式需要先选择文件");
      }
      const formData = new FormData();
      formData.append("profile", profile);
      formData.append("prompt_version", promptVersion);
      formData.append("models", JSON.stringify(models));
      selectedFiles.forEach((file) => formData.append("files", file));
      data = await getJson(`${baseUrl}/api/run/upload`, {
        method: "POST",
        body: formData
      });
    } else {
      data = await getJson(`${baseUrl}/api/run/full`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source,
          profile,
          models,
          prompt_version: promptVersion
        })
      });
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
    batch_id: document.getElementById("searchBatchId").value.trim(),
    model_key: document.getElementById("searchModelKey").value
  };
  if (includeFailed.checked) {
    filters.include_failed = true;
  }
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
    searchResults.innerHTML = `<tr><td colspan="11" class="hint">无结果</td></tr>`;
    return;
  }
  searchResults.innerHTML = items
    .map(
      (item, index) => `
        <tr>
          <td>
            <button
              class="preview-thumb-button search-preview-trigger"
              type="button"
              data-index="${index}"
              data-page-id="${escapeHtml(item.page_id || "")}"
              data-batch-id="${escapeHtml(item.batch_id || "")}"
              data-model-key="${escapeHtml(item.model_key || "")}"
              ${item.batch_id && item.page_id ? "" : "disabled title=\"缺少 batch_id/page_id\""}
            >
              <img class="thumb-mini" src="${baseUrl}${item.thumb_url}" alt="${escapeHtml(item.page_id)}" />
            </button>
            <button
              class="secondary preview-action search-preview-trigger"
              type="button"
              data-index="${index}"
              data-page-id="${escapeHtml(item.page_id || "")}"
              data-batch-id="${escapeHtml(item.batch_id || "")}"
              data-model-key="${escapeHtml(item.model_key || "")}"
              ${item.batch_id && item.page_id ? "" : "disabled title=\"缺少 batch_id/page_id\""}
            >
              预览
            </button>
          </td>
          <td>${escapeHtml(item.invoice_no || "")}</td>
          <td>${escapeHtml(item.invoice_date || "")}</td>
          <td>${escapeHtml(item.model_key || "")}</td>
          <td>${escapeHtml(item.result_status || "done")}</td>
          <td>${escapeHtml([item.error_reason, item.error_message].filter(Boolean).join(": "))}</td>
          <td>${escapeHtml(item.buyer_name || "")}</td>
          <td>${escapeHtml(item.seller_name || "")}</td>
          <td>${escapeHtml(item.amount || "")}</td>
          <td>${escapeHtml(item.tax || "")}</td>
          <td>${escapeHtml(item.total || "")}</td>
        </tr>
      `
    )
    .join("");

  searchResults.querySelectorAll(".search-preview-trigger").forEach((button) => {
    button.addEventListener("click", () => {
      const record = state.latestSearch[Number(button.dataset.index || "-1")] || {};
      openPreviewModal({
        batch_id: button.dataset.batchId,
        page_id: button.dataset.pageId,
        model_key: button.dataset.modelKey,
        record
      });
    });
  });
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
    const filters = {
      ...collectFilters(),
      export_scope: exportScope.value
    };
    const data = await getJson(`${baseUrl}/api/exports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filters)
    });
    const targetUrl = kind === "csv" ? `${baseUrl}${data.csv_url}` : `${baseUrl}${data.zip_url}`;
    window.open(targetUrl, "_blank", "noopener,noreferrer");
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
previewCloseButton.addEventListener("click", closePreviewModal);
previewModal.addEventListener("click", (event) => {
  if (event.target === previewModal) {
    closePreviewModal();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !previewModal.hidden) {
    closePreviewModal();
  }
});
previewZoomIn.addEventListener("click", () => setPreviewScale(state.previewScale + 0.2));
previewZoomOut.addEventListener("click", () => setPreviewScale(state.previewScale - 0.2));
previewZoomReset.addEventListener("click", () => setPreviewScale(1));
previewImageViewport.addEventListener(
  "wheel",
  (event) => {
    if (previewModal.hidden) {
      return;
    }
    event.preventDefault();
    setPreviewScale(state.previewScale + (event.deltaY < 0 ? 0.1 : -0.1));
  },
  { passive: false }
);

renderStatus({ steps: [] });
renderPages({ pages: [], results: [] });
renderSearch([]);
loadModels();
