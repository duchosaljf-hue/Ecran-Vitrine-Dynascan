"use strict";

// Player de la vitrine.
// Adresse : /?screen=NOM_DE_L_ECRAN  (sans paramètre : l'écran par défaut)
// Récupère la liste des contenus auprès du serveur, les diffuse dans un ordre aléatoire pondéré
// (les biens importants reviennent plus souvent), intercale la vue météo, et réagit aux messages urgents.

const params = new URLSearchParams(location.search);
const SLUG = params.get("screen") || "default";
const PREVIEW = params.get("preview") === "1";
const ONLY = params.get("item");   // aperçu d'un seul bien (réservé aux utilisateurs connectés)
const API = "/api/player/" + encodeURIComponent(SLUG) + "/playlist" +
  (ONLY ? "?only=" + encodeURIComponent(ONLY) : (PREVIEW ? "?preview=1" : ""));
const CACHE_KEY = "vitrine-playlist-" + SLUG;

const POLL_MS = 30 * 1000;            // vérification de la liste auprès du serveur
const WEATHER_MS = 15 * 60 * 1000;    // rafraîchissement de la météo
const WEATHER_MAX_AGE_MS = 3 * 3600 * 1000;
const VIDEO_TIMEOUT_MS = 10 * 60 * 1000;
const LOAD_TIMEOUT_MS = 30 * 1000;

const layers = [document.getElementById("layerA"), document.getElementById("layerB")];
const emptyEl = document.getElementById("empty");
const widgetEl = document.getElementById("widget");
const clockEl = document.getElementById("clock");
const dateEl = document.getElementById("date");
const tickerEl = document.getElementById("ticker");
const tickerText = document.getElementById("tickerText");
const overrideEl = document.getElementById("override");
const overrideText = document.getElementById("overrideText");

let cfg = { screen: {}, site: {} };   // réglages de l'écran et du site (toujours les plus récents)
let units = null;                     // contenus en cours de diffusion
let unitsSig = "";
let pendingUnits = null;              // nouvelle liste, adoptée à la fin du paquet en cours
let deck = [];
let deckPos = 0;
let lastUnitId = null;
let queue = [];                       // diapositives restantes du contenu en cours
let sinceWeather = 0;
let timer = null;
let override = null;
let paused = false;
let weather = null;
let clockOffset = 0;                  // décalage entre l'heure du serveur et celle de l'appareil
let front = 0;
let started = false;
let appVersion = null;

// ---------------------------------------------------------------- utilitaires
function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function serverNow() {
  return new Date(Date.now() + clockOffset);
}

function fmtTime(d) {
  const opts = { hour: "2-digit", minute: "2-digit", timeZone: cfg.site.timezone || undefined };
  try { return d.toLocaleTimeString("fr-FR", opts); } catch (_) { delete opts.timeZone; return d.toLocaleTimeString("fr-FR", opts); }
}

function fmtDate(d) {
  const opts = { weekday: "long", day: "numeric", month: "long", timeZone: cfg.site.timezone || undefined };
  try { return d.toLocaleDateString("fr-FR", opts); } catch (_) { delete opts.timeZone; return d.toLocaleDateString("fr-FR", opts); }
}

function setUnit() {
  const u = Math.min(window.innerWidth / 100, (window.innerHeight * 0.5625) / 100);
  document.documentElement.style.setProperty("--u", u + "px");
}
window.addEventListener("resize", setUnit);
setUnit();

async function fetchJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(url + " : " + res.status);
  return res.json();
}

// ---------------------------------------------------------------- liste des contenus
async function pollPlaylist() {
  let data = null;
  try {
    data = await fetchJSON(API);
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(data)); } catch (_) { /* ignoré */ }
  } catch (err) {
    console.warn("serveur injoignable :", err);
    if (!started) {
      try { data = JSON.parse(localStorage.getItem(CACHE_KEY) || "null"); } catch (_) { data = null; }
    }
  }
  if (data) applyPlaylist(data);
}

function applyPlaylist(data) {
  // après une mise à jour du serveur, la page se recharge toute seule pour prendre la nouvelle version
  if (data.app_version) {
    if (appVersion && appVersion !== data.app_version) { location.reload(); return; }
    appVersion = data.app_version;
  }
  if (data.server_time) clockOffset = data.server_time - Date.now();
  cfg = { screen: data.screen || {}, site: data.site || {} };
  widgetEl.hidden = !cfg.screen.corner_clock;
  renderTicker(cfg.screen.ticker || []);

  const sig = JSON.stringify(data.items);
  if (units === null) {
    units = data.items;
    unitsSig = sig;
  } else if (sig !== unitsSig) {
    pendingUnits = data.items;          // adoptée à la fin du paquet pour ne pas couper une série
    unitsSig = sig;
    if (!units.length) adoptPending();   // rien à l'écran : on peut tout de suite
  }

  const newOverride = data.override ? data.override.text : null;
  const oldOverride = override;
  override = newOverride;
  paused = !!data.paused;
  if (override !== oldOverride) {
    renderOverride();
    if (!override) kick();
  }
  if (!started) {
    started = true;
    kick();
  } else if (!timer && !override) {
    kick();
  }
  setUnit();
}

function adoptPending() {
  if (pendingUnits) {
    units = pendingUnits;
    pendingUnits = null;
    deck = [];
    deckPos = 0;
  }
}

function renderOverride() {
  if (override) {
    overrideText.textContent = override;
    overrideEl.hidden = false;
    clearTimeout(timer);
    timer = null;
  } else {
    overrideEl.hidden = true;
  }
}

function renderTicker(messages) {
  document.body.classList.toggle("has-ticker", messages.length > 0);
  if (!messages.length) {
    tickerEl.hidden = true;
    return;
  }
  const text = messages.join("   •   ");
  if (tickerText.textContent === text && !tickerEl.hidden) return;
  tickerText.textContent = text;
  tickerEl.hidden = false;
  const width = tickerText.scrollWidth + window.innerWidth;
  tickerText.style.animationDuration = Math.max(15, width / (window.innerWidth / 9)) + "s";
}

// ---------------------------------------------------------------- ordre de diffusion
// Chaque contenu apparaît autant de fois que son importance (1 à 5) dans un « paquet »,
// réparti régulièrement au hasard ; deux fois de suite le même contenu est évité.
function buildDeck(list, mode, avoidFirstId) {
  if (!list.length) return [];
  const entries = [];
  list.forEach((u, index) => {
    const w = Math.max(1, Math.min(5, u.weight || 1));
    for (let k = 0; k < w; k++) {
      const jitter = mode === "random" ? Math.random() : 0.5;
      entries.push({ u, index, pos: (k + jitter) / w });
    }
  });
  entries.sort((a, b) => (a.pos - b.pos) || (a.index - b.index));
  const out = entries.map((e) => e.u);
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 1; i < out.length; i++) {
      if (out[i].id !== out[i - 1].id) continue;
      for (let j = i + 1; j < out.length; j++) {
        if (out[j].id !== out[i - 1].id && (j + 1 >= out.length || out[j + 1].id !== out[i].id)) {
          const tmp = out[i]; out[i] = out[j]; out[j] = tmp;
          break;
        }
      }
    }
  }
  if (out.length > 1 && out[0].id === avoidFirstId) {
    for (let j = 1; j < out.length; j++) {
      if (out[j].id !== avoidFirstId) { const tmp = out[0]; out[0] = out[j]; out[j] = tmp; break; }
    }
  }
  return out;
}

function nextUnit() {
  if (deckPos >= deck.length) {
    adoptPending();
    deck = buildDeck(units || [], cfg.screen.order_mode || "random", lastUnitId);
    deckPos = 0;
    if (!deck.length) return null;
  }
  const unit = deck[deckPos++];
  lastUnitId = unit.id;
  return unit;
}

function takeSlide() {
  if (!queue.length) {
    const unit = nextUnit();
    if (!unit) return null;
    if (unit.view) queue = [{ unit, view: unit.view, idx: 0, total: 1 }];
    else queue = unit.slides.map((slide, idx) => ({ unit, slide, idx, total: unit.slides.length }));
  }
  return queue.shift();
}

// ---------------------------------------------------------------- affichage
function present(el) {
  const back = 1 - front;
  const old = layers[front];
  layers[back].replaceChildren(el);
  layers[back].classList.add("active");
  old.classList.remove("active");
  front = back;
  setTimeout(() => { if (!old.classList.contains("active")) old.replaceChildren(); }, 1500);
}

function addRibbon(root, unit) {
  if (!unit.badge) return;
  root.appendChild(mk("div", "ribbon ribbon-" + unit.badge, unit.badge_label));
}

function waitFor(el, eventName) {
  return new Promise((resolve, reject) => {
    el.addEventListener(eventName, () => resolve(), { once: true });
    el.addEventListener("error", () => reject(new Error("chargement impossible")), { once: true });
    setTimeout(() => reject(new Error("délai dépassé")), LOAD_TIMEOUT_MS);
  });
}

async function buildSlide(entry) {
  const fit = cfg.screen.fit || "contain";
  if (entry.view) return { el: buildView(entry.view, entry.unit), video: null };
  const root = mk("div", "slide fit-" + fit);
  if (entry.slide.type === "video") {
    const v = document.createElement("video");
    v.className = "main";
    v.src = entry.slide.src;
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    root.appendChild(v);
    addRibbon(root, entry.unit);
    await waitFor(v, "canplay");
    return { el: root, video: v };
  }
  if (fit === "blur") {
    const bg = mk("div", "bg");
    bg.style.backgroundImage = "url(\"" + entry.slide.src + "\")";
    root.appendChild(bg);
  }
  const img = new Image();
  img.className = "main";
  img.decoding = "async";
  img.src = entry.slide.src;
  root.appendChild(img);
  addRibbon(root, entry.unit);
  await waitFor(img, "load");
  return { el: root, video: null };
}

function schedule(ms) {
  clearTimeout(timer);
  timer = setTimeout(showNext, ms);
}

function kick() {
  clearTimeout(timer);
  timer = null;
  if (override) return;
  showNext();
}

let rendering = false;

async function showNext() {
  if (rendering) return;   // un affichage est déjà en cours de préparation
  rendering = true;
  try {
    await showNextInner();
  } catch (err) {
    // quoi qu'il arrive, l'écran ne doit jamais rester figé : on réessaie dans quelques secondes
    console.error("erreur d'affichage :", err);
    schedule(5000);
  } finally {
    rendering = false;
  }
}

async function showNextInner() {
  clearTimeout(timer);
  timer = null;
  if (override) return;
  if (paused) { schedule(3000); return; }

  const every = Number(cfg.screen.weather_every) || 0;
  if (!queue.length && every > 0 && sinceWeather >= every && weatherIsFresh()) {
    sinceWeather = 0;
    present(buildWeatherSlide(weather));
    emptyEl.hidden = true;
    schedule((Number(cfg.screen.weather_seconds) || 10) * 1000);
    return;
  }

  const entry = takeSlide();
  if (!entry) {
    emptyEl.hidden = false;
    schedule(5000);
    return;
  }
  emptyEl.hidden = true;

  let built;
  try {
    built = await buildSlide(entry);
  } catch (err) {
    console.warn(err.message, entry.slide ? entry.slide.src : "");
    schedule(1000);
    return;
  }
  if (override) return;
  present(built.el);
  sinceWeather += 1;

  if (built.video) {
    built.video.play().catch((e) => console.warn("lecture vidéo refusée :", e));
    let finished = false;
    const done = () => { if (finished) return; finished = true; clearTimeout(timer); showNext(); };
    built.video.addEventListener("ended", done, { once: true });
    built.video.addEventListener("error", done, { once: true });
    timer = setTimeout(done, VIDEO_TIMEOUT_MS);
  } else {
    schedule((entry.unit.duration || 10) * 1000);
  }
}

// ---------------------------------------------------------------- vues (annonce, QR code, agence)
const RING = '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="38" fill="none"' +
  ' stroke="#E30613" stroke-width="15" stroke-dasharray="190 49" transform="rotate(-40 50 50)"/></svg>';

function ring(small) {
  const div = mk("div", "ring" + (small ? " small" : ""));
  div.innerHTML = RING;   // constante, sans donnée saisie
  return div;
}

function buildView(view, unit) {
  const d = view.data || {};
  const root = mk("div", "view-slide");
  if (view.type === "texte") {
    root.classList.add("theme-" + (d.theme || "creme"));
    root.append(ring(false), ring(true));
    if (d.heading) root.append(mk("div", "v-heading", d.heading));
    if (d.text) root.append(mk("div", "v-text", d.text));
  } else if (view.type === "qr") {
    root.append(ring(false));
    if (d.heading) root.append(mk("div", "v-heading", d.heading));
    const img = new Image();
    img.className = "v-qr";
    img.src = d.qr_src || "";
    root.append(img);
    if (d.caption) root.append(mk("div", "v-caption", d.caption));
  } else {
    root.append(ring(false), ring(true));
    root.append(mk("div", "v-heading", d.heading || "Notre agence"));
    if (d.name) root.append(mk("div", "v-name", d.name));
    if (d.address) root.append(mk("div", "v-line", d.address));
    if (d.phone) root.append(mk("div", "v-phone", d.phone));
    if (d.hours) root.append(mk("div", "v-line", d.hours));
    if (d.site) root.append(mk("div", "v-site", d.site));
  }
  const wrap = mk("div", "slide");
  wrap.appendChild(root);
  addRibbon(wrap, unit);
  return wrap;
}

// ---------------------------------------------------------------- météo
const WEATHER_CODES = [
  [[0], "☀️", "Ensoleillé"],
  [[1, 2], "🌤️", "Éclaircies"],
  [[3], "☁️", "Couvert"],
  [[45, 48], "🌫️", "Brouillard"],
  [[51, 53, 55, 56, 57], "🌦️", "Bruine"],
  [[61, 63, 65, 66, 67, 80, 81, 82], "🌧️", "Pluie"],
  [[71, 73, 75, 77, 85, 86], "❄️", "Neige"],
  [[95, 96, 99], "⛈️", "Orage"],
];

function describeWeather(code) {
  for (let i = 0; i < WEATHER_CODES.length; i++) {
    if (WEATHER_CODES[i][0].indexOf(code) !== -1) return { icon: WEATHER_CODES[i][1], label: WEATHER_CODES[i][2] };
  }
  return { icon: "🌡️", label: "" };
}

function weatherIsFresh() {
  return weather && Date.now() - weather.at < WEATHER_MAX_AGE_MS;
}

async function updateWeather() {
  if (cfg.site.latitude === undefined) { setTimeout(updateWeather, 5000); return; }
  const url = "https://api.open-meteo.com/v1/forecast" +
    "?latitude=" + cfg.site.latitude + "&longitude=" + cfg.site.longitude +
    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m" +
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max" +
    "&timezone=" + encodeURIComponent(cfg.site.timezone || "auto") + "&forecast_days=5";
  try {
    const data = await fetchJSON(url);
    const days = data.daily.time.map((day, i) => ({
      day,
      code: data.daily.weather_code[i],
      min: Math.round(data.daily.temperature_2m_min[i]),
      max: Math.round(data.daily.temperature_2m_max[i]),
      rain: data.daily.precipitation_probability_max ? data.daily.precipitation_probability_max[i] : null,
    }));
    weather = {
      temp: Math.round(data.current.temperature_2m),
      feels: Math.round(data.current.apparent_temperature),
      wind: Math.round(data.current.wind_speed_10m),
      code: data.current.weather_code,
      days,
      at: Date.now(),
    };
    try { localStorage.setItem("vitrine-weather", JSON.stringify(weather)); } catch (_) { /* ignoré */ }
  } catch (err) {
    console.warn("météo indisponible :", err);
    if (!weather) {
      try { weather = JSON.parse(localStorage.getItem("vitrine-weather") || "null"); } catch (_) { weather = null; }
    }
  }
}

function buildWeatherSlide(w) {
  const now = serverNow();
  const current = describeWeather(w.code);
  const root = mk("div", "weather-slide");

  const head = mk("div", "ws-head");
  head.append(mk("div", "ws-city", cfg.site.city || ""));
  head.append(mk("div", "ws-time", fmtTime(now)));
  head.append(mk("div", "ws-date", fmtDate(now)));
  root.append(head);

  const main = mk("div", "ws-main");
  main.append(mk("div", "ws-icon", current.icon));
  main.append(mk("div", "ws-temp", w.temp + "°"));
  main.append(mk("div", "ws-label", current.label));
  const today = w.days[0];
  const details = ["Ressenti " + w.feels + "°", "Vent " + w.wind + " km/h"];
  if (today && today.rain !== null) details.push("Pluie " + today.rain + " %");
  main.append(mk("div", "ws-details", details.join("  ·  ")));
  if (today) main.append(mk("div", "ws-range", "Min " + today.min + "°   Max " + today.max + "°"));
  root.append(main);

  const forecast = mk("div", "ws-forecast");
  w.days.slice(1, 5).forEach((d) => {
    const cell = mk("div", "ws-day");
    let name = "";
    try { name = new Date(d.day + "T12:00:00").toLocaleDateString("fr-FR", { weekday: "short" }); } catch (_) { name = d.day; }
    cell.append(mk("div", "ws-day-name", name));
    cell.append(mk("div", "ws-day-icon", describeWeather(d.code).icon));
    cell.append(mk("div", "ws-day-max", d.max + "°"));
    cell.append(mk("div", "ws-day-min", d.min + "°"));
    forecast.append(cell);
  });
  root.append(forecast);
  return root;
}

// ---------------------------------------------------------------- horloge en coin
function tickClock() {
  const now = serverNow();
  clockEl.textContent = fmtTime(now);
  dateEl.textContent = fmtDate(now);
  const live = document.querySelector(".ws-time");
  if (live) live.textContent = fmtTime(now);
}

function nudgeWidget() {
  const dx = Math.round((Math.random() - 0.5) * 24);
  const dy = Math.round((Math.random() - 0.5) * 24);
  widgetEl.style.transform = "translate(" + dx + "px, " + dy + "px)";
}

// ---------------------------------------------------------------- démarrage
async function start() {
  setInterval(tickClock, 1000);
  setInterval(nudgeWidget, 60 * 1000);
  await pollPlaylist();
  if (!started) {
    emptyEl.hidden = false;
    // le serveur peut démarrer un peu après le navigateur
    const retry = setInterval(async () => {
      await pollPlaylist();
      if (started) clearInterval(retry);
    }, 3000);
  }
  setInterval(pollPlaylist, POLL_MS);
  await updateWeather();
  setInterval(updateWeather, WEATHER_MS);
}

start();
