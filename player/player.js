"use strict";

// Player de la vitrine : lit /api/playlist en boucle, avec fondu enchaîné,
// horloge, météo (Open-Meteo) et bandeau de messages.

const POLL_MS = 60 * 1000;          // vérification de la playlist
const WEATHER_MS = 15 * 60 * 1000;  // rafraîchissement météo
const VIDEO_TIMEOUT_MS = 10 * 60 * 1000; // sécurité : une vidéo bloquée est abandonnée

const layers = [document.getElementById("layerA"), document.getElementById("layerB")];
const emptyEl = document.getElementById("empty");
const widgetEl = document.getElementById("widget");
const clockEl = document.getElementById("clock");
const dateEl = document.getElementById("date");
const weatherEl = document.getElementById("weather");
const tickerEl = document.getElementById("ticker");
const tickerText = document.getElementById("tickerText");

let playlist = { version: null, items: [], messages: [], config: {} };
let pending = null;     // nouvelle playlist, appliquée à la fin du cycle
let index = 0;
let front = 0;          // couche actuellement visible
let timer = null;
let config = {};

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
  setWidgets(config.widgets !== false);
  emptyEl.hidden = next.items.length > 0;
}

function renderTicker(messages) {
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

  const back = 1 - front;
  const old = layers[front];
  layers[back].replaceChildren(el);
  layers[back].classList.add("active");
  old.classList.remove("active");
  front = back;
  // libère l'ancienne diapositive (surtout une vidéo) une fois le fondu terminé
  setTimeout(() => { if (!old.classList.contains("active")) old.replaceChildren(); }, 1500);

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
  } else if (items.length === 1 && !pending) {
    // une seule image : elle reste affichée, on revérifie simplement la playlist
    index = 0;
    timer = setTimeout(showNext, POLL_MS);
  } else {
    timer = setTimeout(showNext, (item.duration || 10) * 1000);
  }
}

// ---------------------------------------------------------------- widgets
function setWidgets(on) {
  widgetEl.hidden = !on;
}

function tickClock() {
  const now = new Date();
  clockEl.textContent = now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  dateEl.textContent = now.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

// Léger déplacement du widget toutes les minutes pour limiter l'image rémanente.
function nudgeWidget() {
  const dx = Math.round((Math.random() - 0.5) * 24);
  const dy = Math.round((Math.random() - 0.5) * 24);
  widgetEl.style.transform = `translate(${dx}px, ${dy}px)`;
}

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
    "&current=temperature_2m,weather_code" +
    "&daily=temperature_2m_max,temperature_2m_min" +
    `&timezone=${encodeURIComponent(config.timezone || "auto")}&forecast_days=1`;
  try {
    const data = await fetchJSON(url);
    const w = {
      temp: Math.round(data.current.temperature_2m),
      code: data.current.weather_code,
      min: Math.round(data.daily.temperature_2m_min[0]),
      max: Math.round(data.daily.temperature_2m_max[0]),
      at: Date.now(),
    };
    try { localStorage.setItem("weather", JSON.stringify(w)); } catch (_) { /* ignoré */ }
    renderWeather(w);
  } catch (err) {
    console.warn("météo indisponible :", err);
    try {
      const cached = JSON.parse(localStorage.getItem("weather") || "null");
      // on garde l'ancienne valeur au plus 3 heures
      if (cached && Date.now() - cached.at < 3 * 3600 * 1000) renderWeather(cached);
      else weatherEl.textContent = "";
    } catch (_) {
      weatherEl.textContent = "";
    }
  }
}

function renderWeather(w) {
  const { icon, label } = describeWeather(w.code);
  const city = config.city ? config.city + " · " : "";
  weatherEl.textContent = `${icon} ${city}${w.temp}°C ${label} (${w.min}° / ${w.max}°)`;
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
