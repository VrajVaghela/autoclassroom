const DEFAULT_SERVER_URL = "https://autoclassroom.onrender.com";
let SERVER = DEFAULT_SERVER_URL;
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
        // If nothing stored or stored as legacy local URL, default to cloud server
        if (!res || !res.server_url || res.server_url === "http://127.0.0.1:5000") {
          resolve(DEFAULT_SERVER_URL);
        } else {
          resolve(res.server_url.trim().replace(/\/+$/, ""));
        }
      });
    });
  }
  return DEFAULT_SERVER_URL;
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
  if (offline) {
    if (SERVER && SERVER.startsWith("https://")) {
      return "Can't reach remote server. Render free instances sleep when idle and take ~30–50s to wake up. Please wait a moment and try again.";
    }
    return "Can't reach server. Please run 'python server.py' on your computer or check Backend Server URL in settings.";
  }
  if (/HTTP 404/i.test(error.message)) {
    return "Server returned HTTP 404. If using Render, the backend may be restarting. Try again or check your Backend Server URL in Settings.";
  }
  if (/HTTP 502|HTTP 503|HTTP 504/i.test(error.message)) {
    return "Server is temporarily unavailable (wake-up in progress). Please wait a few seconds and try again.";
  }
  return error.message;
}

/* ---------------------------------------------------------------- main view */

function updateGoogleAuthUI(authInfo) {
  const badge = $("settingsAuthBadge");
  const btn = $("settingsAuthBtn");

  if (!authInfo) return;

  if (authInfo.authenticated) {
    if (badge) {
      badge.textContent = "Connected ✓";
      badge.className = "auth-badge ok";
    }
    if (btn) btn.hidden = true;
  } else {
    if (badge) {
      badge.textContent = authInfo.has_credentials ? "Not Connected" : "Missing Credentials";
      badge.className = "auth-badge err";
    }
    if (btn) {
      btn.hidden = false;
      btn.textContent = "Sign in with Google";
    }
  }
}

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
    if (health.google_auth) {
      updateGoogleAuthUI(health.google_auth);
    }
  } catch (error) {
    line.textContent = serverDownMessage(error);
    $("triggerBtn").disabled = true;
  }
}

async function uploadTokenFile(file) {
  const status = $("settingsStatus");
  show(status, "info", "Uploading and verifying token…", true);
  try {
    const text = await file.text();
    const tokenObj = JSON.parse(text);
    const res = await api("/auth/token", {
      method: "POST",
      body: JSON.stringify({ token: tokenObj }),
    });
    if (res.success) {
      show(status, "ok", "Google Classroom token synced successfully!");
      if (res.google_auth) updateGoogleAuthUI(res.google_auth);
      refreshHeader();
    } else {
      show(status, "err", res.error || "Failed to sync token.");
    }
  } catch (err) {
    show(status, "err", `Token upload error: ${err.message}`);
  }
}

async function signInGoogle() {
  const isSettings = $("settingsView") && !$("settingsView").hidden;
  const status = isSettings ? $("settingsStatus") : $("status");
  const settingsBtn = $("settingsAuthBtn");
  if (settingsBtn) settingsBtn.disabled = true;

  const serverUrl = await getStoredServerUrl();
  const isRemote = serverUrl.startsWith("https://");

  if (isRemote) {
    show(status, "info", "Opening Google sign-in page in a new tab…", true);
    const authStartUrl = `${serverUrl}/auth/google/start`;
    if (typeof chrome !== "undefined" && chrome.tabs && chrome.tabs.create) {
      chrome.tabs.create({ url: authStartUrl });
    } else {
      window.open(authStartUrl, "_blank");
    }

    // Poll auth status for 90 seconds while user completes Google auth in the new tab
    let pollAttempts = 0;
    const pollInterval = setInterval(async () => {
      pollAttempts++;
      if (pollAttempts > 36) {
        clearInterval(pollInterval);
        if (settingsBtn) settingsBtn.disabled = false;
        return;
      }
      try {
        const check = await api("/auth/status");
        if (check && check.authenticated) {
          clearInterval(pollInterval);
          show(status, "ok", "Google Classroom connected successfully!");
          updateGoogleAuthUI(check);
          refreshHeader();
          if (settingsBtn) settingsBtn.disabled = false;
        }
      } catch {}
    }, 2500);
    return;
  }

  show(status, "info", "Opening Google sign-in page in your browser… Please complete the login.", true);

  try {
    const res = await api("/auth/login", { method: "POST" });
    if (res.requires_web_auth && res.auth_url) {
      if (typeof chrome !== "undefined" && chrome.tabs && chrome.tabs.create) {
        chrome.tabs.create({ url: res.auth_url });
      } else {
        window.open(res.auth_url, "_blank");
      }
      show(status, "info", "Sign-in tab opened. Please complete the login, then return here.", true);
    } else if (res.success) {
      show(status, "ok", "Google Classroom connected successfully!");
      if (res.google_auth) updateGoogleAuthUI(res.google_auth);
      refreshHeader();
    } else {
      show(status, "err", res.error || "Failed to sign in with Google Classroom.");
    }
  } catch (error) {
    show(status, "err", serverDownMessage(error));
  } finally {
    if (settingsBtn) settingsBtn.disabled = false;
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
  const isRemote = SERVER.startsWith("https://");
  const rawDir = $("outputDir").value.trim();
  const patch = {
    provider,
    run_code: $("runCode").checked,
    repair_attempts: Number($("repairAttempts").value),
    models: { [provider]: $("modelInput").value.trim() },
  };
  if (!isRemote && rawDir && rawDir !== "Downloads Folder (Browser)") {
    patch.output_dir = rawDir;
  }
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

async function switchServer() {
  const status = $("settingsStatus");
  const urlInput = $("serverUrlInput");
  const target = (urlInput?.value || "").trim().replace(/\/+$/, "") || DEFAULT_SERVER_URL;
  if (urlInput) urlInput.value = target;

  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
    await chrome.storage.local.set({ server_url: target });
  }
  SERVER = target;
  show(status, "info", `Connecting to ${target}…`, true);
  try {
    await loadSettings();
    show(status, "ok", "Connected successfully!");
    refreshHeader();
  } catch (err) {
    show(status, "err", serverDownMessage(err));
  }
}

async function saveSettings() {
  const status = $("settingsStatus");
  const button = $("saveBtn");
  button.disabled = true;
  show(status, "info", "Saving…", true);

  const customServer = ($("serverUrlInput")?.value || "").trim().replace(/\/+$/, "");
  if (customServer) {
    if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
      await chrome.storage.local.set({ server_url: customServer });
    }
    SERVER = customServer;
  }

  try {
    if (!settings) {
      settings = await api("/settings");
    }
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

  const switchBtn = $("switchServerBtn");
  if (switchBtn) switchBtn.addEventListener("click", switchServer);

  const serverInput = $("serverUrlInput");
  if (serverInput) {
    serverInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        switchServer();
      }
    });
  }

  if ($("settingsAuthBtn")) $("settingsAuthBtn").addEventListener("click", signInGoogle);

  const syncBtn = $("syncTokenBtn");
  const tokenFile = $("tokenFileInput");
  if (syncBtn && tokenFile) {
    syncBtn.addEventListener("click", () => tokenFile.click());
    tokenFile.addEventListener("change", (e) => {
      if (e.target.files && e.target.files[0]) {
        uploadTokenFile(e.target.files[0]);
      }
    });
  }

  $("providerSelect").addEventListener("change", renderProviderFields);
  $("runCode").addEventListener("change", renderRunFields);
  $("apiKeyInput").addEventListener("input", (event) => {
    typedKeys[$("providerSelect").value] = event.target.value;
  });

  refreshHeader();
});
