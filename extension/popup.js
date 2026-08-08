let SERVER = "https://autoclassroom.onrender.com";
const CLIENT_HEADER = { "X-AutoClassroom-Client": "extension" };

const $ = (id) => document.getElementById(id);
let settings = null;
const typedKeys = {};

function show(el, kind, text, busy = false) {
  el.className = `status ${kind}`;
  el.textContent = "";
  if (busy) {
    const spinner = document.createElement("span");
    spinner.className = "spin";
    el.appendChild(spinner);
  }
  el.appendChild(document.createTextNode(text));
}

function clear(el) {
  el.className = "status";
  el.textContent = "";
}

async function getStoredServerUrl() {
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
    return new Promise((resolve) => {
      chrome.storage.local.get("server_url", (res) => {
        resolve(res && res.server_url ? res.server_url.trim().replace(/\/+$/, "") : "https://autoclassroom.onrender.com");
      });
    });
  }
  return "https://autoclassroom.onrender.com";
}

async function api(path, options = {}) {
  SERVER = await getStoredServerUrl();
  const response = await fetch(SERVER + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...CLIENT_HEADER, ...(options.headers || {}) },
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    throw new Error(`Server returned a non-JSON response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(body?.error || `HTTP ${response.status}`);
  }
  return body;
}

function serverDownMessage(error) {
  const offline = error instanceof TypeError || /Failed to fetch/i.test(error.message);
  return offline ? "Can't reach the server. Is server.py running?" : error.message;
}

/* ---------------------------------------------------------------- main view */

async function refreshHeader() {
  const line = $("providerLine");
  try {
    const health = await api("/health");
    const model = health.model || "no model set";
    line.textContent = health.has_key
      ? `${health.provider_label} · ${model}`
      : `${health.provider_label} · no API key set`;
    $("triggerBtn").disabled = !health.has_key;
    if (!health.has_key) {
      show($("status"), "err", "Add an API key in settings to get started.");
    }
  } catch (error) {
    line.textContent = serverDownMessage(error);
    $("triggerBtn").disabled = true;
  }
}

function currentTab() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => resolve(tabs[0]));
  });
}

async function downloadFileFromApi(dirPath, filename) {
  const serverUrl = await getStoredServerUrl();
  const url = `${serverUrl}/download_file?dir=${encodeURIComponent(dirPath)}&filename=${encodeURIComponent(filename)}`;
  const response = await fetch(url, { headers: CLIENT_HEADER });
  if (!response.ok) throw new Error(`Download failed (HTTP ${response.status})`);
  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  
  if (typeof chrome !== "undefined" && chrome.downloads && chrome.downloads.download) {
    chrome.downloads.download({
      url: blobUrl,
      filename: filename,
      saveAs: false,
    });
  } else {
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

async function downloadZipFromApi(dirPath, zipName) {
  const serverUrl = await getStoredServerUrl();
  const url = `${serverUrl}/download_zip?dir=${encodeURIComponent(dirPath)}`;
  const response = await fetch(url, { headers: CLIENT_HEADER });
  if (!response.ok) throw new Error(`Zip download failed (HTTP ${response.status})`);
  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);
  
  if (typeof chrome !== "undefined" && chrome.downloads && chrome.downloads.download) {
    chrome.downloads.download({
      url: blobUrl,
      filename: zipName || "solution.zip",
      saveAs: false,
    });
  } else {
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = zipName || "solution.zip";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

async function runAssignment() {
  const status = $("status");
  const files = $("fileList");
  const button = $("triggerBtn");

  files.hidden = true;
  files.textContent = "";

  const tab = await currentTab();
  const match = (tab?.url || "").match(/\/c\/([^/]+)\/(?:a|m)\/([^/]+)/);
  if (!match) {
    show(status, "err", "Open a Google Classroom assignment page first.");
    return;
  }

  button.disabled = true;
  show(status, "info", "Reading the assignment and generating your solution…", true);

  try {
    const data = await api("/process_assignment", {
      method: "POST",
      body: JSON.stringify({ courseId: match[1], courseWorkId: match[2] }),
    });

    const notes = (data.notes || []).join(" ");
    show(status, "ok", `Solution generated! Downloading files to your machine… ${notes}`);

    const fileList = data.files || [];
    for (const name of fileList) {
      const item = document.createElement("li");
      item.className = "file-item";

      const nameSpan = document.createElement("span");
      nameSpan.textContent = name;
      item.appendChild(nameSpan);

      const dlBtn = document.createElement("button");
      dlBtn.className = "dl-btn";
      dlBtn.textContent = "⬇️ Save";
      dlBtn.onclick = () => downloadFileFromApi(data.dir, name);
      item.appendChild(dlBtn);

      files.appendChild(item);

      // Auto-trigger browser download
      try {
        downloadFileFromApi(data.dir, name);
      } catch (err) {
        console.error("Auto-download failed:", err);
      }
    }

    if (fileList.length > 1) {
      const zipItem = document.createElement("li");
      zipItem.className = "file-item";
      zipItem.style.border = "none";
      zipItem.style.marginTop = "6px";

      const zipBtn = document.createElement("button");
      zipBtn.className = "primary";
      zipBtn.style.padding = "4px 10px";
      zipBtn.style.fontSize = "11px";
      zipBtn.textContent = "📦 Download All (.zip)";
      zipBtn.onclick = () => downloadZipFromApi(data.dir, `${data.title || "solution"}.zip`);
      zipItem.appendChild(zipBtn);
      files.appendChild(zipItem);
    }

    files.hidden = !fileList.length;
  } catch (error) {
    show(status, "err", serverDownMessage(error));
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------ settings view */

function providerEntry(key) {
  return (settings?.providers || []).find((p) => p.key === key);
}

function renderProviderFields() {
  const key = $("providerSelect").value;
  const entry = providerEntry(key);
  if (!entry) return;

  $("modelInput").value = entry.model || "";
  $("modelInput").placeholder = entry.default_model || "model name";
  $("modelHint").textContent = entry.default_model
    ? `Leave blank to use ${entry.default_model}.`
    : "Required for this provider.";

  $("baseUrlField").hidden = key !== "custom";

  const keyInput = $("apiKeyInput");
  keyInput.value = typedKeys[key] || "";
  if (typedKeys[key]) {
    keyInput.placeholder = "";
    $("keyHint").textContent = "Unsaved key — press Save to store it.";
  } else if (entry.key_from_env) {
    keyInput.placeholder = `Using ${entry.env_var} from .env`;
    $("keyHint").textContent = "Typing here overrides the environment variable.";
  } else if (entry.has_key) {
    keyInput.placeholder = entry.masked_key;
    $("keyHint").textContent = "A key is saved. Type a new one to replace it.";
  } else {
    keyInput.placeholder = "Paste your API key";
    $("keyHint").textContent = `Stored locally in config.json (or set ${entry.env_var}).`;
  }
}

// Repairs need a program to have failed in front of us, so the control only
// means anything while local execution is on.
function renderRunFields() {
  $("repairField").hidden = !$("runCode").checked;
}

async function loadSettings() {
  const status = $("settingsStatus");
  if ($("serverUrlInput")) {
    const currentUrl = await getStoredServerUrl();
    $("serverUrlInput").value = currentUrl;
  }
  try {
    settings = await api("/settings");
  } catch (error) {
    show(status, "err", serverDownMessage(error));
    return;
  }

  const isRemote = SERVER.startsWith("https://");
  if (isRemote) {
    $("outputDir").value = "Downloads Folder (Browser)";
    $("outputDir").disabled = true;
    $("browseBtn").disabled = true;
  } else {
    $("outputDir").value = settings.output_dir || "";
    $("outputDir").disabled = false;
    $("browseBtn").disabled = false;
  }
  $("baseUrlInput").value = settings.custom_base_url || "";
  $("runCode").checked = Boolean(settings.run_code);
  $("repairAttempts").value = settings.repair_attempts ?? 2;
  renderRunFields();

  const select = $("providerSelect");
  select.textContent = "";
  for (const provider of settings.providers) {
    const option = document.createElement("option");
    option.value = provider.key;
    option.textContent = provider.has_key ? `${provider.label} ✓` : provider.label;
    select.appendChild(option);
  }
  select.value = settings.provider;
  renderProviderFields();
  clear(status);
}

function collectPatch() {
  const provider = $("providerSelect").value;
  const patch = {
    output_dir: $("outputDir").value.trim(),
    provider,
    run_code: $("runCode").checked,
    repair_attempts: Number($("repairAttempts").value),
    models: { [provider]: $("modelInput").value.trim() },
  };
  if (provider === "custom") {
    patch.custom_base_url = $("baseUrlInput").value.trim();
  }
  // Only send keys the user actually typed, so a blank field never wipes a
  // saved key.
  const keys = {};
  for (const [key, value] of Object.entries(typedKeys)) {
    if (value.trim()) keys[key] = value.trim();
  }
  if (Object.keys(keys).length) patch.api_keys = keys;
  return patch;
}

async function saveSettings() {
  const status = $("settingsStatus");
  const button = $("saveBtn");
  button.disabled = true;
  show(status, "info", "Saving…", true);

  if ($("serverUrlInput") && typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
    const customServer = $("serverUrlInput").value.trim();
    if (customServer) {
      await chrome.storage.local.set({ server_url: customServer });
    }
  }

  try {
    const data = await api("/settings", {
      method: "POST",
      body: JSON.stringify(collectPatch()),
    });
    settings = data.settings;
    for (const key of Object.keys(typedKeys)) delete typedKeys[key];
    await loadSettings();
    show(status, "ok", "Settings saved.");
    refreshHeader();
  } catch (error) {
    show(status, "err", serverDownMessage(error));
  } finally {
    button.disabled = false;
  }
}

async function browseFolder() {
  const status = $("settingsStatus");
  const button = $("browseBtn");
  button.disabled = true;
  show(status, "info", "Waiting for the folder dialog…", true);

  try {
    const data = await api("/browse_folder", { method: "POST", body: "{}" });
    if (data.path) {
      $("outputDir").value = data.path;
      show(status, "info", "Folder chosen. Press Save to apply.");
    } else {
      show(status, "info", data.error || "No folder chosen.");
    }
  } catch (error) {
    show(status, "err", serverDownMessage(error));
  } finally {
    button.disabled = false;
  }
}

async function testConnection() {
  const status = $("settingsStatus");
  const button = $("testBtn");
  const provider = $("providerSelect").value;

  button.disabled = true;
  show(status, "info", "Testing…", true);

  try {
    // Save first so the test uses exactly what's in the form.
    await api("/settings", { method: "POST", body: JSON.stringify(collectPatch()) });
    for (const key of Object.keys(typedKeys)) delete typedKeys[key];

    const data = await api("/test_provider", {
      method: "POST",
      body: JSON.stringify({ provider }),
    });
    if (data.success) {
      show(status, "ok", `Connected to ${data.model}. Replied: ${data.reply}`);
    } else {
      show(status, "err", data.error || "The provider rejected the request.");
    }
    await loadSettings();
    refreshHeader();
  } catch (error) {
    show(status, "err", serverDownMessage(error));
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------- wiring */

function openSettings() {
  $("mainView").hidden = true;
  $("settingsView").hidden = false;
  loadSettings();
}

function closeSettings() {
  $("settingsView").hidden = true;
  $("mainView").hidden = false;
  clear($("status"));
  refreshHeader();
}

document.addEventListener("DOMContentLoaded", () => {
  $("triggerBtn").addEventListener("click", runAssignment);
  $("openSettings").addEventListener("click", openSettings);
  $("backBtn").addEventListener("click", closeSettings);
  $("saveBtn").addEventListener("click", saveSettings);
  $("browseBtn").addEventListener("click", browseFolder);
  $("testBtn").addEventListener("click", testConnection);

  $("providerSelect").addEventListener("change", renderProviderFields);
  $("runCode").addEventListener("change", renderRunFields);
  $("apiKeyInput").addEventListener("input", (event) => {
    typedKeys[$("providerSelect").value] = event.target.value;
  });

  refreshHeader();
});
