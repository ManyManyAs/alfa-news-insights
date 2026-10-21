// Глобус «Бизнес-радара» без библиотек: canvas 2D, ортографическая проекция.
// Точки суши — из globe-data.js; сигналы — события ленты: дуга от места события к компании,
// по дуге бежит импульс; цвет — эффект для компании (риск / возможность / отслеживать).
/* global GLOBE_LAND */

const GEO = {
  // регионы — как в данных кейса (поле REGION) и в классификаторе новостей; координаты — столица региона
  "Москва": [55.76, 37.62], "Санкт-Петербург": [59.94, 30.31], "Московская область": [55.95, 37.95],
  "Ленинградская область": [59.55, 30.1], "Новосибирская область": [55.03, 82.92], "Свердловская область": [56.84, 60.6],
  "Республика Татарстан": [55.79, 49.12], "Краснодарский край": [45.04, 38.98], "Челябинская область": [55.16, 61.4],
  "Нижегородская область": [56.33, 44.0], "Красноярский край": [56.01, 92.85], "Ростовская область": [47.23, 39.7],
  "Приморский край": [43.12, 131.89], "Самарская область": [53.2, 50.15], "Калининградская область": [54.71, 20.51],
  "Башкортостан (Республика)": [54.73, 55.95], "Воронежская область": [51.67, 39.18], "Тюменская область": [57.15, 65.53],
  "Пермский край": [58.01, 56.23], "Удмуртская Республика": [56.85, 53.2], "Алтайский край": [53.35, 83.78],
  "Хабаровский край": [48.48, 135.08], "Омская область": [54.99, 73.37], "Иркутская область": [52.29, 104.3],
  "Ярославская область": [57.63, 39.87], "Кемеровская область": [55.35, 86.09], "Ульяновская область": [54.32, 48.4],
  "Пензенская область": [53.2, 45.0], "Томская область": [56.48, 84.95], "Белгородская область": [50.6, 36.59],
  "Республика Крым": [44.95, 34.1], "Саратовская область": [51.53, 46.03], "Мурманская область": [68.97, 33.08],
  "Липецкая область": [52.61, 39.6], "Вологодская область": [59.22, 39.89], "Архангельская область": [64.54, 40.54],
  "Чувашская Республика-Чувашия": [56.13, 47.25], "Калужская область": [54.51, 36.26], "Тульская область": [54.19, 37.62],
  "Волгоградская область": [48.71, 44.51], "Тверская область": [56.86, 35.9], "Мордовия (Республика)": [54.18, 45.18],
  "Амурская область": [50.27, 127.53], "Рязанская область": [54.63, 39.74], "Оренбургская область": [51.77, 55.1],
  "Ивановская область": [57.0, 40.97], "Кировская область": [58.6, 49.66], "Саха (Республика) (Якутия)": [62.03, 129.73],
  "Сахалинская область": [46.96, 142.74], "Смоленская область": [54.78, 32.05], "Карелия (Республика)": [61.79, 34.36],
  "Ставропольский край": [45.04, 41.97], "Владимирская область": [56.13, 40.41], "Псковская область": [57.82, 28.33],
  "Брянская область": [53.24, 34.36], "Марий Эл (Республика)": [56.63, 47.89], "Тамбовская область": [52.72, 41.45],
  "Бурятия (Республика)": [51.83, 107.58], "Костромская область": [57.77, 40.93], "Магаданская область": [59.56, 150.8],
  "Курганская область": [55.44, 65.34], "Забайкальский край": [52.03, 113.5], "Новгородская область": [58.52, 31.27],
  "Камчатский край": [53.02, 158.65], "Хакасия (Республика)": [53.72, 91.44], "Калмыкия (Республика)": [46.31, 44.27],
  "Донецкая Народная Республика": [48.0, 37.8], "Орловская область": [52.97, 36.06], "Курская область": [51.73, 36.19],
  "Астраханская область": [46.35, 48.04], "Коми (Республика)": [61.67, 50.84], "Алтай (Республика)": [51.96, 85.96],
  "Дагестан (Республика)": [42.98, 47.5], "Чеченская Республика": [43.32, 45.69], "Федеральная территория «Сириус»": [43.41, 39.95],
  "Кабардино-Балкарская Республика": [43.49, 43.6], "Северная Осетия-Алания (Республика)": [43.02, 44.68],
  "Адыгея (Республика) (Адыгея)": [44.61, 40.1], "Луганская Народная Республика": [48.57, 39.33], "Тыва (Республика)": [51.72, 94.45],
  "Карачаево-Черкесская Республика": [44.23, 42.05], "Еврейская автономная область": [48.79, 132.92],
  // страны — партнёры ВЭД и страны из новостей (в верхнем регистре, как в данных ФТС)
  "КИТАЙ": [31.23, 121.47], "ТУРЦИЯ": [41.01, 28.97], "ИНДИЯ": [19.08, 72.88], "ЛИТВА": [54.69, 25.28], "ОАЭ": [25.2, 55.27],
  "ИТАЛИЯ": [45.46, 9.19], "УЗБЕКИСТАН": [41.3, 69.24], "ПОЛЬША": [52.23, 21.01], "ГОНКОНГ": [22.32, 114.17],
  "ЛАТВИЯ": [56.95, 24.1], "ГРУЗИЯ": [41.72, 44.79], "ЮЖНАЯ КОРЕЯ": [37.57, 126.98], "ГЕРМАНИЯ": [52.52, 13.4],
  "АЗЕРБАЙДЖАН": [40.41, 49.87], "США": [40.71, -74.0], "СЕРБИЯ": [44.79, 20.45], "ЧЕХИЯ": [50.08, 14.44],
  "ВЬЕТНАМ": [21.03, 105.85], "ТАЙВАНЬ": [25.03, 121.56], "АБХАЗИЯ": [43.0, 41.02], "ЕГИПЕТ": [30.04, 31.24],
  "ТАИЛАНД": [13.75, 100.5], "БЕЛАРУСЬ": [53.9, 27.56], "МОНГОЛИЯ": [47.92, 106.92], "ЯПОНИЯ": [35.68, 139.69],
  "ИСПАНИЯ": [40.42, -3.7], "ЭСТОНИЯ": [59.44, 24.75], "ЧИЛИ": [-33.45, -70.67], "БЕЛЬГИЯ": [50.85, 4.35],
  "ТАДЖИКИСТАН": [38.56, 68.79], "ФРАНЦИЯ": [48.86, 2.35], "ИЗРАИЛЬ": [32.08, 34.78], "МАЛАЙЗИЯ": [3.14, 101.69],
  "МОЛДОВА": [47.01, 28.86], "НИДЕРЛАНДЫ": [52.37, 4.9], "ПЕРУ": [-12.05, -77.04], "АВСТРИЯ": [48.21, 16.37],
  "ГРЕЦИЯ": [37.98, 23.73], "ОМАН": [23.59, 58.41], "КАЗАХСТАН": [43.24, 76.89], "ДАНИЯ": [55.68, 12.57],
  "КИРГИЗИЯ": [42.87, 74.59], "ФИНЛЯНДИЯ": [60.17, 24.94], "БОЛГАРИЯ": [42.7, 23.32], "ТУРКМЕНИСТАН": [37.96, 58.33],
  "БРАЗИЛИЯ": [-23.55, -46.63], "ШВЕЙЦАРИЯ": [46.95, 7.45], "ВЕНГРИЯ": [47.5, 19.04], "ИНДОНЕЗИЯ": [-6.2, 106.85],
  "ИРАК": [33.31, 44.36], "ТУНИС": [36.81, 10.18], "МАЛЬДИВЫ": [4.18, 73.51], "АРГЕНТИНА": [-34.6, -58.38],
  "КИПР": [35.17, 33.36], "СИНГАПУР": [1.35, 103.82], "БАНГЛАДЕШ": [23.81, 90.41], "АЛЖИР": [36.75, 3.06],
  "СЛОВЕНИЯ": [46.06, 14.51], "АРМЕНИЯ": [40.18, 44.51], "УРУГВАЙ": [-34.9, -56.16], "ИРАН": [35.69, 51.39],
  "ВЕЛИКОБРИТАНИЯ": [51.5, -0.13], "КАНАДА": [45.42, -75.7], "САУДОВСКАЯ АРАВИЯ": [24.71, 46.68],
};
const GEO_NAME = { "Москва": "Москва", "Санкт-Петербург": "Санкт-Петербург" };
function geoOf(name) {
  if (!name) return null;
  return GEO[name] || GEO[String(name).toUpperCase()] || null;
}
function geoShort(name) {
  if (GEO_NAME[name]) return GEO_NAME[name];
  const s = String(name).replace(/\s*\(Республика\).*$/, "").replace(/^Республика\s+/, "");
  return s.length > 2 && s === s.toUpperCase() ? s[0] + s.slice(1).toLowerCase() : s;
}

const RAD = Math.PI / 180;
const EFFECT_RGB = { risk: [255, 69, 58], opportunity: [48, 209, 120], mixed: [255, 179, 64], watch: [100, 168, 255] };

class BizGlobe {
  constructor(canvas, opts = {}) {
    this.cv = canvas;
    this.ctx = canvas.getContext("2d");
    this.opts = { ambient: false, onPick: null, tip: null, ...opts };
    this.lon = 70; this.tilt = opts.ambient ? 30 : 40; this.vel = opts.ambient ? 5 : 2.4;  // градусы, градусы/с
    this.dragging = false; this.idleT = 0; this.hover = null;
    this.signals = []; this.partners = []; this.hq = null;
    this.reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.land = [];
    for (let i = 0; i < GLOBE_LAND.length; i += 2) {
      const lat = GLOBE_LAND[i] / 10, l2 = GLOBE_LAND[i + 1];
      const rus = ((l2 % 2) + 2) % 2 === 1;
      const lon = (l2 - (rus ? 1 : 0)) / 20;
      this.land.push([Math.cos(lat * RAD), Math.sin(lat * RAD), lon * RAD, rus]);
    }
    this.t0 = performance.now(); this.last = this.t0;
    this.visible = true;
    this.resize();
    try { new ResizeObserver(() => this.resize()).observe(canvas); } catch (e) { addEventListener("resize", () => this.resize()); }
    try { new IntersectionObserver((es) => { this.visible = es[0].isIntersecting; if (this.visible) this.kick(); }).observe(canvas); } catch (e) { /* без наблюдателя */ }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) this.kick(); });
    this.bind();
    this.kick();
  }

  resize() {
    const r = this.cv.getBoundingClientRect();
    const dpr = Math.min(2, devicePixelRatio || 1);
    this.w = Math.max(10, r.width); this.h = Math.max(10, r.height); this.dpr = dpr;
    this.cv.width = Math.round(this.w * dpr); this.cv.height = Math.round(this.h * dpr);
    this.R = Math.min(this.w, this.h) * (this.opts.ambient ? 0.46 : 0.42);
    this.cx = this.w / 2; this.cy = this.h / 2 + (this.opts.ambient ? 0 : this.h * 0.01);
    this.draw(performance.now());
  }

  // точка на сфере (широта, долгота в градусах, высота над поверхностью в радиусах) → экран и глубина
  project(lat, lon, alt = 0) {
    const cl = Math.cos(lat * RAD), x0 = cl * Math.sin((lon - this.lon) * RAD), y0 = Math.sin(lat * RAD), z0 = cl * Math.cos((lon - this.lon) * RAD);
    const t = this.tilt * RAD, y = y0 * Math.cos(t) - z0 * Math.sin(t), z = y0 * Math.sin(t) + z0 * Math.cos(t);
    const k = this.R * (1 + alt);
    return [this.cx + x0 * k, this.cy - y * k, z];
  }

  setData({ hq, partners = [], signals = [] }) {
    this.hq = hq || null;
    this.partners = partners.filter((p) => p.geo);
    this.signals = signals.filter((s) => s.geo).map((s, i) => ({ ...s, phase: i * 0.37 % 1, arc: this.hq ? this.arcPoints(s.geo, this.hq.geo) : null }));
    const key = this.hq ? this.hq.geo.join(",") : "";
    if (this.hq && key !== this.hqKey) { this.lon = this.hq.geo[1] + 8; this.phase = 0; this.idleT = 3; this.hqKey = key; }
    this.kick();
  }

  // дуга большого круга, приподнятая пропорционально расстоянию
  arcPoints(a, b, n = 48) {
    const v = (p) => [Math.cos(p[0] * RAD) * Math.cos(p[1] * RAD), Math.cos(p[0] * RAD) * Math.sin(p[1] * RAD), Math.sin(p[0] * RAD)];
    const A = v(a), B = v(b);
    const d = Math.acos(Math.max(-1, Math.min(1, A[0] * B[0] + A[1] * B[1] + A[2] * B[2])));
    if (d < 0.02) return null;
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n, s1 = Math.sin((1 - t) * d) / Math.sin(d), s2 = Math.sin(t * d) / Math.sin(d);
      const x = s1 * A[0] + s2 * B[0], y = s1 * A[1] + s2 * B[1], z = s1 * A[2] + s2 * B[2];
      pts.push([Math.asin(z) / RAD, Math.atan2(y, x) / RAD, Math.sin(Math.PI * t) * Math.min(0.42, 0.08 + d * 0.32)]);
    }
    return pts;
  }

  bind() {
    const c = this.cv;
    let px = 0, py = 0, lastT = 0;
    c.addEventListener("pointerdown", (e) => { this.dragging = true; px = e.clientX; py = e.clientY; lastT = performance.now(); c.setPointerCapture(e.pointerId); c.classList.add("grab"); });
    c.addEventListener("pointermove", (e) => {
      if (this.dragging) {
        const dx = e.clientX - px, dy = e.clientY - py, now = performance.now();
        this.lon -= dx * 0.35; this.tilt = Math.max(-10, Math.min(55, this.tilt + dy * 0.25));
        this.vel = -dx * 0.35 / Math.max(8, now - lastT) * 1000 * 0.25;
        px = e.clientX; py = e.clientY; lastT = now; this.idleT = 0; this.kick();
      } else this.pick(e);
    });
    const up = () => { this.dragging = false; c.classList.remove("grab"); };
    c.addEventListener("pointerup", up); c.addEventListener("pointercancel", up);
    c.addEventListener("pointerleave", () => { this.hover = null; this.showTip(null); });
    c.addEventListener("click", (e) => { const s = this.hit(e); if (s && this.opts.onPick) this.opts.onPick(s); });
  }

  hit(e) {
    const r = this.cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    let best = null, bd = 16;
    for (const s of this.signals) {
      const [sx, sy, z] = this.project(s.geo[0], s.geo[1]);
      const d = Math.hypot(sx - x, sy - y);
      if (z > 0.05 && d < bd) { bd = d; best = s; }
    }
    return best;
  }

  pick(e) {
    const s = this.hit(e);
    if (s !== this.hover) { this.hover = s; this.showTip(s, e); this.cv.style.cursor = s ? "pointer" : ""; }
    else if (s) this.showTip(s, e);
  }

  showTip(s, e) {
    const tip = this.opts.tip;
    if (!tip) return;
    if (!s) { tip.hidden = true; return; }
    const r = this.cv.getBoundingClientRect(), pr = this.cv.parentElement.getBoundingClientRect();
    tip.innerHTML = s.tipHtml;
    tip.hidden = false;
    const x = e.clientX - pr.left, y = e.clientY - pr.top;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(Math.max(8, x - w / 2), pr.width - w - 8) + "px";
    tip.style.top = (y - h - 16 < 0 ? y + 18 : y - h - 16) + "px";
    void r;
  }

  kick() {
    if (this.raf) return;
    const loop = (now) => {
      this.raf = 0;
      const dt = Math.min(0.05, (now - this.last) / 1000); this.last = now;
      if (!this.dragging && !this.reduce) {
        this.idleT += dt;
        if (this.hq && !this.opts.ambient) {
          // компания всегда в кадре: глобус плавно покачивается вокруг неё; после перетаскивания — мягко возвращается
          this.phase = (this.phase || 0) + (this.hover ? 0 : dt * 0.12);
          const target = this.hq.geo[1] + 8 + 32 * Math.sin(this.phase);
          const k = Math.min(1, dt * (this.idleT > 2.5 ? 0.8 : 0));
          this.lon += ((target - this.lon + 540) % 360 - 180) * k;
          this.tilt += (40 - this.tilt) * k * 0.5;
        } else {
          const target = this.opts.ambient ? 5 : 2.4;
          this.vel += (target - this.vel) * Math.min(1, dt * 1.5);
          if (!this.hover) this.lon += this.vel * dt;
        }
      }
      this.draw(now);
      if (this.visible && !document.hidden && !this.reduce) this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  draw(now) {
    const ctx = this.ctx, d = this.dpr, R = this.R, t = (now - this.t0) / 1000;
    ctx.setTransform(d, 0, 0, d, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    // атмосфера и тело сферы
    const glow = ctx.createRadialGradient(this.cx, this.cy, R * 0.85, this.cx, this.cy, R * 1.32);
    glow.addColorStop(0, "rgba(239,49,36,0.22)"); glow.addColorStop(0.45, "rgba(239,49,36,0.07)"); glow.addColorStop(1, "rgba(239,49,36,0)");
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(this.cx, this.cy, R * 1.32, 0, Math.PI * 2); ctx.fill();
    const body = ctx.createRadialGradient(this.cx - R * 0.35, this.cy - R * 0.4, R * 0.1, this.cx, this.cy, R);
    body.addColorStop(0, "rgba(46,48,56,0.95)"); body.addColorStop(1, "rgba(14,14,17,0.98)");
    ctx.fillStyle = body; ctx.beginPath(); ctx.arc(this.cx, this.cy, R, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.08)"; ctx.lineWidth = 1; ctx.stroke();

    // суша: точки, Россия ярче; задняя полусфера — едва видна
    const ct = Math.cos(this.tilt * RAD), st = Math.sin(this.tilt * RAD), L = this.lon * RAD;
    const ds = Math.max(0.9, R / 190);
    for (const [cl, sl, lon, rus] of this.land) {
      const x0 = cl * Math.sin(lon - L), z0 = cl * Math.cos(lon - L);
      const y = sl * ct - z0 * st, z = sl * st + z0 * ct;
      if (z < -0.15) continue;
      const sx = this.cx + x0 * R, sy = this.cy - y * R;
      if (z < 0) { ctx.fillStyle = "rgba(255,255,255,0.035)"; ctx.fillRect(sx, sy, ds, ds); continue; }
      const a = 0.22 + 0.7 * z;
      ctx.fillStyle = rus ? `rgba(255,255,255,${Math.min(1, a + 0.15)})` : `rgba(150,160,175,${a * 0.75})`;
      const s = ds * (rus ? 1.5 : 1.2) * (0.7 + 0.3 * z);
      ctx.fillRect(sx - s / 2, sy - s / 2, s, s);
    }

    // партнёры ВЭД: тонкие пунктирные дуги к компании
    if (this.hq) {
      ctx.setLineDash([2, 4]); ctx.lineWidth = 1;
      for (const p of this.partners) {
        const arc = p.arc || (p.arc = this.arcPoints(p.geo, this.hq.geo, 32));
        if (arc) this.strokeArc(arc, "rgba(255,255,255,0.28)");
      }
      ctx.setLineDash([]);
      for (const p of this.partners) {
        const [x, y, z] = this.project(p.geo[0], p.geo[1]);
        if (z <= 0) continue;
        ctx.fillStyle = `rgba(255,255,255,${0.5 + 0.5 * z})`;
        ctx.beginPath(); ctx.moveTo(x, y - 4); ctx.lineTo(x + 4, y); ctx.lineTo(x, y + 4); ctx.lineTo(x - 4, y); ctx.closePath(); ctx.fill();
        if (z > 0.35 && !this.opts.ambient) { ctx.font = "500 11px Inter, system-ui, sans-serif"; ctx.fillStyle = "rgba(255,255,255,0.55)"; ctx.fillText(p.label, x + 7, y + 4); }
      }
    }

    // сигналы: дуги «событие → компания» с бегущим импульсом
    for (const s of this.signals) {
      const rgb = EFFECT_RGB[s.effect] || EFFECT_RGB.watch, hot = this.hover === s;
      if (s.arc) {
        ctx.lineWidth = hot ? 2.4 : 1.4;
        this.strokeArc(s.arc, `rgba(${rgb},${hot ? 0.95 : 0.5})`);
        const p = ((t * 0.32 + s.phase) % 1), n = s.arc.length - 1;
        for (let k = 0; k < 7; k++) {
          const q = p - k * 0.018;
          if (q < 0) continue;
          const pt = s.arc[Math.round(q * n)], [x, y, z] = this.project(pt[0], pt[1], pt[2]);
          if (z < -0.1) continue;
          ctx.fillStyle = `rgba(${rgb},${(1 - k / 7) * 0.95})`;
          ctx.beginPath(); ctx.arc(x, y, (hot ? 3.2 : 2.6) * (1 - k / 9), 0, Math.PI * 2); ctx.fill();
        }
      }
      const [x, y, z] = this.project(s.geo[0], s.geo[1]);
      if (z <= 0) continue;
      const pulse = (t * 0.8 + s.phase) % 1, r0 = 3 + 3 * (s.weight || 0.5);
      ctx.strokeStyle = `rgba(${rgb},${(1 - pulse) * 0.8})`; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, r0 + pulse * 16, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = `rgb(${rgb})`; ctx.shadowColor = `rgba(${rgb},0.9)`; ctx.shadowBlur = hot ? 18 : 10;
      ctx.beginPath(); ctx.arc(x, y, hot ? r0 + 2 : r0, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
    }

    // компания: красный маяк и подпись
    if (this.hq) {
      const [x, y, z] = this.project(this.hq.geo[0], this.hq.geo[1]);
      if (z > 0) {
        for (let k = 0; k < 2; k++) {
          const p = (t * 0.6 + k * 0.5) % 1;
          ctx.strokeStyle = `rgba(239,49,36,${(1 - p) * 0.9})`; ctx.lineWidth = 2;
          ctx.beginPath(); ctx.arc(x, y, 6 + p * 26, 0, Math.PI * 2); ctx.stroke();
        }
        ctx.fillStyle = "#ef3124"; ctx.shadowColor = "rgba(239,49,36,1)"; ctx.shadowBlur = 20;
        ctx.beginPath(); ctx.arc(x, y, 6.5, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x, y, 2.4, 0, Math.PI * 2); ctx.fill();
        if (!this.opts.ambient) {
          const label = this.hq.label;
          ctx.font = "700 12px Inter, system-ui, sans-serif";
          const w = ctx.measureText(label).width + 16, lx = Math.min(x + 12, this.w - w - 4), ly = y - 30;
          ctx.fillStyle = "rgba(239,49,36,0.95)"; this.roundRect(lx, ly, w, 22, 11); ctx.fill();
          ctx.fillStyle = "#fff"; ctx.fillText(label, lx + 8, ly + 15);
        }
      }
    }
    // блик
    const hl = ctx.createRadialGradient(this.cx - R * 0.45, this.cy - R * 0.5, 0, this.cx - R * 0.45, this.cy - R * 0.5, R * 0.9);
    hl.addColorStop(0, "rgba(255,255,255,0.07)"); hl.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = hl; ctx.beginPath(); ctx.arc(this.cx, this.cy, R, 0, Math.PI * 2); ctx.fill();
  }

  strokeArc(arc, color) {
    const ctx = this.ctx;
    ctx.strokeStyle = color; ctx.beginPath();
    let pen = false;
    for (const pt of arc) {
      const [x, y, z] = this.project(pt[0], pt[1], pt[2]);
      const behind = z < 0 && Math.hypot(x - this.cx, y - this.cy) < this.R;  // за сферой — не рисуем
      if (behind) { pen = false; continue; }
      if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
    }
    ctx.stroke();
  }

  roundRect(x, y, w, h, r) {
    const c = this.ctx; c.beginPath();
    c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }
}
