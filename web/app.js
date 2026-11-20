/* Cartouche : interface (JavaScript sans dépendance). */
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="cartouche-token"]').content;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
  const icon = (name, cls = "") => `<i class="ph-duotone ph-${name}${cls ? " " + cls : ""}" aria-hidden="true"></i>`;

  const LEVEL_ICONS = { ok: "check-circle", success: "check-circle", info: "info", warn: "warning-circle", error: "x-circle" };
  const STATUS_ICONS = {
    installed: "check-circle", not_installed: "circle-dashed", needs_base: "warning-circle",
    unsupported: "prohibit", unknown: "question",
  };
  const COMPAT_TEXT = {
    playable: "Jouable du début à la fin, avec parfois quelques défauts.",
    ingame: "On peut jouer, mais avec des problèmes visibles (graphismes, lenteurs ou plantages).",
    menus: "Arrive jusqu'aux menus, mais ne lance pas de partie.",
    boots: "Démarre, sans aller plus loin pour l'instant.",
    nothing: "Ne démarre pas encore avec shadPS4.",
    unknown: "Pas encore évalué.",
  };
  const FIX_LABELS = {
    confirm_install: "Réinstaller",
    pick_pkg_extractor: "Choisir pkg_extractor.exe",
    pick_games_dir: "Choisir le dossier",
    pick_rpcs3_exe: "Choisir rpcs3.exe",
    pick_shadps4_exe: "Choisir shadPS4.exe",
    install_firmware: "Installer le firmware",
    null_gpu: "Réactiver le rendu",
    light_preset: "Appliquer le préréglage léger",
    update_shadps4: "Installer la mise à jour",
  };

  const state = {
    view: "library",
    library: null,
    settings: null,
    emu: null,
    diag: null,
    updates: null,
    status: null,
    revision: -1,
    lastEvent: 0,
    filters: { q: "", platform: "all", kind: "all" },
    drawer: null,
    openLogs: new Set(),
    enter: true,
  };

  // ------------------------------------------------------------------ API

  class ApiError extends Error {
    constructor(message, fix, status) {
      super(message);
      this.fix = fix;
      this.status = status;
    }
  }

  async function api(path, body) {
    const options = { headers: { "X-Cartouche-Token": TOKEN } };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new ApiError("Cartouche ne répond plus. Relance l'application.", null, 0);
    }
    let data = null;
    try {
      data = await response.json();
    } catch {
      /* réponse vide */
    }
    if (response.status === 403 && /Jeton/.test(data?.error || "")) {
      // Cartouche a redémarré : cette page porte un ancien jeton, on la recharge pour obtenir le nouveau.
      location.reload();
      throw new ApiError("Cartouche a redémarré, rechargement…", null, 403);
    }
    if (!response.ok) throw new ApiError(data?.error || `Erreur ${response.status}`, data?.fix || null, response.status);
    return data;
  }

  // ------------------------------------------------------------------ formats

  const nf1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
  function bytes(n) {
    if (!n) return "—";
    const units = ["o", "Ko", "Mo", "Go", "To"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i += 1;
    }
    return `${i < 2 ? Math.round(n) : nf1.format(n)} ${units[i]}`;
  }
  function dateTime(ts) {
    return ts ? new Date(ts * 1000).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }) : "—";
  }
  function dayDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("fr-FR", { dateStyle: "medium" });
  }
  function duration(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} h ${String(m).padStart(2, "0")}`;
    if (m) return `${m} min ${String(s % 60).padStart(2, "0")}`;
    return `${s} s`;
  }
  const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

  // ------------------------------------------------------------------ petits composants

  function emptyState(ic, title, text, actions = "") {
    return `<div class="empty">${icon(ic)}<h2>${esc(title)}</h2><p>${esc(text)}</p>${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
  }
  const skeleton = () => `<div class="skeleton" aria-label="Chargement"></div>`;
  function compatTag(c) {
    return c ? `<span class="compat s-${esc(c.status)}">${esc(c.label)}</span>` : "";
  }
  function searchTool(placeholder) {
    return `<label class="search">${icon("magnifying-glass")}<input id="search" type="search" placeholder="${esc(placeholder)}" value="${esc(state.filters.q)}" autocomplete="off" aria-label="${esc(placeholder)}"><kbd>/</kbd></label>`;
  }
  function seg(name, options) {
    return `<div class="seg" role="group">${options
      .map(([value, label]) => {
        const on = state.filters[name] === value;
        return `<button type="button" data-action="filter" data-filter="${name}" data-value="${value}" class="${on ? "is-on" : ""}" aria-pressed="${on}">${label}</button>`;
      })
      .join("")}</div>`;
  }
  const platformSeg = () => seg("platform", [["all", "Tout"], ["PS4", "PS4"], ["PS3", "PS3"]]);
  const kindSeg = () => seg("kind", [["all", "Tous"], ["game", "Jeux"], ["update", "MAJ"], ["dlc", "DLC"]]);
  const refreshBtn = () =>
    `<button class="btn icon" type="button" data-action="refresh" aria-label="Actualiser" title="Actualiser">${icon("arrow-clockwise")}</button>`;

  function activeJobFor(ref) {
    return (state.status?.jobs || []).find((j) => j.ref === ref && (j.status === "running" || j.status === "queued"));
  }
  function progressBar(job) {
    const pct = job.progress != null ? Math.round(job.progress * 100) : null;
    return `<div class="progress${pct == null ? " indeterminate" : ""}"><i style="--p:${pct ?? 0}%"></i></div>`;
  }

  // ------------------------------------------------------------------ vues

  const VIEWS = {
    library: {
      eyebrow: "Tes jeux installés",
      title: "Bibliothèque",
      tools: () => searchTool("Rechercher un jeu") + platformSeg() + refreshBtn(),
      render: renderLibrary,
    },
    pkgs: {
      eyebrow: "Tes fichiers .pkg",
      title: "Fichiers PKG",
      tools: () => searchTool("Rechercher un PKG") + platformSeg() + kindSeg() + refreshBtn(),
      render: renderPkgs,
    },
    jobs: { eyebrow: "File d'attente", title: "Installations", tools: () => "", render: renderJobs },
    settings: { eyebrow: "Configuration", title: "Réglages", tools: () => "", render: renderSettings },
    diagnostic: {
      eyebrow: "Santé du système",
      title: "Diagnostic",
      tools: () => `<button class="btn" type="button" data-action="reload-diag">${icon("arrow-clockwise")} Relancer l'analyse</button>`,
      render: renderDiagnostic,
    },
  };

  function go(view) {
    if (!VIEWS[view]) view = "library";
    state.view = view;
    state.enter = true;
    state.drawer = null;
    renderDrawer();
    $$(".nav-item").forEach((a) => {
      const on = a.dataset.view === view;
      a.classList.toggle("is-active", on);
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    const v = VIEWS[view];
    $("#view-eyebrow").textContent = v.eyebrow;
    $("#view-title").textContent = v.title;
    $("#view-tools").innerHTML = v.tools();
    document.title = `${v.title} · Cartouche`;
    $(".stage").scrollTop = 0;
    renderView();
    loadFor(view);
  }

  function renderView() {
    const enter = state.enter;
    state.enter = false;
    $("#view").innerHTML = VIEWS[state.view].render(enter);
  }

  async function loadFor(view) {
    try {
      if (view === "library" || view === "pkgs") {
        if (!state.library) await loadLibrary();
      } else if (view === "settings") {
        await Promise.all([loadSettings(), loadEmu()]);
        if (!state.updates) loadUpdates();
      } else if (view === "diagnostic") {
        await loadDiag();
      }
    } catch (error) {
      handleError(error);
    }
    if (state.view === view) renderView();
  }

  async function loadLibrary(refresh = false) {
    state.library = await api(`/api/library${refresh ? "?refresh=1" : ""}`);
    badges();
  }
  async function loadSettings() {
    state.settings = await api("/api/settings");
    renderRail();
  }
  async function loadEmu() {
    state.emu = await api("/api/emu-settings");
  }
  async function loadDiag() {
    state.diag = null;
    if (state.view === "diagnostic") renderView();
    state.diag = await api("/api/diagnostics");
  }
  async function loadUpdates(force = false) {
    try {
      state.updates = await api(`/api/updates${force ? "?force=1" : ""}`);
    } catch {
      state.updates = { release: { error: "Vérification impossible." }, update_available: false };
    }
    renderRail();
    if (state.view === "settings") renderView();
  }
  async function refreshAll() {
    await Promise.allSettled([loadLibrary(), loadSettings(), state.view === "settings" ? loadEmu() : null]);
    renderView();
    renderDrawer();
  }

  // ------------------------------------------------------------------ bibliothèque

  function matchGame(g) {
    const f = state.filters;
    if (f.platform !== "all" && g.platform !== f.platform) return false;
    if (!f.q) return true;
    const q = f.q.toLowerCase();
    return [g.title, g.title_id].some((v) => (v || "").toLowerCase().includes(q));
  }

  function renderLibrary(enter) {
    const lib = state.library;
    if (!lib) return skeleton();
    if (!lib.games.length) {
      return emptyState("game-controller", "Rayon vide pour l'instant",
        "Aucun jeu installé. Installe un PKG depuis « Fichiers PKG » et il apparaîtra ici.",
        `<a class="btn primary" href="#pkgs">${icon("package")} Voir mes PKG</a>`);
    }
    const games = lib.games.filter(matchGame);
    if (!games.length) return emptyState("magnifying-glass", "Aucun résultat", "Aucun jeu ne correspond à ta recherche.");
    return `<div class="shelf${enter ? " enter" : ""}">${games.map(cartCard).join("")}</div>`;
  }

  function cartCard(g, i) {
    const running = state.status?.runner?.running?.key === g.key;
    const flags = [];
    if (g.update) flags.push(`<span title="Mise à jour installée">${icon("arrow-circle-up")} ${esc(g.update.version)}</span>`);
    if (g.dlc.length) flags.push(`<span title="Contenus additionnels">${icon("puzzle-piece")} ${g.dlc.length}</span>`);
    if (g.mods) flags.push(`<span title="Mods">${icon("sparkle")} Mods</span>`);
    if (g.custom_config) flags.push(`<span title="Réglages propres à ce jeu">${icon("gear-six")}</span>`);
    const play = g.launchable
      ? `<button class="play" type="button" data-action="${running ? "stop" : "launch"}" data-key="${esc(g.key)}" aria-label="${running ? "Arrêter" : "Jouer à"} ${esc(g.title)}">${icon(running ? "stop" : "play")}</button>`
      : "";
    return `<article class="cart${g.launchable ? "" : " is-dim"}${running ? " is-running" : ""}" style="--i:${i}" role="button" tabindex="0" data-action="open-game" data-key="${esc(g.key)}" aria-label="${esc(g.title)}">
      <div class="cart-art">
        ${g.icon_url ? `<img src="${esc(g.icon_url)}" alt="" loading="lazy" decoding="async">` : `<div class="art-fallback">${icon("game-controller")}</div>`}
        <span class="plat plat-${esc(g.platform.toLowerCase())}">${esc(g.platform)}</span>
        ${compatTag(g.compat)}
        ${play}
      </div>
      <div class="cart-label">
        <h3>${esc(g.title)}</h3>
        <p class="serial">${esc(g.title_id || "—")}${g.version ? ` · v${esc(g.version)}` : ""}</p>
        <div class="cart-flags">${flags.join("")}</div>
      </div>
      ${g.note ? `<p class="cart-note">${esc(g.note)}</p>` : ""}
    </article>`;
  }

  // ------------------------------------------------------------------ fichiers PKG

  function matchPkg(p) {
    const f = state.filters;
    if (f.platform !== "all" && p.platform !== f.platform) return false;
    const kind = p.category === "app" ? "game" : p.category; // les applis se rangent avec les jeux
    if (f.kind !== "all" && kind !== f.kind) return false;
    if (!f.q) return true;
    const q = f.q.toLowerCase();
    return [p.title, p.title_id, p.file_name, p.content_id].some((v) => (v || "").toLowerCase().includes(q));
  }

  function sourceChips(lib) {
    const dirs = lib.pkg_dirs.map((d) => ({ kind: "pkg_dirs", path: d, ic: "folder-simple" }));
    const files = lib.pkg_files.map((f) => ({ kind: "pkg_files", path: f, ic: "file" }));
    return [...dirs, ...files]
      .map((s) => `<span class="chip" title="${esc(s.path)}">${icon(s.ic)}<span>${esc(s.path)}</span><button type="button" data-action="remove-source" data-kind="${s.kind}" data-path="${esc(s.path)}" aria-label="Retirer ${esc(s.path)}">${icon("x")}</button></span>`)
      .join("");
  }
  const addSourceButtons = () =>
    `<button class="btn small" type="button" data-action="pick" data-kind="pkg_dir">${icon("folder-plus")} Ajouter un dossier</button>` +
    `<button class="btn small" type="button" data-action="pick" data-kind="pkg_files">${icon("file-plus")} Ajouter des fichiers</button>`;

  function renderPkgs(enter) {
    const lib = state.library;
    if (!lib) return skeleton();
    const chips = sourceChips(lib);
    const sources = `<div class="sources"><span>Recherche dans</span>${chips || "<em>aucun dossier</em>"}${addSourceButtons()}</div>`;
    let body;
    if (!lib.pkgs.length) {
      body = emptyState("package", "Aucun PKG trouvé",
        "Ajoute le dossier où tu ranges tes fichiers .pkg (sous-dossiers compris) ou choisis des fichiers un par un.");
    } else {
      const items = lib.pkgs.filter(matchPkg);
      body = items.length
        ? `<div class="row-head" aria-hidden="true"><span></span><span>Titre</span><span class="c-plat">Console</span><span class="c-kind">Type</span><span class="c-version">Version</span><span class="c-size">Taille</span><span class="c-status">État</span><span></span></div>
           <div class="table${enter ? " enter" : ""}">${items.map(pkgRow).join("")}</div>`
        : emptyState("magnifying-glass", "Aucun résultat", "Aucun PKG ne correspond à ces filtres.");
    }
    const ignored = lib.ignored.length
      ? `<details class="ignored"><summary>${plural(lib.ignored.length, "fichier .pkg ignoré", "fichiers .pkg ignorés")} (pas des PKG PlayStation)</summary><ul>${lib.ignored
          .map((i) => `<li>${esc(i.name)} · ${esc(i.reason)}</li>`)
          .join("")}</ul></details>`
      : "";
    return sources + body + ignored;
  }

  function pkgStatus(p) {
    return `<span class="status ${esc(p.status)}">${icon(STATUS_ICONS[p.status] || "question")} ${esc(p.status_label)}</span>${p.compat ? `<br>${compatTag(p.compat)}` : ""}`;
  }

  function pkgAction(p) {
    const job = activeJobFor(p.path);
    if (job) {
      const pct = job.progress != null ? `${Math.round(job.progress * 100)} %` : job.status === "queued" ? "En attente" : "Démarrage";
      return `<div class="inline-progress" data-job-progress="${esc(job.id)}">${progressBar(job)}<span><span data-job-text>${pct}</span> · <button class="linklike" type="button" data-action="cancel-job" data-id="${esc(job.id)}">Annuler</button></span></div>`;
    }
    if (!p.supported) return `<span class="tag err">${p.truncated ? "Incomplet" : "Non pris en charge"}</span>`;
    if (p.status === "installed") {
      return `<button class="btn small ghost" type="button" data-action="install" data-id="${esc(p.id)}">${icon("arrow-counter-clockwise")} Réinstaller</button>`;
    }
    return `<button class="btn small${p.status === "needs_base" ? "" : " primary"}" type="button" data-action="install" data-id="${esc(p.id)}">${icon("download-simple")} Installer</button>`;
  }

  function pkgRow(p, i) {
    const warn = p.truncated
      ? `<small class="warn-line">${icon("warning-circle")} Fichier incomplet</small>`
      : !p.supported && p.notes.length ? `<small class="warn-line">${esc(p.notes[0])}</small>` : "";
    const thumb = p.icon_url
      ? `<img src="${esc(p.icon_url)}" alt="" loading="lazy" decoding="async">`
      : icon(p.platform === "PS3" ? "disc" : "package");
    return `<div class="row${p.supported ? "" : " is-off"}" style="--i:${i}" role="button" tabindex="0" data-action="open-pkg" data-id="${esc(p.id)}">
      <div class="thumb">${thumb}</div>
      <div class="row-title"><b>${esc(p.title)}</b><small>${esc(p.title_id || "—")} · ${esc(p.file_name)}</small>${warn}</div>
      <div class="c-plat"><span class="plat plat-${esc(p.platform.toLowerCase())}">${esc(p.platform)}</span></div>
      <div class="cell c-kind">${esc(p.kind_label)}</div>
      <div class="cell mono c-version">${p.version ? "v" + esc(p.version) : "—"}</div>
      <div class="cell mono c-size">${bytes(p.size)}</div>
      <div class="c-status">${pkgStatus(p)}</div>
      <div class="row-action">${pkgAction(p)}</div>
    </div>`;
  }

  // ------------------------------------------------------------------ installations

  const JOB_TAGS = {
    queued: ["", "En attente"], running: ["warn", "En cours"], done: ["ok", "Terminé"],
    error: ["err", "Échec"], cancelled: ["", "Annulé"],
  };

  function renderJobs(enter) {
    const jobs = state.status?.jobs || [];
    if (!jobs.length) {
      return emptyState("download-simple", "Rien en cours",
        "Les installations de PKG et les mises à jour de shadPS4 s'afficheront ici.",
        `<a class="btn" href="#pkgs">${icon("package")} Voir mes PKG</a>`);
    }
    return `<div class="stack${enter ? " enter" : ""}">${jobs.map(jobCard).join("")}</div>`;
  }

  function jobCard(j, i) {
    const active = j.status === "running" || j.status === "queued";
    const [tagClass, tagLabel] = JOB_TAGS[j.status] || ["", j.status];
    const open = state.openLogs.has(j.id);
    const when = `${dateTime(j.started || j.created)}${j.finished && j.started ? " · " + duration(j.finished - j.started) : ""}`;
    return `<article class="card job" data-job="${esc(j.id)}" style="--i:${i}">
      <div class="job-top">${icon(j.kind === "update" ? "arrow-circle-up" : "package")}
        <div><b>${esc(j.title)}</b><br><small>${esc(j.subtitle)}</small></div>
        <span class="tag ${tagClass}">${tagLabel}</span>
      </div>
      ${active ? progressBar(j) : ""}
      <div class="job-detail${j.status === "error" ? " err" : ""}" data-job-detail>${esc(j.status === "error" ? j.error : j.detail)}</div>
      <div class="actions-row">
        ${active ? `<button class="btn small danger" type="button" data-action="cancel-job" data-id="${esc(j.id)}">${icon("x")} Annuler</button>` : ""}
        ${j.log.length ? `<button class="btn small ghost" type="button" data-action="toggle-log" data-id="${esc(j.id)}">${icon("terminal-window")} ${open ? "Masquer" : "Voir"} le détail</button>` : ""}
        <small class="when">${esc(when)}</small>
      </div>
      ${open ? `<pre data-job-log>${esc(j.log.join("\n"))}</pre>` : ""}
    </article>`;
  }

  /** Met à jour les barres de progression sans tout redessiner. */
  function patchProgress() {
    for (const job of state.status?.jobs || []) {
      if (job.status !== "running" && job.status !== "queued") continue;
      const pct = job.progress != null ? Math.round(job.progress * 100) : null;
      for (const box of $$(`[data-job-progress="${CSS.escape(job.id)}"], [data-job="${CSS.escape(job.id)}"]`)) {
        const bar = $(".progress", box);
        if (bar) {
          bar.classList.toggle("indeterminate", pct == null);
          $("i", bar).style.setProperty("--p", `${pct ?? 0}%`);
        }
        const text = $("[data-job-text]", box);
        if (text) text.textContent = pct != null ? `${pct} %` : job.status === "queued" ? "En attente" : "Démarrage";
        const detail = $("[data-job-detail]", box);
        if (detail) detail.textContent = job.detail;
        const log = $("[data-job-log]", box);
        if (log) {
          const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
          log.textContent = job.log.join("\n");
          if (atBottom) log.scrollTop = log.scrollHeight;
        }
      }
    }
  }

  // ------------------------------------------------------------------ réglages

  function settingRow(label, help, control, extraClass = "", path = "") {
    return `<div class="setting ${extraClass}"><div class="label"><b>${label}</b>${help ? `<small>${help}</small>` : ""}${path ? `<div class="path">${esc(path)}</div>` : ""}</div><div class="actions">${control}</div></div>`;
  }
  function switchControl(attr, key, checked, label) {
    return `<label class="switch"><input type="checkbox" ${attr}="${esc(key)}" ${checked ? "checked" : ""} aria-label="${esc(label)}"><span></span></label>`;
  }
  const pickButton = (kind, label = "Parcourir") =>
    `<button class="btn small" type="button" data-action="pick" data-kind="${kind}">${icon("folder-open")} ${label}</button>`;

  function updateCallout(t) {
    const up = state.updates;
    const job = activeJobFor("shadps4-update");
    if (job) {
      return `<div class="callout info" data-job-progress="${esc(job.id)}">${icon("download-simple")}<div class="grow"><b>Mise à jour de shadPS4 en cours</b><small data-job-detail>${esc(job.detail)}</small>${progressBar(job)}</div></div>`;
    }
    if (!up) return `<div class="callout info">${icon("spinner-gap")}<div class="grow"><b>Recherche de la dernière version de shadPS4…</b></div></div>`;
    const release = up.release || {};
    if (release.error) {
      return `<div class="callout warn">${icon("warning-circle")}<div class="grow"><b>Vérification des mises à jour impossible</b><small>${esc(release.error)}</small></div><button class="btn small" type="button" data-action="check-updates">${icon("arrow-clockwise")} Réessayer</button></div>`;
    }
    if (up.update_available) {
      const size = release.asset?.size ? ` (${bytes(release.asset.size)})` : "";
      return `<div class="callout warn">${icon("arrow-circle-up")}<div class="grow"><b>shadPS4 ${esc(release.version)} est disponible${release.published ? `, sortie le ${esc(dayDate(release.published))}` : ""}</b><small>Tu utilises ${esc(up.current_label || "une version plus ancienne")}. Chaque version fait mieux tourner les jeux. Téléchargement depuis le GitHub officiel${esc(size)}.</small></div><button class="btn small primary" type="button" data-action="install-update">${icon("download-simple")} Installer</button></div>`;
    }
    if (up.latest_installed && release.version && up.current !== release.version) {
      const build = t.shadps4.builds.find((b) => b.version === release.version);
      return `<div class="callout info">${icon("info")}<div class="grow"><b>La ${esc(release.version)} est déjà sur ton PC</b><small>Elle n'est pas celle utilisée en ce moment.</small></div>${build ? `<button class="btn small primary" type="button" data-action="use-build" data-path="${esc(build.path)}">Utiliser la ${esc(release.version)}</button>` : ""}</div>`;
    }
    return `<div class="callout ok">${icon("check-circle")}<div class="grow"><b>shadPS4 est à jour${release.version ? ` (${esc(release.version)})` : ""}</b></div><button class="btn small ghost" type="button" data-action="check-updates">${icon("arrow-clockwise")} Vérifier</button></div>`;
  }

  function renderSettings(enter) {
    const data = state.settings;
    if (!data) return skeleton();
    const t = data.tools;
    const s = data.settings;
    const build = t.shadps4.build;
    const options = [`<option value="">Automatique</option>`]
      .concat(t.shadps4.builds.map((b) => `<option value="${esc(b.path)}" ${s.shadps4_exe && b.path === s.shadps4_exe ? "selected" : ""}>${esc(b.label)} · ${esc(b.source)}</option>`))
      .join("");
    const rp = t.rpcs3;
    const firmware = rp.found
      ? settingRow("Firmware PS3", rp.firmware ? `Installé : ${esc(rp.firmware)}` : "Manquant : RPCS3 en a besoin pour lancer les jeux. Il se télécharge gratuitement sur le site de PlayStation.",
          `<button class="btn small ghost" type="button" data-action="open-url" data-url="https://www.playstation.com/fr-fr/support/hardware/ps3/system-software/">${icon("arrow-square-out")} Site officiel</button><button class="btn small${rp.firmware ? "" : " primary"}" type="button" data-action="pick" data-kind="firmware">${icon("download-simple")} Installer un firmware</button>`,
          rp.firmware ? "" : "is-bad")
      : "";
    const chips = sourceChips({ pkg_dirs: s.pkg_dirs, pkg_files: s.pkg_files });

    const tools = `<section class="card" style="--i:0">
      <h2>${icon("cpu")} Émulateurs et outils</h2>
      <p class="lead">Cartouche pilote les émulateurs installés sur ton PC. Seules la recherche de mises à jour et la liste de compatibilité passent par Internet (GitHub).</p>
      ${updateCallout(t)}
      ${settingRow("shadPS4 (PS4)", build ? `${esc(build.label)} · ${esc(build.source)}` : "Introuvable", `<select data-setting="shadps4_exe" aria-label="Version de shadPS4">${options}</select>${pickButton("shadps4_exe")}`, build ? "" : "is-bad", build?.path)}
      ${settingRow("pkg_extractor (installe les PKG PS4)", t.pkg_extractor.found ? "Trouvé" : "Introuvable : il est livré avec le shadPS4 Qt Launcher.", pickButton("pkg_extractor"), t.pkg_extractor.found ? "" : "is-bad", t.pkg_extractor.path)}
      ${settingRow("RPCS3 (PS3)", rp.found ? "Trouvé" : "Non installé. Nécessaire seulement pour les PKG PS3.", `${rp.found ? "" : `<button class="btn small ghost" type="button" data-action="open-url" data-url="https://rpcs3.net/download">${icon("arrow-square-out")} Télécharger</button>`}${pickButton("rpcs3_exe")}`, "", rp.path)}
      ${firmware}
    </section>`;

    const folders = `<section class="card" style="--i:1">
      <h2>${icon("folder-open")} Dossiers</h2>
      ${settingRow("Jeux PS4 installés", "Les PKG PS4 sont extraits ici. Prévois de la place : un jeu fait souvent 20 à 50 Go.",
        `${pickButton("games_dir", "Changer")}<button class="btn small ghost" type="button" data-action="open" data-kind="games_dir">${icon("arrow-square-out")} Ouvrir</button>`,
        t.shadps4.games_dir ? "" : "is-bad", t.shadps4.games_dir || "Non défini")}
      ${settingRow("Où chercher les PKG", `Dossiers parcourus (avec 3 niveaux de sous-dossiers) et fichiers ajoutés un par un.<span class="chips">${chips || "<em>Aucun</em>"}</span>`, addSourceButtons())}
    </section>`;

    const launch = `<section class="card" style="--i:2">
      <h2>${icon("play")} Lancement</h2>
      ${settingRow("Plein écran", "Les jeux démarrent directement en plein écran.", switchControl("data-setting", "fullscreen", s.fullscreen, "Plein écran"))}
      ${settingRow("Afficher les FPS", "Ajoute un compteur d'images par seconde dans shadPS4.", switchControl("data-setting", "show_fps", s.show_fps, "Afficher les FPS"))}
      ${settingRow("Console de l'émulateur", "Montre les messages de l'émulateur en direct. Laisse désactivé sinon.", switchControl("data-setting", "show_console", s.show_console, "Console de l'émulateur"))}
      ${settingRow("Titres en français", "Utilise le nom français du jeu quand il existe.", switchControl("data-setting", "prefer_french_titles", s.prefer_french_titles, "Titres en français"))}
    </section>`;

    const about = `<section class="card" style="--i:4">
      <h2>${icon("info")} Cartouche</h2>
      ${settingRow(`Version ${esc(data.app_version)}`, "Réglages, cache et journaux de Cartouche.", `<button class="btn small ghost" type="button" data-action="open" data-kind="data">${icon("arrow-square-out")} Ouvrir le dossier</button>`)}
    </section>`;

    return `<div class="stack${enter ? " enter" : ""}">${tools}${folders}${launch}${emuCard()}${about}</div>`;
  }

  function emuCard() {
    const emu = state.emu;
    if (!emu) return `<section class="card" style="--i:3">${skeleton()}</section>`;
    if (!emu.available) {
      return `<section class="card" style="--i:3"><h2>${icon("gauge")} shadPS4 · performances</h2><div class="callout info">${icon("info")}<div class="grow"><b>config.json introuvable</b><small>Lance un jeu une fois avec shadPS4 pour qu'il crée sa configuration.</small></div></div></section>`;
    }
    const warnings = emu.warnings.map((w) => `<div class="callout ${w.level === "error" ? "error" : w.level === "warn" ? "warn" : "info"}">${icon(LEVEL_ICONS[w.level] || "info")}<div class="grow"><b>${esc(w.text)}</b></div></div>`).join("");
    const running = emu.game_running
      ? `<div class="callout info">${icon("info")}<div class="grow"><b>Un jeu tourne</b><small>Les changements s'appliqueront au prochain lancement.</small></div></div>`
      : "";
    const rows = emu.items.map((item) => {
      const bad = item.warn_when !== undefined && item.value === item.warn_when;
      let control;
      if (item.type === "bool") {
        control = switchControl("data-emu", item.id, item.value === true, item.label);
      } else {
        const opts = item.choices
          .map((c) => `<option value="${esc(JSON.stringify(c.value))}" ${JSON.stringify(c.value) === JSON.stringify(item.value) ? "selected" : ""}>${esc(c.label)}</option>`)
          .join("");
        control = `<select data-emu="${esc(item.id)}" aria-label="${esc(item.label)}" ${item.available ? "" : "disabled"}>${opts}</select>`;
      }
      return settingRow(esc(item.label), esc(item.help), control, `${bad ? "is-bad" : ""} ${item.available ? "" : "unavailable"}`);
    }).join("");
    return `<section class="card" style="--i:3">
      <h2>${icon("gauge")} shadPS4 · performances</h2>
      <p class="lead">Ces options modifient le fichier config.json de shadPS4. Une copie de sauvegarde est faite au premier changement.</p>
      ${warnings}${running}
      <div class="actions-row" style="margin-bottom:6px">
        <button class="btn primary" type="button" data-action="light-preset">${icon("lightning")} Préréglage léger</button>
        ${emu.backup ? `<button class="btn ghost" type="button" data-action="restore-backup">${icon("arrow-counter-clockwise")} Restaurer la sauvegarde</button>` : ""}
        <button class="btn ghost" type="button" data-action="open" data-kind="shadps4_config">${icon("folder-open")} Dossier de config</button>
      </div>
      <small class="hint">Le préréglage léger coupe tout ce qui sert au débogage, garde les shaders compilés et règle l'écran émulé en 1280 × 720 : le plus adapté à une petite carte graphique.</small>
      ${rows}
    </section>`;
  }

  // ------------------------------------------------------------------ diagnostic

  function checkRow(c) {
    const fix = c.fix && FIX_LABELS[c.fix] ? `<button class="btn small" type="button" data-action="fix" data-fix="${esc(c.fix)}">${esc(FIX_LABELS[c.fix])}</button>` : "";
    const cls = c.level === "error" ? "error" : c.level;
    return `<div class="callout ${esc(cls)}">${icon(LEVEL_ICONS[c.level] || "info")}<div class="grow"><b>${esc(c.title)}</b>${c.text ? `<small>${esc(c.text)}</small>` : ""}</div>${fix}</div>`;
  }

  function renderDiagnostic(enter) {
    const d = state.diag;
    if (!d) return skeleton();
    const counts = { error: 0, warn: 0 };
    d.checks.forEach((c) => {
      if (c.level in counts) counts[c.level] += 1;
    });
    const summary = counts.error
      ? `${plural(counts.error, "problème bloque", "problèmes bloquent")} le lancement des jeux.`
      : counts.warn ? `Tout fonctionne, avec ${plural(counts.warn, "point", "points")} à améliorer.` : "Tout est en ordre.";
    const last = d.last_run
      ? `<section class="card" style="--i:1"><h2>${icon("clock")} Dernier lancement</h2>
          <div class="facts">
            <div class="fact"><small>Jeu</small><b>${esc(d.last_run.title)}</b></div>
            <div class="fact"><small>Émulateur</small><b>${esc(d.last_run.emulator)}</b></div>
            <div class="fact"><small>Durée</small><b>${duration(d.last_run.duration)}</b></div>
            <div class="fact"><small>Fin</small><b>${esc(dateTime(d.last_run.ended))}</b></div>
          </div>
          <div class="callout ${d.last_run.crashed ? "warn" : "ok"}">${icon(d.last_run.crashed ? "warning-circle" : "check-circle")}<div class="grow"><b>${esc(d.last_run.summary)}</b></div></div>
        </section>`
      : "";
    return `<div class="stack${enter ? " enter" : ""}">
      <section class="card" style="--i:0"><h2>${icon("shield-check")} Vérifications</h2><p class="lead">${esc(summary)}</p>${d.checks.map(checkRow).join("")}</section>
      ${last}
      ${logCard(d.log)}
    </div>`;
  }

  function logCard(log) {
    if (!log || !log.exists) {
      return `<section class="card" style="--i:2"><h2>${icon("file-text")} Journal de shadPS4</h2><p class="lead">Aucun journal pour l'instant : il est créé au premier lancement d'un jeu.</p></section>`;
    }
    const fact = (label, value) => (value ? `<div class="fact"><small>${label}</small><b title="${esc(value)}">${esc(value)}</b></div>` : "");
    const hints = log.hints.map((h) => checkRow({ level: h.level, title: h.title, text: h.text })).join("");
    const errors = log.errors.length
      ? `<table class="errors"><tbody>${log.errors
          .map((e) => `<tr><td><span class="tag ${e.level === "Critical" ? "err" : "warn"}">${e.level === "Critical" ? "Critique" : "Erreur"}</span></td><td class="msg">${esc(e.channel)} · ${esc(e.message)}</td><td class="n">×${e.count}</td></tr>`)
          .join("")}</tbody></table>`
      : `<p class="lead">Aucune erreur relevée dans le dernier journal.</p>`;
    return `<section class="card" style="--i:2">
      <h2>${icon("file-text")} Journal de shadPS4</h2>
      <p class="lead">Analyse du dernier lancement, écrit le ${esc(dateTime(log.modified))}.</p>
      <div class="facts">
        ${fact("Version", log.emulator_version)}
        ${fact("Jeu", log.game_title ? `${log.game_title} (${log.game_id})` : log.game_id)}
        ${fact("Carte graphique", log.gpu)}
        ${fact("Vulkan", log.vulkan)}
        ${fact("Pilote", log.driver)}
        ${fact("Processeur", log.cpu)}
        ${fact("Mémoire", log.ram)}
      </div>
      ${hints}
      ${errors}
      <details><summary>Dernières lignes du journal</summary><pre class="log-tail">${esc(log.tail.join("\n"))}</pre></details>
      <div class="actions-row"><button class="btn small ghost" type="button" data-action="open" data-kind="shadps4_log">${icon("folder-open")} Ouvrir le dossier du journal</button></div>
    </section>`;
  }

  // ------------------------------------------------------------------ panneau de détails

  function openDrawer(type, id) {
    state.drawer = { type, id };
    renderDrawer();
    $("#drawer .drawer-close")?.focus();
  }
  function closeDrawer() {
    if (!state.drawer) return false;
    state.drawer = null;
    renderDrawer();
    return true;
  }
  function renderDrawer() {
    const drawer = $("#drawer");
    const backdrop = $("#drawer-backdrop");
    const d = state.drawer;
    const item = d && (d.type === "game"
      ? state.library?.games.find((g) => g.key === d.id)
      : state.library?.pkgs.find((p) => p.id === d.id));
    if (!item) {
      state.drawer = null;
      drawer.hidden = true;
      backdrop.hidden = true;
      return;
    }
    drawer.innerHTML = d.type === "game" ? gameDrawer(item) : pkgDrawer(item);
    drawer.hidden = false;
    backdrop.hidden = false;
  }

  function drawerHead(item, subtitle) {
    const bg = item.background_url ? ` style="background-image:url('${esc(item.background_url)}')"` : "";
    const thumb = item.icon_url ? `<img src="${esc(item.icon_url)}" alt="">` : icon(item.platform === "PS3" ? "disc" : "game-controller");
    return `<div class="drawer-hero"${bg}><button class="btn icon drawer-close" type="button" data-action="close-drawer" aria-label="Fermer">${icon("x")}</button></div>
      <div class="drawer-id"><div class="thumb">${thumb}</div><div><span class="plat plat-${esc(item.platform.toLowerCase())}">${esc(item.platform)}</span><h2>${esc(item.title)}</h2><p class="serial">${esc(subtitle)}</p></div></div>`;
  }

  function compatBox(c, platform) {
    if (platform !== "PS4") return "";
    if (!c) {
      return `<div class="compat-box"><b>Compatibilité inconnue</b><p>Ce jeu n'est pas encore dans la base de compatibilité de shadPS4.</p></div>`;
    }
    const tested = [c.os ? `Testé sous ${esc(c.os)}` : "Testé", c.version ? ` avec shadPS4 ${esc(c.version)}` : "", c.last_tested ? `, le ${esc(dayDate(c.last_tested))}` : ""].join("");
    return `<div class="compat-box s-${esc(c.status)}"><b>${esc(c.label)}</b><p>${esc(COMPAT_TEXT[c.status] || "")}</p><p>${tested}.</p>${c.url ? `<button class="btn small ghost" type="button" data-action="open-url" data-url="${esc(c.url)}">${icon("arrow-square-out")} Voir le rapport de test</button>` : ""}</div>`;
  }

  const dl = (rows) => `<dl class="details">${rows.filter(Boolean).join("")}</dl>`;
  const dt = (label, value, mono = false) => (value ? `<dt>${label}</dt><dd${mono ? ' class="mono"' : ""}>${esc(value)}</dd>` : "");

  function gameDrawer(g) {
    const running = state.status?.runner?.running?.key === g.key;
    const action = !g.launchable
      ? `<button class="btn primary" type="button" disabled>${icon("prohibit")} Non lançable</button>`
      : running
        ? `<button class="btn primary" type="button" data-action="stop">${icon("stop")} Arrêter</button>`
        : `<button class="btn primary" type="button" data-action="launch" data-key="${esc(g.key)}">${icon("play")} Jouer</button>`;
    return `${drawerHead(g, `${g.title_id || "—"}${g.version ? " · v" + g.version : ""}`)}
      <div class="drawer-body">
        <div class="drawer-actions">${action}<button class="btn" type="button" data-action="open" data-kind="game" data-ref="${esc(g.key)}">${icon("folder-open")} Dossier</button></div>
        ${g.note ? `<div class="callout warn">${icon("warning-circle")}<div class="grow"><b>${esc(g.note)}</b></div></div>` : ""}
        ${compatBox(g.compat, g.platform)}
        ${dl([
          dt("Émulateur", g.emulator),
          dt("Version", g.version ? "v" + g.version : ""),
          dt("Mise à jour", g.platform === "PS4" ? (g.update ? `v${g.update.version} installée` : "Aucune") : ""),
          dt("DLC", g.platform === "PS4" ? (g.dlc.length ? plural(g.dlc.length, "installé", "installés") : "Aucun") : ""),
          dt("Firmware requis", g.system_version),
          dt("Réglages propres", g.custom_config ? "Oui (custom_configs)" : ""),
          dt("Dossier", g.folder, true),
        ])}
      </div>`;
  }

  function pkgDrawer(p) {
    let action;
    const job = activeJobFor(p.path);
    if (job) action = `<button class="btn primary" type="button" disabled>${icon("spinner-gap")} Installation en cours</button>`;
    else if (!p.supported) action = `<button class="btn primary" type="button" disabled>${icon("prohibit")} Non pris en charge</button>`;
    else action = `<button class="btn primary" type="button" data-action="install" data-id="${esc(p.id)}">${icon(p.status === "installed" ? "arrow-counter-clockwise" : "download-simple")} ${p.status === "installed" ? "Réinstaller" : "Installer"}</button>`;
    const notes = p.notes.length ? `<ul class="notes">${p.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
    return `${drawerHead(p, `${p.title_id || "—"}${p.version ? " · v" + p.version : ""} · ${p.kind_label}`)}
      <div class="drawer-body">
        <div class="drawer-actions">${action}<button class="btn" type="button" data-action="open" data-kind="pkg" data-ref="${esc(p.id)}">${icon("folder-open")} Emplacement</button></div>
        <div class="callout ${p.status === "installed" ? "ok" : p.status === "needs_base" ? "warn" : "info"}">${icon(STATUS_ICONS[p.status] || "info")}<div class="grow"><b>${esc(p.status_label)}</b>${p.status === "needs_base" ? "<small>Installe d'abord le jeu de base, puis cette mise à jour.</small>" : ""}${!p.emulator_ready && p.supported ? `<small>${p.platform === "PS3" ? "RPCS3 n'est pas installé." : "pkg_extractor est introuvable."}</small>` : ""}</div></div>
        ${notes}
        ${compatBox(p.compat, p.platform)}
        ${dl([
          dt("Fichier", p.file_name, true),
          dt("Dossier", p.folder, true),
          dt("Type", p.kind_label),
          dt("Taille", bytes(p.size)),
          dt("Contenu", p.content_size && p.content_size !== p.size ? "≈ " + bytes(p.content_size) + " une fois installé" : ""),
          dt("Content ID", p.content_id, true),
          dt("Firmware requis", p.system_version),
          dt("Émulateur", p.emulator),
          dt("Paquet", p.retail === false ? "Debug" : ""),
        ])}
      </div>`;
  }

  // ------------------------------------------------------------------ fenêtres, notifications

  function modal({ ic = "question", title, text, actions }) {
    return new Promise((resolve) => {
      const root = $("#modal-root");
      root.innerHTML = `<div class="modal-wrap" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal">
        <h2 id="modal-title">${icon(ic)} ${esc(title)}</h2><p>${esc(text)}</p>
        <div class="actions">${actions.map((a, i) => `<button class="btn${a.primary ? " primary" : " ghost"}" type="button" data-modal="${i}">${esc(a.label)}</button>`).join("")}</div>
      </div></div>`;
      const close = (value) => {
        root.innerHTML = "";
        state.modalClose = null;
        resolve(value);
      };
      state.modalClose = () => close(false);
      root.querySelectorAll("[data-modal]").forEach((b) => b.addEventListener("click", () => close(actions[Number(b.dataset.modal)].value)));
      root.querySelector(".modal-wrap").addEventListener("click", (ev) => {
        if (ev.target.classList.contains("modal-wrap")) close(false);
      });
      (root.querySelector(".btn.primary") || root.querySelector(".btn"))?.focus();
    });
  }
  const confirmModal = (title, text, okLabel, cancelLabel = "Annuler") =>
    modal({ ic: "question", title, text, actions: [{ label: cancelLabel, value: false }, { label: okLabel, value: true, primary: true }] });

  function toast(level, text) {
    const box = document.createElement("div");
    box.className = `toast ${level}`;
    box.innerHTML = `${icon(LEVEL_ICONS[level] || "info")}<div>${esc(text)}</div>`;
    $("#toasts").appendChild(box);
    setTimeout(() => box.remove(), level === "error" ? 10000 : 6000);
  }

  function handleError(error, retry) {
    if (error?.fix && FIX_LABELS[error.fix]) {
      modal({
        ic: "warning-circle",
        title: "Une étape manque",
        text: error.message,
        actions: [{ label: "Annuler", value: false }, { label: FIX_LABELS[error.fix], value: true, primary: true }],
      }).then((ok) => ok && runFix(error.fix, retry));
      return;
    }
    toast("error", error?.message || String(error));
  }

  async function runFix(fix, retry) {
    try {
      switch (fix) {
        case "null_gpu":
          await api("/api/fix", { name: "null_gpu" });
          toast("success", "Rendu graphique réactivé dans shadPS4.");
          break;
        case "light_preset": {
          const result = await api("/api/fix", { name: "light_preset" });
          toast("success", result.changed.length ? `Préréglage léger appliqué (${plural(result.changed.length, "réglage", "réglages")}).` : "shadPS4 est déjà réglé au plus léger.");
          state.emu = result;
          break;
        }
        case "update_shadps4":
          await installUpdate();
          return;
        case "install_firmware":
          await pick("firmware");
          return;
        case "pick_pkg_extractor":
          if (!(await pick("pkg_extractor"))) return;
          break;
        case "pick_games_dir":
          if (!(await pick("games_dir"))) return;
          break;
        case "pick_rpcs3_exe":
          if (!(await pick("rpcs3_exe"))) return;
          break;
        case "pick_shadps4_exe":
          if (!(await pick("shadps4_exe"))) return;
          break;
        default:
          break;
      }
      await refreshAll();
      if (retry) await retry();
    } catch (error) {
      handleError(error);
    }
  }

  // ------------------------------------------------------------------ actions

  async function withBusy(el, fn) {
    if (!(el instanceof HTMLButtonElement)) return fn();
    const html = el.innerHTML;
    el.disabled = true;
    const ic = el.querySelector(".ph-duotone");
    if (ic) ic.className = "ph-duotone ph-spinner-gap";
    try {
      return await fn();
    } finally {
      if (el.isConnected) {
        el.disabled = false;
        el.innerHTML = html;
      }
    }
  }

  async function pick(kind) {
    toast("info", "Une fenêtre de choix s'ouvre (elle peut apparaître derrière celle-ci).");
    const result = await api("/api/pick", { kind });
    const picked = Array.isArray(result.picked) ? result.picked.length > 0 : Boolean(result.picked);
    if (picked) await refreshAll();
    return picked;
  }

  async function launchGame(key, el) {
    try {
      await withBusy(el, () => api("/api/launch", { key }));
      await poll();
    } catch (error) {
      handleError(error, () => launchGame(key));
    }
  }

  async function installPkg(id, confirm, el) {
    try {
      await withBusy(el, () => api("/api/install", { id, confirm }));
      toast("info", "Installation ajoutée à la file d'attente.");
      await poll();
    } catch (error) {
      if (error.fix === "confirm_install") {
        if (await confirmModal("Réinstaller ?", error.message, "Réinstaller")) installPkg(id, true);
        return;
      }
      handleError(error, () => installPkg(id, confirm));
    }
  }

  async function installUpdate() {
    await api("/api/updates/install", {});
    toast("info", "Téléchargement de shadPS4 lancé.");
    await poll();
    if (state.view === "settings") renderView();
  }

  async function quit() {
    if (state.status?.busy && !(await confirmModal("Quitter Cartouche ?", "Une installation est en cours : elle sera interrompue.", "Quitter quand même"))) return;
    try {
      await api("/api/quit", {});
    } catch {
      /* déjà arrêté */
    }
    clearTimeout(pollTimer);
    document.body.insertAdjacentHTML("beforeend",
      `<div class="closed"><div>${icon("power")}<h2>Cartouche est fermé</h2><p>Tu peux fermer cette fenêtre.</p></div></div>`);
    setTimeout(() => window.close(), 500);
  }

  const ACTIONS = {
    "open-game": (el) => openDrawer("game", el.dataset.key),
    "open-pkg": (el) => openDrawer("pkg", el.dataset.id),
    "close-drawer": () => closeDrawer(),
    launch: (el) => launchGame(el.dataset.key, el),
    stop: async () => {
      try {
        await api("/api/stop", {});
        toast("info", "Arrêt demandé.");
        await poll();
      } catch (error) {
        handleError(error);
      }
    },
    install: (el) => installPkg(el.dataset.id, false, el),
    "cancel-job": async (el) => {
      if (!(await confirmModal("Annuler l'installation ?", "Le dossier partiellement extrait sera supprimé.", "Annuler l'installation", "Continuer"))) return;
      await api("/api/jobs/cancel", { id: el.dataset.id }).catch(handleError);
      await poll();
    },
    "toggle-log": (el) => {
      const id = el.dataset.id;
      if (state.openLogs.has(id)) state.openLogs.delete(id);
      else state.openLogs.add(id);
      renderView();
    },
    refresh: async (el) => {
      try {
        await withBusy(el, () => loadLibrary(true));
        renderView();
      } catch (error) {
        handleError(error);
      }
    },
    filter: (el) => {
      state.filters[el.dataset.filter] = el.dataset.value;
      $$(`[data-filter="${el.dataset.filter}"]`).forEach((b) => {
        const on = b.dataset.value === el.dataset.value;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-pressed", String(on));
      });
      renderView();
    },
    pick: (el) => withBusy(el, () => pick(el.dataset.kind)).catch(handleError),
    "remove-source": async (el) => {
      try {
        await api("/api/sources/remove", { kind: el.dataset.kind, path: el.dataset.path });
        await refreshAll();
      } catch (error) {
        handleError(error);
      }
    },
    open: (el) => api("/api/open", { kind: el.dataset.kind, ref: el.dataset.ref || "" }).catch(handleError),
    "open-url": (el) => api("/api/open-url", { url: el.dataset.url }).catch(handleError),
    "light-preset": (el) => withBusy(el, () => runFix("light_preset")).then(() => renderView()),
    "restore-backup": async (el) => {
      if (!(await confirmModal("Restaurer la sauvegarde ?", "config.json de shadPS4 reviendra à son état d'avant Cartouche.", "Restaurer"))) return;
      try {
        state.emu = await withBusy(el, () => api("/api/emu-settings/restore", {}));
        toast("success", "Configuration de shadPS4 restaurée.");
        renderView();
      } catch (error) {
        handleError(error);
      }
    },
    "check-updates": async (el) => {
      state.updates = null;
      renderView();
      await withBusy(el, () => loadUpdates(true));
    },
    "install-update": (el) => withBusy(el, installUpdate).catch(handleError),
    "use-build": async (el) => {
      await saveSetting("shadps4_exe", el.dataset.path);
      loadUpdates();
    },
    fix: (el) => withBusy(el, () => runFix(el.dataset.fix)).then(() => state.view === "diagnostic" && loadDiag().then(renderView)),
    "reload-diag": (el) => withBusy(el, loadDiag).then(renderView).catch(handleError),
    quit: () => quit(),
  };

  async function saveSetting(key, value) {
    try {
      await api("/api/settings", { changes: { [key]: value } });
      toast("success", "Réglage enregistré.");
      await refreshAll();
    } catch (error) {
      handleError(error);
    }
  }

  function bindEvents() {
    document.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-action]");
      if (!el || el.tagName === "A") return;
      const handler = ACTIONS[el.dataset.action];
      if (!handler) return;
      ev.preventDefault();
      handler(el, ev);
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        if (state.modalClose) state.modalClose();
        else closeDrawer();
        return;
      }
      const typing = ev.target.matches("input, select, textarea");
      if (ev.key === "/" && !typing) {
        const search = $("#search");
        if (search) {
          ev.preventDefault();
          search.focus();
        }
      }
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches(".cart, .row")) {
        ev.preventDefault();
        ev.target.click();
      }
    });
    document.addEventListener("input", (ev) => {
      if (ev.target.id === "search") {
        state.filters.q = ev.target.value;
        renderView();
      }
    });
    document.addEventListener("change", async (ev) => {
      const el = ev.target;
      if (el.dataset.setting) {
        await saveSetting(el.dataset.setting, el.type === "checkbox" ? el.checked : el.value);
      } else if (el.dataset.emu) {
        const value = el.type === "checkbox" ? el.checked : JSON.parse(el.value);
        try {
          state.emu = await api("/api/emu-settings", { changes: { [el.dataset.emu]: value } });
          toast("success", "config.json de shadPS4 mis à jour.");
          renderView();
        } catch (error) {
          handleError(error);
        }
      }
    });
    window.addEventListener("hashchange", () => go(location.hash.slice(1)));
    window.addEventListener("pagehide", () => {
      navigator.sendBeacon("/api/bye", new Blob([JSON.stringify({ token: TOKEN })], { type: "text/plain" }));
    });
  }

  // ------------------------------------------------------------------ barre latérale, suivi

  function badges() {
    const lib = state.library;
    const setCount = (name, value) => {
      const el = $(`[data-count="${name}"]`);
      if (el) el.textContent = value ? String(value) : "";
    };
    if (lib) {
      setCount("games", lib.games.filter((g) => g.launchable).length);
      setCount("pkgs", lib.pkgs.length);
    }
    const active = (state.status?.jobs || []).filter((j) => j.status === "running" || j.status === "queued").length;
    setCount("jobs", active);
    const settingsAlert = Boolean(state.updates?.update_available) ||
      (state.settings?.tools.shadps4.warnings || []).some((w) => w.level === "error");
    $('[data-dot="settings"]')?.classList.toggle("on", settingsAlert);
  }

  function renderRail() {
    const t = state.settings?.tools;
    if (!t) return;
    const build = t.shadps4.build;
    const update = state.updates?.update_available
      ? `<a class="tag warn" href="#settings">${icon("arrow-circle-up")} ${esc(state.updates.release.version)} disponible</a>`
      : "";
    $("#emu-shadps4").innerHTML = `<span class="lamp ${build ? "ok" : "off"}"></span><b>shadPS4</b><small>${build ? esc(build.label) : "introuvable"}</small>${update}`;
    const rp = t.rpcs3;
    $("#emu-rpcs3").innerHTML = `<span class="lamp ${rp.found ? (rp.firmware ? "ok" : "warn") : "off"}"></span><b>RPCS3</b><small>${rp.found ? (rp.firmware ? "firmware " + esc(rp.firmware) : "firmware manquant") : "non installé"}</small>`;
    badges();
  }

  function renderNowPlaying() {
    const box = $("#now-playing");
    const running = state.status?.runner?.running;
    if (!running) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.innerHTML = `<span class="pulse"></span><div><b>${esc(running.title)}</b><br><small>${esc(running.emulator)} · en cours depuis ${duration(Date.now() / 1000 - running.started)}</small></div><button class="btn small danger" type="button" data-action="stop">${icon("stop")} Arrêter</button>`;
  }

  let pollTimer = null;
  async function poll() {
    clearTimeout(pollTimer);
    try {
      const status = await api("/api/status");
      $("#offline").hidden = true;
      const first = state.status === null;
      state.status = status;
      for (const event of status.events) {
        if (event.id > state.lastEvent) {
          if (!first) toast(event.level, event.text);
          state.lastEvent = event.id;
        }
      }
      if (status.revision !== state.revision) {
        const initial = state.revision === -1;
        state.revision = status.revision;
        if (!initial) {
          await Promise.allSettled([loadLibrary(), loadSettings(), state.view === "settings" ? loadEmu() : null]);
          if (!state.modalClose) {
            renderView();
            renderDrawer();
          }
        } else if (state.view === "jobs") {
          renderView();
        }
      } else {
        patchProgress();
      }
      renderNowPlaying();
      badges();
    } catch (error) {
      if (error.status === 0) $("#offline").hidden = false;
    }
    const fast = state.status && (state.status.busy || state.status.runner.running);
    pollTimer = setTimeout(poll, fast ? 1200 : 3500);
  }

  // ------------------------------------------------------------------ démarrage

  bindEvents();
  go(location.hash.slice(1) || "library");
  loadSettings().catch(handleError);
  loadUpdates();
  poll();
})();
