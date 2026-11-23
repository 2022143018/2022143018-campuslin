// 메인 지도 화면: 필터 → 목록/마커 → 가게 상세 → 리뷰 작성·신고
(function () {
  const APP = window.APP;
  const $ = (s, el = document) => el.querySelector(s);
  const L = k => APP.labels[k] || k;

  const filterForm = $('#filters');
  const listEl = $('#place-list');
  const detailEl = $('#detail');

  let map = null, clusterer = null, markers = {}, places = [], unknown = [];
  let sortMode = 'rating', myPos = null, myOverlay = null;   // 정렬 방식, 내 위치
  let currentId = null;

  // ---------- 지도 초기화 ----------
  function initMap() {
    kakaoReady(() => {
      const center = new kakao.maps.LatLng(APP.school.lat, APP.school.lng);
      map = new kakao.maps.Map($('#map'), { center, level: 4 });
      map.addControl(new kakao.maps.ZoomControl(), kakao.maps.ControlPosition.RIGHT);
      // 마커가 몰린 곳은 숫자로 묶어서 표시 (클러스터링)
      clusterer = new kakao.maps.MarkerClusterer({ map, averageCenter: true, minLevel: 5 });

      // 학교 위치 표시
      new kakao.maps.CustomOverlay({
        map, position: center, yAnchor: 1.4,
        content: `<div class="school-pin">🎓 ${esc(APP.school.name)}</div>`,
      });
      renderMarkers();
    }, showMapMissing);

    // 키가 잘못되면 load 콜백이 안 오는 경우가 있어 4초 뒤 확인
    setTimeout(() => { if (!map) showMapMissing(); }, 4000);
  }

  function showMapMissing() {
    $('#map').innerHTML = `<div class="map-missing">${esc(KAKAO_MISSING_MSG)}<br><br>목록과 리뷰 기능은 지도 없이도 사용할 수 있습니다.</div>`;
  }

  // ---------- 목록 불러오기 ----------
  async function loadPlaces() {
    const params = new URLSearchParams(new FormData(filterForm));
    for (const [k, v] of [...params]) if (!v) params.delete(k);
    try {
      const data = await api('/api/places?' + params.toString());
      places = data.places;
      unknown = data.unknown || [];
    } catch (e) {
      listEl.innerHTML = `<li class="empty">${esc(e.message)}</li>`;
      return;
    }
    renderList();
    renderMarkers();
  }

  function tagChips(p) {
    const chips = [];
    p.prices.forEach(t => chips.push(`<span class="tag tag-price">${esc(L(t))}</span>`));
    p.tags.forEach(t => chips.push(`<span class="tag">${esc(L(t))}</span>`));
    return chips.join('');
  }

  // ---------- 거리 계산 / 정렬 ----------
  function distM(p) {
    const o = myPos || APP.school;            // 내 위치가 없으면 학교 기준
    const R = 6371000, rad = d => d * Math.PI / 180;
    const dLat = rad(p.lat - o.lat), dLng = rad(p.lng - o.lng);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(o.lat)) * Math.cos(rad(p.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }
  function distText(p) {
    const m = distM(p);
    const walk = Math.max(1, Math.round(m / 67));   // 걷는 속도 약 4km/h
    return m < 1000 ? `도보 ${walk}분 · ${Math.round(m)}m` : `도보 ${walk}분 · ${(m / 1000).toFixed(1)}km`;
  }
  function sorted(list) {
    return sortMode === 'near' ? [...list].sort((a, b) => distM(a) - distM(b)) : list;
  }

  function placeItem(p) {
    return `
      <li class="place-item ${p.id === currentId ? 'active' : ''}" data-id="${p.id}">
        ${p.thumb ? `<img class="pi-thumb" src="${esc(p.thumb)}" alt="" loading="lazy">` : ''}
        <div class="row space">
          <b>${esc(p.name)}</b><span class="muted small">${esc(p.category)}</span>
        </div>
        <div class="small"><span class="star">${stars(p.rating)}</span> ${p.rating.toFixed(1)}
          <span class="muted">(${p.reviewCount})</span>
          ${p.verifiedCount ? `<span class="badge">방문 인증 ${p.verifiedCount}</span>` : ''}
          ${p.reviewCount === 0 ? '<span class="badge badge-pioneer">첫 리뷰 기다리는 중</span>' : ''}
          ${p.stale ? '<span class="badge badge-stale">🕰 최근 6개월 리뷰 없음</span>' : ''}
          ${p.sampleOnly ? '<span class="badge badge-sample">🧪 샘플 리뷰</span>' : ''}
        </div>
        <div class="tags">${tagChips(p)}</div>
        ${sortMode === 'near' ? `<div class="dist">📍 ${distText(p)}</div>` : ''}
      </li>`;
  }

  function renderList() {
    $('#count').textContent = unknown.length
      ? `${places.length}곳 · 정보 없음 ${unknown.length}곳` : `${places.length}곳`;

    let html = places.length
      ? sorted(places).map(placeItem).join('')
      : `<li class="empty">조건이 확인된 가게가 없어요.${unknown.length ? '' : '<br>첫 번째로 등록해 보세요!'}</li>`;

    // 가격·좌석 필터를 켰을 때, 리뷰가 없어 아직 모르는 가게는 따로 모아서 보여 줌
    if (unknown.length) {
      const open = !places.length || unknown.some(p => p.id === currentId);
      html += `
        <li class="unknown-group">
          <details ${open ? 'open' : ''}>
            <summary>📭 아직 정보가 없는 가게 ${unknown.length}곳</summary>
            <p class="muted small">리뷰가 없어 가격·좌석을 아직 몰라요. 가 본 곳이면 10초 리뷰로 알려 주세요! (첫 리뷰 = 🏅 개척자)</p>
            <ul class="place-list">${sorted(unknown).map(placeItem).join('')}</ul>
          </details>
        </li>`;
    }
    listEl.innerHTML = html;
  }

  function renderMarkers() {
    if (!map) return;
    clusterer.clear();
    markers = {};
    const list = places.map(p => {
      const m = new kakao.maps.Marker({ position: new kakao.maps.LatLng(p.lat, p.lng), title: p.name });
      kakao.maps.event.addListener(m, 'click', () => openDetail(p.id));
      markers[p.id] = m;
      return m;
    });
    clusterer.addMarkers(list);
  }

  // ---------- 가게 상세 ----------
  async function openDetail(id) {
    currentId = id;
    renderList();
    let p;
    try {
      p = await api(`/api/places/${id}`);
    } catch (e) {
      alert(e.message);
      return;
    }
    if (map) {
      map.setLevel(Math.min(map.getLevel(), 3));
      map.panTo(new kakao.maps.LatLng(p.lat, p.lng));
    }

    detailEl.innerHTML = `
      <button class="close" aria-label="닫기">✕</button>
      <h2>${esc(p.name)} <span class="muted small">${esc(p.category)}</span></h2>
      ${p.photos.length
        ? `<div class="gallery">${p.photos.map(ph => `<img src="${esc(ph.url)}" alt="리뷰 사진" loading="lazy"
              data-photo-id="${ph.id}" data-mine="${ph.mine ? 1 : 0}" data-reported="${ph.reported ? 1 : 0}">`).join('')}</div>`
        : '<p class="no-photo">📷 아직 사진이 없어요. 리뷰와 함께 첫 사진을 올려 주세요!</p>'}
      <p class="small muted">${esc(p.address || '')}${p.phone ? ' · ' + esc(p.phone) : ''}</p>
      <p><span class="star">${stars(p.rating)}</span> <b>${p.rating.toFixed(1)}</b>
        <span class="muted small">리뷰 ${p.reviewCount}개</span></p>
      <div class="tags">${tagChips(p)}</div>
      <p class="muted small">태그는 리뷰어 과반이 선택한 것만 표시돼요.</p>
      ${p.stale ? `<p class="stale-note">🕰 마지막 리뷰가 ${esc(p.lastReview)}이에요. 지금도 영업 중인지 <b>🕐 영업시간·메뉴 보기</b>로 확인해 보세요. 문을 닫았다면 <b>폐업 신고</b>를 눌러 주세요.</p>` : ''}
      <div class="row wrap">
        ${p.kakaoUrl ? `<a class="btn btn-sm btn-hours" href="${esc(p.kakaoUrl)}" target="_blank" rel="noopener">🕐 영업시간·메뉴 보기</a>
          <a class="btn btn-sm btn-kakao-review" href="${esc(p.kakaoUrl)}#review" target="_blank" rel="noopener">⭐ 카카오 리뷰 보기</a>` : ''}
        <a class="btn btn-sm" href="${APP.voteNewUrl}?place=${p.id}">투표 후보로 추가</a>
        <button class="btn btn-sm btn-ghost js-closed" ${p.closedReported ? 'disabled' : ''}>
          ${p.closedReported ? '폐업 신고함' : '폐업 신고'}</button>
      </div>
      <div class="review-slot"></div>
      <h3>리뷰</h3>
      <ul class="reviews">
        ${p.reviews.map(r => `
          <li>
            <div class="row space">
              <span><span class="star">${stars(r.rating)}</span>
                <b class="small">${r.sample ? '시연용' : '익명'}${r.mine ? ' (나)' : ''}</b>
                ${r.sample ? '<span class="badge badge-sample">🧪 시연용 샘플</span>' : ''}
                ${r.pioneer ? '<span class="badge badge-pioneer">🏅 개척자</span>' : ''}
                ${r.receipt ? '<span class="badge">방문 인증</span>' : ''}</span>
              <span class="muted small">${esc(r.date)}</span>
            </div>
            ${r.content ? `<p>${esc(r.content)}</p>` : ''}
            ${r.photos.length ? `<div class="review-photos">${r.photos.map(ph =>
              `<img src="${esc(ph.url)}" alt="리뷰 사진" loading="lazy" data-photo-id="${ph.id}" data-mine="${r.mine ? 1 : 0}">`).join('')}</div>` : ''}
            <div class="tags">
              ${r.prices.map(t => `<span class="tag tag-price">${esc(L(t))}</span>`).join('')}
              ${r.tags.map(t => `<span class="tag">${esc(L(t))}</span>`).join('')}
            </div>
            <div class="right">
              ${r.mine
                ? `<button class="link-btn small js-delete" data-id="${r.id}">삭제</button>`
                : `<button class="link-btn small js-report" data-id="${r.id}" ${r.reported ? 'disabled' : ''}>
                     ${r.reported ? '신고함' : '신고'}</button>`}
            </div>
          </li>`).join('') || '<li class="empty">아직 리뷰가 없어요.<br>첫 리뷰를 남기면 <b>🏅 개척자 배지</b>를 받아요!</li>'}
      </ul>
      ${p.kakaoReviews && p.kakaoReviews.length ? `
      <h3 class="kakao-review-title">카카오맵 리뷰 <span class="muted small">출처: 카카오맵</span></h3>
      <p class="muted small">카카오맵에서 가져온 리뷰예요. 캠슐랭 별점·랭킹에는 포함되지 않아요.</p>
      <ul class="reviews kakao-reviews">
        ${p.kakaoReviews.map(k => `
          <li>
            <div class="row space">
              <span>${k.rating ? `<span class="star">${stars(k.rating)}</span>` : ''}
                <span class="badge badge-kakao">카카오맵</span></span>
              <span class="muted small">${esc(k.date)}</span>
            </div>
            <p>${esc(k.content)}</p>
          </li>`).join('')}
      </ul>
      ${p.kakaoUrl ? `<p class="right small"><a href="${esc(p.kakaoUrl)}#review" target="_blank" rel="noopener">카카오맵에서 리뷰 더 보기 →</a></p>` : ''}` : ''}`;

    if (!p.myReview) {
      const form = $('#review-form-tpl').content.cloneNode(true);
      $('.review-slot', detailEl).appendChild(form);
      bindReviewForm(id);
    } else {
      $('.review-slot', detailEl).innerHTML = '<p class="muted small">이 가게에 이미 리뷰를 남겼어요. (1인 1리뷰)</p>';
    }
    detailEl.hidden = false;
    detailEl.scrollTop = 0;
    history.replaceState(null, '', `?place=${id}`);
  }

  function closeDetail() {
    detailEl.hidden = true;
    currentId = null;
    renderList();
    history.replaceState(null, '', location.pathname);
  }

  function bindReviewForm(placeId) {
    const form = $('.review-form', detailEl);
    const errEl = $('.form-error', form);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const photoInput = form.querySelector('input[name=photos]');
      if (photoInput && photoInput.files.length > 3) {
        errEl.textContent = '사진은 최대 3장까지 올릴 수 있어요.';
        errEl.hidden = false;
        return;
      }
      const btn = $('button[type=submit]', form);
      btn.disabled = true;
      errEl.hidden = true;
      try {
        await api(`/api/places/${placeId}/reviews`, { method: 'POST', body: new FormData(form) });
        await loadPlaces();
        await openDetail(placeId);
      } catch (err) {
        errEl.textContent = err.message;
        errEl.hidden = false;
        btn.disabled = false;
      }
    });
  }

  // ---------- 사진 크게 보기 / 신고 ----------
  const lightbox = $('#lightbox');
  function openLightbox(img) {
    $('img', lightbox).src = img.src;
    lightbox.dataset.photoId = img.dataset.photoId;
    const reportBtn = $('.js-photo-report', lightbox);
    reportBtn.hidden = img.dataset.mine === '1';
    reportBtn.disabled = img.dataset.reported === '1';
    reportBtn.textContent = img.dataset.reported === '1' ? '신고함' : '사진 신고';
    lightbox.hidden = false;
  }
  function closeLightbox() { lightbox.hidden = true; $('img', lightbox).src = ''; }

  lightbox.addEventListener('click', async e => {
    if (e.target === lightbox || e.target.closest('.js-lb-close')) return closeLightbox();
    if (e.target.closest('.js-photo-report')) {
      if (!confirm('부적절한 사진(음식과 무관, 얼굴·개인정보 노출, 광고 등)으로 신고할까요?')) return;
      try {
        const r = await api(`/api/photos/${lightbox.dataset.photoId}/report`, { method: 'POST', json: {} });
        alert(r.hidden ? '신고가 누적되어 사진이 숨겨졌습니다.' : '신고가 접수되었습니다.');
        closeLightbox();
        await loadPlaces();
        if (currentId) await openDetail(currentId);
      } catch (err) { alert(err.message); }
    }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !lightbox.hidden) closeLightbox(); });

  // ---------- 별점순 / 가까운 순 ----------
  const sortSeg = $('#sort-seg');
  const locMsg = $('#loc-msg');

  function showMyPos() {
    if (!map || !myPos) return;
    const pos = new kakao.maps.LatLng(myPos.lat, myPos.lng);
    if (myOverlay) myOverlay.setMap(null);
    myOverlay = new kakao.maps.CustomOverlay({ map, position: pos, yAnchor: 0.5,
      content: '<div class="me-pin" title="내 위치"></div>' });
    map.setLevel(4);
    map.panTo(pos);
  }

  function setSort(mode) {
    sortMode = mode;
    sortSeg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.sort === mode));
    renderList();
    listEl.scrollTop = 0;
  }

  sortSeg.addEventListener('click', e => {
    const btn = e.target.closest('button[data-sort]');
    if (!btn) return;
    if (btn.dataset.sort === 'rating') { locMsg.textContent = ''; return setSort('rating'); }
    if (myPos) { showMyPos(); return setSort('near'); }

    if (!navigator.geolocation) {
      locMsg.textContent = '이 브라우저는 위치를 지원하지 않아 학교 기준으로 정렬했어요.';
      return setSort('near');
    }
    locMsg.textContent = '내 위치 확인 중...';
    navigator.geolocation.getCurrentPosition(pos => {
      const here = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      myPos = here;
      if (distM(APP.school) > 5000) {        // 학교에서 5km 넘게 떨어져 있으면 학교 기준이 더 쓸모 있음
        myPos = null;
        locMsg.textContent = '학교에서 멀리 있어서 학교 정문 기준으로 정렬했어요.';
      } else {
        locMsg.textContent = '내 위치 기준이에요.';
        showMyPos();
      }
      setSort('near');
    }, () => {
      locMsg.textContent = '위치 권한이 없어 학교 정문 기준으로 정렬했어요.';
      setSort('near');
    }, { enableHighAccuracy: false, timeout: 8000, maximumAge: 60000 });
  });

  // ---------- 랜덤 뽑기 (지금 필터에 맞는 가게 중에서) ----------
  const randomBtn = $('#random-btn');
  const randomMsg = $('#random-msg');
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  randomBtn.addEventListener('click', async () => {
    if (!places.length) {
      randomMsg.hidden = false;
      randomMsg.textContent = '조건에 맞는 가게가 없어요. 필터를 줄여 보세요.';
      return;
    }
    // 방금 뽑힌 가게는 가능하면 피하기
    const pool = places.length > 1 ? places.filter(p => p.id !== currentId) : places;
    const pick = pool[Math.floor(Math.random() * pool.length)];

    randomBtn.disabled = true;
    randomMsg.hidden = false;
    randomMsg.classList.remove('done');
    for (let i = 0; i < 12; i++) {   // 룰렛처럼 이름이 바뀌다가 점점 느려짐
      randomMsg.textContent = '🎲 ' + places[Math.floor(Math.random() * places.length)].name;
      await sleep(50 + i * 18);
    }
    randomMsg.textContent = `오늘은 여기 어때요? 👉 ${pick.name}`;
    randomMsg.classList.add('done');
    randomBtn.disabled = false;

    await openDetail(pick.id);
    const li = listEl.querySelector('.place-item.active');
    if (li) li.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });

  // ---------- 이벤트 ----------
  detailEl.addEventListener('click', async e => {
    const t = e.target;
    try {
      if (t.closest('.close')) return closeDetail();
      if (t.matches('img[data-photo-id]')) return openLightbox(t);
      if (t.classList.contains('js-report')) {
        const reason = prompt('신고 사유를 적어 주세요. (광고, 비방, 허위 등)');
        if (reason === null) return;
        const r = await api(`/api/reviews/${t.dataset.id}/report`, { method: 'POST', json: { reason } });
        alert(r.hidden ? '신고가 누적되어 리뷰가 숨겨졌습니다.' : '신고가 접수되었습니다.');
        await loadPlaces();
        return openDetail(currentId);
      }
      if (t.classList.contains('js-delete')) {
        if (!confirm('내 리뷰를 삭제할까요?')) return;
        await api(`/api/reviews/${t.dataset.id}`, { method: 'DELETE' });
        await loadPlaces();
        return openDetail(currentId);
      }
      if (t.classList.contains('js-closed')) {
        if (!confirm('이 가게가 폐업했거나 이전했나요? 신고가 3번 쌓이면 지도에서 숨겨집니다.')) return;
        const r = await api(`/api/places/${currentId}/closed`, { method: 'POST', json: {} });
        alert(r.hidden ? '지도에서 숨겨졌습니다.' : '신고가 접수되었습니다.');
        await loadPlaces();
        if (r.hidden) return closeDetail();
        return openDetail(currentId);
      }
    } catch (err) {
      alert(err.message);
    }
  });

  listEl.addEventListener('click', e => {
    const li = e.target.closest('.place-item');
    if (li) openDetail(Number(li.dataset.id));
  });

  let timer;
  filterForm.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(loadPlaces, 250); });
  filterForm.addEventListener('reset', () => setTimeout(loadPlaces, 0));
  filterForm.addEventListener('submit', e => { e.preventDefault(); loadPlaces(); });

  // ---------- 시작 ----------
  initMap();
  loadPlaces().then(() => {
    const id = Number(new URLSearchParams(location.search).get('place'));
    if (id) openDetail(id);
  });
})();
