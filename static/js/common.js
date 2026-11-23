// 공통 도우미 함수
const CSRF = document.querySelector('meta[name="csrf-token"]').content;

// 사용자 입력을 화면에 넣을 때는 반드시 esc()로 감싸기 (XSS 방지)
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function stars(n) {
  const r = Math.round(n);
  return '★'.repeat(r) + '☆'.repeat(5 - r);
}

// fetch + CSRF + 오류 처리
async function api(url, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  opts.headers = { 'X-CSRFToken': CSRF, ...(options.headers || {}) };
  if (opts.json !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* 빈 응답 */ }
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

// 카카오맵 SDK 준비 확인
function kakaoReady(cb, onFail) {
  if (window.kakao && kakao.maps && kakao.maps.load) {
    kakao.maps.load(cb);
  } else if (onFail) {
    onFail();
  }
}

const KAKAO_MISSING_MSG =
  '카카오맵을 불러오지 못했습니다. .env의 KAKAO_JS_KEY, 카카오 개발자 콘솔의 ' +
  'Web 플랫폼 도메인(http://localhost:5000) 등록, 카카오맵 사용 설정을 확인하세요.';
