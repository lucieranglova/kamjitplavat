/* =============================================
   Kam jít plavat? — script.js v2
   ============================================= */

const DAY_KEYS = ['ne','po','ut','st','ct','pa','so','ne']; // JS: 0=Sun
const DAY_LABELS = { po:'Pondělí', ut:'Úterý', st:'Středa', ct:'Čtvrtek', pa:'Pátek', so:'Sobota', ne:'Neděle' };

const MAP_BOUNDS = { latMin:49.98, latMax:50.18, lngMin:14.30, lngMax:14.65 };
function latToY(lat) { return ((MAP_BOUNDS.latMax - lat) / (MAP_BOUNDS.latMax - MAP_BOUNDS.latMin)) * 85 + 7; }
function lngToX(lng) { return ((lng - MAP_BOUNDS.lngMin) / (MAP_BOUNDS.lngMax - MAP_BOUNDS.lngMin)) * 85 + 7; }

// ─── State ──────────────────────────────────
let allPools = [];
let lanesData = {};
let petynkaLive = {};   // ← NOVÉ
let activeFilters = {
  search: '',
  length: 'all',
  multi: 'all',
  day: 'today',
  time: 'now',
  sort: 'order'
};

// ─── Helpers ─────────────────────────────────
function todayKey() {
  return DAY_KEYS[new Date().getDay()];
}
function resolvedDay() {
  return activeFilters.day === 'today' ? todayKey() : activeFilters.day;
}
function resolvedTimeMinutes() {
  if (activeFilters.time === 'now') {
    const n = new Date();
    return n.getHours() * 60 + n.getMinutes();
  }
  const [h, m] = activeFilters.time.split(':').map(Number);
  return h * 60 + (m || 0);
}
function parseMins(str) {
  if (!str) return 0;
  const [h, m] = str.split(':').map(Number);
  return h * 60 + (m || 0);
}

function isPoolOpen(pool, day, timeMins) {
  const oh = pool.open_hours;
  if (!oh) return true;
  const dayHours = oh[day];
  if (!dayHours) return true;
  const [openStr, closeStr] = dayHours;
  return timeMins >= parseMins(openStr) && timeMins < parseMins(closeStr);
}

function getSlotAtTime(poolLaneData, day, timeMins, poolKey) {
  if (!poolLaneData) return null;
  const key = poolKey || Object.keys(poolLaneData)[0];
  if (!key) return null;
  const poolInfo = poolLaneData[key];
  const sched = poolInfo.schedule?.[day] || [];
  for (const slot of sched) {
    if (timeMins >= parseMins(slot.from) && timeMins < parseMins(slot.to)) {
      return { ...slot, total_lanes: poolInfo.total_lanes, pool_name: poolInfo.name };
    }
  }
  return null;
}

// ─── Init ────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadData();
  setupFilters();
  renderPins();
  renderCards();
  setupModal();
});

async function loadData() {
  try {
    // ← ZMĚNA: přidán petynka_live.json paralelně
    const [poolsRes, lanesRes, petynkaRes] = await Promise.all([
      fetch('data/pools.json'),
      fetch('data/lanes.json'),
      fetch('data/petynka_live.json').catch(() => null)
    ]);
    allPools = await poolsRes.json();
    const lanes = await lanesRes.json();
    lanesData = lanes.pools || {};
    petynkaLive = petynkaRes ? await petynkaRes.json().catch(() => ({})) : {};

    if (lanes.updated_at) {
      const d = new Date(lanes.updated_at);
      const diff = Math.floor((Date.now() - d) / 3600000);
      const txt = diff < 2 ? 'Právě aktualizováno' :
                  diff < 24 ? `Aktualizováno před ${diff} h` :
                  `Aktualizováno ${d.toLocaleDateString('cs-CZ')}`;
      document.getElementById('updated-text').textContent = txt;
    }
  } catch (e) {
    console.warn('Chyba při načítání dat:', e);
    document.getElementById('pool-grid').innerHTML =
      '<p class="no-results">Nepodařilo se načíst data. Zkus obnovit stránku.</p>';
  }
}

// ─── Filters ─────────────────────────────────
function setupFilters() {
  document.getElementById('search-input').addEventListener('input', e => {
    activeFilters.search = e.target.value.toLowerCase();
    renderCards();
  });

  ['filter-length','filter-multi'].forEach(id => {
    document.getElementById(id).addEventListener('click', e => {
      if (!e.target.matches('.toggle')) return;
      document.querySelectorAll(`#${id} .toggle`).forEach(b => b.classList.remove('active'));
      e.target.classList.add('active');
      activeFilters[id === 'filter-length' ? 'length' : 'multi'] = e.target.dataset.val;
      renderCards();
    });
  });

  document.getElementById('filter-day').addEventListener('click', e => {
    if (!e.target.matches('.toggle')) return;
    document.querySelectorAll('#filter-day .toggle').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    activeFilters.day = e.target.dataset.val;
    refreshLanesOnly();
    if (activeFilters.sort === 'free') renderCards();
  });

  document.getElementById('time-select').addEventListener('change', e => {
    activeFilters.time = e.target.value;
    refreshLanesOnly();
    if (activeFilters.sort === 'free') renderCards();
  });

  document.getElementById('sort-select').addEventListener('change', e => {
    activeFilters.sort = e.target.value;
    renderCards();
  });
}

function refreshLanesOnly() {
  const day = resolvedDay();
  const timeMins = resolvedTimeMinutes();
  const timeLabel = activeFilters.time === 'now' ? 'Právě teď' : activeFilters.time;

  document.querySelectorAll('.pool-card').forEach(card => {
    const pool = allPools.find(p => p.id === card.dataset.poolId);
    if (!pool) return;

    const summaryEl = card.querySelector('.lanes-summary');
    if (!summaryEl) return;

    summaryEl.style.opacity = '0';
    summaryEl.style.transform = 'translateY(4px)';

    setTimeout(() => {
      summaryEl.innerHTML = `
        <span class="lanes-time-label">${timeLabel}:</span>
        ${buildLanesSummary(pool, day, timeMins)}
      `;
      summaryEl.style.transition = 'opacity 0.22s ease, transform 0.22s ease';
      summaryEl.style.opacity = '1';
      summaryEl.style.transform = 'translateY(0)';
    }, 80);
  });
}

function filterAndSort(pools) {
  const day = resolvedDay();
  const timeMins = resolvedTimeMinutes();
  let result = [...pools];

  if (activeFilters.search) {
    result = result.filter(p =>
      p.name.toLowerCase().includes(activeFilters.search) ||
      p.district.toLowerCase().includes(activeFilters.search) ||
      p.address.toLowerCase().includes(activeFilters.search)
    );
  }
  if (activeFilters.length !== 'all') {
    const len = parseInt(activeFilters.length);
    result = result.filter(p => p.pools.some(pool => pool.length === len));
  }
  if (activeFilters.multi === 'yes') {
    result = result.filter(p => p.multisport);
  }

  result.sort((a, b) => {
    if (activeFilters.sort === 'name')     return a.name.localeCompare(b.name, 'cs');
    if (activeFilters.sort === 'district') return a.district.localeCompare(b.district, 'cs');
    if (activeFilters.sort === 'price') {
      return (a.pricing[0]?.price ?? 999) - (b.pricing[0]?.price ?? 999);
    }
    if (activeFilters.sort === 'free') {
      const fa = getSlotAtTime(lanesData[a.id], day, timeMins)?.free_lanes?.length ?? -1;
      const fb = getSlotAtTime(lanesData[b.id], day, timeMins)?.free_lanes?.length ?? -1;
      return fb - fa;
    }
    return (a.order ?? 99) - (b.order ?? 99);
  });

  return result;
}

// ─── Render cards ────────────────────────────
function renderCards() {
  const grid = document.getElementById('pool-grid');
  const filtered = filterAndSort(allPools);
  const day = resolvedDay();
  const timeMins = resolvedTimeMinutes();

  const indoor   = filtered.filter(p => !p.seasonal);
  const seasonal = filtered.filter(p => p.seasonal);

  const total = filtered.length;
  document.getElementById('results-count').textContent =
    `${total} ${total === 1 ? 'bazén' : total < 5 ? 'bazény' : 'bazénů'}`;

  if (!total) {
    grid.innerHTML = '<p class="no-results">🏊 Žádný bazén neodpovídá filtru.</p>';
    return;
  }

  let html = indoor.map((pool, i) => cardHTML(pool, i, day, timeMins)).join('');

  if (seasonal.length) {
    html += `<div class="section-divider"><span>☀ Sezónní koupaliště</span></div>`;
    html += seasonal.map((pool, i) => cardHTML(pool, indoor.length + i, day, timeMins)).join('');
  }

  grid.innerHTML = html;

  grid.querySelectorAll('.pool-card').forEach(card => {
    card.addEventListener('click', e => {
      if (e.target.tagName === 'A') return;
      const pool = allPools.find(p => p.id === card.dataset.poolId);
      openModal(pool);
    });
  });
}

function cardHTML(pool, idx, day, timeMins) {
  const minPrice = pool.pricing.length ? Math.min(...pool.pricing.map(p => p.price)) : null;

  const poolChips = pool.pools.map(p =>
    `<span class="pool-chip${p.type === 'outdoor' ? ' outdoor' : ''}">` +
    `${p.length ? p.length + 'm' : ''}${p.seasonal ? ' ☀' : ''}</span>`
  ).join('');

  const weekdayHours = getOpenHoursForDay(pool, day);
  const lanesSummary = buildLanesSummary(pool, day, timeMins);
  const timeLabel = activeFilters.time === 'now' ? 'Právě teď' : activeFilters.time;

  return `
    <article class="pool-card" data-pool-id="${pool.id}"
      style="animation-delay:${idx * 0.045}s" tabindex="0" role="button" aria-label="${pool.name}">
      <div class="card-top${pool.multisport ? '' : ' grey'}"></div>
      <div class="card-body">
        <div class="card-header">
          <h2 class="card-name">${pool.name}</h2>
          ${pool.multisport ? '<span class="badge-multi">💳 Multisport</span>' : ''}
        </div>
        <p class="card-address">📍 ${pool.address}</p>
        <div class="card-pools">${poolChips}</div>
        <div class="card-meta">
          <span>🕐 ${weekdayHours}</span>
          ${minPrice ? `<span>💰 od ${minPrice} Kč / hod</span>` : ''}
        </div>
      </div>
      <div class="lanes-summary">
        <span class="lanes-time-label">${timeLabel}:</span>
        ${lanesSummary}
      </div>
      <div class="card-footer">
        <button class="btn btn-primary">Detail</button>
      </div>
    </article>`;
}

function getOpenHoursForDay(pool, day) {
  const oh = pool.open_hours;
  if (oh && oh[day]) return oh[day].join('–');
  const ohText = pool.opening_hours;
  const isWeekend = day === 'so' || day === 'ne';
  return (isWeekend ? ohText.weekend : ohText.weekday) || ohText.note || '—';
}

function buildLanesSummary(pool, day, timeMins) {
  if (!isPoolOpen(pool, day, timeMins)) {
    return `<span class="lane-count closed">zavřeno</span>`;
  }

  const poolLaneData = lanesData[pool.id];
  if (!poolLaneData) {
    return `<span class="lane-count no-data">data nejsou k dispozici</span>`;
  }

  const poolKeys = Object.keys(poolLaneData);

  if (poolKeys.length === 1) {
    const slot = getSlotAtTime(poolLaneData, day, timeMins);
    if (!slot) return `<span class="lane-count closed">zavřeno</span>`;
    const freeCnt = slot.free_lanes?.length ?? 0;
    const resCnt  = slot.reserved_lanes?.length ?? 0;
    return `
      <span class="lane-count free"><span class="num">${freeCnt}</span> volných</span>
      ${resCnt ? `<span class="lane-count reserved"><span class="num">${resCnt}</span> rezerv.</span>` : ''}
    `;
  }

  return poolKeys.map(key => {
    const poolInfo = poolLaneData[key];
    const slot = getSlotAtTime(poolLaneData, day, timeMins, key);
    const name = poolInfo.name;
    const seasonal = poolInfo.seasonal ? ' ☀' : '';

    if (!slot) {
      return `<div class="lane-row">
        <span class="lane-row-name">${name}${seasonal}</span>
        <span class="lane-count closed">zavřeno</span>
      </div>`;
    }

    const freeCnt = slot.free_lanes?.length ?? 0;
    const resCnt  = slot.reserved_lanes?.length ?? 0;
    return `<div class="lane-row">
      <span class="lane-row-name">${name}${seasonal}</span>
      <span class="lane-count free"><span class="num">${freeCnt}</span> vol.</span>
      ${resCnt ? `<span class="lane-count reserved"><span class="num">${resCnt}</span> rez.</span>` : ''}
    </div>`;
  }).join('');
}

// ─── Leaflet Map ─────────────────────────────
let leafletMap = null;

function renderPins() {
  const container = document.getElementById('map-container');
  const pinsEl = document.getElementById('map-pins');
  if (pinsEl) pinsEl.remove();

  leafletMap = L.map('map-container', { zoomControl: true, scrollWheelZoom: false })
    .setView([50.075, 14.44], 12);

  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19
  }).addTo(leafletMap);

  const icon = (multisport) => L.divIcon({
    className: '',
    html: `<div style="
      width:14px;height:14px;
      background:${multisport ? '#1db954' : '#00b4d8'};
      border:2.5px solid #0d0d0d;
      border-radius:50%;
      box-shadow:2px 2px 0 #0d0d0d;
    "></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });

  allPools.forEach(pool => {
    const marker = L.marker([pool.lat, pool.lng], { icon: icon(pool.multisport) })
      .addTo(leafletMap);

    const minPrice = pool.pricing.length ? Math.min(...pool.pricing.map(p => p.price)) : null;
    marker.bindPopup(`
      <div style="font-family:'DM Sans',sans-serif;min-width:180px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:1.1rem;letter-spacing:.04em;margin-bottom:4px;">${pool.name}</div>
        <div style="font-size:.78rem;color:#666;margin-bottom:8px;">📍 ${pool.address}</div>
        ${pool.multisport ? '<div style="font-size:.72rem;font-weight:700;background:#c8ffd4;border:1.5px solid #0d0d0d;padding:2px 8px;display:inline-block;margin-bottom:8px;">💳 MULTISPORT</div>' : ''}
        ${minPrice ? `<div style="font-size:.8rem;font-weight:700;">od ${minPrice} Kč / hod</div>` : ''}
        <button onclick="window.__openPoolModal('${pool.id}')" style="
          margin-top:10px;width:100%;padding:7px;
          background:#00b4d8;border:2px solid #0d0d0d;
          font-family:'DM Sans',sans-serif;font-size:.78rem;
          font-weight:700;text-transform:uppercase;letter-spacing:.06em;
          cursor:pointer;
        ">Detail</button>
      </div>
    `, { maxWidth: 220 });
  });

  window.__openPoolModal = (id) => {
    const pool = allPools.find(p => p.id === id);
    if (pool) openModal(pool);
  };
}

// ─── Modal ───────────────────────────────────
function setupModal() {
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target.id === 'modal-overlay') closeModal();
  });
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
}

function openModal(pool) {
  document.getElementById('modal-content').innerHTML = buildModalHTML(pool);

  const poolLaneData = lanesData[pool.id];
  const poolKeys = poolLaneData ? Object.keys(poolLaneData) : [];
  const today = todayKey();
  let activePoolKey = poolKeys[0] || null;
  let activeDay = today;

  function refresh() {
    const el = document.getElementById('modal-schedule-rows');
    if (el) {
      el.style.opacity = '0';
      setTimeout(() => {
        el.innerHTML = buildScheduleHTML(pool, activeDay, activePoolKey);
        el.style.transition = 'opacity 0.18s';
        el.style.opacity = '1';
      }, 80);
    }
  }

  document.querySelectorAll('.pool-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.pool-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      activePoolKey = tab.dataset.poolKey;
      refresh();
    });
  });

  document.querySelectorAll('.day-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.day-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      activeDay = tab.dataset.day;
      refresh();
    });
  });

  const overlay = document.getElementById('modal-overlay');
  overlay.setAttribute('aria-hidden', 'false');
  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  const overlay = document.getElementById('modal-overlay');
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}

function buildModalHTML(pool) {
  const pricingRows = pool.pricing.map(p =>
    `<tr><td>${p.label}</td><td>${p.price} Kč</td></tr>`).join('');

  const poolsRows = pool.pools.map(p => `
    <div class="modal-pool-row">
      <span class="modal-pool-name">${p.name}</span>
      <span class="modal-pool-info">
        ${p.lanes ? p.lanes + ' drah · ' : ''}${p.type === 'outdoor' ? 'venkovní' : 'krytý'}${p.seasonal ? ' · sezónní ☀' : ''}
      </span>
    </div>`).join('');

  const amenities = pool.amenities.map(a => `<span class="amenity-tag">${a}</span>`).join('');
  const hoursHtml = formatHours(pool.opening_hours);

  const laneSection = buildModalLaneSection(pool);

  return `
    <h2 class="modal-title">${pool.name}</h2>
    <p class="modal-address">📍 ${pool.address} · ${pool.district}</p>

    ${pool.multisport ? `
      <div class="modal-multi-banner">
        <span>💳</span>
        <div><strong>Multisport přijímán</strong><br/>${pool.multisport_note || ''}</div>
      </div>` : ''}

    <div class="modal-section">
      <h4>Bazény</h4>
      <div class="modal-pools-list">${poolsRows}</div>
    </div>

    ${laneSection}

    <div class="modal-section">
      <h4>Otevírací doba</h4>
      <div style="font-size:.9rem;line-height:1.9;">${hoursHtml}</div>
    </div>

    <div class="modal-section">
      <h4>Ceník</h4>
      <table class="modal-pricing-table">${pricingRows}</table>
    </div>

    ${amenities ? `<div class="modal-section">
      <h4>Vybavení</h4>
      <div class="modal-amenities">${amenities}</div>
    </div>` : ''}

    ${pool.phone ? `<div class="modal-section">
      <h4>Kontakt</h4>
      <p style="font-size:.88rem;color:var(--muted)">📞 ${pool.phone}</p>
    </div>` : ''}

    ${pool.id === 'petynka' ? buildPetynkaLiveHTML(petynkaLive) : ''}

    ${pool.website ? `<a class="modal-website-btn" href="${pool.website}" target="_blank" rel="noopener">Přejít na web bazénu ↗</a>` : ''}
  `;
}

function buildModalLaneSection(pool) {
  const poolLaneData = lanesData[pool.id];
  if (!poolLaneData) return '';

  const poolKeys = Object.keys(poolLaneData);
  const today = todayKey();
  const days = ['po','ut','st','ct','pa','so','ne'];
  const dayNames = { po:'Po', ut:'Út', st:'St', ct:'Čt', pa:'Pá', so:'So', ne:'Ne' };

  const poolTabsHTML = poolKeys.length > 1
    ? `<div class="pool-tabs">
        ${poolKeys.map((key, i) => {
          const info = poolLaneData[key];
          return `<button class="pool-tab${i === 0 ? ' active' : ''}" data-pool-key="${key}">${info.name}${info.seasonal ? ' ☀' : ''}</button>`;
        }).join('')}
      </div>`
    : '';

  const dayTabsHTML = `<div class="day-tabs">
    ${days.map(d =>
      `<button class="day-tab${d === today ? ' active' : ''}" data-day="${d}">${dayNames[d]}</button>`
    ).join('')}
  </div>`;

  const scheduleHTML = buildScheduleHTML(pool, today, poolKeys[0]);

  return `
    <div class="modal-section">
      <h4>Rozvrh drah</h4>
      ${poolTabsHTML}
      ${dayTabsHTML}
      <div class="modal-schedule" id="modal-schedule-rows">${scheduleHTML}</div>
      <p style="font-size:.73rem;color:var(--muted);margin-top:8px">
        Data stahována automaticky každou noc
      </p>
    </div>`;
}

function buildScheduleHTML(pool, day, poolKey) {
  const poolLaneData = lanesData[pool.id];
  if (!poolLaneData) return '<p class="lanes-no-data">Data nejsou k dispozici.</p>';

  const key = poolKey || Object.keys(poolLaneData)[0];
  const poolInfo = poolLaneData[key];
  let slots = poolInfo?.schedule?.[day] || [];

  if (!slots.length) return '<p class="lanes-no-data">Pro tento den není rozvrh k dispozici.</p>';

  const oh = pool.open_hours?.[day];
  if (oh) {
    const openMins  = parseMins(oh[0]);
    const closeMins = parseMins(oh[1]);
    slots = slots.filter(s => parseMins(s.to) > openMins && parseMins(s.from) < closeMins);
    if (slots.length) {
      slots = slots.map((s, i) => {
        let from = s.from, to = s.to;
        if (i === 0 && parseMins(from) < openMins)  from = oh[0];
        if (i === slots.length - 1 && parseMins(to) > closeMins) to = oh[1];
        return { ...s, from, to };
      });
    }
  }

  if (!slots.length) return '<p class="lanes-no-data">V tento den zavřeno.</p>';

  const nowMins = new Date().getHours() * 60 + new Date().getMinutes();
  const isToday = day === todayKey();

  return slots.map(slot => {
    const isNow = isToday && nowMins >= parseMins(slot.from) && nowMins < parseMins(slot.to);
    const total = poolInfo.total_lanes || 8;
    const freeSet = new Set(slot.free_lanes || []);
    const resSet  = new Set(slot.reserved_lanes || []);

    const laneBubbles = Array.from({length: total}, (_, i) => {
      const n = i + 1;
      const cls = freeSet.has(n) ? 'free' : resSet.has(n) ? 'reserved' : 'free';
      return `<span class="sch-lane ${cls}" title="Dráha ${n}">${n}</span>`;
    }).join('');

    const freeCnt = slot.free_lanes?.length ?? 0;
    const resCnt  = slot.reserved_lanes?.length ?? 0;
    const countsTxt = resCnt
      ? `<span class="sch-counts">${freeCnt} vol. · ${resCnt} rez.</span>`
      : `<span class="sch-counts">${freeCnt} volných</span>`;

    return `
      <div class="schedule-row${isNow ? ' highlight' : ''}">
        <span class="schedule-time">${slot.from}–${slot.to}${isNow ? ' ◀' : ''}</span>
        <span class="sch-type ${slot.type}">${slot.type}</span>
        <div class="sch-lanes">${laneBubbles}</div>
        ${countsTxt}
      </div>`;
  }).join('');
}

function formatHours(oh) {
  const parts = [];
  if (oh.weekday)  parts.push(`<strong>Po–Pá:</strong> ${oh.weekday}`);
  if (oh.weekend)  parts.push(`<strong>So–Ne:</strong> ${oh.weekend}`);
  if (oh.mon)      parts.push(`<strong>Po:</strong> ${oh.mon}`);
  if (oh.tue)      parts.push(`<strong>Út:</strong> ${oh.tue}`);
  if (oh.wed_thu)  parts.push(`<strong>St–Čt:</strong> ${oh.wed_thu}`);
  if (oh.fri)      parts.push(`<strong>Pá:</strong> ${oh.fri}`);
  if (oh.sat)      parts.push(`<strong>So:</strong> ${oh.sat}`);
  if (oh.sun)      parts.push(`<strong>Ne:</strong> ${oh.sun}`);
  if (oh.note)     parts.push(`<em>${oh.note}</em>`);
  return parts.join('<br/>') || '—';
}

// ─── Petynka živá data ───────────────────────
function buildPetynkaLiveHTML(live) {
  const webcamBtn = `
    <a href="https://koupalistepetynka.cz/foto-a-kamery/bazen"
       target="_blank" rel="noopener" class="btn-webcam">
      📷 Webkamera – bazén živě
    </a>`;

  if (!live || !live.open) {
    return `
      <div class="modal-section">
        <h4>Aktuální stav</h4>
        <div class="petynka-live petynka-live--closed">
          🚫 Koupaliště je mimo sezónu – živá data nejsou k dispozici.
        </div>
        ${webcamBtn}
      </div>`;
  }

  const occ = (live.visitors != null && live.visitors_max)
    ? Math.round((live.visitors / live.visitors_max) * 100) : null;

  const tiles = [
    live.water_temp != null && {
      icon: '🌊', value: `${live.water_temp} °C`,
      label: 'Teplota vody', warn: live.water_temp < 18
    },
    live.air_temp != null && {
      icon: '☀️', value: `${live.air_temp} °C`,
      label: 'Teplota vzduchu', warn: false
    },
    live.visitors != null && {
      icon: '🏊',
      value: live.visitors_max ? `${live.visitors}/${live.visitors_max}` : live.visitors,
      label: occ != null ? `Obsazenost ${occ} %` : 'Návštěvníků',
      warn: occ != null && occ >= 90
    },
    live.parking_free != null && {
      icon: '🅿️', value: live.parking_free,
      label: 'Volných parkovacích míst',
      warn: live.parking_free === 0
    },
  ].filter(Boolean);

  const tilesHTML = tiles.map(t => `
    <div class="petynka-tile${t.warn ? ' petynka-tile--warn' : ''}">
      <span class="petynka-tile__icon">${t.icon}</span>
      <span class="petynka-tile__value">${t.value}</span>
      <span class="petynka-tile__label">${t.label}</span>
    </div>`).join('');

  const updated = live.updated_at
    ? new Date(live.updated_at).toLocaleString('cs-CZ', {
        day:'2-digit', month:'2-digit', year:'numeric',
        hour:'2-digit', minute:'2-digit'
      })
    : null;

  return `
    <div class="modal-section">
      <h4>Aktuální stav</h4>
      <div class="petynka-live">
        <div class="petynka-live__title">
          <span class="live-dot"></span> Živá data
        </div>
        <div class="petynka-live__tiles">${tilesHTML}</div>
        ${updated ? `<p class="petynka-live__updated">Aktualizováno: ${updated}</p>` : ''}
      </div>
      ${webcamBtn}
    </div>`;
}