/* 세션 타이머 (챗봇·관리실 공용, UI/UX 1-1·1-2)
 * - 남은 시간은 서버(/api/me 의 session_remaining)가 정한다. 새로고침·페이지 이동에도 이어진다.
 * - 연장 버튼을 누를 때만 30분으로 다시 채운다 (POST /api/session/extend). 질문만으로는 연장되지 않는다.
 * - 만료 2분 전에 '연장 / 로그아웃' 안내 창을 한 번 띄운다.
 * 사용: DojangSession.start(남은초, {timerEl, pillEl, extendBtn, onExpire})
 */
(function () {
  const WARN_SECONDS = 120;                                   // 이 시간(초) 남으면 안내 창
  let deadline = 0;                                           // 만료 시각 (Date.now() 기준 ms)
  let handle = null;                                          // setInterval 핸들
  let warned = false;                                         // 안내 창을 이미 띄웠는지
  let els = {};                                               // 화면 요소들
  let modal = null;                                           // 안내 창 (처음 쓸 때 만든다)

  function fmt(sec) {                                         // 초 → "29:59"
    const m = Math.floor(sec / 60), s = sec % 60;             // 분·초
    return `${m}:${String(s).padStart(2, "0")}`;              // 초는 두 자리
  }

  function left() {                                           // 남은 초 (0 아래로는 안 내려감)
    return Math.max(0, Math.round((deadline - Date.now()) / 1000));
  }

  function buildModal() {                                     // 만료 안내 창 (두 화면 공통 모양)
    modal = document.createElement("div");
    modal.className = "ds-overlay";                           // 배경
    modal.setAttribute("role", "dialog");                     // 스크린리더용
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "dsTitle");
    modal.innerHTML = `
      <div class="ds-box">
        <h3 id="dsTitle">곧 로그아웃돼요</h3>
        <p><b id="dsLeft">2:00</b> 뒤에 자동으로 로그아웃돼요. 계속 쓰려면 연장해 주세요.</p>
        <div class="ds-btns">
          <button type="button" class="ds-out">로그아웃</button>
          <button type="button" class="ds-ext">30분 연장</button>
        </div>
      </div>`;
    const style = document.createElement("style");            // 두 화면 모두 같은 색 변수(--cap 등)를 쓴다
    style.textContent = `
      .ds-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;place-items:center;z-index:300}
      .ds-overlay.open{display:grid}
      .ds-box{background:#fffcf6;border:1px solid var(--line,#e2d3bf);border-radius:18px;padding:26px 24px;width:90%;max-width:360px;text-align:center}
      .ds-box h3{margin:0 0 8px;font-size:18px}
      .ds-box p{margin:0 0 16px;font-size:13.5px;color:var(--ink2,#7a6253);line-height:1.6}
      .ds-btns{display:flex;gap:8px}
      .ds-btns button{flex:1;font:inherit;font-weight:600;font-size:14px;padding:10px;border-radius:12px;border:none;cursor:pointer}
      .ds-ext{background:var(--cap,#2f5d62);color:#fff}
      .ds-out{background:#eee;color:var(--ink,#3a2a20)}
      .ds-btns button:focus-visible{outline:3px solid var(--gold,#e7b84e);outline-offset:2px}`;
    document.head.appendChild(style);                         // 스타일 붙이기
    document.body.appendChild(modal);                         // 창 붙이기
    modal.querySelector(".ds-ext").addEventListener("click", extend);   // 연장
    modal.querySelector(".ds-out").addEventListener("click", logout);   // 로그아웃
    document.addEventListener("keydown", e => {               // Esc 로 닫기 (연장은 안 됨)
      if (e.key === "Escape" && modal.classList.contains("open")) modal.classList.remove("open");
    });
  }

  function showWarning() {                                    // 만료 2분 전 안내
    if (!modal) buildModal();                                 // 처음이면 만든다
    modal.classList.add("open");                              // 보이기
    modal.querySelector(".ds-ext").focus();                   // 연장 버튼에 포커스
  }

  function render() {                                         // 1초마다 화면 갱신
    const sec = left();                                       // 남은 초
    if (els.timerEl) els.timerEl.textContent = fmt(sec);      // 타이머 글자
    if (els.pillEl) els.pillEl.classList.toggle("warn", sec <= WARN_SECONDS);   // 2분 이하면 빨간색
    if (modal) { const l = modal.querySelector("#dsLeft"); if (l) l.textContent = fmt(sec); }   // 안내 창 남은 시간
    if (sec <= WARN_SECONDS && !warned) { warned = true; showWarning(); }       // 한 번만 띄운다
    if (sec <= 0) expire();                                   // 끝나면 로그아웃 처리
  }

  function expire() {                                         // 시간이 다 됨
    clearInterval(handle);                                    // 타이머 멈춤
    if (els.onExpire) els.onExpire();                         // 화면별 정리 (대화 기록 지우기 등)
    fetch("/api/logout", { method: "POST" }).finally(() => location.replace("/login?expired=1"));
  }

  async function extend() {                                   // 연장 버튼
    try {
      const r = await fetch("/api/session/extend", { method: "POST" });   // 서버 시간을 다시 30분으로
      if (r.status === 401) { expire(); return; }             // 이미 끝났으면 로그인 화면으로
      if (!r.ok) throw new Error(r.status);                   // 그 밖의 오류
      const d = await r.json();
      deadline = Date.now() + d.session_remaining * 1000;     // 새 만료 시각
      warned = false;                                         // 다음 만료 전에 다시 안내
      if (modal) modal.classList.remove("open");              // 안내 창 닫기
      render();                                               // 바로 갱신
    } catch (e) {
      if (modal) modal.querySelector("p").textContent = "연장하지 못했어요. 잠시 후 다시 눌러 주세요.";
    }
  }

  function logout() {                                         // 안내 창의 로그아웃 버튼
    if (els.onExpire) els.onExpire();
    fetch("/api/logout", { method: "POST" }).finally(() => location.replace("/login"));
  }

  function start(remainingSec, options) {                     // 시작 (화면이 /api/me 를 읽은 뒤 부른다)
    els = options || {};
    deadline = Date.now() + Math.max(0, remainingSec || 0) * 1000;   // 서버가 알려 준 남은 시간 기준
    warned = false;
    if (els.pillEl) els.pillEl.hidden = false;                // 타이머 보이기
    if (els.extendBtn) els.extendBtn.addEventListener("click", extend);   // 타이머 옆 연장 버튼
    clearInterval(handle);
    render();                                                 // 첫 표시를 바로 (15:00 같은 기본 글자가 보이지 않게)
    handle = setInterval(render, 1000);                       // 1초마다
  }

  window.DojangSession = { start, extend };                   // 화면에서 쓰는 함수
})();
