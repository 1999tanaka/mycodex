/* mycodex ブラウザ版: 画面 (次にやることのカード / 状態 / ファイル編集 / 変更内容 / 記録) */
"use strict";

const PHASE_LABEL = {
  NO_TASK: "タスク未開始", INIT: "タスク未開始", WAITING_COPILOT: "Copilot に相談",
  PATCH_APPLIED: "修正を適用済み", BUILD_FAILED: "ビルド失敗", FLASH_FAILED: "書込み失敗",
  TEST_FAILED: "テスト失敗", WAITING_TEST_RESULT: "テスト結果の入力待ち", DONE: "完了",
  HUMAN_REVIEW_REQUIRED: "確認が必要",
};
const STAGE_LABEL = { build: "ビルド (コンパイル)", flash: "書込み", test: "動作テスト" };
const NEXT_LABEL = {
  NO_TASK: "やりたいことを入力して、タスクを開始する",
  INIT: "やりたいことを入力して、タスクを開始する",
  WAITING_COPILOT: "Copilot に相談する (左のカードの 1〜4)",
  BUILD_FAILED: "エラー内容を Copilot に伝えて直してもらう (左のカードの 1〜4)",
  TEST_FAILED: "テスト結果を Copilot に伝えて直してもらう (左のカードの 1〜4)",
  FLASH_FAILED: "接続を確認して、もう一度ビルド・テストする",
  PATCH_APPLIED: "ビルドとテストを実行する",
  WAITING_TEST_RESULT: "ビルドとテストをして、結果を入力する",
  DONE: "変更内容を確認する (問題なければ完了)",
  HUMAN_REVIEW_REQUIRED: "内容を確認して、相談を再開する",
};

// ------------------------------------------------------------ 共通の描画
function render() {
  if (!state) return;
  renderStatusbar();
  renderNextCard();
  renderStatusTab();
  if (editing && !editing.dirty) openFile(editing.path, true); // 処理でファイルが変わった場合に表示を更新
  if (!$("tab-diff").hidden) renderDiff();
}

function renderStatusbar() {
  const s = state;
  if (!s.initialized) {
    $("statusbar").innerHTML = `<span class="chip phase">準備前</span>`;
    return;
  }
  const phase = s.phase;
  const cls = phase === "DONE" ? "ok" : /FAILED|REVIEW/.test(phase) ? "ng" : "";
  $("statusbar").innerHTML = [
    `<span class="chip phase ${cls}">${esc(PHASE_LABEL[phase] || phase)}</span>`,
    s.task ? `<span class="chip">やりたいこと: ${esc(s.task)}</span>` : "",
    s.run_id ? `<span class="chip">RUN_ID ${esc(s.run_id)}</span>` : "",
    s.task ? `<span class="chip">修正回数 ${s.iteration} / ${s.max_iterations}</span>` : "",
    `<span class="chip">${esc(s.profile_title)}</span>`,
  ].join("");
}

function renderNextCard() {
  const s = state;
  const card = $("next-card");
  if (!s.initialized) return cardInit(card);
  if (s.config_error) {
    card.innerHTML = `<h2>設定ファイルを読めません</h2><p class="reason">${esc(s.config_error)}</p>
      <p>「ファイル編集」タブで .copilot-harness/config.yaml を直してください。</p>`;
    return;
  }
  switch (s.phase) {
    case "NO_TASK": return cardTask(card);
    case "WAITING_TEST_RESULT": return cardTestResult(card);
    case "DONE": return cardDone(card);
    case "HUMAN_REVIEW_REQUIRED": return cardReview(card);
    case "PATCH_APPLIED": return cardPatched(card);
    default: return cardCopilot(card);
  }
}

// ------------------------------------------------------------ 初期化
function cardInit(card) {
  const s = state;
  const options = s.profiles.map((p) => `<option value="${p.name}" ${p.name === s.guess ? "selected" : ""}>${esc(p.title)}</option>`).join("");
  card.innerHTML = `
    <h2>このフォルダで使う準備をします</h2>
    <p class="lead">フォルダの中身から「<b>${esc(s.profiles.find((p) => p.name === s.guess)?.title)}</b>」と判断しました (${esc(s.guess_reason)})。違う場合は選び直してください。</p>
    <label class="field" for="init-profile">プログラムの種類</label>
    <select id="init-profile">${options}</select>
    <div class="row" style="margin-top:14px"><button class="btn primary big" id="b-init">準備する</button></div>
    <p class="muted small">フォルダの中に「.copilot-harness」という作業用フォルダが作られます。元のファイルは変更しません。</p>`;
  $("b-init").onclick = () => runAction("準備", () => pyJson("init", $("init-profile").value));
}

// ------------------------------------------------------------ タスク入力
function taskFormHtml() {
  const runDefault = state.profile === "python" ? "checked" : "";
  return `
    <label class="field" for="t-task">やりたいこと</label>
    <textarea id="t-task" placeholder="例: 消費税の計算を 10% にする / CAN の受信がタイムアウトする不具合を直す"></textarea>
    <label class="field" for="t-kw">関係しそうな言葉 (任意・カンマ区切り)</label>
    <input type="text" id="t-kw" placeholder="例: tax, 消費税, calc">
    <label class="field" for="t-cons">守ってほしいこと (任意・1 行に 1 つ)</label>
    <textarea id="t-cons" placeholder="例: 関数の名前は変えない"></textarea>
    <label class="row"><input type="checkbox" id="t-run" ${runDefault}> 開始したら、今の状態のテスト結果も Copilot に渡す</label>
    <div class="row" style="margin-top:10px"><button class="btn primary big" id="b-start">タスクを開始</button></div>`;
}

function bindTaskForm() {
  $("b-start").onclick = () => {
    const task = $("t-task").value.trim();
    if (!task) return toast("やりたいことを入力してください");
    const args = ["start", task];
    $("t-kw").value.split(/[,、\s]+/).filter(Boolean).forEach((k) => args.push("-k", k));
    $("t-cons").value.split("\n").map((x) => x.trim()).filter(Boolean).forEach((c) => args.push("-c", c));
    const alsoRun = $("t-run").checked;
    runAction("タスク開始", () => {
      const r = pyJson("run_cli", JSON.stringify(args));
      if (alsoRun && r.code === 0) {
        const r2 = pyJson("run_cli", JSON.stringify(["run"]));
        r.output += "\n" + r2.output;
      }
      return r;
    });
  };
}

function cardTask(card) {
  card.innerHTML = `<h2>やりたいことを入力してください</h2>
    <p class="lead">Copilot に渡す資料 (関係するソースや状態) を自動で作ります。</p>${taskFormHtml()}`;
  bindTaskForm();
}

// ------------------------------------------------------------ Copilot に相談
function failureBox() {
  const s = state;
  if (s.phase === "BUILD_FAILED") return `<p class="reason">ビルドでエラーが出ました。エラー内容を Copilot に伝えて直してもらいます。\n${esc(s.failures.join("\n"))}</p>`;
  if (s.phase === "TEST_FAILED") return `<p class="reason">テストが失敗しました。結果を Copilot に伝えて直してもらいます。\n${esc(s.failures.join("\n"))}</p>`;
  if (s.phase === "FLASH_FAILED") return `<p class="reason">書込みに失敗しました。多くの場合はプログラムではなく接続の問題です (USB ケーブル、COM ポート、シリアルモニタを開いたままにしていないか)。確認してから下の「もう一度試す」を押してください。\n${esc(s.failures.join("\n"))}</p>
      <div class="row"><button class="btn" id="b-retry">もう一度ビルド・テストする</button></div>`;
  return "";
}

function cardCopilot(card) {
  const s = state;
  const prompt = s.handoff["NEXT_PROMPT.txt"] || "";
  const bundleLen = Object.values(s.handoff).reduce((n, t) => n + t.length, 0);
  card.innerHTML = `
    <h2>Copilot に相談します</h2>
    <p class="lead">次の 4 つを順番に行ってください。資料は自動で作ってあります。</p>
    ${failureBox()}
    <ol class="steps">
      <li><div class="step-title">Copilot を開く</div>
        <div class="row"><button class="btn" id="b-copilot">Copilot を開く (新しいタブ)</button></div>
        <p class="muted small">Windows の Copilot アプリを使う場合は、アプリで「新しいチャット」を開いてください。</p></li>
      <li><div class="step-title">資料ファイル 3 つを Copilot に添付する</div>
        <div class="row"><button class="btn" id="b-download">3 つのファイルをダウンロード</button></div>
        <p class="muted small">Copilot の入力欄の「＋」→「画像またはファイルを追加」で、ダウンロードした
          STATE.md / SOURCE_CONTEXT.md / TEST_RESULT.md を選びます
          (作業フォルダの .copilot-harness/handoff にも同じファイルがあります)。</p></li>
      <li><div class="step-title">指示文をコピーして Copilot に貼り付け、送信する</div>
        <div class="row"><button class="btn primary" id="b-copy-prompt">指示文をコピー</button>
          <span class="muted small">Copilot の入力欄で Ctrl+V → 送信</span></div></li>
      <li><div class="step-title">Copilot の回答をコピーして反映する</div>
        <p class="muted small">回答の下にあるコピーボタン (四角が重なったアイコン) を押してから、次のボタンを押します。</p>
        <div class="row"><button class="btn primary big" id="b-paste">回答を貼り付けて反映する</button></div>
        <details><summary class="small">ボタンで貼り付けられない場合</summary>
          <textarea id="paste-area" placeholder="ここに Copilot の回答を貼り付け (Ctrl+V)"></textarea>
          <div class="row"><button class="btn" id="b-paste-area">この内容で反映する</button></div>
        </details>
        ${backend.isDemo ? `<div class="row"><button class="btn ghost small" id="b-demo-answer">(デモ用) Copilot の代わりにサンプル回答を使う</button></div>` : ""}
      </li>
    </ol>
    <div class="alt small">ファイルを添付できないときは
      <button class="btn small" id="b-copy-all">指示文と資料をまとめてコピー</button>
      <span class="muted">(${bundleLen.toLocaleString()} 文字。長すぎると Copilot に入らないことがあります)</span></div>`;

  $("b-copilot").onclick = () => window.open(COPILOT_URL, "_blank", "noopener");
  $("b-download").onclick = () => {
    for (const name of ["STATE.md", "SOURCE_CONTEXT.md", "TEST_RESULT.md"]) download(name, s.handoff[name] || "");
    toast("3 つのファイルをダウンロードしました");
  };
  $("b-copy-prompt").onclick = () => copyText(prompt, "指示文");
  $("b-copy-all").onclick = () => copyText(pyJson("handoff_bundle"), "指示文と資料");
  $("b-paste").onclick = async () => {
    let text = "";
    try {
      text = await navigator.clipboard.readText();
    } catch {
      toast("クリップボードを読めませんでした。下の「ボタンで貼り付けられない場合」を使ってください", 6000);
      $("paste-area").closest("details").open = true;
      return;
    }
    applyAnswer(text);
  };
  $("b-paste-area").onclick = () => applyAnswer($("paste-area").value);
  if ($("b-demo-answer")) {
    $("b-demo-answer").onclick = async () => {
      const t = await (await fetch(`demo-python-response.txt?v=${BUILD}`)).text();
      applyAnswer(t.replaceAll("{RUN_ID}", state.run_id));
    };
  }
  if ($("b-retry")) $("b-retry").onclick = () => runAction("ビルド・テスト", () => pyJson("run_cli", JSON.stringify(["run"])));
}

async function applyAnswer(text) {
  if (!text.trim()) return toast("貼り付ける内容が空です");
  const before = state.run_id;
  const res = await runAction("Copilot の回答を反映", () => pyJson("paste_text", text, true));
  if (!res) return;
  if (res.code !== 0 && res.error) return; // エラーはトーストと記録に表示済み
  if (/NEED_CONTEXT/.test(res.output)) {
    toast("Copilot が追加の資料を求めたので、資料を作り直しました。もう一度 2〜4 を行ってください", 7000);
  } else if (state.phase === "DONE") {
    toast("修正が完了しました。変更内容を確認してください", 6000);
  } else if (state.run_id !== before) {
    toast("修正を反映しました");
  }
}

// ------------------------------------------------------------ 修正適用済み (通常は自動で次へ進む)
function cardPatched(card) {
  card.innerHTML = `<h2>修正を反映しました</h2><p class="lead">ビルドとテストで確認します。</p>
    <div class="row"><button class="btn primary big" id="b-run">ビルドとテストを実行</button></div>`;
  $("b-run").onclick = () => runAction("ビルド・テスト", () => pyJson("run_cli", JSON.stringify(["run"])));
}

// ------------------------------------------------------------ テスト結果の入力
function cardTestResult(card) {
  const s = state;
  const cmds = s.local_commands.map((c, i) => `<div class="cmd"><code>${esc(c)}</code><button class="btn small" data-cmd="${i}">コピー</button></div>`).join("");
  card.innerHTML = `
    <h2>ビルドとテストをして、結果を入力します</h2>
    <p class="lead">この作業はブラウザではできないため、いつもの方法 (Arduino IDE、Excel、コマンドなど) で行ってください。</p>
    ${cmds ? `<h3>コマンドで行う場合 (ターミナルに貼り付けて実行)</h3>${cmds}` : ""}
    <h3>結果</h3>
    <div class="radio-row">
      <label><input type="radio" name="r-ok" value="1" checked> うまくいった</label>
      <label><input type="radio" name="r-ok" value="0"> うまくいかなかった</label>
    </div>
    <div id="r-ng" hidden>
      <label class="field" for="r-stage">どこでうまくいかなかったか</label>
      <select id="r-stage">${Object.entries(STAGE_LABEL).map(([k, v]) => `<option value="${k}" ${k === "test" ? "selected" : ""}>${v}</option>`).join("")}</select>
      <label class="field" for="r-detail">何が起きたか (短く)</label>
      <input type="text" id="r-detail" placeholder="例: 受信できない / コンパイルエラーが出る">
    </div>
    <label class="field" for="r-log">エラーメッセージやログ (あれば貼り付け。Copilot に渡します)</label>
    <textarea id="r-log" placeholder="コンパイラのエラー、シリアルモニタの出力、TEST:名前:PASS の行など"></textarea>
    <div class="row" style="margin-top:10px"><button class="btn primary big" id="b-result">結果を登録する</button></div>`;
  card.querySelectorAll("[data-cmd]").forEach((b) => { b.onclick = () => copyText(s.local_commands[+b.dataset.cmd], "コマンド"); });
  card.querySelectorAll("input[name=r-ok]").forEach((r) => { r.onchange = () => { $("r-ng").hidden = r.value === "1" && r.checked; }; });
  $("b-result").onclick = () => {
    const ok = card.querySelector("input[name=r-ok]:checked").value === "1";
    const detail = ok ? "" : $("r-detail").value.trim();
    if (!ok && !detail && !$("r-log").value.trim()) return toast("何が起きたかを入力するか、ログを貼り付けてください");
    runAction("テスト結果の登録", () => pyJson("manual_result", ok, ok ? "test" : $("r-stage").value, detail, $("r-log").value));
  };
}

// ------------------------------------------------------------ 完了
function cardDone(card) {
  card.innerHTML = `
    <h2>完了しました</h2>
    <p class="reason ok">${state.tests.length ? "ビルドとテストに合格しました。" : "修正を反映しました (テストが設定されていないため、動作は未確認です)。"}</p>
    <p>「変更内容」タブで、Copilot が直した箇所を確認してください。問題なければ、このフォルダの変更をそのまま使えます
       (git を使っている場合は commit してください)。</p>
    <div class="row"><button class="btn" id="b-show-diff">変更内容を見る</button></div>
    <h3>修正に納得できない場合</h3>
    <label class="field" for="d-reason">取り消す理由 (Copilot に伝えます)</label>
    <input type="text" id="d-reason" placeholder="例: 計算方法ではなく定数を変えてほしい">
    <label class="field" for="d-cons">今後守ってほしいこと (任意)</label>
    <input type="text" id="d-cons" placeholder="例: 関数の中身は変えない">
    <div class="row"><button class="btn danger" id="b-undo">修正を取り消して、別の方法を頼む</button></div>
    <h3>続けて別の修正を頼む</h3>
    <textarea id="d-more" placeholder="例: 端数を四捨五入にする"></textarea>
    <div class="row"><button class="btn" id="b-more">続けて頼む</button>
      <button class="btn ghost" id="b-new">新しいタスクを始める</button></div>`;
  $("b-show-diff").onclick = () => showTab("diff");
  $("b-undo").onclick = () => {
    if (!$("d-reason").value.trim()) return toast("取り消す理由を入力してください");
    runAction("修正の取り消し", () => pyJson("rollback_with_reason", $("d-reason").value.trim(), $("d-cons").value.trim()));
  };
  $("b-more").onclick = () => {
    const t = $("d-more").value.trim();
    if (!t) return toast("頼みたいことを入力してください");
    runAction("追加の依頼", () => pyJson("request_more", t));
  };
  $("b-new").onclick = () => {
    card.innerHTML = `<h2>新しいタスク</h2>${taskFormHtml()}`;
    bindTaskForm();
  };
}

// ------------------------------------------------------------ 人の確認が必要
function cardReview(card) {
  const s = state;
  const tooLarge = /変更|MAX_CHANGED|MAX_ADDED|MAX_DELETED|行数|ファイル数/.test(s.phase_reason);
  card.innerHTML = `
    <h2>人の確認が必要です</h2>
    <p class="reason">${esc(s.phase_reason)}</p>
    ${tooLarge
      ? `<p>Copilot の修正が大きすぎるため自動では反映していません。回答の内容を確認し、問題なければ反映してください。</p>
         <details><summary>Copilot の回答を見る</summary><pre class="log" id="rv-answer"></pre></details>
         <div class="row"><button class="btn primary" id="b-allow">確認したので反映する</button></div>`
      : `<p>何度修正してもうまくいっていません。Copilot へのヒント (原因の見当や確認済みのこと) を足してから再開してください。</p>
         <label class="field" for="rv-hint">Copilot へのヒント</label>
         <textarea id="rv-hint" placeholder="例: 割り込みの設定ではなく初期化の順番が怪しい"></textarea>`}
    <div class="row"><button class="btn" id="b-resume">Copilot への相談を再開する</button></div>`;
  if (tooLarge) {
    try { $("rv-answer").textContent = pyJson("get_file", ".copilot-harness/inbox/copilot_response.txt").text; } catch { /* 無し */ }
    $("b-allow").onclick = () => runAction("大きな修正の反映", () => pyJson("run_cli", JSON.stringify(["apply", "--allow-large", "--reprocess", "--run"])));
  }
  $("b-resume").onclick = () => runAction("相談の再開", () => {
    const hint = $("rv-hint") ? $("rv-hint").value.trim() : "";
    const args = ["note", "--resume"];
    if (hint) args.push("--finding", hint);
    const r = pyJson("run_cli", JSON.stringify(args));
    const r2 = pyJson("run_cli", JSON.stringify(["next"]));
    r.output += "\n" + r2.output;
    return r;
  });
}

// ------------------------------------------------------------ 状態タブ
function renderStatusTab() {
  const s = state;
  const el = $("tab-status");
  if (!s.initialized || s.config_error) { el.innerHTML = `<p class="muted">まだ準備前です。</p>`; return; }
  const tests = s.tests.length
    ? `<ul class="tight">${s.tests.map((t) => `<li><span class="badge ${esc(t.status)}">${esc(t.status)}</span> ${esc(t.name)} ${t.detail && t.status !== "PASS" ? `<span class="muted">(${esc(t.detail)})</span>` : ""}</li>`).join("")}</ul>`
    : `<span class="muted">まだ実行していません</span>`;
  const list = (xs) => (xs.length ? `<ul class="tight">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<span class="muted">なし</span>`);
  el.innerHTML = `
    <dl class="kv">
      <dt>作業フォルダ</dt><dd>${esc(backend.label)}</dd>
      <dt>やりたいこと</dt><dd>${esc(s.task || "未入力")}</dd>
      <dt>段階</dt><dd>${esc(PHASE_LABEL[s.phase] || s.phase)}</dd>
      <dt>次にやること</dt><dd>${esc(NEXT_LABEL[s.phase] || s.next_action)}</dd>
      <dt>ビルド</dt><dd><span class="badge ${esc(s.build)}">${esc(s.build)}</span></dd>
      ${s.has_flash ? `<dt>書込み</dt><dd><span class="badge ${esc(s.flash)}">${esc(s.flash)}</span></dd>` : ""}
      <dt>テスト</dt><dd>${tests}</dd>
      <dt>原因の見立て</dt><dd>${esc(s.finding || "なし")}</dd>
    </dl>
    <h3>確認済みのこと</h3>
    <p class="muted small">ここに書いたことは Copilot に「確認済み」と伝わり、同じ確認を何度も提案されなくなります。</p>
    ${list(s.verified)}
    <div class="row"><input type="text" id="s-verified" placeholder="例: 配線は確認済み"><button class="btn small" id="b-verified">追加</button></div>
    <h3>守ってほしいこと</h3>${list(s.constraints)}
    <div class="row"><input type="text" id="s-cons" placeholder="例: 関数名を変えない"><button class="btn small" id="b-cons">追加</button></div>
    <div class="row" style="margin-top:14px"><button class="btn small" id="b-next">Copilot 用の資料を作り直す</button>
      ${backend.isDemo ? `<button class="btn small danger" id="b-reset-demo">デモを最初からやり直す</button>` : ""}</div>`;
  if ($("b-reset-demo")) $("b-reset-demo").onclick = () => withBusy("デモを初期化しています", resetDemo);
  const addNote = (flag, inputId, label) => {
    const v = $(inputId).value.trim();
    if (!v) return;
    runAction(label, () => {
      const r = pyJson("run_cli", JSON.stringify(["note", flag, v]));
      pyJson("run_cli", JSON.stringify(["next"]));
      return r;
    });
  };
  $("b-verified").onclick = () => addNote("-v", "s-verified", "確認済みの追加");
  $("b-cons").onclick = () => addNote("-c", "s-cons", "守ってほしいことの追加");
  $("b-next").onclick = () => runAction("資料の作り直し", () => pyJson("run_cli", JSON.stringify(["next"])));
}

// ------------------------------------------------------------ タブ
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  for (const t of ["status", "files", "diff", "log"]) $(`tab-${t}`).hidden = t !== name;
  if (name === "diff") renderDiff();
  if (name === "files") loadFileList();
}

function renderDiff() {
  const text = state && state.initialized ? String(api.diff_text()) : "";
  $("diff-view").innerHTML = text
    ? text.split("\n").map((l) => {
        const cls = l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : "";
        return cls ? `<span class="${cls}">${esc(l)}</span>` : esc(l);
      }).join("\n")
    : "このタスクで Copilot が変更したファイルはまだありません。";
}

// ------------------------------------------------------------ ファイル編集
let editing = null;
async function loadFileList() {
  if (!state || !state.initialized) return;
  await backend.syncIn();
  const files = pyJson("list_files");
  const sel = $("file-list");
  sel.innerHTML = files.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("");
  if (editing) sel.value = editing.path;
}

function openFile(path, silent = false) {
  try {
    const f = pyJson("get_file", path);
    editing = { path, encoding: f.encoding, eol: f.eol, dirty: false };
    $("file-name").textContent = path;
    $("file-enc").textContent = `${f.encoding} / ${f.eol === "\r\n" ? "CRLF" : "LF"}`;
    $("file-text").value = f.text;
    $("file-text").disabled = false;
    $("btn-save").disabled = false;
    if (!silent) $("save-msg").textContent = "";
  } catch (e) {
    if (silent) {  // 処理でファイルが削除された
      editing = null;
      $("file-name").textContent = "ファイルを選んでください";
      $("file-text").value = "";
      $("file-text").disabled = true;
      $("btn-save").disabled = true;
    } else {
      toast("開けませんでした: " + e.message);
    }
  }
}

async function saveFile() {
  if (!editing) return;
  const res = await runAction("ファイルの保存", () => pyJson("put_file", editing.path, $("file-text").value, editing.encoding, editing.eol), { quiet: true });
  if (res && res.ok === false) {
    toast(res.error, 6000);
  } else {
    editing.dirty = false;
    $("save-msg").textContent = `保存しました (${new Date().toLocaleTimeString("ja-JP")})`;
  }
}

// ------------------------------------------------------------ 起動とフォルダ選択
async function enterApp() {
  $("welcome").hidden = true;
  $("app").hidden = false;
  $("top-actions").hidden = false;
  $("project-label").hidden = false;
  $("project-label").textContent = `作業フォルダ: ${backend.label}`;
  await withBusy("フォルダを読み込んでいます", async () => {
    await backend.syncIn();
    await refreshState();
  });
}

async function useFolder(handle) {
  if ((await handle.queryPermission({ mode: "readwrite" })) !== "granted"
      && (await handle.requestPermission({ mode: "readwrite" })) !== "granted") {
    return toast("フォルダへの書き込みが許可されませんでした");
  }
  fsRmTree(PROJECT);
  fsMkdirs(PROJECT);
  backend = new FolderBackend(handle, handle.name);
  await handleStore.set("last", handle);
  await enterApp();
}

async function openDemo() {
  const opfs = await navigator.storage.getDirectory();
  const dir = await opfs.getDirectoryHandle("mycodex-demo", { create: true });
  let empty = true;
  for await (const _ of dir.keys()) { empty = false; break; }
  fsRmTree(PROJECT);
  fsMkdirs(PROJECT);
  backend = new FolderBackend(dir, "デモ (ブラウザ内の一時フォルダ)", true);
  if (empty) {
    const buf = await (await fetch(`demo-python.zip?v=${BUILD}`)).arrayBuffer();
    py.unpackArchive(buf, "zip", { extractDir: PROJECT });
    await backend.syncOut();
  }
  await enterApp();
  if (backend.isDemo) addLog("デモ", "税込み価格の計算 (8% のまま) を 10% に直すデモです。テストはブラウザ内の Python で実行されます。");
}

async function resetDemo() {
  const opfs = await navigator.storage.getDirectory();
  await opfs.removeEntry("mycodex-demo", { recursive: true }).catch(() => {});
  await openDemo();
}

async function boot() {
  try {
    await startPython();
  } catch (e) {
    $("loading-text").textContent = "Python を読み込めませんでした: " + e.message;
    return;
  }
  $("loading").hidden = true;
  $("welcome").hidden = false;
  const canPick = "showDirectoryPicker" in window;
  $("unsupported").hidden = canPick;
  $("btn-open").disabled = !canPick;
  const last = canPick ? await handleStore.get("last") : null;
  if (last) {
    $("btn-reopen").hidden = false;
    $("btn-reopen").textContent = `前回のフォルダを開く (${last.name})`;
    $("btn-reopen").onclick = () => useFolder(last).catch((e) => toast(e.message));
  }
  $("btn-open").onclick = async () => {
    try {
      await useFolder(await window.showDirectoryPicker({ mode: "readwrite" }));
    } catch (e) {
      if (e.name !== "AbortError") toast(e.message);
    }
  };
  $("btn-demo").onclick = () => openDemo().catch((e) => toast("デモを開けませんでした: " + e.message, 6000));
  $("btn-switch").onclick = () => location.reload();
  $("btn-refresh").onclick = () => withBusy("最新の状態に更新しています", async () => { await backend.syncIn(); await refreshState(); toast("最新の状態にしました"); });
  document.querySelectorAll(".tabs button").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
  $("file-list").onchange = (e) => {
    if (editing && editing.dirty && !confirm("保存していない変更があります。破棄して別のファイルを開きますか?")) {
      e.target.value = editing.path;
      return;
    }
    openFile(e.target.value);
  };
  $("file-text").oninput = () => { if (editing) editing.dirty = true; };
  window.addEventListener("beforeunload", (e) => { if (editing && editing.dirty) e.preventDefault(); });
  $("btn-save").onclick = saveFile;
  window.resetDemo = resetDemo; // 開発者用: コンソールから resetDemo()
}

boot();
