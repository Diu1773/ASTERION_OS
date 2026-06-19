/* ASTERION 임베디드 AI 채팅 위젯 — 대시보드 우하단 떠다니는 버블.
 * Gridstack 그리드와 완전 독립(자체 DOM+스타일, position:fixed). /api/agent/* 호출.
 * 백엔드 미설정([agent] 비움)이면 안내 메시지를 그대로 보여줌. */
(function () {
  "use strict";
  if (window.__asterionChat) return;
  window.__asterionChat = true;

  var css = `
  #ast-chat-fab{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;
    background:#2585cc;color:#fff;border:none;cursor:pointer;z-index:99998;display:flex;
    align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,0,0,.4);transition:transform .15s}
  #ast-chat-fab:hover{transform:scale(1.07)}
  #ast-chat-fab svg{width:28px;height:28px;stroke:#fff;fill:none;stroke-width:1.8;
    stroke-linecap:round;stroke-linejoin:round}
  #ast-chat-panel{position:fixed;right:20px;bottom:88px;width:370px;max-width:calc(100vw - 40px);
    height:540px;max-height:calc(100vh - 120px);background:#11151c;border:1px solid #2a3340;
    border-radius:14px;display:none;flex-direction:column;z-index:99999;overflow:hidden;
    box-shadow:0 8px 32px rgba(0,0,0,.5);font:14px/1.5 Pretendard,system-ui,sans-serif;color:#e6edf3}
  #ast-chat-panel.open{display:flex}
  .ast-h{padding:12px 14px;background:#161c25;border-bottom:1px solid #2a3340;display:flex;
    align-items:center;justify-content:space-between}
  .ast-h b{font-size:14px} .ast-h .ast-sub{font-size:11px;color:#8b98a8}
  .ast-x{background:none;border:none;color:#8b98a8;font-size:18px;cursor:pointer}
  .ast-modelbar{display:flex;flex-direction:column;gap:6px;padding:7px 14px;background:#10151d;
    border-bottom:1px solid #2a3340;font-size:11px;color:#8b98a8}
  .ast-mrow{display:flex;align-items:center;gap:8px}
  .ast-mrow label{width:42px;flex:none;color:#8b98a8}
  .ast-modelbar select{flex:1;min-width:0;background:#0d1117;color:#e6edf3;border:1px solid #2a3340;
    border-radius:7px;padding:4px 7px;font:12px/1.3 inherit;cursor:pointer}
  .ast-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
  .ast-m{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
  .ast-u{align-self:flex-end;background:#2585cc;color:#fff;border-bottom-right-radius:4px}
  .ast-a{align-self:flex-start;background:#1e2630;border-bottom-left-radius:4px}
  .ast-e{align-self:flex-start;background:#3a1d22;color:#ffd7d7;border:1px solid #6b2b34;
    border-bottom-left-radius:4px}
  .ast-t{align-self:flex-start;font-size:11px;color:#7c8794;background:transparent;padding:0 4px}
  .ast-in{display:flex;gap:6px;padding:10px;border-top:1px solid #2a3340;background:#161c25}
  .ast-in textarea{flex:1;resize:none;height:38px;max-height:120px;background:#0d1117;color:#e6edf3;
    border:1px solid #2a3340;border-radius:8px;padding:8px;font:14px/1.4 inherit}
  .ast-in button{background:#2585cc;color:#fff;border:none;border-radius:8px;padding:0 14px;cursor:pointer}
  .ast-in button:disabled{opacity:.5;cursor:default}
  `;
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  // 망원경 아이콘(인라인 SVG) — Gemini 스파클과 무관, 천문대 테마.
  var TELESCOPE =
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    + '<path d="M3 14l11.5-6.5 2 3.5L5 17.5z"/>'      // 경통
    + '<path d="M14.5 7.5l3-1.7 2 3.5-3 1.7"/>'        // 대물부
    + '<path d="M9 16l2.5 4.5M7 17l1.7 3"/>'           // 삼각대 다리
    + '<circle cx="19" cy="5.5" r="1.3"/></svg>';      // 별

  var fab = document.createElement("button");
  fab.id = "ast-chat-fab"; fab.title = "ASTERION 어시스턴트"; fab.innerHTML = TELESCOPE;
  var panel = document.createElement("div"); panel.id = "ast-chat-panel";
  panel.innerHTML =
    '<div class="ast-h"><div><b>ASTERION 어시스턴트</b> <span class="ast-sub" id="ast-status"></span></div>'
    + '<button class="ast-x" title="닫기">×</button></div>'
    + '<div class="ast-modelbar">'
    + '<div class="ast-mrow"><label>공급자</label><select id="ast-provider"></select></div>'
    + '<div class="ast-mrow"><label>모델</label><select id="ast-model"></select></div></div>'
    + '<div class="ast-msgs" id="ast-msgs"></div>'
    + '<div class="ast-in"><textarea id="ast-ta" placeholder="예: 오늘 화성 보여줘"></textarea>'
    + '<button id="ast-send">전송</button></div>';
  document.body.appendChild(fab); document.body.appendChild(panel);

  var msgsEl = panel.querySelector("#ast-msgs");
  var ta = panel.querySelector("#ast-ta");
  var sendBtn = panel.querySelector("#ast-send");
  var statusEl = panel.querySelector("#ast-status");
  var modelSel = panel.querySelector("#ast-model");
  var provSel = panel.querySelector("#ast-provider");
  var history = [];   // [{role, content}]
  var providersLoaded = false;

  function add(cls, text) {
    var d = document.createElement("div"); d.className = "ast-m " + cls; d.textContent = text;
    msgsEl.appendChild(d); msgsEl.scrollTop = msgsEl.scrollHeight; return d;
  }
  function toggle(open) {
    panel.classList.toggle("open", open);
    if (open) { ta.focus(); if (!msgsEl.children.length) greet(); }
  }
  fab.onclick = function () { toggle(!panel.classList.contains("open")); };
  panel.querySelector(".ast-x").onclick = function () { toggle(false); };

  function setStatus(s) {
    if (!s) { statusEl.textContent = ""; return; }
    if (!s.configured) { statusEl.textContent = "· 공급자 미설정"; return; }
    var p = s.provider ? (s.provider + " / ") : "";
    statusEl.textContent = "· " + p + (s.model || "");
  }

  function loadProviders() {
    if (providersLoaded) return;
    fetch("/api/agent/providers").then(function (r) { return r.json(); }).then(function (d) {
      providersLoaded = true;
      var provs = d.providers || [];
      provSel.innerHTML = "";
      if (!provs.length) {
        var o = document.createElement("option");
        o.textContent = "(공급자 없음)"; o.disabled = true; provSel.appendChild(o); return;
      }
      provs.forEach(function (p) {
        var op = document.createElement("option"); op.value = p.name;
        op.textContent = p.name + (p.has_key || /11434|ollama/.test(p.base_url) ? "" : " (키 없음)");
        if (p.active) op.selected = true; provSel.appendChild(op);
      });
    }).catch(function () { providersLoaded = true; });
  }

  function loadModels() {
    fetch("/api/agent/models").then(function (r) { return r.json(); }).then(function (d) {
      var models = d.models || [];
      modelSel.innerHTML = "";
      if (!models.length) {
        var o = document.createElement("option");
        o.textContent = d.error ? ("(목록 불가: " + d.error + ")") : "(모델 없음)";
        o.disabled = true; modelSel.appendChild(o); return;
      }
      models.forEach(function (m) {
        var op = document.createElement("option"); op.value = m; op.textContent = m;
        if (m === d.current) op.selected = true; modelSel.appendChild(op);
      });
      if (d.error) { var w = document.createElement("option");
        w.textContent = "⚠ " + d.error; w.disabled = true; modelSel.appendChild(w); }
    }).catch(function () {});
  }

  provSel.onchange = function () {
    var name = provSel.value; if (!name) return;
    fetch("/api/agent/provider", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.ok) {
        setStatus({ configured: res.configured, provider: name, model: res.model });
        add("ast-t", "공급자 전환 → " + name + (res.model ? (" / " + res.model) : ""));
        loadModels();   // 새 공급자의 모델 목록으로 갱신
      } else { add("ast-t", "⚠ " + (res.error || "공급자 전환 실패")); }
    }).catch(function () {});
  };

  modelSel.onchange = function () {
    var m = modelSel.value; if (!m) return;
    fetch("/api/agent/model", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: m })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res.ok) { setStatus({ configured: true, provider: provSel.value, model: res.model });
        add("ast-t", "모델 전환 → " + res.model); }
    }).catch(function () {});
  };

  function greet() {
    add("ast-a", "안녕하세요 — 청람천문대 ASTERION 어시스턴트예요. \"오늘 화성 보여줘\" 처럼 말해보세요.");
    fetch("/api/agent/status").then(function (r) { return r.json(); })
      .then(setStatus).catch(function () {});
    loadProviders();
    loadModels();
  }

  async function send() {
    var text = ta.value.trim(); if (!text) return;
    ta.value = ""; add("ast-u", text); history.push({ role: "user", content: text });
    sendBtn.disabled = true;
    var thinking = add("ast-t", "…생각 중");
    try {
      var r = await fetch("/api/agent/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: history.slice(0, -1) })
      });
      var data;
      try { data = await r.json(); }
      catch (_) { data = { reply: "서버 응답을 읽지 못했어요 (HTTP " + r.status + ").", error: true }; }
      thinking.remove();
      (data.transcript || []).forEach(function (t) {
        add("ast-t", "🔧 " + t.tool + (t.result && t.result.error ? " ⚠" : ""));
      });
      var reply = data.reply || "(빈 응답)";
      add(data.error ? "ast-e" : "ast-a", reply);
      if (!data.error) history.push({ role: "assistant", content: reply });
    } catch (e) {
      thinking.remove(); add("ast-e", "오류: " + e);
    } finally { sendBtn.disabled = false; ta.focus(); }
  }
  sendBtn.onclick = send;
  ta.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
})();
