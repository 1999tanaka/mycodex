/* mycodex ブラウザ版: 土台 (Pyodide / フォルダ同期 / Python 呼び出し) */
"use strict";

const BUILD = "__BUILD__";
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/";
const COPILOT_URL = "https://copilot.microsoft.com/";
const PROJECT = "/project";
// 読み込まないフォルダ (大きい・不要)。.copilot-harness 内の作業フォルダは読み込む
const SKIP_DIRS = new Set([".git", "node_modules", ".venv", "venv", "__pycache__", ".pio", ".vs", ".idea", "dist", "obj"]);
const MAX_BYTES = 2 * 1024 * 1024;

const $ = (id) => document.getElementById(id);
let py = null;        // Pyodide
let api = null;       // copilot_harness.webapi
let backend = null;   // FolderBackend

// ------------------------------------------------------------ 小物
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastTimer = null;
function toast(msg, ms = 3500) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

function addLog(title, text) {
  const v = $("log-view");
  const time = new Date().toLocaleTimeString("ja-JP");
  v.textContent = `[${time}] ${title}\n${(text || "").trimEnd()}\n\n` + v.textContent;
}

function busy(on, text = "処理しています…") {
  $("busy").hidden = !on;
  $("busy-text").textContent = text;
}

async function withBusy(text, fn) {
  busy(true, text);
  await new Promise((r) => setTimeout(r, 40)); // 表示を反映させてから重い処理へ
  try {
    return await fn();
  } finally {
    busy(false);
  }
}

function fnv(bytes) {
  let h = 0x811c9dc5;
  for (let i = 0; i < bytes.length; i++) {
    h ^= bytes[i];
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16) + ":" + bytes.length;
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label}をコピーしました`);
    return true;
  } catch (e) {
    toast("コピーできませんでした。ブラウザのクリップボード許可を確認してください");
    return false;
  }
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ------------------------------------------------------------ Pyodide のファイルシステム
function fsExists(p) {
  try { py.FS.stat(p); return true; } catch { return false; }
}
function fsMkdirs(p) {
  if (!fsExists(p)) py.FS.mkdirTree(p);
}
function fsWrite(path, bytes) {
  fsMkdirs(path.slice(0, path.lastIndexOf("/")) || "/");
  py.FS.writeFile(path, bytes);
}
function fsRemove(path) {
  try { py.FS.unlink(path); } catch { /* 無ければ何もしない */ }
}
function fsRmTree(dir) {
  if (!fsExists(dir)) return;
  for (const name of py.FS.readdir(dir)) {
    if (name === "." || name === "..") continue;
    const p = `${dir}/${name}`;
    if (py.FS.isDir(py.FS.stat(p).mode)) fsRmTree(p);
    else py.FS.unlink(p);
  }
  py.FS.rmdir(dir);
}
function fsList(dir = PROJECT, prefix = "", out = new Map()) {
  for (const name of py.FS.readdir(dir)) {
    if (name === "." || name === "..") continue;
    const full = `${dir}/${name}`;
    const rel = prefix ? `${prefix}/${name}` : name;
    if (py.FS.isDir(py.FS.stat(full).mode)) {
      if (!SKIP_DIRS.has(name)) fsList(full, rel, out);
    } else {
      out.set(rel, py.FS.readFile(full));
    }
  }
  return out;
}

// ------------------------------------------------------------ ローカルフォルダとの同期
class FolderBackend {
  constructor(handle, label, isDemo = false) {
    this.root = handle;
    this.label = label;
    this.isDemo = isDemo;
    this.snap = new Map(); // rel -> {size, mtime, hash}
  }

  async walk(dir, prefix, out) {
    for await (const [name, entry] of dir.entries()) {
      const rel = prefix ? `${prefix}/${name}` : name;
      if (entry.kind === "directory") {
        if (!SKIP_DIRS.has(name)) await this.walk(entry, rel, out);
      } else {
        out.push([rel, entry]);
      }
    }
  }

  /** フォルダ → ブラウザ内 Python (変更されたファイルだけ読み込む) */
  async syncIn() {
    const files = [];
    await this.walk(this.root, "", files);
    const seen = new Set();
    for (const [rel, fh] of files) {
      const f = await fh.getFile();
      if (f.size > MAX_BYTES) continue;
      seen.add(rel);
      const prev = this.snap.get(rel);
      if (prev && prev.size === f.size && prev.mtime === f.lastModified && fsExists(`${PROJECT}/${rel}`)) continue;
      const bytes = new Uint8Array(await f.arrayBuffer());
      fsWrite(`${PROJECT}/${rel}`, bytes);
      this.snap.set(rel, { size: f.size, mtime: f.lastModified, hash: fnv(bytes) });
    }
    for (const rel of [...this.snap.keys()]) {
      if (!seen.has(rel)) {
        fsRemove(`${PROJECT}/${rel}`);
        this.snap.delete(rel);
      }
    }
  }

  async fileHandle(rel, create) {
    const parts = rel.split("/");
    let dir = this.root;
    for (const p of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(p, { create });
    return dir.getFileHandle(parts[parts.length - 1], { create });
  }

  /** ブラウザ内 Python → フォルダ (変更・追加・削除されたファイルだけ書き込む) */
  async syncOut() {
    const cur = fsList();
    let written = 0;
    for (const [rel, bytes] of cur) {
      const hash = fnv(bytes);
      const prev = this.snap.get(rel);
      if (prev && prev.hash === hash) continue;
      const fh = await this.fileHandle(rel, true);
      const w = await fh.createWritable();
      await w.write(bytes);
      await w.close();
      const f = await fh.getFile();
      this.snap.set(rel, { size: f.size, mtime: f.lastModified, hash });
      written++;
    }
    for (const rel of [...this.snap.keys()]) {
      if (!cur.has(rel)) {
        try {
          const parts = rel.split("/");
          let dir = this.root;
          for (const p of parts.slice(0, -1)) dir = await dir.getDirectoryHandle(p);
          await dir.removeEntry(parts[parts.length - 1]);
        } catch { /* 既に無い */ }
        this.snap.delete(rel);
        written++;
      }
    }
    return written;
  }
}

// ------------------------------------------------------------ 前回のフォルダ (IndexedDB)
const handleStore = {
  open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open("mycodex", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("handles");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  },
  async get(key) {
    try {
      const db = await this.open();
      return await new Promise((resolve) => {
        const r = db.transaction("handles").objectStore("handles").get(key);
        r.onsuccess = () => resolve(r.result || null);
        r.onerror = () => resolve(null);
      });
    } catch { return null; }
  },
  async set(key, value) {
    try {
      const db = await this.open();
      db.transaction("handles", "readwrite").objectStore("handles").put(value, key);
    } catch { /* 保存できなくても動作には影響しない */ }
  },
};

// ------------------------------------------------------------ Python 呼び出し
async function startPython() {
  $("loading-text").textContent = "Python (Pyodide) を読み込んでいます…";
  py = await loadPyodide({ indexURL: PYODIDE_URL });
  $("loading-text").textContent = "mycodex を読み込んでいます…";
  const zip = await (await fetch(`harness.zip?v=${BUILD}`)).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/harness" });
  py.runPython("import sys\nif '/harness' not in sys.path: sys.path.insert(0, '/harness')");
  api = py.pyimport("copilot_harness.webapi");
  fsMkdirs(PROJECT);
}

function pyJson(fnName, ...args) {
  const out = api[fnName](...args);
  return typeof out === "string" ? JSON.parse(out) : out;
}

/** フォルダを読み込み → Python 処理 → 変更を書き戻し → 画面更新 */
async function runAction(label, fn, { quiet = false } = {}) {
  return withBusy(`${label}…`, async () => {
    await backend.syncIn();
    let res;
    try {
      res = fn();
    } catch (e) {
      res = { code: 1, output: "", error: String((e && e.message) || e) };
    } finally {
      await backend.syncOut();
    }
    if (res && typeof res === "object" && "output" in res) {
      addLog(label, (res.output || "") + (res.error ? `\nERROR: ${res.error}` : ""));
      if (res.error && !quiet) toast(res.error.split("\n")[0], 6000);
    }
    await refreshState();
    return res;
  });
}

let state = null;
async function refreshState() {
  state = pyJson("ui_state");
  render();
}
