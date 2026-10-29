"use strict";

// Page de gestion de la vitrine : biens, vues, écrans, comptes, réglages.
// Aucune dépendance externe : tout fonctionne hors connexion Internet, sur le réseau de l'agence.

const state = { user: null, csrf: "", screens: [], filters: { biens: "live", vues: "live" }, q: "", timer: null };
const $app = document.getElementById("app");

// append() ignore les valeurs vides (sinon « null » s'afficherait à l'écran)
const _append = Element.prototype.append;
Element.prototype.append = function (...kids) {
  return _append.apply(this, kids.filter((k) => k !== null && k !== undefined && k !== false));
};

const BADGES = { "": "Aucune", nouveau: "Nouveau", baisse: "Baisse de prix", exclusif: "Exclusivité", compromis: "Sous compromis", vendu: "Vendu" };
const VIEW_TYPES = { texte: "Annonce", qr: "QR code", agence: "Agence" };
const ACTIONS = {
  connexion: "Connexion", "bien-cree": "Bien ajouté", "bien-modifie": "Bien modifié", "fichier-ajoute": "Fichier traité",
  "fichier-remplace": "Affiche remplacée", "erreur-fichier": "Erreur de fichier", "bien-corbeille": "Mis à la corbeille",
  "bien-restaure": "Restauré", "bien-supprime": "Supprimé définitivement", "bien-importe": "Importé", "vue-creee": "Vue créée",
  "utilisateur-cree": "Compte créé", "utilisateur-modifie": "Compte modifié", "utilisateur-supprime": "Compte supprimé",
  "mot-de-passe-change": "Mot de passe changé", "mot-de-passe-reinitialise": "Mot de passe réinitialisé",
  "ecran-cree": "Écran ajouté", "ecran-modifie": "Écran modifié", "ecran-supprime": "Écran supprimé",
  "message-urgent": "Message urgent", "message-urgent-retire": "Message retiré", pause: "Pause", reprise: "Reprise",
  "reglages-modifies": "Réglages modifiés", "sauvegarde-telechargee": "Sauvegarde téléchargée",
};
const RING = '<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><circle cx="50" cy="50" r="36" fill="none" stroke="#E30613" stroke-width="15" stroke-dasharray="180 46" transform="rotate(-40 50 50)"/></svg>';

// ------------------------------------------------------------------ utilitaires DOM
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const key of Object.keys(attrs || {})) {
    const v = attrs[key];
    if (v === null || v === undefined || v === false) continue;
    if (key === "class") el.className = v;
    else if (key === "value") value = v;
    else if (key === "checked") el.checked = !!v;
    else if (key === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (key.indexOf("on") === 0) el.addEventListener(key.slice(2), v);
    else el.setAttribute(key, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  if (value !== undefined) el.value = value;
  return el;
}

function svg(markup) {
  const wrap = document.createElement("span");
  wrap.innerHTML = markup;   // constantes de ce fichier uniquement
  return wrap.firstChild;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function fmtDay(iso) {
  if (!iso) return "";
  const p = iso.split("-");
  return p[2] + "/" + p[1] + "/" + p[0];
}

function fmtDateTime(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch (_) { return iso; }
}

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "jamais";
  if (seconds < 90) return "à l'instant";
  if (seconds < 3600) return "il y a " + Math.round(seconds / 60) + " min";
  if (seconds < 86400) return "il y a " + Math.round(seconds / 3600) + " h";
  return "il y a " + Math.round(seconds / 86400) + " j";
}

function stars(n) { return "★".repeat(n) + "☆".repeat(5 - n); }

function setTimer(fn, ms) {
  clearInterval(state.timer);
  state.timer = setInterval(fn, ms);
}

function toast(message, isError) {
  const box = document.getElementById("toasts");
  const el = h("div", { class: "toast" + (isError ? " err" : "") }, message);
  box.append(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3200);
}

// ------------------------------------------------------------------ communication avec le serveur
function errorMessage(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join(" ; ");
  return "Erreur " + status;
}

async function api(method, url, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  if (method !== "GET") opts.headers["X-CSRF-Token"] = state.csrf || "";
  let res;
  try { res = await fetch(url, opts); } catch (_) { throw new Error("Le serveur ne répond pas. Vérifiez que le NUC est allumé."); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* pas de JSON */ }
  if (res.status === 401 && url.indexOf("/api/login") === -1) {
    state.user = null;
    renderLogin("Votre session a expiré, reconnectez-vous.");
    throw new Error("Session expirée");
  }
  if (!res.ok) throw new Error(errorMessage(data, res.status));
  return data;
}

function upload(url, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-CSRF-Token", state.csrf);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* ignoré */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else if (xhr.status === 401) { state.user = null; renderLogin("Votre session a expiré, reconnectez-vous."); reject(new Error("Session expirée")); }
      else reject(new Error(errorMessage(data, xhr.status)));
    };
    xhr.onerror = () => reject(new Error("Connexion perdue pendant l'envoi."));
    xhr.send(form);
  });
}

// ------------------------------------------------------------------ fenêtres
function modal(title, content, opts) {
  opts = opts || {};
  const overlay = h("div", { class: "overlay" });
  const actions = h("div", { class: "actions" });
  const box = h("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true" }, h("h2", {}, title), content, actions);
  overlay.append(box);
  function close() { overlay.remove(); document.removeEventListener("keydown", onKey); if (opts.onClose) opts.onClose(); }
  function onKey(e) { if (e.key === "Escape") close(); }
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", onKey);
  for (const a of opts.actions || []) {
    const btn = h("button", { class: "btn " + (a.kind || ""), type: "button", onclick: async () => {
      if (!a.onClick) { close(); return; }
      btn.disabled = true;
      try { const keep = await a.onClick(close, btn); if (keep !== false && !a.keepOpen) close(); }
      catch (err) { toast(err.message, true); }
      finally { btn.disabled = false; }
    } }, a.label);
    actions.append(btn);
  }
  document.body.append(overlay);
  const first = box.querySelector("input, textarea, select");
  if (first && !opts.noFocus) first.focus();
  return { close, box };
}

function confirmBox(message, okLabel, danger) {
  return new Promise((resolve) => {
    let answered = false;
    modal("Confirmation", h("p", {}, message), {
      onClose: () => { if (!answered) resolve(false); },
      actions: [
        { label: "Annuler", onClick: () => { answered = true; resolve(false); } },
        { label: okLabel || "Confirmer", kind: danger ? "danger" : "primary", onClick: () => { answered = true; resolve(true); } },
      ],
    });
  });
}

function showPassword(title, intro, password) {
  const box = h("div", { class: "stack" }, h("p", {}, intro), h("div", { class: "pass" }, password),
    h("p", { class: "muted small" }, "Ce mot de passe n'est affiché qu'une fois. Il devra être changé à la première connexion."));
  modal(title, box, { actions: [
    { label: "Copier", onClick: async () => { try { await navigator.clipboard.writeText(password); toast("Copié"); } catch (_) { toast("Sélectionnez le texte pour le copier.", true); } return false; }, keepOpen: true },
    { label: "Fermer", kind: "primary" },
  ] });
}

// ------------------------------------------------------------------ champs de formulaire
function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl" }, label), input, hint ? h("span", { class: "hint" }, hint) : null);
}

function segmented(options, current, onChange) {
  const wrap = h("div", { class: "seg", role: "group" });
  const buttons = [];
  options.forEach((o) => {
    const b = h("button", { type: "button", class: o.value === current ? "on" : "", onclick: () => {
      buttons.forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      onChange(o.value);
    } }, o.label, o.sub ? h("small", {}, o.sub) : null);
    buttons.push(b);
    wrap.append(b);
  });
  return wrap;
}

function screenName(slug) {
  const s = state.screens.find((x) => x.slug === slug);
  return s ? s.name : slug;
}

// ------------------------------------------------------------------ connexion
function renderLogin(message) {
  clearInterval(state.timer);
  clear($app);
  const err = h("div", { class: "err-text", role: "alert" }, message || "");
  const user = h("input", { type: "text", autocomplete: "username", autocapitalize: "none", autocorrect: "off", required: true });
  const pass = h("input", { type: "password", autocomplete: "current-password", required: true });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    try {
      const data = await api("POST", "/api/login", { username: user.value, password: pass.value });
      state.user = data.user; state.csrf = data.csrf;
      await start();
    } catch (ex) { err.textContent = ex.message; pass.value = ""; pass.focus(); }
  } },
    h("div", { class: "logo" }, svg(RING), h("b", {}, "Vitrine")),
    field("Identifiant", user), field("Mot de passe", pass), err,
    h("button", { class: "btn primary", type: "submit", style: { width: "100%" } }, "Se connecter"),
    h("p", { class: "muted small" }, "Mot de passe oublié ? Demandez à l'administrateur de le réinitialiser."));
  $app.append(h("div", { class: "loginwrap" }, form));
  user.focus();
}

function renderForcedPassword() {
  clear($app);
  const err = h("div", { class: "err-text", role: "alert" });
  const p1 = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 8 });
  const p2 = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 8 });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    if (p1.value !== p2.value) { err.textContent = "Les deux mots de passe sont différents."; return; }
    try {
      await api("POST", "/api/me/password", { current: "", new: p1.value });
      state.user.must_change_pw = false;
      toast("Mot de passe enregistré");
      await start();
    } catch (ex) { err.textContent = ex.message; }
  } },
    h("div", { class: "logo" }, svg(RING), h("b", {}, "Bienvenue")),
    h("p", {}, "Choisissez votre mot de passe personnel (8 caractères minimum)."),
    field("Nouveau mot de passe", p1), field("Confirmer", p2), err,
    h("button", { class: "btn primary", type: "submit", style: { width: "100%" } }, "Enregistrer et continuer"));
  $app.append(h("div", { class: "loginwrap" }, form));
  p1.focus();
}

// ------------------------------------------------------------------ cadre commun
const NAV = [
  { id: "biens", label: "Biens", href: "#/biens" },
  { id: "vues", label: "Vues", href: "#/vues" },
  { id: "ecrans", label: "Écrans", href: "#/ecrans" },
  { id: "utilisateurs", label: "Utilisateurs", href: "#/utilisateurs", admin: true },
  { id: "historique", label: "Historique", href: "#/historique", admin: true },
  { id: "reglages", label: "Réglages", href: "#/reglages", admin: true },
];

let viewEl = null;

function buildShell() {
  clear($app);
  const initial = (state.user.display_name || state.user.username).slice(0, 1).toUpperCase();
  const top = h("header", { class: "topbar" },
    h("div", { class: "topbar-inner" },
      h("a", { class: "brand", href: "#/biens" }, svg(RING), "Vitrine"),
      h("div", { class: "grow" }),
      h("button", { class: "userbtn", type: "button", onclick: openAccountMenu }, h("span", { class: "avatar" }, initial), h("span", { class: "small" }, state.user.display_name))),
    h("nav", { class: "tabs", id: "tabs" }, NAV.filter((n) => !n.admin || state.user.role === "admin").map((n) =>
      h("a", { class: "tab", href: n.href, "data-id": n.id }, n.label))));
  viewEl = h("main", { class: "container", id: "view" });
  $app.append(top, viewEl);
}

function setActiveTab(id) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.id === id));
}

function openAccountMenu() {
  let m = null;
  const body = h("div", { class: "stack" },
    h("p", {}, "Connecté en tant que ", h("b", {}, state.user.display_name), " (" + state.user.username + ", " + (state.user.role === "admin" ? "administrateur" : "utilisateur") + ")."),
    h("a", { class: "btn", href: "#/compte", onclick: () => { if (m) m.close(); } }, "Changer mon mot de passe"));
  m = modal("Mon compte", body, { actions: [
    { label: "Se déconnecter", kind: "danger", onClick: async () => { try { await api("POST", "/api/logout"); } catch (_) { /* ignoré */ } state.user = null; renderLogin(); } },
    { label: "Fermer" },
  ] });
}

// ------------------------------------------------------------------ liste des biens et des vues
const FILTERS = [["live", "Diffusés"], ["scheduled", "À venir"], ["draft", "Brouillons"], ["expired", "Expirés"], ["trash", "Corbeille"]];

function stateChip(it) {
  if (it.error && !it.pages && it.kind !== "view") return h("span", { class: "chip red" }, "Erreur");
  if (it.busy || it.state === "processing") return h("span", { class: "chip" }, "Traitement…");
  if (it.state === "trash") return h("span", { class: "chip dark" }, "Corbeille");
  if (it.state === "draft") return h("span", { class: "chip light" }, "Brouillon");
  if (it.state === "scheduled") return h("span", { class: "chip orange" }, "À partir du " + fmtDay(it.start_date));
  if (it.state === "expired") return h("span", { class: "chip red" }, "Expiré");
  if (it.days_left !== null && it.days_left <= 7) return h("span", { class: "chip orange" }, it.days_left <= 0 ? "Expire aujourd'hui" : "Expire dans " + it.days_left + " j");
  return h("span", { class: "chip green" }, "Diffusé");
}

function itemCard(it) {
  const href = "#/" + (it.kind === "view" ? "vues" : "biens") + "/" + it.id;
  let thumb;
  if (it.thumb) thumb = h("img", { src: it.thumb, alt: "", loading: "lazy" });
  else if (it.kind === "view") thumb = h("div", { class: "ph" }, VIEW_TYPES[it.view_type] || "Vue");
  else thumb = h("div", { class: "ph" }, it.busy ? "Traitement…" : "Aperçu indisponible");
  const screens = it.screens === "*" ? "Tous les écrans" : it.screens.map(screenName).join(", ");
  return h("a", { class: "card item", href },
    h("div", { class: "thumb" }, thumb,
      it.badge_active ? h("span", { class: "chip red" }, BADGES[it.badge_active]) : null,
      it.pages > 1 ? h("span", { class: "pages" }, it.pages + " pages") : null),
    h("div", { class: "body" },
      h("div", { class: "title" }, it.title || "Sans titre"),
      h("div", { class: "pills" }, stateChip(it)),
      h("div", { class: "meta" },
        h("span", { class: "stars", title: "Importance" }, stars(it.weight)),
        h("span", {}, it.duration ? it.duration + " s" : "durée par défaut"),
        it.end_date ? h("span", {}, "jusqu'au " + fmtDay(it.end_date)) : null),
      h("div", { class: "meta" }, h("span", {}, screens))));
}

async function pageItems(isViews) {
  const key = isViews ? "vues" : "biens";
  setActiveTab(key);
  clear(viewEl);
  let list = h("div", { class: "grid" });
  const filtersEl = h("div", { class: "filters" });
  const tray = h("div", { class: "stack" });
  const search = h("input", { type: "search", placeholder: "Rechercher…", value: state.q, style: { maxWidth: "260px" }, oninput: () => { state.q = search.value; debounceLoad(); } });
  let t = null;
  function debounceLoad() { clearTimeout(t); t = setTimeout(load, 250); }

  const fileInput = h("input", { type: "file", multiple: true, accept: ".pdf,.jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.m4v,.webm,application/pdf,image/*,video/*", class: "hidden", onchange: () => { sendFiles(Array.from(fileInput.files)); fileInput.value = ""; } });
  const addBtn = isViews
    ? h("button", { class: "btn primary", type: "button", onclick: openNewView }, "+ Nouvelle vue")
    : h("button", { class: "btn primary", type: "button", onclick: () => fileInput.click() }, "+ Ajouter une affiche");
  viewEl.append(
    h("div", { class: "pagehead" }, h("h1", { class: "grow" }, isViews ? "Vues" : "Biens"), search, addBtn, fileInput),
    isViews ? h("p", { class: "muted" }, "Les vues sont des écrans d'information intercalés avec vos affiches : annonce, QR code, présentation de l'agence. La météo se règle dans la page Écrans.") : null,
    tray, filtersEl, list);

  if (!isViews) {
    const drop = h("div", { class: "drop" }, "Glissez vos affiches (PDF, images, vidéos) ici, ou utilisez le bouton « Ajouter une affiche ».");
    tray.after(drop);
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => sendFiles(Array.from(e.dataTransfer.files)));
  }

  async function sendFiles(files) {
    const ok = /\.(pdf|jpe?g|png|webp|gif|mp4|m4v|mov|webm)$/i;
    files = files.filter((f) => { if (!ok.test(f.name)) { toast("« " + f.name + " » n'est pas un format accepté.", true); return false; } return true; });
    if (!files.length) return;
    const created = [];
    for (const file of files) {
      const bar = h("div", {});
      const row = h("div", { class: "card" }, h("div", { class: "small" }, file.name), h("div", { class: "progress" }, bar));
      tray.append(row);
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await upload("/api/items", form, (r) => { bar.style.width = Math.round(r * 100) + "%"; });
        created.push(res.id);
        row.remove();
      } catch (err) { row.remove(); toast("« " + file.name + " » : " + err.message, true); }
    }
    if (created.length === 1) location.hash = "#/biens/" + created[0];
    else if (created.length) { toast(created.length + " affiches ajoutées"); load(); }
  }

  async function load() {
    let data;
    try { data = await api("GET", "/api/items?views=" + (isViews ? 1 : 0) + "&state=" + state.filters[key] + "&q=" + encodeURIComponent(state.q)); }
    catch (err) { toast(err.message, true); return; }
    clear(filtersEl);
    FILTERS.forEach(([id, label]) => {
      filtersEl.append(h("button", { type: "button", class: "pill" + (state.filters[key] === id ? " on" : ""), onclick: () => { state.filters[key] = id; load(); } },
        label, h("span", { class: "n" }, data.counts[id])));
    });
    clear(list);
    if (!data.items.length) {
      const msgs = { live: isViews ? "Aucune vue pour le moment." : "Aucune affiche diffusée. Ajoutez-en une avec le bouton rouge.", scheduled: "Rien de programmé.", draft: "Aucun brouillon.", expired: "Aucun contenu expiré.", trash: "La corbeille est vide." };
      list.replaceWith(list = h("div", { class: "empty" }, msgs[state.filters[key]]));
    } else if (!list.classList.contains("grid")) {
      const grid = h("div", { class: "grid" });
      list.replaceWith(grid); list = grid;
    }
    if (data.items.length) data.items.forEach((it) => list.append(itemCard(it)));
    if (data.items.some((i) => i.busy)) setTimeout(() => { if (document.contains(list)) load(); }, 2000);
  }
  await load();
}

function openNewView() {
  const types = [
    ["texte", "Annonce", "Un titre et un texte : promotion, information, vœux…"],
    ["qr", "QR code", "Un QR code vers votre site, une estimation en ligne, une offre d'emploi…"],
    ["agence", "Agence", "Nom, adresse, téléphone et horaires de l'agence."],
  ];
  const m = modal("Nouvelle vue", h("div", { class: "typegrid" }, types.map(([type, label, desc]) =>
    h("button", { class: "typebtn", type: "button", onclick: async () => {
      try {
        const res = await api("POST", "/api/items/view", { view_type: type, view_data: type === "qr" ? { url: "https://www.orpi.com" } : {} });
        m.close();
        location.hash = "#/vues/" + res.item.id;
      } catch (err) { toast(err.message, true); }
    } }, h("b", {}, label), h("span", { class: "muted small" }, desc)))), { actions: [{ label: "Annuler" }], noFocus: true });
}

// ------------------------------------------------------------------ page d'un bien ou d'une vue
function openPreview(options) {
  const screens = state.screens;
  let slug = options.slug || (screens[0] && screens[0].slug) || "default";
  let orientation = (screens.find((s) => s.slug === slug) || {}).orientation || "portrait";
  const holder = h("div", { style: { textAlign: "center" } });
  function draw() {
    clear(holder);
    const vh = Math.min(window.innerHeight * 0.62, 640);
    const vw = Math.min(window.innerWidth - 80, 640);
    let w, hgt;
    if (orientation === "portrait") { hgt = vh; w = hgt * 9 / 16; } else { w = vw; hgt = w * 9 / 16; }
    const src = "/?screen=" + encodeURIComponent(slug) + "&preview=1" + (options.itemId ? "&item=" + options.itemId : "");
    holder.append(h("div", { class: "frame", style: { width: w + "px", height: hgt + "px" } }, h("iframe", { src, title: "Aperçu" })));
  }
  const sel = h("select", { onchange: () => { slug = sel.value; orientation = (screens.find((s) => s.slug === slug) || {}).orientation || orientation; draw(); } },
    screens.map((s) => h("option", { value: s.slug, selected: s.slug === slug }, s.name)));
  const orient = segmented([{ value: "portrait", label: "Portrait" }, { value: "landscape", label: "Paysage" }], orientation, (v) => { orientation = v; draw(); });
  const body = h("div", { class: "stack" }, h("div", { class: "row" }, field("Écran", sel), field("Format", orient)), holder,
    h("p", { class: "muted small" }, options.itemId ? "Aperçu de ce contenu seul, tel qu'il apparaîtra à l'écran (la météo n'est pas montrée)." : "Aperçu de l'enchaînement de cet écran, avec les mêmes règles de diffusion qu'en vitrine."));
  draw();
  modal(options.title || "Aperçu", body, { wide: true, actions: [{ label: "Fermer", kind: "primary" }], noFocus: true });
}

async function pageItem(id, expectView) {
  setActiveTab(expectView ? "vues" : "biens");
  clear(viewEl);
  let it;
  try { it = (await api("GET", "/api/items/" + id)).item; }
  catch (err) { viewEl.append(h("div", { class: "banner err" }, err.message)); return; }
  const isView = it.kind === "view";
  const back = isView ? "#/vues" : "#/biens";
  const draft = {
    status: it.status === "trash" ? "draft" : it.status, weight: it.weight, badge: it.badge,
    allScreens: it.screens === "*", screens: it.screens === "*" ? state.screens.map((s) => s.slug) : it.screens.slice(),
  };
  const trashed = it.status === "trash";
  let origTitle = it.title;

  const title = h("input", { type: "text", value: it.title, maxlength: 200 });
  const ref = h("input", { type: "text", value: it.ref, maxlength: 60, placeholder: "n° de mandat, référence SweepBright…" });
  const price = h("input", { type: "number", min: 0, step: 1000, value: it.price === null ? "" : it.price, placeholder: "ex. 299520" });
  const duration = h("input", { type: "number", min: 3, max: 600, value: it.duration === null ? "" : it.duration, placeholder: "durée par défaut de l'écran" });
  const start = h("input", { type: "date", value: it.start_date || "" });
  const end = h("input", { type: "date", value: it.end_date || "" });
  const badgeUntil = h("input", { type: "date", value: it.badge_until || "" });
  const notes = h("textarea", { maxlength: 1000, placeholder: "Notes internes (non affichées à l'écran)" }, it.notes);
  const badgeSel = h("select", { onchange: () => { draft.badge = badgeSel.value; badgeRow.classList.toggle("hidden", !draft.badge); if (draft.badge && !badgeUntil.value) badgeUntil.value = ""; } },
    Object.keys(BADGES).map((k) => h("option", { value: k, selected: k === it.badge }, BADGES[k])));
  const badgeRow = h("div", { class: draft.badge ? "" : "hidden" }, field("Pastille jusqu'au", badgeUntil, "Vide = jusqu'à retrait manuel. Remplie automatiquement à l'enregistrement."));

  const addDays = (n) => {
    const d = new Date(); d.setDate(d.getDate() + n);
    end.value = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  };
  const shortcuts = h("div", { class: "pills", style: { marginTop: "6px" } },
    [[30, "+ 30 jours"], [60, "+ 60 jours"], [90, "+ 90 jours"]].map(([n, label]) => h("button", { class: "btn small", type: "button", onclick: () => addDays(n) }, label)),
    h("button", { class: "btn small", type: "button", onclick: () => { end.value = ""; } }, "Sans fin"));

  const allChk = h("input", { type: "checkbox", checked: draft.allScreens, onchange: () => { draft.allScreens = allChk.checked; boxes.forEach((b) => { b.input.checked = draft.allScreens || b.input.checked; }); } });
  const boxes = state.screens.map((s) => ({ slug: s.slug, input: h("input", { type: "checkbox", checked: draft.allScreens || draft.screens.indexOf(s.slug) !== -1, onchange: () => {
    const on = boxes.filter((b) => b.input.checked).map((b) => b.slug);
    draft.screens = on; draft.allScreens = on.length === state.screens.length; allChk.checked = draft.allScreens;
  } }), name: s.name }));
  const screensEl = h("div", { class: "checks" },
    h("label", { class: "chk" }, allChk, "Tous les écrans"),
    boxes.map((b) => h("label", { class: "chk" }, b.input, b.name)));

  const imp = segmented([
    { value: 1, label: "1", sub: "Normale" }, { value: 2, label: "2" }, { value: 3, label: "3", sub: "Fréquente" }, { value: 4, label: "4" }, { value: 5, label: "5", sub: "Très fréquente" },
  ], it.weight, (v) => { draft.weight = v; });
  const statusSeg = segmented([{ value: "active", label: "Diffusé" }, { value: "draft", label: "Brouillon" }], draft.status, (v) => { draft.status = v; });

  // contenu propre aux vues
  let viewFields = null, collectView = null;
  if (isView) {
    const d = it.view_data || {};
    const inputs = {};
    const t = it.view_type;
    const mkInput = (name, label, opts) => {
      opts = opts || {};
      inputs[name] = opts.area ? h("textarea", { maxlength: opts.max || 300 }, d[name] || "") : h("input", { type: opts.type || "text", value: d[name] || "", maxlength: opts.max || 200, placeholder: opts.placeholder || "" });
      return field(label, inputs[name], opts.hint);
    };
    if (t === "texte") {
      inputs.theme = h("select", {}, [["creme", "Crème (sobre)"], ["rouge", "Rouge Orpi"], ["vert", "Vert Orpi"], ["sombre", "Sombre"]].map(([v, l]) => h("option", { value: v, selected: (d.theme || "creme") === v }, l)));
      viewFields = [mkInput("heading", "Titre", { max: 80 }), mkInput("text", "Texte", { area: true, max: 600 }), field("Couleur de fond", inputs.theme)];
    } else if (t === "qr") {
      viewFields = [mkInput("heading", "Titre", { max: 80, placeholder: "Estimez votre bien" }), mkInput("url", "Adresse du QR code (URL)", { type: "url", max: 300, placeholder: "https://…" }), mkInput("caption", "Légende", { max: 200, placeholder: "Scannez pour une estimation gratuite" })];
    } else {
      viewFields = [mkInput("heading", "Titre", { max: 80, placeholder: "Notre agence" }), mkInput("name", "Nom de l'agence", { max: 80, hint: "Laissez vide pour reprendre le nom saisi dans les Réglages." }),
        mkInput("address", "Adresse", { max: 200 }), mkInput("phone", "Téléphone", { max: 40 }), mkInput("hours", "Horaires", { area: true, max: 300 }), mkInput("site", "Site web", { max: 100 })];
    }
    collectView = () => { const out = {}; Object.keys(inputs).forEach((k) => { out[k] = inputs[k].value; }); return out; };
  }

  async function save(btn) {
    const payload = {
      title: title.value, ref: ref.value, notes: notes.value, status: draft.status, weight: draft.weight,
      duration: duration.value === "" ? null : parseInt(duration.value, 10),
      start_date: start.value || null, end_date: end.value || null,
      badge: draft.badge, badge_until: draft.badge ? (badgeUntil.value || null) : null,
      screens: draft.allScreens ? "*" : draft.screens,
    };
    if (!isView) payload.price = price.value === "" ? null : parseInt(price.value, 10);
    if (isView) payload.view_data = collectView();
    if (!draft.allScreens && !draft.screens.length) { toast("Choisissez au moins un écran.", true); return; }
    btn.disabled = true;
    try {
      const res = await api("PATCH", "/api/items/" + id, payload);
      toast("Enregistré");
      it = res.item;
      if (it.badge && !badgeUntil.value && it.badge_until) badgeUntil.value = it.badge_until;
    } catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; }
  }

  // colonne de gauche : aperçu
  const left = h("div", { class: "card stack" });
  const mediaBox = h("div", { class: "preview-box" });
  function drawMedia() {
    clear(mediaBox);
    if (isView) { mediaBox.append(h("div", { class: "empty" }, h("div", { class: "big" }, "▣"), "Vue « " + (VIEW_TYPES[it.view_type] || "") + " »")); return; }
    if (it.busy) { mediaBox.append(h("div", { class: "empty" }, "Traitement du fichier en cours…")); return; }
    if (it.error && !it.slides.length) { mediaBox.append(h("div", { class: "banner err" }, "Ce fichier n'a pas pu être traité : " + it.error)); return; }
    const first = it.slides[0];
    if (first && first.type === "video") mediaBox.append(h("video", { src: first.src, controls: true, muted: true, playsinline: true, style: { width: "100%", borderRadius: "10px" } }));
    else if (first) mediaBox.append(h("img", { src: first.src, alt: "Affiche" }));
    if (it.slides.length > 1) mediaBox.append(h("div", { class: "slides" }, it.slides.map((s) => h("img", { src: s.src, alt: "", loading: "lazy" }))));
    if (it.error) mediaBox.append(h("div", { class: "banner err", style: { marginTop: "10px" } }, "Dernier remplacement impossible : " + it.error));
  }
  drawMedia();
  left.append(mediaBox);

  const replaceInput = h("input", { type: "file", class: "hidden", accept: ".pdf,.jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.m4v,.webm,application/pdf,image/*,video/*", onchange: async () => {
    const file = replaceInput.files[0];
    replaceInput.value = "";
    if (!file) return;
    const bar = h("div", {});
    const prog = h("div", { class: "progress" }, bar);
    left.append(prog);
    try {
      const form = new FormData(); form.append("file", file);
      await upload("/api/items/" + id + "/replace", form, (r) => { bar.style.width = Math.round(r * 100) + "%"; });
      prog.remove();
      toast("Fichier reçu, traitement en cours…");
      it.busy = true; drawMedia();
      await waitIdle();
    } catch (err) { prog.remove(); toast(err.message, true); }
  } });

  async function waitIdle() {
    for (let i = 0; i < 900; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      if (!document.contains(viewEl) || !location.hash.endsWith("/" + id)) return;
      try { it = (await api("GET", "/api/items/" + id)).item; } catch (_) { return; }
      if (!it.busy) {
        const wasBadge = draft.badge;
        draft.badge = it.badge; badgeSel.value = it.badge; badgeRow.classList.toggle("hidden", !it.badge); badgeUntil.value = it.badge_until || "";
        if (it.price !== null) price.value = it.price;
        if (title.value === origTitle) title.value = it.title;   // titre lu dans le PDF, si l'utilisateur ne l'a pas modifié
        origTitle = it.title;
        drawMedia();
        if (it.error) toast("Remplacement impossible : " + it.error, true);
        else toast(it.badge === "baisse" && wasBadge !== "baisse" ? "Affiche remplacée : la pastille « Baisse de prix » a été ajoutée." : "Affiche remplacée.");
        return;
      }
    }
  }

  if (!isView) {
    left.append(h("div", { class: "row" },
      h("button", { class: "btn", type: "button", disabled: trashed, onclick: () => replaceInput.click() }, "Remplacer l'affiche"), replaceInput));
  }
  left.append(h("button", { class: "btn", type: "button", onclick: () => openPreview({ itemId: id, title: it.title || "Aperçu" }) }, "Voir sur un écran"));
  const histEl = h("div", {}, h("h3", {}, "Historique"), h("ul", { class: "history" }, it.history.length ? it.history.map((e) =>
    h("li", {}, h("b", {}, ACTIONS[e.action] || e.action), " · ", e.username, h("div", { class: "muted" }, fmtDateTime(e.ts) + (e.detail ? " — " + e.detail : "")))) : h("li", { class: "muted" }, "Aucun historique.")));
  left.append(histEl);

  // colonne de droite : formulaire
  const saveBtn = h("button", { class: "btn primary", type: "button", disabled: trashed, onclick: () => save(saveBtn) }, "Enregistrer");
  const actions = h("div", { class: "actionbar" }, saveBtn,
    h("a", { class: "btn", href: back }, "Retour"),
    h("div", { class: "grow" }));
  if (trashed) {
    actions.append(h("button", { class: "btn", type: "button", onclick: async () => { await api("POST", "/api/items/" + id + "/restore"); toast("Restauré en brouillon"); location.hash = back; } }, "Restaurer"));
    if (state.user.role === "admin") actions.append(h("button", { class: "btn danger", type: "button", onclick: async () => {
      if (await confirmBox("Supprimer définitivement « " + it.title + " » et ses fichiers ? Cette action est irréversible.", "Supprimer", true)) {
        try { await api("DELETE", "/api/items/" + id); toast("Supprimé"); location.hash = back; } catch (err) { toast(err.message, true); }
      }
    } }, "Supprimer définitivement"));
  } else {
    actions.append(h("button", { class: "btn danger", type: "button", onclick: async () => {
      if (await confirmBox("Mettre « " + it.title + " » à la corbeille ? Il ne sera plus diffusé ; l'administrateur pourra le récupérer pendant 30 jours.", "Mettre à la corbeille", true)) {
        try { await api("POST", "/api/items/" + id + "/trash"); toast("Mis à la corbeille"); location.hash = back; } catch (err) { toast(err.message, true); }
      }
    } }, "Corbeille"));
  }

  const form = h("div", { class: "card" },
    trashed ? h("div", { class: "banner" }, "Ce contenu est dans la corbeille. Restaurez-le pour le modifier.") : null,
    field("Titre", title),
    isView ? viewFields : null,
    isView ? null : h("div", { class: "row" }, field("Référence", ref), field("Prix affiché (€)", price, "Lu dans le PDF si possible. Sert à détecter une baisse de prix.")),
    field("Statut", statusSeg, "Un brouillon n'est pas diffusé."),
    field("Importance", imp, "Plus l'importance est élevée, plus ce contenu revient souvent dans l'enchaînement."),
    field(isView ? "Durée d'affichage (secondes)" : "Durée par page (secondes)", duration, "Laissez vide pour utiliser la durée par défaut de l'écran."),
    h("div", { class: "row" }, field("Date de début", start, "Par défaut : le jour de l'ajout."), h("div", {}, field("Date de fin", end), shortcuts)),
    field("Pastille sur l'affiche", badgeSel), badgeRow,
    field("Écrans concernés", screensEl),
    field("Notes", notes),
    actions);
  if (trashed) form.querySelectorAll("input, select, textarea").forEach((e) => { e.disabled = true; });

  viewEl.append(
    h("div", { class: "pagehead" }, h("a", { class: "btn small", href: back }, "← Retour"), h("h1", { class: "grow" }, it.title || "Sans titre"), stateChip(it)),
    h("div", { class: "editgrid" }, left, form));
  if (it.busy) waitIdle();
}

// ------------------------------------------------------------------ écrans
async function loadScreens() {
  const data = await api("GET", "/api/screens");
  state.screens = data.screens;
  state.defaultScreen = data.default_screen;
  return data;
}

async function pageScreens() {
  setActiveTab("ecrans");
  clear(viewEl);
  const grid = h("div", { class: "screens" });
  const admin = state.user.role === "admin";
  viewEl.append(h("div", { class: "pagehead" }, h("h1", { class: "grow" }, "Écrans"),
    admin ? h("button", { class: "btn primary", type: "button", onclick: openNewScreen }, "+ Ajouter un écran") : null),
    h("p", { class: "muted" }, "Chaque écran affiche sa propre sélection de contenus. Saisissez l'adresse indiquée sur chaque carte dans l'écran (navigateur, ou programme « Web » de LedArt)."), grid);

  async function draw() {
    let data;
    try { data = await loadScreens(); } catch (err) { toast(err.message, true); return; }
    clear(grid);
    data.screens.forEach((s) => grid.append(screenCard(s, admin, data.default_screen, draw)));
  }
  await draw();
  setTimer(() => { if (document.contains(grid)) draw(); }, 15000);
}

function screenCard(s, admin, defaultSlug, refresh) {
  const url = location.origin + "/?screen=" + s.slug;
  const status = s.online
    ? h("span", {}, h("span", { class: "dot on" }), "En ligne")
    : h("span", {}, h("span", { class: "dot " + (s.last_seen ? "off" : "") }), s.last_seen ? "Hors ligne" : "Jamais connecté");
  const act = (fn, okMsg) => async () => { try { await fn(); if (okMsg) toast(okMsg); refresh(); } catch (err) { toast(err.message, true); } };
  const buttons = h("div", { class: "pills" },
    h("button", { class: "btn small", type: "button", onclick: () => openPreview({ slug: s.slug, title: "Aperçu : " + s.name }) }, "Aperçu"),
    s.paused_until
      ? h("button", { class: "btn small primary", type: "button", onclick: act(() => api("DELETE", "/api/screens/" + s.id + "/pause"), "Diffusion reprise") }, "Reprendre")
      : h("button", { class: "btn small", type: "button", onclick: act(() => api("POST", "/api/screens/" + s.id + "/pause", { minutes: 30 }), "Diffusion en pause pendant 30 minutes") }, "Pause 30 min"),
    s.override
      ? h("button", { class: "btn small primary", type: "button", onclick: act(() => api("DELETE", "/api/screens/" + s.id + "/override"), "Message retiré") }, "Retirer le message")
      : h("button", { class: "btn small", type: "button", onclick: () => openOverride(s, refresh) }, "Message urgent"),
    admin ? h("button", { class: "btn small", type: "button", onclick: () => openScreenSettings(s, refresh) }, "Réglages") : null);
  return h("div", { class: "card stack" },
    h("div", { class: "row", style: { alignItems: "center" } },
      h("h2", { class: "grow" }, s.name), h("span", { class: "small" }, status)),
    h("div", { class: "meta" },
      h("span", {}, s.orientation === "portrait" ? "Portrait" : "Paysage"),
      h("span", {}, "dernier signe : " + ago(s.seconds_since)),
      s.slug === defaultSlug ? h("span", { class: "chip light" }, "écran par défaut") : null,
      s.last_ip ? h("span", {}, s.last_ip) : null),
    s.override ? h("div", { class: "banner" }, "Message affiché : « " + s.override.text + " »") : null,
    s.paused_until ? h("div", { class: "banner" }, "Diffusion en pause jusqu'à " + fmtDateTime(s.paused_until) + ".") : null,
    h("div", {}, h("div", { class: "small muted" }, "Adresse de cet écran"),
      h("div", { class: "urlbox" }, h("span", { class: "grow" }, url),
        h("button", { class: "btn small", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(url); toast("Adresse copiée"); } catch (_) { toast("Copiez l'adresse à la main.", true); } } }, "Copier"))),
    buttons);
}

function openOverride(s, refresh) {
  const text = h("textarea", { maxlength: 300, placeholder: "Ex. : Fermeture exceptionnelle ce midi" });
  const minutes = h("select", {}, [[60, "1 heure"], [180, "3 heures"], [720, "12 heures"], [1440, "24 heures"], [4320, "3 jours"]].map(([v, l]) => h("option", { value: v }, l)));
  modal("Message urgent : " + s.name, h("div", {}, h("p", { class: "muted" }, "Le message remplace l'enchaînement des affiches, en grand, sur fond rouge."), field("Message", text), field("Pendant", minutes)), { actions: [
    { label: "Annuler" },
    { label: "Afficher", kind: "primary", onClick: async () => { await api("POST", "/api/screens/" + s.id + "/override", { text: text.value, minutes: parseInt(minutes.value, 10) }); toast("Message affiché"); refresh(); } },
  ] });
}

function openNewScreen() {
  const name = h("input", { type: "text", maxlength: 60, placeholder: "Ex. : Hall d'entrée" });
  const orient = h("select", {}, [["portrait", "Portrait"], ["landscape", "Paysage"]].map(([v, l]) => h("option", { value: v }, l)));
  modal("Ajouter un écran", h("div", {}, field("Nom", name), field("Orientation", orient)), { actions: [
    { label: "Annuler" },
    { label: "Créer", kind: "primary", onClick: async () => { await api("POST", "/api/screens", { name: name.value, orientation: orient.value }); toast("Écran ajouté"); pageScreens(); } },
  ] });
}

function openScreenSettings(s, refresh) {
  const f = {
    name: h("input", { type: "text", value: s.name, maxlength: 60 }),
    orientation: h("select", {}, [["portrait", "Portrait"], ["landscape", "Paysage"]].map(([v, l]) => h("option", { value: v, selected: v === s.orientation }, l))),
    order_mode: h("select", {}, [["random", "Aléatoire pondéré (conseillé)"], ["recent", "Du plus récent au plus ancien"]].map(([v, l]) => h("option", { value: v, selected: v === s.order_mode }, l))),
    default_duration: h("input", { type: "number", min: 3, max: 600, value: s.default_duration }),
    fit: h("select", {}, [["contain", "Affiche entière, bandes noires"], ["blur", "Affiche entière, fond flou"], ["cover", "Remplir l'écran (recadrée)"]].map(([v, l]) => h("option", { value: v, selected: v === s.fit }, l))),
    weather_every: h("input", { type: "number", min: 0, max: 50, value: s.weather_every }),
    weather_seconds: h("input", { type: "number", min: 3, max: 120, value: s.weather_seconds }),
    corner_clock: h("input", { type: "checkbox", checked: s.corner_clock }),
    ticker: h("textarea", { maxlength: 1000 }, s.ticker),
    open_time: h("input", { type: "time", value: s.open_time }),
    close_time: h("input", { type: "time", value: s.close_time }),
    hours_enabled: h("input", { type: "checkbox", checked: s.hours_enabled }),
  };
  const body = h("div", {},
    h("div", { class: "row" }, field("Nom", f.name), field("Orientation", f.orientation)),
    h("div", { class: "row" }, field("Ordre de diffusion", f.order_mode), field("Durée par défaut (s)", f.default_duration)),
    field("Affichage des affiches", f.fit),
    h("div", { class: "row" }, field("Météo toutes les … diapositives", f.weather_every, "0 = jamais"), field("Durée de la météo (s)", f.weather_seconds)),
    h("label", { class: "chk", style: { marginBottom: "14px" } }, f.corner_clock, "Petite horloge en coin sur toutes les diapositives"),
    field("Bandeau de messages défilants", f.ticker, "Un message par ligne. Vide = pas de bandeau."),
    h("div", { class: "row" }, field("Ouverture (allumage)", f.open_time, "Sert aussi aux alertes « écran hors ligne »."), field("Fermeture (extinction)", f.close_time)),
    h("label", { class: "chk", style: { marginBottom: "14px" } }, f.hours_enabled, "Écran noir en dehors de ces heures (la vitrine ne diffuse plus rien la nuit)"));
  const actions = [{ label: "Annuler" }];
  if (s.slug !== state.defaultScreen) {
    actions.unshift({ label: "Supprimer", kind: "danger", onClick: async () => {
      if (!(await confirmBox("Supprimer l'écran « " + s.name + " » ?", "Supprimer", true))) return false;
      await api("DELETE", "/api/screens/" + s.id); toast("Écran supprimé"); refresh();
    } });
  }
  actions.push({ label: "Enregistrer", kind: "primary", onClick: async () => {
    await api("PATCH", "/api/screens/" + s.id, {
      name: f.name.value, orientation: f.orientation.value, order_mode: f.order_mode.value, fit: f.fit.value,
      default_duration: parseInt(f.default_duration.value, 10), weather_every: parseInt(f.weather_every.value, 10),
      weather_seconds: parseInt(f.weather_seconds.value, 10), corner_clock: f.corner_clock.checked, ticker: f.ticker.value,
      open_time: f.open_time.value, close_time: f.close_time.value, hours_enabled: f.hours_enabled.checked,
    });
    toast("Réglages enregistrés"); refresh();
  } });
  modal("Réglages : " + s.name, body, { wide: true, actions });
}

// ------------------------------------------------------------------ utilisateurs
async function pageUsers() {
  setActiveTab("utilisateurs");
  clear(viewEl);
  const box = h("div", { class: "stack" });
  viewEl.append(h("div", { class: "pagehead" }, h("h1", { class: "grow" }, "Utilisateurs"),
    h("button", { class: "btn primary", type: "button", onclick: openNewUser }, "+ Nouvel utilisateur")),
    h("p", { class: "muted" }, "L'administrateur gère les comptes, les écrans et les réglages. Les utilisateurs ajoutent et modifient les affiches ; seul l'administrateur supprime définitivement."), box);

  async function draw() {
    let data;
    try { data = await api("GET", "/api/users"); } catch (err) { toast(err.message, true); return; }
    clear(box);
    const table = h("table", { class: "table" }, h("thead", {}, h("tr", {}, ["Nom", "Identifiant", "Rôle", "Dernière connexion", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, data.users.map((u) => h("tr", {},
        h("td", {}, h("b", {}, u.display_name), !u.active ? h("span", { class: "chip dark", style: { marginLeft: "6px" } }, "désactivé") : null, u.must_change_pw ? h("span", { class: "chip orange", style: { marginLeft: "6px" } }, "mot de passe temporaire") : null),
        h("td", {}, u.username),
        h("td", {}, u.role === "admin" ? "Administrateur" : "Utilisateur"),
        h("td", {}, u.last_login ? fmtDateTime(u.last_login) : "jamais"),
        h("td", {}, h("button", { class: "btn small", type: "button", onclick: () => openEditUser(u, draw) }, "Modifier"))))));
    box.append(h("div", { class: "card scroll" }, table));
  }
  await draw();

  function openNewUser() {
    const username = h("input", { type: "text", autocapitalize: "none", maxlength: 32, placeholder: "ex. marie" });
    const name = h("input", { type: "text", maxlength: 60, placeholder: "ex. Marie Dupont" });
    const role = h("select", {}, [["user", "Utilisateur"], ["admin", "Administrateur"]].map(([v, l]) => h("option", { value: v }, l)));
    modal("Nouvel utilisateur", h("div", {}, field("Identifiant", username, "Lettres, chiffres, point ou tiret."), field("Nom affiché", name), field("Rôle", role)), { actions: [
      { label: "Annuler" },
      { label: "Créer le compte", kind: "primary", onClick: async (close) => {
        const res = await api("POST", "/api/users", { username: username.value, display_name: name.value, role: role.value });
        close(); draw();
        showPassword("Compte créé", "Transmettez ce mot de passe temporaire à " + res.user.display_name + " (identifiant : " + res.user.username + ").", res.temporary_password);
        return false;
      } },
    ] });
  }
}

function openEditUser(u, refresh) {
  const name = h("input", { type: "text", value: u.display_name, maxlength: 60 });
  const role = h("select", {}, [["user", "Utilisateur"], ["admin", "Administrateur"]].map(([v, l]) => h("option", { value: v, selected: v === u.role }, l)));
  const self = u.id === state.user.id;
  const body = h("div", { class: "stack" }, field("Nom affiché", name), field("Rôle", role),
    self ? h("p", { class: "muted small" }, "C'est votre compte : vous ne pouvez ni le désactiver, ni changer votre rôle, ni le supprimer.") : null);
  if (self) role.disabled = true;
  const actions = [{ label: "Fermer" }];
  if (!self) {
    actions.unshift({ label: "Supprimer", kind: "danger", onClick: async () => {
      if (!(await confirmBox("Supprimer le compte « " + u.username + " » ?", "Supprimer", true))) return false;
      await api("DELETE", "/api/users/" + u.id); toast("Compte supprimé"); refresh();
    } });
    actions.splice(1, 0, { label: u.active ? "Désactiver" : "Réactiver", onClick: async () => { await api("PATCH", "/api/users/" + u.id, { active: !u.active }); toast(u.active ? "Compte désactivé" : "Compte réactivé"); refresh(); } });
  }
  actions.splice(actions.length - 1, 0, { label: "Réinitialiser le mot de passe", onClick: async (close) => {
    if (!(await confirmBox("Générer un nouveau mot de passe temporaire pour « " + u.username + " » ?", "Réinitialiser"))) return false;
    const res = await api("POST", "/api/users/" + u.id + "/reset-password");
    close(); refresh();
    showPassword("Nouveau mot de passe", "Mot de passe temporaire de " + u.display_name + " :", res.temporary_password);
    return false;
  } });
  actions.push({ label: "Enregistrer", kind: "primary", onClick: async () => {
    await api("PATCH", "/api/users/" + u.id, { display_name: name.value, role: role.value });
    toast("Enregistré"); refresh();
  } });
  modal("Compte : " + u.username, body, { actions });
}

// ------------------------------------------------------------------ historique
async function pageAudit() {
  setActiveTab("historique");
  clear(viewEl);
  let data;
  try { data = await api("GET", "/api/audit?limit=300"); } catch (err) { viewEl.append(h("div", { class: "banner err" }, err.message)); return; }
  viewEl.append(h("div", { class: "pagehead" }, h("h1", { class: "grow" }, "Historique")),
    h("p", { class: "muted" }, "Les 300 dernières actions : qui a fait quoi, et quand."),
    h("div", { class: "card scroll" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["Date", "Qui", "Action", "Détail"].map((x) => h("th", {}, x)))),
      h("tbody", {}, data.audit.map((a) => h("tr", {}, h("td", {}, fmtDateTime(a.ts)), h("td", {}, a.username), h("td", {}, ACTIONS[a.action] || a.action),
        h("td", { class: "muted" }, a.detail || "")))))));
}

// ------------------------------------------------------------------ réglages
async function pageSettings() {
  setActiveTab("reglages");
  clear(viewEl);
  let settings, system;
  try {
    settings = (await api("GET", "/api/settings")).settings;
    system = await api("GET", "/api/system");
    await loadScreens();
  } catch (err) { viewEl.append(h("div", { class: "banner err" }, err.message)); return; }
  const f = {};
  const txt = (key, opts) => { f[key] = h("input", Object.assign({ type: "text", value: settings[key] }, opts || {})); return f[key]; };
  const num = (key, min, max) => { f[key] = h("input", { type: "number", min, max, value: settings[key] }); return f[key]; };

  f.default_screen = h("select", {}, state.screens.map((s) => h("option", { value: s.slug, selected: s.slug === settings.default_screen }, s.name)));
  f.smtp_security = h("select", {}, [["starttls", "STARTTLS (587)"], ["ssl", "SSL/TLS (465)"], ["none", "Aucune"]].map(([v, l]) => h("option", { value: v, selected: v === settings.smtp_security }, l)));
  f.alerts_enabled = h("input", { type: "checkbox", checked: settings.alerts_enabled });
  f.smtp_password = h("input", { type: "password", autocomplete: "new-password", placeholder: settings.smtp_password_set ? "•••••• (inchangé)" : "" });

  const t = system.temperature || {};
  const sysCard = h("div", { class: "card stack" }, h("h2", {}, "Système"),
    h("div", { class: "meta", style: { fontSize: "0.95rem" } },
      h("span", {}, "Version " + system.version),
      h("span", {}, "Température du NUC : " + (t.current_c !== null && t.current_c !== undefined ? t.current_c + " °C (max 24 h : " + t.max_24h_c + " °C)" : "non disponible")),
      h("span", {}, "Disque libre : " + system.disk_free_gb + " Go sur " + system.disk_total_gb + " Go"),
      system.uptime_seconds ? h("span", {}, "Allumé depuis " + Math.round(system.uptime_seconds / 3600) + " h") : null),
    Object.keys(system.tools).some((k) => !system.tools[k]) ? h("div", { class: "banner err" }, "Outil manquant : " + Object.keys(system.tools).filter((k) => !system.tools[k]).join(", ")) : null,
    h("div", {}, h("a", { class: "btn", href: "/api/backup", download: "" }, "Télécharger une sauvegarde de la base"),
      h("p", { class: "muted small" }, "Une sauvegarde automatique est faite chaque nuit (14 conservées dans data/backups). Les affiches se trouvent dans le dossier data/media du NUC.")));

  async function saveSettings() {
    saveBtn.disabled = true;
    try {
      const body = {
        agency_name: f.agency_name.value, agency_address: f.agency_address.value, agency_phone: f.agency_phone.value,
        agency_hours: f.agency_hours.value, agency_site: f.agency_site.value, city: f.city.value, latitude: f.latitude.value,
        longitude: f.longitude.value, timezone: f.timezone.value, default_duration: parseInt(f.default_duration.value, 10),
        default_weight: parseInt(f.default_weight.value, 10), new_badge_days: parseInt(f.new_badge_days.value, 10),
        default_screen: f.default_screen.value, alerts_enabled: f.alerts_enabled.checked, alert_to: f.alert_to.value,
        smtp_host: f.smtp_host.value, smtp_port: parseInt(f.smtp_port.value, 10) || 587, smtp_security: f.smtp_security.value,
        smtp_user: f.smtp_user.value, smtp_from: f.smtp_from.value, smtp_password: f.smtp_password.value,
      };
      const res = await api("PUT", "/api/settings", body);
      settings = res.settings; f.smtp_password.value = "";
      toast("Réglages enregistrés");
      return true;
    } catch (err) { toast(err.message, true); return false; }
    finally { saveBtn.disabled = false; }
  }
  const saveBtn = h("button", { class: "btn primary", type: "button", onclick: saveSettings }, "Enregistrer les réglages");

  viewEl.append(h("div", { class: "pagehead" }, h("h1", { class: "grow" }, "Réglages")),
    h("div", { class: "stack" },
      h("div", { class: "card" }, h("h2", {}, "Agence"), h("p", { class: "muted small" }, "Ces informations alimentent la vue « Agence »."),
        h("div", { class: "row" }, field("Nom", txt("agency_name")), field("Site web", txt("agency_site"))),
        field("Adresse", txt("agency_address")), field("Téléphone", txt("agency_phone")), field("Horaires", txt("agency_hours"), "Ex. : Lundi-vendredi 9h-12h30 / 14h-18h30 ; samedi 9h-12h30")),
      h("div", { class: "card" }, h("h2", {}, "Météo"),
        h("div", { class: "row" }, field("Ville", txt("city")), field("Fuseau horaire", txt("timezone"))),
        h("div", { class: "row" }, field("Latitude", txt("latitude")), field("Longitude", txt("longitude")))),
      h("div", { class: "card" }, h("h2", {}, "Valeurs par défaut"),
        h("div", { class: "row" }, field("Durée d'une page (s)", num("default_duration", 3, 600)), field("Importance d'un nouveau bien (1-5)", num("default_weight", 1, 5)),
          field("Durée des pastilles « Nouveau » et « Baisse de prix » (jours)", num("new_badge_days", 1, 365))),
        field("Écran par défaut", f.default_screen, "C'est celui affiché quand l'adresse du player n'indique aucun écran (le DynaScan du NUC).")),
      h("div", { class: "card" }, h("h2", {}, "Alertes par e-mail"),
        h("p", { class: "muted small" }, "Prévient si un écran ne répond plus pendant ses heures d'ouverture, ou si le NUC chauffe trop. Les informations de connexion sont conservées sur le NUC."),
        h("label", { class: "chk", style: { marginBottom: "14px" } }, f.alerts_enabled, "Activer les alertes"),
        field("Envoyer à", txt("alert_to", { type: "email", placeholder: "vous@exemple.fr (plusieurs adresses : séparées par une virgule)" })),
        h("div", { class: "row" }, field("Serveur SMTP", txt("smtp_host", { placeholder: "smtp.gmail.com" })), field("Port", num("smtp_port", 1, 65535)), field("Sécurité", f.smtp_security)),
        h("div", { class: "row" }, field("Identifiant SMTP", txt("smtp_user")), field("Mot de passe SMTP", f.smtp_password, "Pour Gmail : un « mot de passe d'application »."), field("Expéditeur", txt("smtp_from", { placeholder: "vitrine@exemple.fr" }))),
        h("button", { class: "btn", type: "button", onclick: async (e) => {
          const b = e.currentTarget; b.disabled = true;
          try { if (!(await saveSettings())) return; const r = await api("POST", "/api/settings/test-email"); toast("E-mail de test envoyé à " + r.sent_to); } catch (err) { toast(err.message, true); }
          finally { b.disabled = false; }
        } }, "Enregistrer et envoyer un e-mail de test")),
      saveBtn, sysCard));
}

// ------------------------------------------------------------------ mon compte
function pageAccount() {
  setActiveTab("");
  clear(viewEl);
  const cur = h("input", { type: "password", autocomplete: "current-password" });
  const p1 = h("input", { type: "password", autocomplete: "new-password", minlength: 8 });
  const p2 = h("input", { type: "password", autocomplete: "new-password", minlength: 8 });
  const btn = h("button", { class: "btn primary", type: "button", onclick: async () => {
    if (p1.value !== p2.value) { toast("Les deux mots de passe sont différents.", true); return; }
    btn.disabled = true;
    try { await api("POST", "/api/me/password", { current: cur.value, new: p1.value }); toast("Mot de passe changé"); cur.value = p1.value = p2.value = ""; }
    catch (err) { toast(err.message, true); }
    finally { btn.disabled = false; }
  } }, "Changer mon mot de passe");
  viewEl.append(h("div", { class: "pagehead" }, h("h1", { class: "grow" }, "Mon compte")),
    h("div", { class: "card", style: { maxWidth: "460px" } }, field("Mot de passe actuel", cur), field("Nouveau mot de passe", p1, "8 caractères minimum."), field("Confirmer", p2), btn));
}

// ------------------------------------------------------------------ routes
async function route() {
  if (!state.user) return;
  clearInterval(state.timer);
  const parts = (location.hash || "#/biens").replace(/^#\/?/, "").split("/");
  const page = parts[0] || "biens";
  try {
    if (page === "biens") parts[1] ? await pageItem(parseInt(parts[1], 10), false) : await pageItems(false);
    else if (page === "vues") parts[1] ? await pageItem(parseInt(parts[1], 10), true) : await pageItems(true);
    else if (page === "ecrans") await pageScreens();
    else if (page === "utilisateurs" && state.user.role === "admin") await pageUsers();
    else if (page === "historique" && state.user.role === "admin") await pageAudit();
    else if (page === "reglages" && state.user.role === "admin") await pageSettings();
    else if (page === "compte") pageAccount();
    else { location.hash = "#/biens"; }
  } catch (err) { if (err.message !== "Session expirée") { clear(viewEl); viewEl.append(h("div", { class: "banner err" }, err.message)); } }
  window.scrollTo(0, 0);
}

async function start() {
  if (state.user.must_change_pw) { renderForcedPassword(); return; }
  buildShell();
  try { await loadScreens(); } catch (_) { /* ignoré */ }
  if (!location.hash) location.hash = "#/biens";
  await route();
}

window.addEventListener("hashchange", route);

(async function init() {
  try {
    const me = await fetch("/api/me", { credentials: "same-origin" });
    if (me.ok) {
      const data = await me.json();
      state.user = data.user; state.csrf = data.csrf;
      await start();
      return;
    }
  } catch (_) { /* hors ligne : écran de connexion */ }
  renderLogin();
})();
