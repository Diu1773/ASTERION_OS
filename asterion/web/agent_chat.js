/* ASTERION 임베디드 AI 채팅 위젯 — 대시보드 우하단 떠다니는 버블.
 * Muuri 그리드와 완전 독립(자체 DOM+스타일, position:fixed). /api/agent/chat 호출.
 * 백엔드 미설정([agent] 비움)이면 안내 메시지를 그대로 보여줌. */
(function () {
  "use strict";
  if (window.__asterionChat) return;
  window.__asterionChat = true;

  var css = `
  #ast-chat-fab{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;
    background:#1b6fe0;color:#fff;border:none;font-size:24px;cursor:pointer;z-index:99998;
    box-shadow:0 4px 16px rgba(0,0,0,.4);transition:transform .15s}
  #ast-chat-fab:hover{transform:scale(1.07)}
  #ast-chat-panel{position:fixed;right:20px;bottom:88px;width:370px;max-width:calc(100vw - 40px);
    height:540px;max-height:calc(100vh - 120px);background:#11151c;border:1px solid #2a3340;
    border-radius:14px;display:none;flex-direction:column;z-index:99999;overflow:hidden;
    box-shadow:0 8px 32px rgba(0,0,0,.5);font:14px/1.5 Pretendard,system-ui,sans-serif;color:#e6edf3}
  #ast-chat-panel.open{display:flex}
  .ast-h{padding:12px 14px;background:#161c25;border-bottom:1px solid #2a3340;display:flex;
    align-items:center;justify-content:space-between}
  .ast-h b{font-size:14px} .ast-h .ast-sub{font-size:11px;color:#8b98a8}
  .ast-x{background:none;border:none;color:#8b98a8;font-size:18px;cursor:pointer}
  .ast-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
  .ast-m{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
  .ast-u{align-self:flex-end;background:#1b6fe0;color:#fff;border-bottom-right-radius:4px}
  .ast-a{align-self:flex-start;background:#1e2630;border-bottom-left-radius:4px}
  .ast-t{align-self:flex-start;font-size:11px;color:#7c8794;background:transparent;padding:0 4px}
  .ast-in{display:flex;gap:6px;padding:10px;border-top:1px solid #2a3340;background:#161c25}
  .ast-in textarea{flex:1;resize:none;height:38px;max-height:120px;background:#0d1117;color:#e6edf3;
    border:1px solid #2a3340;border-radius:8px;padding:8px;font:14px/1.4 inherit}
  .ast-in button{background:#1b6fe0;color:#fff;border:none;border-radius:8px;padding:0 14px;cursor:pointer}
  .ast-in button:disabled{opacity:.5;cursor:default}
  `;
  var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  var fab = document.createElement("button");
  fab.id = "ast-chat-fab"; fab.title = "ASTERION 어시스턴트"; fab.textContent = "✨";
  var panel = document.createElement("div"); panel.id = "ast-chat-panel";
  panel.innerHTML =
    '<div class="ast-h"><div><b>ASTERION 어시스턴트</b> <span class="ast-sub" id="ast-status"></span></div>'
    + '<button class="ast-x" title="닫기">×</button></div>'
    + '<div class="ast-msgs" id="ast-msgs"></div>'
    + '<div class="ast-in"><textarea id="ast-ta" placeholder="예: 오늘 화성 보여줘"></textarea>'
    + '<button id="ast-send">전송</button></div>';
  document.body.appendChild(fab); document.body.appendChild(panel);

  var msgsEl = panel.querySelector("#ast-msgs");
  var ta = panel.querySelector("#ast-ta");
  var sendBtn = panel.querySelector("#ast-send");
  var statusEl = panel.querySelector("#ast-status");
  var history = [];   // [{role, content}]

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

  function greet() {
    add("ast-a", "안녕하세요 — 청람천문대 ASTERION 어시스턴트예요. \"오늘 화성 보여줘\" 처럼 말해보세요.");
    fetch("/api/agent/status").then(function (r) { return r.json(); }).then(function (s) {
      statusEl.textContent = s.configured ? ("· " + (s.model || "")) : "· 모델 미설정";
    }).catch(function () {});
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
      var data = await r.json();
      thinking.remove();
      (data.transcript || []).forEach(function (t) {
        add("ast-t", "🔧 " + t.tool + (t.result && t.result.error ? " ⚠" : ""));
      });
      var reply = data.reply || "(빈 응답)";
      add("ast-a", reply); history.push({ role: "assistant", content: reply });
    } catch (e) {
      thinking.remove(); add("ast-a", "오류: " + e);
    } finally { sendBtn.disabled = false; ta.focus(); }
  }
  sendBtn.onclick = send;
  ta.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
})();
