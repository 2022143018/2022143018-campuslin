// 가게 등록: 카카오 장소 검색으로 가게를 고르면 좌표·주소가 자동 입력됨
(function () {
  const $ = s => document.querySelector(s);
  const form = $('#place-form');
  const msg = $('#search-msg');
  const resultsEl = $('#search-results');
  let ps = null, results = [];

  kakaoReady(() => { ps = new kakao.maps.services.Places(); },
             () => { msg.textContent = KAKAO_MISSING_MSG; });

  // 카카오 카테고리 이름으로 음식 종류를 추측
  function guessCategory(name) {
    const rules = [['술집', ['술집', '호프', '포차', '이자카야', '주점', '바']],
                   ['카페', ['카페', '디저트']], ['분식', ['분식', '김밥', '떡볶이']],
                   ['중식', ['중식', '중국']], ['일식', ['일식', '초밥', '돈까스', '라멘']],
                   ['양식', ['양식', '피자', '파스타', '햄버거', '패스트푸드']], ['한식', ['한식']]];
    for (const [cat, words] of rules) if (words.some(w => name.includes(w))) return cat;
    return null;
  }

  $('#search-form').addEventListener('submit', e => {
    e.preventDefault();
    if (!ps) { msg.textContent = KAKAO_MISSING_MSG; return; }
    const keyword = $('#keyword').value.trim();
    if (!keyword) return;
    msg.textContent = '검색 중...';
    ps.keywordSearch(keyword, (data, status) => {
      if (status === kakao.maps.services.Status.ZERO_RESULT) {
        msg.textContent = '검색 결과가 없어요.'; resultsEl.innerHTML = ''; return;
      }
      if (status !== kakao.maps.services.Status.OK) {
        msg.textContent = '검색 중 오류가 발생했어요.'; return;
      }
      results = data;
      msg.textContent = '학교에서 가까운 순으로 보여줘요.';
      resultsEl.innerHTML = data.map((p, i) => {
        const km = Number(p.distance) / 1000;
        const far = km > window.MAX_KM;
        return `<li class="${far ? 'far' : ''}">
          <button type="button" data-i="${i}" ${far ? 'disabled' : ''}>
            <b>${esc(p.place_name)}</b> <span class="muted small">${esc(p.category_name.split(' > ').pop())}</span><br>
            <span class="small muted">${esc(p.road_address_name || p.address_name)} · ${km.toFixed(1)}km${far ? ' (범위 밖)' : ''}</span>
          </button></li>`;
      }).join('');
    }, {
      location: new kakao.maps.LatLng(window.SCHOOL.lat, window.SCHOOL.lng),
      radius: Math.round(window.MAX_KM * 1000),
      sort: kakao.maps.services.SortBy.DISTANCE,
    });
  });

  resultsEl.addEventListener('click', e => {
    const btn = e.target.closest('button[data-i]');
    if (!btn) return;
    const p = results[Number(btn.dataset.i)];
    const set = (name, v) => { form.elements[name].value = v || ''; };
    set('kakao_id', p.id);
    set('name', p.place_name);
    set('address', p.road_address_name || p.address_name);
    set('phone', p.phone);
    set('kakao_url', p.place_url);
    set('lat', p.y);
    set('lng', p.x);

    $('#selected').classList.remove('empty');
    $('#selected').innerHTML = `<b>${esc(p.place_name)}</b><br><span class="small muted">${esc(p.road_address_name || p.address_name)}</span>`;
    const cat = guessCategory(p.category_name);
    if (cat) {
      const box = form.querySelector(`input[name=category][value="${cat}"]`);
      if (box) box.checked = true;
    }
    $('#submit-btn').disabled = false;
    resultsEl.querySelectorAll('li').forEach(li => li.classList.remove('active'));
    btn.parentElement.classList.add('active');
  });

  form.addEventListener('submit', e => {
    if (!form.elements.kakao_id.value) { e.preventDefault(); alert('먼저 가게를 검색해서 선택해 주세요.'); return; }
    const photoInput = form.querySelector('input[name=photos]');
    if (photoInput && photoInput.files.length > 3) { e.preventDefault(); alert('사진은 최대 3장까지 올릴 수 있어요.'); return; }
    // 종류·가격대·좌석 최소 1개씩 (서버에서도 다시 검사)
    if (!form.querySelector('input[name=category]:checked')) { e.preventDefault(); alert('음식 종류를 하나 이상 선택해 주세요.'); return; }
    if (!form.querySelector('input[name=price]:checked')) { e.preventDefault(); alert('가격대를 하나 이상 선택해 주세요.'); return; }
    const seat = ['solo', 'small', 'mid', 'group'].some(k => form.querySelector(`input[name=tags][value=${k}]:checked`));
    if (!seat) { e.preventDefault(); alert('좌석 규모를 하나 이상 선택해 주세요.'); }
  });
})();
