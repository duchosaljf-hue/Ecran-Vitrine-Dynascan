"use strict";

// Player de la vitrine : lit /api/playlist en boucle, avec fondu enchaîné,
// vue météo intercalée (Open-Meteo), petite horloge optionnelle et bandeau de messages.

const POLL_MS = 60 * 1000;          // vérification de la playlist
const WEATHER_MS = 15 * 60 * 1000;  // rafraîchissement météo
const WEATHER_MAX_AGE_MS = 3 * 3600 * 1000; // au-delà, la météo en cache n'est plus affichée
const VIDEO_TIMEOUT_MS = 10 * 60 * 1000; // sécurité : une vidéo bloquée est abandonnée

const layers = [document.getElementById("layerA"), document.getElementById("layerB")];
const emptyEl = document.getElementById("empty");
const widgetEl = document.getElementById("widget");
const clockEl = document.getElementById("clock");
const dateEl = document.getElementById("date");
const tickerEl = document.getElementById("ticker");
const tickerText = document.getElementById("tickerText");

let playlist = { version: null, items: [], messages: [], config: {} };
let pending = null;     // nouvelle playlist, appliquée à la fin du cycle
let index = 0;
let front = 0;          // couche actuellement visible
let timer = null;
let config = {};
let weather = null;     // dernière météo connue
let sinceWeather = 0;   // diapositives affichées depuis la dernière vue météo

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(url + " : " + res.status);
  return res.json();
}

// ---------------------------------------------------------------- playlist
async function pollPlaylist() {
  try {
    const next = await fetchJSON("/api/playlist");
    config = next.config || {};
    widgetEl.hidden = !config.corner_clock;
    if (next.version !== playlist.version) {
      if (playlist.items.length === 0) {
        applyPlaylist(next);
        showNext();
      } else {
        pending = next; // pris en compte à la fin du cycle en cours
      }
    }
  } catch (err) {
    console.warn("playlist indisponible :", err);
  }
}

function applyPlaylist(next) {
  playlist = next;
  pending = null;
  index = 0;
  renderTicker(next.messages || []);
  emptyEl.hidden = next.items.length > 0;
}

function renderTicker(messages) {
  document.body.classList.toggle("has-ticker", messages.length > 0);
  if (!messages.length) {
    tickerEl.hidden = true;
    return;
  }
  tickerText.textContent = messages.join("   •   ");
  tickerEl.hidden = false;
  // vitesse ~ 120 px/s quelle que soit la longueur du texte
  const width = tickerText.scrollWidth + window.innerWidth;
  tickerText.style.animationDuration = Math.max(15, width / 120) + "s";
}

// ---------------------------------------------------------------- diaporama
function buildElement(item) {
  if (item.type === "video") {
    const v = document.createElement("video");
    v.src = item.src;
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    return v;
  }
  const img = new Image();
  img.src = item.src;
  img.decoding = "async";
  return img;
}

function waitLoaded(el, item) {
  return new Promise((resolve, reject) => {
    if (item.type === "video") {
      el.addEventListener("canplay", () => resolve(), { once: true });
    } else {
      el.addEventListener("load", () => resolve(), { once: true });
    }
    el.addEventListener("error", () => reject(new Error("chargement impossible : " + item.src)), { once: true });
    setTimeout(() => reject(new Error("délai dépassé : " + item.src)), 30000);
  });
}

// Affiche un élément sur la couche arrière puis fait un fondu enchaîné.
function present(el) {
  const back = 1 - front;
  const old = layers[front];
  layers[back].replaceChildren(el);
  layers[back].classList.add("active");
  old.classList.remove("active");
  front = back;
  // libère l'ancienne diapositive (surtout une vidéo) une fois le fondu terminé
  setTimeout(() => { if (!old.classList.contains("active")) old.replaceChildren(); }, 1500);
}

function weatherIsFresh() {
  return weather && Date.now() - weather.at < WEATHER_MAX_AGE_MS;
}

async function showNext() {
  clearTimeout(timer);
  timer = null;

  if (index >= playlist.items.length) {
    index = 0;
    if (pending) applyPlaylist(pending);
  }
  const items = playlist.items;
  if (!items.length) {
    emptyEl.hidden = false;
    timer = setTimeout(showNext, POLL_MS);
    return;
  }

  // Vue météo dédiée, intercalée toutes les N diapositives
  const every = Number(config.weather_every) || 0;
  if (every > 0 && sinceWeather >= every && weatherIsFresh()) {
    sinceWeather = 0;
    present(buildWeatherSlide(weather));
    timer = setTimeout(showNext, (Number(config.weather_seconds) || 10) * 1000);
    return;
  }

  const item = items[index];
  index += 1;
  const el = buildElement(item);

  try {
    await waitLoaded(el, item);
  } catch (err) {
    console.warn(err.message);
    timer = setTimeout(showNext, 1000);
    return;
  }

  present(el);
  sinceWeather += 1;

  if (item.type === "video") {
    el.play().catch((e) => console.warn("lecture vidéo refusée :", e));
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      showNext();
    };
    el.addEventListener("ended", done, { once: true });
    el.addEventListener("error", done, { once: true });
    timer = setTimeout(done, VIDEO_TIMEOUT_MS);
  } else {
    timer = setTimeout(showNext, (item.duration || 10) * 1000);
  }
}

// ---------------------------------------------------------------- horloge
function formatTime(now) {
  return now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function formatDate(now) {
  return now.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

function tickClock() {
  const now = new Date();
  clockEl.textContent = formatTime(now);
  dateEl.textContent = formatDate(now);
  // l'horloge de la vue météo, si elle est à l'écran
  document.querySelectorAll(".ws-time").forEach((e) => { e.textContent = formatTime(now); });
}

// Léger déplacement de l'horloge en coin toutes les minutes pour limiter l'image rémanente.
function nudgeWidget() {
  const dx = Math.round((Math.random() - 0.5) * 24);
  const dy = Math.round((Math.random() - 0.5) * 24);
  widgetEl.style.transform = `translate(${dx}px, ${dy}px)`;
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
  for (const [codes, icon, label] of WEATHER_CODES) {
    if (codes.includes(code)) return { icon, label };
  }
  return { icon: "🌡️", label: "" };
}

async function updateWeather() {
  if (config.latitude === undefined) return;
  const url = "https://api.open-meteo.com/v1/forecast" +
    `?latitude=${config.latitude}&longitude=${config.longitude}` +
    "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m" +
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max" +
    `&timezone=${encodeURIComponent(config.timezone || "auto")}&forecast_days=5`;
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
    try { localStorage.setItem("weather2", JSON.stringify(weather)); } catch (_) { /* ignoré */ }
  } catch (err) {
    console.warn("météo indisponible :", err);
    if (!weather) {
      try { weather = JSON.parse(localStorage.getItem("weather2") || "null"); } catch (_) { weather = null; }
    }
  }
}

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

// Vue météo plein écran (portrait) : ville, heure, météo actuelle, prévisions sur 4 jours.
function buildWeatherSlide(w) {
  const now = new Date();
  const current = describeWeather(w.code);
  const root = mk("div", "weather-slide");

  const head = mk("div", "ws-head");
  head.append(mk("div", "ws-city", config.city || ""));
  head.append(mk("div", "ws-time", formatTime(now)));
  head.append(mk("div", "ws-date", formatDate(now)));
  root.append(head);

  const main = mk("div", "ws-main");
  main.append(mk("div", "ws-icon", current.icon));
  main.append(mk("div", "ws-temp", `${w.temp}°`));
  main.append(mk("div", "ws-label", current.label));
  const today = w.days[0];
  const details = [`Ressenti ${w.feels}°`, `Vent ${w.wind} km/h`];
  if (today && today.rain !== null) details.push(`Pluie ${today.rain} %`);
  main.append(mk("div", "ws-details", details.join("  ·  ")));
  if (today) main.append(mk("div", "ws-range", `Min ${today.min}°   Max ${today.max}°`));
  root.append(main);

  const forecast = mk("div", "ws-forecast");
  w.days.slice(1, 5).forEach((d) => {
    const cell = mk("div", "ws-day");
    const name = new Date(d.day + "T12:00:00").toLocaleDateString("fr-FR", { weekday: "short" });
    cell.append(mk("div", "ws-day-name", name));
    cell.append(mk("div", "ws-day-icon", describeWeather(d.code).icon));
    cell.append(mk("div", "ws-day-max", `${d.max}°`));
    cell.append(mk("div", "ws-day-min", `${d.min}°`));
    forecast.append(cell);
  });
  root.append(forecast);
  return root;
}

// ---------------------------------------------------------------- démarrage
async function start() {
  tickClock();
  setInterval(tickClock, 1000);
  setInterval(nudgeWidget, 60 * 1000);

  // le serveur peut démarrer un peu après le navigateur
  for (;;) {
    await pollPlaylist();
    if (playlist.version !== null || pending) break;
    emptyEl.hidden = false;
    await sleep(3000);
  }
  setInterval(pollPlaylist, POLL_MS);

  updateWeather();
  setInterval(updateWeather, WEATHER_MS);
}

start();
