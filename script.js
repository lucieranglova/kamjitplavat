/* =============================================
   Bazény Praha — script.js
   ============================================= */

// Prague map bounds for pin positioning (rough bounding box)
const MAP_BOUNDS = {
  latMin: 49.98, latMax: 50.18,
  lngMin: 14.30, lngMax: 14.65
};

function latToY(lat) {
  return ((MAP_BOUNDS.latMax - lat) / (MAP_BOUNDS.latMax - MAP_BOUNDS.latMin)) * 88 + 6;
}
function lngToX(lng) {
  return ((lng - MAP_BOUNDS.lngMin) / (MAP_BOUNDS.lngMax - MAP_BOUNDS.lngMin)) * 88 + 6;
}

// ─── State ──────────────────────────────────
let allPools = [];
let lanesData = {};
let activeFilters = { search: '', length: 'all', multi: 'all', sort: 'name' };

// ─── Init ────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadData();
  setupFilters();
  renderPins();
  renderCards();
  setupModal();
  updateTimestamp();
});

async function loadData() {
  try {
    const [poolsRes, lanesRes] = await Promise.all([
      fetch('data/pools.json'),
      fetch('data/lanes.json')
    ]);
    allPools = await poolsRes.json();
    const lanes = await lanesRes.json();
    lanesData = lanes.pools || {};
    updateUpdatedBadge(lanes.updated_at);
  } catch (e) {
    console.warn('Chyba při načítání dat:', e);
    document.getElementById('pool-grid').innerHTML =
      '<p class="no-results">Nepodařilo se načíst data. Zkus to znovu.</p>';
  }
}

function updateUpdatedBadge(dateStr) {
  if (!dateStr) return;
  const d = new Date(dateStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 3600000);
  const badge = document.getElementById('updated-badge');
  if (badge) {
    const txt = diff < 2 ? 'Právě aktualizováno' :
                diff < 24 ? `Aktualizováno před ${diff} h` :
                `Aktualizováno ${d.toLocaleDateString('cs-CZ')}`;
    badge.querySelector('span:last-child')?.remove();
    const s = document.createElement('span');
    s.textContent = txt;
    badge.appendChild(s);
  }
}

function updateTimestamp() {
  const el = document.getElementById('updated-badge');
  if (el) {
    const spans = el.querySelectorAll('span');
    if (spans.length < 2) {
      const s = document.createElement('span');
      s.textContent = 'Aktualizováno každou noc';
      el.appendChild(s);
    }
  }
}

// ─── Filters ─────────────────────────────────
function setupFilters() {
  document.getElementById('search-input').addEventListener('input', e => {
    activeFilters.search = e.target.value.toLowerCase();
    renderCards();
  });

  document.getElementById('filter-length').addEventListener('click', e => {
    if (!e.target.matches('.toggle')) return;
    document.querySelectorAll('#filter-length .toggle').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    activeFilters.length = e.target.dataset.val;
    renderCards();
  });

  document.getElementById('filter-multi').addEventListener('click', e => {
    if (!e.target.matches('.toggle')) return;
    document.querySelectorAll('#filter-multi .toggle').forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    activeFilters.multi = e.target.dataset.val;
    renderCards();
  });

  document.getElementById('sort-select').addEventListener('change', e => {
    activeFilters.sort = e.target.value;
    renderCards();
  });
}

function filterAndSort(pools) {
  let result = [...pools];

  // Search
  if (activeFilters.search) {
    result = result.filter(p =>
      p.name.toLowerCase().includes(activeFilters.search) ||
      p.district.toLowerCase().includes(activeFilters.search) ||
      p.address.toLowerCase().includes(activeFilters.search)
    );
  }

  // Length
  if (activeFilters.length !== 'all') {
    const len = parseInt(activeFilters.length);
    result = result.filter(p => p.pools.some(pool => pool.length === len));
  }

  // Multisport
  if (activeFilters.multi === 'yes') {
    result = result.filter(p => p.multisport);
  }

  // Sort
  result.sort((a, b) => {
    if (activeFilters.sort === 'name') return a.name.localeCompare(b.name, 'cs');
    if (activeFilters.sort === 'price') {
      const pa = a.pricing[0]?.price ?? 999;
      const pb = b.pricing[0]?.price ?? 999;
      return pa - pb;
    }
    if (activeFilters.sort === 'district') return a.district.localeCompare(b.district, 'cs');
    return 0;
  });

  return result;
}

// ─── Render cards ────────────────────────────
function renderCards() {
  const grid = document.getElementById('pool-grid');
  const filtered = filterAndSort(allPools);

  document.getElementById('results-count').textContent =
    `${filtered.length} ${filtered.length === 1 ? 'bazén' : filtered.length < 5 ? 'bazény' : 'bazénů'}`;

  if (filtered.length === 0) {
    grid.innerHTML = '<p class="no-results">Žádný bazén neodpovídá filtru.</p>';
    return;
  }

  grid.innerHTML = filtered.map((pool, i) => cardHTML(pool, i)).join('');

  // Attach click handlers
  grid.querySelectorAll('.pool-card').forEach(card => {
    card.addEventListener('click', e => {
      if (e.target.matches('a')) return; // Don't open modal for links
      const id = card.dataset.poolId;
      const pool = allPools.find(p => p.id === id);
      openModal(pool);
    });
  });
}

function cardHTML(pool, idx) {
  const minPrice = pool.pricing.length ? Math.min(...pool.pricing.map(p => p.price)) : null;
  const poolChips = pool.pools.map(p =>
    `<span class="pool-chip${p.type === 'outdoor' ? ' outdoor' : ''}">
      ${p.length ? p.length + 'm' : ''} ${p.name}${p.seasonal ? ' ☀' : ''}
     </span>`
  ).join('');

  const weekdayHours = pool.opening_hours.weekday || pool.opening_hours.note || '—';

  // Lanes for this pool
  const lanesHTML = buildLanesStrip(pool);

  return `
    <article class="pool-card" data-pool-id="${pool.id}" style="animation-delay:${idx * 0.05}s" tabindex="0" role="button" aria-label="${pool.name}">
      <div class="card-stripe${pool.multisport ? '' : ' no-multi'}"></div>
      <div class="card-body">
        <div class="card-header">
          <h2 class="card-name">${pool.name}</h2>
          ${pool.multisport ? '<span class="badge-multi">💳 Multisport</span>' : ''}
        </div>
        <p class="card-address">📍 ${pool.address}</p>
        <div class="card-pools">${poolChips}</div>
        <p class="card-hours"><span class="icon">🕐</span> ${weekdayHours}</p>
        ${minPrice ? `<p class="card-price">od <span class="price-value">${minPrice} Kč</span> / 60 min</p>` : ''}
      </div>
      ${lanesHTML}
      <div class="card-footer">
        <button class="btn btn-primary">Detail</button>
        ${pool.website ? `<a class="btn btn-secondary" href="${pool.website}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Web ↗</a>` : ''}
      </div>
    </article>
  `;
}

function buildLanesStrip(pool) {
  const data = lanesData[pool.id];
  if (!data) {
    return `
      <div class="lanes-strip">
        <span class="lanes-label">Dráhy dnes</span>
        <span style="font-size:0.78rem;color:var(--text-light)">data nejsou k dispozici</span>
      </div>`;
  }

  // Get first pool's schedule
  const key = Object.keys(data)[0];
  const schedule = data[key]?.schedule || [];

  // Find current block
  const now = new Date();
  const hm = now.getHours() * 60 + now.getMinutes();

  function parseMins(str) {
    const [h, m] = str.split(':').map(Number);
    return h * 60 + (m || 0);
  }

  let currentBlock = null;
  for (const block of schedule) {
    const [start, end] = block.time.split('–');
    if (hm >= parseMins(start) && hm < parseMins(end)) {
      currentBlock = block;
      break;
    }
  }

  const typeColors = { volno: '#00c853', klub: '#e53935', kurzy: '#7e57c2' };
  const typeLabels = { volno: 'Volné plavání', klub: 'Rezervováno klubem', kurzy: 'Plavecké kurzy' };

  if (currentBlock) {
    const color = typeColors[currentBlock.type] || '#cfd8dc';
    return `
      <div class="lanes-strip">
        <span class="lanes-label">Právě teď</span>
        <span style="font-size:0.78rem;font-weight:600;color:${color}">● ${typeLabels[currentBlock.type] || currentBlock.type}</span>
      </div>`;
  }

  return `
    <div class="lanes-strip">
      <span class="lanes-label">Dráhy dnes</span>
      <span style="font-size:0.78rem;color:var(--text-light)">mimo provoz</span>
    </div>`;
}

// ─── Map pins ────────────────────────────────
function renderPins() {
  const container = document.getElementById('map-pins');
  if (!container) return;

  allPools.forEach(pool => {
    const x = lngToX(pool.lng);
    const y = latToY(pool.lat);
    const pin = document.createElement('div');
    pin.className = 'map-pin';
    pin.style.left = x + '%';
    pin.style.top = y + '%';
    pin.innerHTML = `
      <div class="pin-dot${pool.multisport ? ' multi' : ''}"></div>
      <div class="pin-label">${pool.name.split(' ').slice(-1)[0]}</div>
    `;
    pin.addEventListener('click', () => openModal(pool));
    pin.title = pool.name;
    container.appendChild(pin);
  });
}

// ─── Modal ───────────────────────────────────
function setupModal() {
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target === document.getElementById('modal-overlay')) closeModal();
  });
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
  });
}

function openModal(pool) {
  const content = document.getElementById('modal-content');
  content.innerHTML = buildModalHTML(pool);
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
  const lanesSection = buildModalLanes(pool);
  const pricingRows = pool.pricing.map(p =>
    `<tr><td>${p.label}</td><td>${p.price} Kč</td></tr>`
  ).join('');

  const poolsRows = pool.pools.map(p => `
    <div class="modal-pool-row">
      <span class="modal-pool-name">${p.name}</span>
      <span class="modal-pool-info">
        ${p.lanes ? p.lanes + ' drah' : ''}
        ${p.type === 'outdoor' ? '· venkovní' : '· krytý'}
        ${p.seasonal ? '· sezónní' : ''}
      </span>
    </div>
  `).join('');

  const amenitiesHTML = pool.amenities.map(a =>
    `<span class="amenity-tag">${a}</span>`
  ).join('');

  const hoursDisplay = formatHours(pool.opening_hours);

  return `
    <div class="modal-header-stripe"></div>
    <div style="padding:0 32px 32px">
      <h2 class="modal-title">${pool.name}</h2>
      <p class="modal-address">📍 ${pool.address} · ${pool.district}</p>

      ${pool.multisport ? `
        <div class="modal-multi-banner">
          <span>💳</span>
          <div><strong>Multisport přijímán</strong><br/>${pool.multisport_note || ''}</div>
        </div>` : ''}

      <div class="modal-section" style="margin-top:20px">
        <h4>Bazény</h4>
        <div class="modal-pools-list">${poolsRows}</div>
      </div>

      ${lanesSection}

      <div class="modal-section">
        <h4>Otevírací doba</h4>
        <div style="font-size:0.9rem; line-height:1.8;">${hoursDisplay}</div>
      </div>

      <div class="modal-section">
        <h4>Ceník</h4>
        <table class="modal-pricing-table">
          ${pricingRows}
        </table>
      </div>

      ${amenitiesHTML ? `
        <div class="modal-section">
          <h4>Vybavení</h4>
          <div class="modal-amenities">${amenitiesHTML}</div>
        </div>` : ''}

      <div class="modal-section">
        <h4>Kontakt</h4>
        <p style="font-size:0.88rem;color:var(--text-muted)">
          ${pool.phone ? '📞 ' + pool.phone : ''}
        </p>
      </div>

      ${pool.website ? `<a class="modal-website-btn" href="${pool.website}" target="_blank" rel="noopener">Přejít na web bazénu ↗</a>` : ''}
    </div>
  `;
}

function buildModalLanes(pool) {
  const data = lanesData[pool.id];
  if (!data) return '';

  const blocks = Object.values(data)
    .flatMap(p => (p.schedule || []).map(s => ({ ...s, pool: p.name })));

  if (!blocks.length) return '';

  const rows = blocks.map(b => `
    <div class="schedule-row">
      <span class="schedule-time">${b.time}</span>
      <span class="schedule-type type-${b.type}">${b.type}</span>
      <span class="schedule-note">${b.note || ''}</span>
    </div>
  `).join('');

  return `
    <div class="modal-section">
      <h4>Rozvrh drah — dnes</h4>
      <div class="modal-schedule">${rows}</div>
      <p style="font-size:0.75rem;color:var(--text-light);margin-top:8px">
        Data stahována automaticky každou noc
      </p>
    </div>
  `;
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