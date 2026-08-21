let globalState = { players: [], summary: {} };
let teamsState = { teams: [], selectedTeamId: null, players: [], summary: {} };
let calculatorState = { players: [] };
let editingGlobalPlayer = null;
let activeView = "teams";
let globalPage = 1;
let globalLimit = 100;
let globalSearchTimer = null;
let autocompleteTimers = {};
let teamAssignmentRefreshTimer = null;
let teamAssignmentInFlight = 0;
let lastTeamAssignmentAt = 0;
let teamAssignmentQueue = Promise.resolve();
const HOME_TEAM_ID = "club-atletico-horizonte";
const TEAM_ASSIGNMENT_REFRESH_DELAY_MS = 1500;
const CALCULATOR_STORAGE_KEY = "eplCalculatorPlayers";
const POSITION_GROUPS = {
  forwards: ["DC", "EI", "ED"],
  midfielders: ["MC", "MCD", "MCO", "MI", "MD"],
  defenders: ["DFC", "LI", "LD"],
  goalkeepers: ["POR", "PO"],
};
const POSITION_GROUP_LABELS = {
  forwards: "Delanteros",
  midfielders: "Mediocampistas",
  defenders: "Defensores",
  goalkeepers: "Arqueros",
  unknown: "Sin clasificar",
};

const foldText = (value = "") => String(value)
  .normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "")
  .toLowerCase();

const money = (value) => {
  const safe = Number(value || 0);
  const sign = safe < 0 ? "-" : "";
  const abs = Math.abs(safe);
  if (abs >= 1) return `${sign}$${abs.toFixed(abs % 1 ? 2 : 0)}M`;
  return `${sign}$${Math.round(abs * 1000)}K`;
};

function budgetLabel(remainingM) {
  return money(remainingM);
}

const byId = (id) => document.getElementById(id);
const attrText = (value = "") => String(value).replaceAll('"', "'");
const transfermarktStatusLabel = (player) => {
  if (player.player_kind === "generic_unlicensed" || player.transfermarkt_validation_status === "generic_unlicensed") {
    return "Generico";
  }
  if (player.sofifa_roster_status === "retired" || player.transfermarkt_validation_status === "retired") {
    return "Retirado";
  }
  const labels = {
    match: "OK",
    warning: "Warning",
    no_value: "Sin valor",
    failed: "Fallido",
    generic_unlicensed: "Generico",
  };
  return labels[player.transfermarkt_validation_status] || player.transfermarkt_validation_status || "-";
};

function primaryPosition(player) {
  return String(player?.position || "").split(",")[0].trim().toUpperCase();
}

function positionGroup(player) {
  const first = primaryPosition(player);
  if (POSITION_GROUPS.forwards.includes(first)) return "forwards";
  if (POSITION_GROUPS.midfielders.includes(first)) return "midfielders";
  if (POSITION_GROUPS.defenders.includes(first)) return "defenders";
  if (POSITION_GROUPS.goalkeepers.includes(first)) return "goalkeepers";
  return "unknown";
}

function lineCounters(players = []) {
  return players.reduce((totals, player) => {
    totals[positionGroup(player)] += 1;
    return totals;
  }, { forwards: 0, midfielders: 0, defenders: 0, goalkeepers: 0, unknown: 0 });
}

function renderLineCounters(players = []) {
  const counts = lineCounters(players);
  return `
    <div class="line-counters">
      <span><strong>${counts.forwards}</strong>${POSITION_GROUP_LABELS.forwards}</span>
      <span><strong>${counts.midfielders}</strong>${POSITION_GROUP_LABELS.midfielders}</span>
      <span><strong>${counts.defenders}</strong>${POSITION_GROUP_LABELS.defenders}</span>
      <span><strong>${counts.goalkeepers}</strong>${POSITION_GROUP_LABELS.goalkeepers}</span>
      ${counts.unknown ? `<span class="warning"><strong>${counts.unknown}</strong>${POSITION_GROUP_LABELS.unknown}</span>` : ""}
    </div>
  `;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || response.statusText;
    try {
      const parsed = JSON.parse(text);
      message = parsed.detail || message;
    } catch {
      // Keep the raw response text when the server did not return JSON.
    }
    throw new Error(message);
  }
  return response.json();
}

async function initializeApp() {
  loadCalculatorState();
  await loadTeams();
  renderTeams();
  renderCalculator();
  renderActiveView();
}

async function loadTeams(selectedTeamId = teamsState.selectedTeamId) {
  const data = await api("/api/teams");
  teamsState.teams = data.teams || [];
  teamsState.selectedTeamId = selectedTeamId || teamsState.selectedTeamId || teamsState.teams[0]?.id || null;
  if (teamsState.selectedTeamId && !teamsState.teams.some((team) => team.id === teamsState.selectedTeamId)) {
    teamsState.selectedTeamId = teamsState.teams[0]?.id || null;
  }
  await loadSelectedTeamRoster();
}

async function loadSelectedTeamRoster() {
  if (!teamsState.selectedTeamId) {
    teamsState.players = [];
    teamsState.summary = {};
    return;
  }
  const data = await api(`/api/teams/${teamsState.selectedTeamId}/players`);
  teamsState.players = data.players || [];
  teamsState.summary = data.summary || {};
  const updatedTeam = data.team;
  if (updatedTeam) {
    teamsState.teams = teamsState.teams.map((team) => team.id === updatedTeam.id ? updatedTeam : team);
  }
}

async function loadGlobalPlayers(page = globalPage) {
  globalPage = page;
  const params = new URLSearchParams();
  params.set("page", String(globalPage));
  params.set("limit", String(globalLimit));
  const query = byId("globalSearchInput")?.value.trim();
  const position = byId("globalPositionInput")?.value.trim();
  const tmStatus = byId("globalTmStatusInput")?.value || "all";
  const minOverall = byId("globalMinOverallInput")?.value.trim();
  const maxValue = byId("globalMaxValueInput")?.value.trim();
  if (query) params.set("q", query);
  if (position) params.set("position", position);
  if (tmStatus !== "all") params.set("tm_status", tmStatus);
  if (minOverall) params.set("min_overall", minOverall);
  if (maxValue) params.set("max_value_m", maxValue);
  globalState = await api(`/api/global-players?${params.toString()}`);
}

function renderBudget() {
  const summary = teamsState.summary || {};
  const budgetM = summary.budget_m ?? 300;
  const spentM = summary.spent_m ?? 0;
  const remainingM = summary.remaining_m ?? budgetM;
  byId("remainingBudget").textContent = budgetLabel(remainingM);
  byId("initialBudget").textContent = money(budgetM);
  byId("spentBudget").textContent = money(spentM);
  byId("marketBudget").textContent = money(summary.market_m ?? 0);
  byId("salaryBudget").textContent = money(summary.salaries_m ?? 0);

  const usedPercent = Math.min(100, Math.max(0, budgetM ? (spentM / budgetM) * 100 : 0));
  byId("budgetFill").style.width = `${usedPercent}%`;
  byId("budgetFill").classList.toggle("over-budget", remainingM < 0);
  byId("remainingBudget").style.color = remainingM < 0 ? "var(--danger)" : "var(--text)";
}

function assignedTeamPill(player) {
  const teamName = player.assigned_team_name || "";
  return `<span class="team-pill ${teamName ? "" : "empty"}" title="${teamName ? `Pertenece a ${attrText(teamName)}` : ""}">${teamName ? `Equipo: ${teamName}` : "Sin equipo"}</span>`;
}

function teamAssignmentSelect(player, datasetName = "assignTeam") {
  const options = [
    `<option value="">Sin equipo</option>`,
    ...teamsState.teams.map((team) => (
      `<option value="${team.id}" ${player.assigned_team_id === team.id ? "selected" : ""}>${team.name}</option>`
    )),
  ].join("");
  return `
    <label class="team-assign-control" title="Equipo">
      <select aria-label="Equipo" data-${datasetName}="${player.id}" data-saved-team-id="${player.assigned_team_id || ""}">
        ${options}
      </select>
    </label>
  `;
}

function setTeamSelectValue(select, teamId) {
  if (!select) return;
  select.value = teamId || "";
  select.dataset.savedTeamId = teamId || "";
}

function playerAvatar(player, imageId = "") {
  const initials = (player.name || "?").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  const playerId = imageId || player.global_player_id || player.id || "";
  return playerId
    ? `<img class="player-avatar" src="/api/global-players/${playerId}/image" alt="${attrText(player.name)}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'), { className: 'player-avatar player-avatar-fallback', textContent: '${initials}' }))" />`
    : `<span class="player-avatar player-avatar-fallback">${initials}</span>`;
}

async function searchGlobalPlayerSuggestions(query) {
  const clean = query.trim();
  if (clean.length < 2) return [];
  const params = new URLSearchParams({ q: clean, limit: "8", page: "1" });
  const data = await api(`/api/global-players?${params.toString()}`);
  return data.players || [];
}

function suggestionSubtitle(player) {
  return [
    player.position || "Sin posicion",
    player.club || "",
    player.nationality || "",
    player.overall ? `Media ${player.overall}` : "",
    player.assigned_team_name ? `Equipo: ${player.assigned_team_name}` : "",
  ].filter(Boolean).join(" - ");
}

function hideAutocomplete(listId) {
  const list = byId(listId);
  if (!list) return;
  list.hidden = true;
  list.innerHTML = "";
}

function renderAutocomplete(inputId, listId, players) {
  const input = byId(inputId);
  const list = byId(listId);
  if (!input || !list) return;
  if (input.value.trim().length < 2) {
    hideAutocomplete(listId);
    return;
  }
  if (!players.length) {
    list.innerHTML = `<span class="autocomplete-empty">Sin coincidencias</span>`;
    list.hidden = false;
    return;
  }
  list.innerHTML = "";
  players.forEach((player) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "autocomplete-option";
    item.dataset.autocompleteTarget = inputId;
    item.dataset.autocompleteList = listId;
    item.dataset.playerId = player.id;
    item.dataset.playerName = player.name || "";
    item.innerHTML = `
      ${playerAvatar(player, player.id)}
      <span>
        <strong>${player.name}</strong>
        <small>${suggestionSubtitle(player)}</small>
      </span>
    `;
    list.appendChild(item);
  });
  list.hidden = false;
}

async function findGlobalPlayerByInput(query, selectedPlayerId = "") {
  const selected = String(selectedPlayerId || "").trim();
  const clean = String(query || "").trim();
  if (!selected && !clean) return null;

  const loaded = selected ? findLoadedPlayer(selected) : null;
  if (loaded) return loaded;

  const params = new URLSearchParams({
    q: selected || clean,
    limit: "10",
    page: "1",
  });
  const data = await api(`/api/global-players?${params.toString()}`);
  const players = data.players || [];
  if (selected) {
    return players.find((player) => player.id === selected || String(player.sofifa_id || "") === selected) || players[0] || null;
  }

  const folded = foldText(clean);
  return players.find((player) => (
    [player.id, player.sofifa_id, player.name, player.long_name]
      .some((value) => foldText(value) === folded)
  )) || players[0] || null;
}

function loadCalculatorState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CALCULATOR_STORAGE_KEY) || "[]");
    calculatorState.players = Array.isArray(parsed) ? parsed : [];
  } catch {
    calculatorState.players = [];
  }
}

function saveCalculatorState() {
  localStorage.setItem(CALCULATOR_STORAGE_KEY, JSON.stringify(calculatorState.players));
}

function buildCalculatorSummary(players = calculatorState.players) {
  const count = players.length;
  const marketM = players.reduce((total, player) => total + Number(player.market_value_m || 0), 0);
  const salaryM = players.reduce((total, player) => total + Number(player.salary_m || 0), 0);
  const totalM = players.reduce((total, player) => total + Number(player.total_cost_m || 0), 0);
  const avgOverall = count
    ? players.reduce((total, player) => total + Number(player.overall || 0), 0) / count
    : 0;
  return {
    count,
    avgOverall,
    marketM,
    salaryM,
    totalM,
    remainingM: 300 - totalM,
  };
}

function renderCalculator() {
  const summary = byId("calculatorSummary");
  const tbody = byId("calculatorPlayersTable");
  if (!summary || !tbody) return;

  const data = buildCalculatorSummary();
  summary.innerHTML = `
    <div class="summary-tile"><span>Jugadores</span><strong>${data.count}</strong></div>
    <div class="summary-tile"><span>Media promedio</span><strong>${data.count ? data.avgOverall.toFixed(1) : "-"}</strong></div>
    <div class="summary-tile"><span>Mercado</span><strong>${money(data.marketM)}</strong></div>
    <div class="summary-tile"><span>Sueldos</span><strong>${money(data.salaryM)}</strong></div>
    <div class="summary-tile"><span>Total</span><strong>${money(data.totalM)}</strong></div>
    <div class="summary-tile ${data.remainingM < 0 ? "over-budget" : ""}"><span>Presupuesto</span><strong>${money(data.remainingM)}</strong></div>
    ${renderLineCounters(calculatorState.players)}
  `;

  tbody.innerHTML = "";
  calculatorState.players.forEach((player) => {
    const sofifaUrl = player.sofifa_url || (player.sofifa_id ? `https://sofifa.com/player/${player.sofifa_id}` : "");
    const transfermarktUrl = player.transfermarkt_url || "";
    const transfermarktIssue = player.transfermarkt_error_detail || "";
    const transfermarktWarning = transfermarktIssue
      ? `<span class="error-badge" title="${attrText(transfermarktIssue)}">!</span>`
      : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div class="player-cell">
          ${playerAvatar(player, player.id)}
          <div class="player-name">
            <strong>${player.name}</strong>
            <span class="muted">${player.position || "Sin posicion"} ${player.club ? `- ${player.club}` : ""}</span>
            <span class="muted">${player.nationality || ""}</span>
            <span class="muted">${player.skill_moves ? `${player.skill_moves} SM` : ""} ${player.weak_foot ? `${player.weak_foot} WF` : ""} ${player.acceleration_type || ""}</span>
            ${assignedTeamPill(player)}
          </div>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>ID: ${player.sofifa_id || "-"}</span>
          <span>Version: ${player.sofifa_version || "-"}</span>
          <span>Media: <strong>${player.overall ?? "-"}</strong></span>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>Valor TM: ${money(player.market_value_m)}</span>
          <span>Moneda: ${player.market_value_currency || "EUR"}</span>
          <span>Consulta: ${player.market_value_checked_at || "-"}</span>
          <span class="validation-line">Validacion: ${transfermarktStatusLabel(player)} ${transfermarktWarning}</span>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>Sueldo: ${money(player.salary_m)}</span>
          <strong>Total: ${money(player.total_cost_m)}</strong>
        </div>
      </td>
      <td>
        <div class="attr-grid">
          <span>RIT ${player.pace ?? "-"}</span>
          <span>TIR ${player.shooting ?? "-"}</span>
          <span>PAS ${player.passing ?? "-"}</span>
          <span>REG ${player.dribbling ?? "-"}</span>
          <span>DEF ${player.defending ?? "-"}</span>
          <span>FIS ${player.physical ?? "-"}</span>
        </div>
      </td>
      <td>
        <div class="row-actions">
          ${sofifaUrl ? `<a class="button-link secondary" href="${sofifaUrl}" target="_blank" rel="noreferrer">SoFIFA</a>` : `<span class="button-link disabled">SoFIFA</span>`}
          ${transfermarktUrl ? `<a class="button-link secondary" href="${transfermarktUrl}" target="_blank" rel="noreferrer">Transfermarkt</a>` : `<span class="button-link disabled">Transfermarkt</span>`}
        </div>
      </td>
      <td>
        <div class="row-actions">
          <button class="secondary" data-edit-global="${player.id}" type="button">Detalles</button>
          <button class="danger" data-remove-calculator-player="${player.id}" type="button">Quitar</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function renderActiveView() {
  byId("teamsView").hidden = activeView !== "teams";
  byId("globalView").hidden = activeView !== "global";
  byId("calculatorView").hidden = activeView !== "calculator";
  byId("teamsTab").classList.toggle("active", activeView === "teams");
  byId("globalTab").classList.toggle("active", activeView === "global");
  byId("calculatorTab").classList.toggle("active", activeView === "calculator");
}

function selectedTeam() {
  return teamsState.teams.find((team) => team.id === teamsState.selectedTeamId) || null;
}

function isHomeTeam(team) {
  return team?.id === HOME_TEAM_ID;
}

function renderTeams() {
  const list = byId("teamsList");
  if (!list) return;
  const team = selectedTeam();
  renderBudget();
  list.innerHTML = "";
  teamsState.teams.forEach((item) => {
    const initials = (item.name || "?").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
    const logo = item.logo_url
      ? `<img class="team-logo" src="${item.logo_url}" alt="${attrText(item.name)}" />`
      : `<span class="team-logo team-logo-fallback">${initials}</span>`;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `team-card ${item.id === teamsState.selectedTeamId ? "active" : ""}`;
    button.dataset.selectTeam = item.id;
    button.innerHTML = `
      ${logo}
      <span>
        <strong>${item.name}</strong>
        <small>${item.owner || "Sin owner"} - ${item.roster_count || 0} jugadores</small>
      </span>
    `;
    list.appendChild(button);
  });

  byId("teamNameInput").value = team?.name || "";
  byId("teamOwnerInput").value = team?.owner || "";
  byId("teamLogoUrlInput").value = team?.logo_url || "";
  byId("teamNameInput").disabled = isHomeTeam(team);
  byId("teamOwnerInput").disabled = isHomeTeam(team);
  byId("deleteTeam").disabled = !team || isHomeTeam(team);
  byId("deleteTeam").title = isHomeTeam(team)
    ? "Club Atletico Horizonte es tu equipo y no se puede borrar."
    : "Borrar equipo y liberar sus jugadores.";

  const header = byId("selectedTeamHeader");
  if (!team) {
    header.innerHTML = `<p class="muted">Crea un equipo para empezar a cargar planteles.</p>`;
  } else {
    const budget = teamsState.summary || {};
    const remainingM = budget.remaining_m ?? budget.budget_m ?? 300;
    const usedPercent = Math.min(100, Math.max(0, ((budget.spent_m || 0) / (budget.budget_m || 300)) * 100));
    const initials = (team.name || "?").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
    const logo = team.logo_url
      ? `<img class="team-logo large" src="${team.logo_url}" alt="${attrText(team.name)}" />`
      : `<span class="team-logo large team-logo-fallback">${initials}</span>`;
    header.innerHTML = `
      <div class="selected-team-title">
        ${logo}
        <div>
          <p class="eyebrow">Plantel</p>
          <h2>${team.name}</h2>
          <span class="muted">${team.owner || "Sin owner/admin"} - ${team.roster_count || 0} jugadores</span>
        </div>
      </div>
      <div class="team-budget">
        <div class="team-budget-main ${remainingM < 0 ? "over-budget" : ""}">
          <span>Presupuesto</span>
          <strong>${budgetLabel(remainingM)}</strong>
          <div class="mini-budget-bar"><span class="${remainingM < 0 ? "over-budget" : ""}" style="width:${usedPercent}%"></span></div>
        </div>
        <div><span>Inicial</span><strong>${money(budget.budget_m ?? 300)}</strong></div>
        <div><span>Gastado</span><strong>${money(budget.spent_m || 0)}</strong></div>
        <div><span>Mercado</span><strong>${money(budget.market_m || 0)}</strong></div>
        <div><span>Sueldos</span><strong>${money(budget.salaries_m || 0)}</strong></div>
      </div>
      ${renderLineCounters(teamsState.players)}
    `;
  }
  renderTeamRoster();
}

function renderTeamRoster() {
  const tbody = byId("teamPlayersTable");
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!teamsState.selectedTeamId) return;
  teamsState.players.forEach((player) => {
    const sofifaUrl = player.sofifa_url || (player.sofifa_id ? `https://sofifa.com/player/${player.sofifa_id}` : "");
    const transfermarktUrl = player.transfermarkt_url || "";
    const transfermarktIssue = player.transfermarkt_error_detail || "";
    const transfermarktWarning = transfermarktIssue
      ? `<span class="error-badge" title="${attrText(transfermarktIssue)}">!</span>`
      : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div class="player-cell">
          ${playerAvatar(player, player.id)}
          <div class="player-name">
            <strong>${player.name}</strong>
            <span class="muted">${player.position || "Sin posicion"} ${player.club ? `- ${player.club}` : ""}</span>
            <span class="muted">${player.nationality || ""}</span>
            <span class="muted">${player.skill_moves ? `${player.skill_moves} SM` : ""} ${player.weak_foot ? `${player.weak_foot} WF` : ""} ${player.acceleration_type || ""}</span>
            ${assignedTeamPill(player)}
          </div>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>ID: ${player.sofifa_id || "-"}</span>
          <span>Version: ${player.sofifa_version || "-"}</span>
          <span>Media: <strong>${player.overall ?? "-"}</strong></span>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>Valor TM: ${money(player.market_value_m)}</span>
          <span>Moneda: ${player.market_value_currency || "EUR"}</span>
          <span>Consulta: ${player.market_value_checked_at || "-"}</span>
          <span class="validation-line">Validacion: ${transfermarktStatusLabel(player)} ${transfermarktWarning}</span>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>Sueldo: ${money(player.salary_m)}</span>
          <strong>Total: ${money(player.total_cost_m)}</strong>
        </div>
      </td>
      <td>
        <div class="attr-grid">
          <span>RIT ${player.pace ?? "-"}</span>
          <span>TIR ${player.shooting ?? "-"}</span>
          <span>PAS ${player.passing ?? "-"}</span>
          <span>REG ${player.dribbling ?? "-"}</span>
          <span>DEF ${player.defending ?? "-"}</span>
          <span>FIS ${player.physical ?? "-"}</span>
        </div>
      </td>
      <td>
        <div class="row-actions">
          ${sofifaUrl ? `<a class="button-link secondary" href="${sofifaUrl}" target="_blank" rel="noreferrer">SoFIFA</a>` : `<span class="button-link disabled">SoFIFA</span>`}
          ${transfermarktUrl ? `<a class="button-link secondary" href="${transfermarktUrl}" target="_blank" rel="noreferrer">Transfermarkt</a>` : `<span class="button-link disabled">Transfermarkt</span>`}
        </div>
      </td>
      <td>
        <div class="row-actions">
          <button class="secondary" data-edit-global="${player.id}" type="button">Detalles</button>
          ${teamAssignmentSelect(player, "move-team-player")}
          <button class="secondary" data-refresh-transfermarkt="${player.id}" type="button">Actualizar TM</button>
          <button class="danger" data-remove-team-player="${player.id}" type="button">Quitar</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function renderGlobalSummary() {
  const summary = globalState.summary || {};
  const metadata = summary.metadata;
  const tmDone = summary.transfermarkt_completed || 0;
  const tmTotal = summary.transfermarkt_available || 0;
  const tmPercent = tmTotal ? ((tmDone / tmTotal) * 100).toFixed(2) : "0.00";
  byId("globalSummary").innerHTML = `
    <div class="summary-tile"><span>Jugadores</span><strong>${summary.total_count || 0}</strong></div>
    <div class="summary-tile"><span>Con SoFIFA</span><strong>${summary.sofifa_completed || 0}</strong></div>
    <div class="summary-tile"><span>Con mercado</span><strong>${summary.market_completed || 0}</strong></div>
    <div class="summary-tile">
      <span>TM actualizado</span>
      <strong>${tmDone}/${tmTotal}</strong>
      <small>${tmPercent}% completo</small>
    </div>
    <div class="summary-tile">
      <span>TM fallidos</span>
      <strong>${summary.transfermarkt_failed || 0}</strong>
      <small>${summary.transfermarkt_warnings || 0} warnings - ${summary.transfermarkt_no_value || 0} sin valor - ${summary.sofifa_retired || 0} retirados</small>
    </div>
    <div class="summary-tile">
      <span>GenÃƒÂ©ricos ocultos</span>
      <strong>${summary.generic_unlicensed || 0}</strong>
      <small>No licenciados ocultos</small>
    </div>
    <div class="summary-tile"><span>${summary.storage === "sqlite" ? "SQLite" : "JSON local"}</span><strong>${metadata?.source_version || summary.avg_overall || "-"}</strong></div>
  `;
  renderGlobalPagination();
}

function renderGlobalPagination() {
  const pagination = globalState.pagination || { page: 1, pages: 1, total: globalState.players.length };
  byId("globalPageInfo").textContent = `Pagina ${pagination.page || 1}/${pagination.pages || 1} - ${pagination.total || 0} resultados`;
  byId("prevGlobalPage").disabled = (pagination.page || 1) <= 1;
  byId("nextGlobalPage").disabled = (pagination.page || 1) >= (pagination.pages || 1);
}

function renderGlobalTable() {
  const tbody = byId("globalPlayersTable");
  tbody.innerHTML = "";

  globalState.players.forEach((player) => {
    const initials = (player.name || "?").split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
    const sofifaUrl = player.sofifa_url || (player.sofifa_id ? `https://sofifa.com/player/${player.sofifa_id}` : "");
    const transfermarktUrl = player.transfermarkt_url || "";
    const transfermarktIssue = player.transfermarkt_error_detail || "";
    const transfermarktStatus = transfermarktStatusLabel(player);
    const transfermarktWarning = transfermarktIssue
      ? `<span class="error-badge" title="${transfermarktIssue.replaceAll('"', "'")}">!</span>`
      : "";
    const avatar = player.sofifa_id
      ? `<img class="player-avatar" src="/api/global-players/${player.id}/image" alt="${player.name}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'), { className: 'player-avatar player-avatar-fallback', textContent: '${initials}' }))" />`
      : `<span class="player-avatar player-avatar-fallback">${initials}</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div class="player-cell">
          ${avatar}
          <div class="player-name">
            <strong>${player.name}</strong>
            <span class="muted">${player.position || "Sin posicion"} ${player.club ? `- ${player.club}` : ""}</span>
            <span class="muted">${player.nationality || ""}</span>
            <span class="muted">${player.skill_moves ? `${player.skill_moves} SM` : ""} ${player.weak_foot ? `${player.weak_foot} WF` : ""} ${player.acceleration_type || ""}</span>
            ${assignedTeamPill(player)}
          </div>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>ID: ${player.sofifa_id || "-"}</span>
          <span>Version: ${player.sofifa_version || "-"}</span>
          <span>Media: <strong>${player.overall ?? "-"}</strong></span>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>Valor TM: ${money(player.market_value_m)}</span>
          <span>Moneda: ${player.market_value_currency || "EUR"}</span>
          <span>Consulta: ${player.market_value_checked_at || "-"}</span>
          <span class="validation-line">Validacion: ${transfermarktStatus} ${transfermarktWarning}</span>
        </div>
      </td>
      <td>
        <div class="cost-stack">
          <span>Sueldo: ${money(player.salary_m)}</span>
          <strong>Total: ${money(player.total_cost_m)}</strong>
        </div>
      </td>
      <td>
        <div class="attr-grid">
          <span>RIT ${player.pace ?? "-"}</span>
          <span>TIR ${player.shooting ?? "-"}</span>
          <span>PAS ${player.passing ?? "-"}</span>
          <span>REG ${player.dribbling ?? "-"}</span>
          <span>DEF ${player.defending ?? "-"}</span>
          <span>FIS ${player.physical ?? "-"}</span>
        </div>
      </td>
      <td>
        <div class="row-actions">
          <button class="secondary" data-edit-global="${player.id}" type="button">Detalles</button>
          ${teamAssignmentSelect(player, "assign-global-team")}
          <button class="secondary" data-refresh-transfermarkt="${player.id}" type="button">Actualizar TM</button>
          ${sofifaUrl ? `<a class="button-link secondary" href="${sofifaUrl}" target="_blank" rel="noreferrer">SoFIFA</a>` : `<span class="button-link disabled">SoFIFA</span>`}
          ${transfermarktUrl ? `<a class="button-link secondary" href="${transfermarktUrl}" target="_blank" rel="noreferrer">Transfermarkt</a>` : `<span class="button-link disabled">Transfermarkt</span>`}
          <button class="danger" data-delete-global="${player.id}" type="button">Borrar</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function emptyGlobalPlayer() {
  return {
    name: "",
    position: "",
    club: "",
    nationality: "",
    sofifa_id: "",
    sofifa_url: "",
    sofifa_version: "2026-07-16",
    transfermarkt_url: "",
    image_url: "",
    overall: null,
    market_value_m: null,
    market_value_currency: "EUR",
    market_value_checked_at: "",
    weak_foot: null,
    skill_moves: null,
    international_reputation: null,
    body_type: "",
    real_face: "",
    release_clause_m: null,
    acceleration_type: "",
    play_styles: "",
    specialities: "",
    roles: [],
    pace: null,
    shooting: null,
    passing: null,
    dribbling: null,
    defending: null,
    physical: null,
    tags: [],
    notes: "",
  };
}

const attributeGroups = [
  {
    title: "RIT",
    items: [
      ["movement_sprint_speed", "Velocidad"],
      ["movement_acceleration", "Aceleracion"],
    ],
  },
  {
    title: "TIR",
    items: [
      ["attacking_finishing", "Definicion"],
      ["mentality_attack_position", "Pos. ataque"],
      ["power_shot_power", "Potencia"],
      ["power_long_shots", "Tiros lejanos"],
      ["mentality_penalties", "Penaltis"],
      ["attacking_volleys", "Voleas"],
    ],
  },
  {
    title: "PAS",
    items: [
      ["mentality_vision", "Vision"],
      ["attacking_crossing", "Centros"],
      ["skill_fk_accuracy", "Precision faltas"],
      ["skill_long_passing", "Pases largos"],
      ["attacking_short_passing", "Pases cortos"],
      ["skill_curve", "Efecto"],
    ],
  },
  {
    title: "REG",
    items: [
      ["movement_agility", "Agilidad"],
      ["movement_balance", "Equilibrio"],
      ["movement_reactions", "Reaccion"],
      ["mentality_composure", "Compostura"],
      ["skill_ball_control", "Control del balon"],
      ["skill_dribbling", "Regates"],
    ],
  },
  {
    title: "DEF",
    items: [
      ["mentality_interceptions", "Intercep."],
      ["attacking_heading_accuracy", "Precision cabeza"],
      ["defending_defensive_awareness", "Conciencia defensiva"],
      ["defending_standing_tackle", "Robos"],
      ["defending_sliding_tackle", "Entrada agresiva"],
    ],
  },
  {
    title: "FIS",
    items: [
      ["power_jumping", "Salto"],
      ["power_stamina", "Resistencia"],
      ["power_strength", "Fuerza"],
      ["mentality_aggression", "Agresividad"],
    ],
  },
  {
    title: "Portero",
    items: [
      ["goalkeeping_gk_diving", "Estirada"],
      ["goalkeeping_gk_handling", "Paradas"],
      ["goalkeeping_gk_kicking", "Saques"],
      ["goalkeeping_gk_positioning", "Colocacion"],
      ["goalkeeping_gk_reflexes", "Reflejos"],
    ],
  },
];

const playStyleLabels = {
  Block: "Bloqueo",
  Intercept: "Interceptor",
  "Slide tackle": "Barridas",
  Bruiser: "LeÃƒÂ±ero",
  "Precision header": "Cabeceador preciso",
};

function attributeClass(value) {
  const safe = Number(value || 0);
  if (safe >= 75) return "high";
  if (safe >= 60) return "mid";
  if (safe >= 45) return "low";
  return "poor";
}

function renderGlobalAttributeDetails(player) {
  const container = byId("globalAttributeDetails");
  if (!container) return;
  const attrs = player?.attributes || {};
  const hasDetails = Object.keys(attrs).length > 0;
  const playStyles = String(player?.play_styles || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const specialities = String(player?.specialities || "")
    .split(",")
    .map((item) => item.trim().replace(/^#/, ""))
    .filter(Boolean);
  const roles = Array.isArray(player?.roles) ? player.roles.filter(Boolean) : [];

  if (!hasDetails && !playStyles.length && !specialities.length && !roles.length) {
    container.innerHTML = `<p class="muted">Sin atributos detallados cargados todavia.</p>`;
    return;
  }

  const groups = attributeGroups
    .map((group) => {
      const rows = group.items
        .filter(([key]) => attrs[key] !== undefined && attrs[key] !== null && attrs[key] !== "")
        .map(([key, label]) => {
          const value = attrs[key];
          return `
            <li>
              <span class="attr-score ${attributeClass(value)}">${value}</span>
              <span>${label}</span>
            </li>
          `;
        })
        .join("");
      if (!rows) return "";
      return `
        <div class="attribute-group">
          <h3>${group.title}</h3>
          <ul>${rows}</ul>
        </div>
      `;
    })
    .join("");

  const styles = playStyles.length
    ? `
      <div class="attribute-group">
        <h3>Estilos de jugador</h3>
        <ul>${playStyles.map((style) => `<li><span>${playStyleLabels[style] || style}</span></li>`).join("")}</ul>
      </div>
    `
    : "";

  const specialitiesMarkup = specialities.length
    ? `
      <div class="attribute-group">
        <h3>Especialidades</h3>
        <ul>${specialities.map((item) => `<li><span>${item}</span></li>`).join("")}</ul>
      </div>
    `
    : "";

  const rolesMarkup = roles.length
    ? `
      <div class="attribute-group attribute-group-wide">
        <h3>Roles</h3>
        <ul>${roles.map((item) => `<li><span>${item}</span></li>`).join("")}</ul>
      </div>
    `
    : "";

  container.innerHTML = `${groups}${styles}${specialitiesMarkup}${rolesMarkup}`;
}

function openGlobalEdit(player = null) {
  editingGlobalPlayer = player;
  const formPlayer = player || emptyGlobalPlayer();
  byId("globalEditTitle").textContent = player ? player.name : "Nuevo jugador";
  byId("globalEditMeta").textContent = player ? "Editar jugador global" : "Agregar jugador global";
  const form = byId("globalForm");
  [...form.elements].forEach((field) => {
    if (!field.name) return;
    if (field.name === "tags") {
      field.value = (formPlayer.tags || []).join(", ");
    } else {
      field.value = formPlayer[field.name] ?? "";
    }
    const isMetadata = ["tags", "notes"].includes(field.name);
    field.readOnly = Boolean(player) && !isMetadata;
    field.classList.toggle("locked-field", Boolean(player) && !isMetadata);
  });
  byId("fetchSofifaForForm").hidden = Boolean(player);
  byId("saveGlobalEdit").textContent = player ? "Guardar notas" : "Guardar";
  renderGlobalAttributeDetails(formPlayer);
  byId("globalDialog").showModal();
}

async function saveGlobalEdit() {
  const form = byId("globalForm");
  const data = {};
  [...form.elements].forEach((field) => {
    if (!field.name) return;
    const value = field.value.trim();
    if (field.name === "tags") {
      data.tags = value ? value.split(",").map((tag) => tag.trim()).filter(Boolean) : [];
    } else if (field.type === "number") {
      data[field.name] = value === "" ? null : Number(value);
    } else {
      data[field.name] = value;
    }
  });

  if (!data.name) {
    alert("El nombre es obligatorio.");
    return;
  }

  if (editingGlobalPlayer) {
    await api(`/api/global-players/${editingGlobalPlayer.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        tags: data.tags,
        notes: data.notes,
      }),
    });
  } else {
    await api("/api/global-players", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }
  byId("globalDialog").close();
  await loadGlobalPlayers();
  renderGlobalSummary();
  renderGlobalTable();
}

function fillGlobalFormFromSofifa(player) {
  const form = byId("globalForm");
  Object.entries(player).forEach(([key, value]) => {
    if (value === "" || value === null || value === undefined) return;
    const field = form.elements[key];
    if (!field || key === "tags") return;
    field.value = value;
  });
}

async function fetchSofifaForForm() {
  const form = byId("globalForm");
  const source = form.elements.sofifa_url.value.trim() || form.elements.sofifa_id.value.trim();
  if (!source) {
    alert("Carga primero una URL o ID de SoFIFA.");
    return;
  }
  try {
    const result = await api("/api/fetch/sofifa", {
      method: "POST",
      body: JSON.stringify({ url_or_id: source }),
    });
    fillGlobalFormFromSofifa(result.player);
    alert("Datos SoFIFA completados. Revisa y guarda el jugador.");
  } catch (error) {
    alert(`No se pudo completar desde SoFIFA: ${error.message}`);
  }
}

async function refreshGlobalPlayerFromSofifa(playerId) {
  try {
    const updated = await api(`/api/global-players/${playerId}/refresh-sofifa`, { method: "POST" });
    await loadGlobalPlayers();
    renderGlobalSummary();
    renderGlobalTable();
    alert(`SoFIFA actualizado para ${updated.name}.`);
  } catch (error) {
    alert(`No se pudo actualizar SoFIFA: ${error.message}`);
  }
}

async function refreshGlobalPlayerFromTransfermarkt(playerId) {
  try {
    const updated = await api(`/api/global-players/${playerId}/refresh-transfermarkt`, { method: "POST" });
    if (activeView === "teams") {
      await loadSelectedTeamRoster();
      renderTeamRoster();
    }
    await loadGlobalPlayers(globalPage);
    renderGlobalSummary();
    renderGlobalTable();
    alert(`Transfermarkt actualizado para ${updated.name}. Costo total: ${money(updated.total_cost_m)}.`);
  } catch (error) {
    alert(`No se pudo actualizar Transfermarkt: ${error.message}`);
  }
}

function setTransfermarktProgress({ current = 0, total = 0, ok = 0, failed = 0, detail = "" }) {
  const progress = byId("tmProgress");
  const percent = total ? Math.round((current / total) * 100) : 0;
  progress.hidden = false;
  byId("tmProgressCount").textContent = `${current}/${total} - ${percent}%`;
  byId("tmProgressFill").style.width = `${percent}%`;
  byId("tmProgressDetail").textContent = detail || `OK ${ok} - Fallidos ${failed}`;
}

async function refreshTransfermarktBatch() {
  const limit = Math.max(1, Math.min(25000, Number(byId("tmBatchLimitInput").value) || 100));
  const workers = Math.max(1, Math.min(10, Number(byId("tmWorkersInput").value) || 10));
  const skipUpdated = byId("tmSkipUpdatedInput").checked;
  const mode = skipUpdated ? "solo pendientes" : "incluyendo ya actualizados";
  if (!confirm(`Actualizar ${limit} jugadores desde Transfermarkt (${mode}) usando ${workers} workers?`)) return;

  const button = byId("refreshTransfermarktBatch");
  button.disabled = true;
  button.textContent = "Actualizando...";
  setTransfermarktProgress({ total: limit, detail: "Buscando jugadores para actualizar..." });
  try {
    const query = new URLSearchParams({ limit: String(limit), skip_updated: String(skipUpdated) });
    const targetResult = await api(`/api/global-players/transfermarkt-targets?${query.toString()}`);
    const targets = targetResult.targets || [];
    if (!targets.length) {
      setTransfermarktProgress({ total: 0, detail: "No hay jugadores pendientes para esta tanda." });
      alert("No hay jugadores para actualizar en esta tanda.");
      return;
    }

    let ok = 0;
    let failed = 0;
    let consecutiveFailures = 0;
    let stopped = false;
    let completed = 0;
    let nextIndex = 0;

    const runWorker = async (workerNumber) => {
      while (!stopped) {
        const target = targets[nextIndex];
        nextIndex += 1;
        if (!target) return;

        setTransfermarktProgress({
          current: completed,
          total: targets.length,
          ok,
          failed,
          detail: `W${workerNumber} actualizando ${target.name} (${target.sofifa_id})...`,
        });

        try {
          await api(`/api/global-players/${target.id}/refresh-transfermarkt`, { method: "POST" });
          ok += 1;
          consecutiveFailures = 0;
          completed += 1;
          setTransfermarktProgress({
            current: completed,
            total: targets.length,
            ok,
            failed,
            detail: `W${workerNumber} OK ${target.name}. OK ${ok} - Fallidos ${failed}`,
          });
        } catch (error) {
          failed += 1;
          consecutiveFailures += 1;
          completed += 1;
          setTransfermarktProgress({
            current: completed,
            total: targets.length,
            ok,
            failed,
            detail: `W${workerNumber} fallo ${target.name}: ${error.message}`,
          });
          if (consecutiveFailures >= 5) {
            stopped = true;
            return;
          }
        }
      }
    };

    const activeWorkers = Array.from(
      { length: Math.min(workers, targets.length) },
      (_, index) => runWorker(index + 1)
    );
    await Promise.all(activeWorkers);

    await loadGlobalPlayers(globalPage);
    renderGlobalSummary();
    renderGlobalTable();
    const stopText = stopped ? " Se corto por 5 fallos consecutivos." : "";
    alert(`Transfermarkt: ${ok} actualizados, ${failed} fallidos.${stopText}`);
  } catch (error) {
    alert(`No se pudo actualizar la tanda Transfermarkt: ${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "Actualizar valores TM";
  }
}

async function deleteGlobalPlayer(playerId) {
  const player = globalState.players.find((item) => item.id === playerId);
  if (!confirm(`Borrar ${player?.name || "este jugador"} de la base global?`)) return;
  await api(`/api/global-players/${playerId}`, { method: "DELETE" });
  await loadGlobalPlayers();
  renderGlobalSummary();
  renderGlobalTable();
}

function readTeamLogoFile() {
  const file = byId("teamLogoFileInput")?.files?.[0];
  if (!file) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ filename: file.name, data_url: reader.result });
    reader.onerror = () => reject(new Error("No se pudo leer el escudo."));
    reader.readAsDataURL(file);
  });
}

async function saveSelectedTeam() {
  const team = selectedTeam();
  const payload = isHomeTeam(team)
    ? { logo_url: byId("teamLogoUrlInput").value.trim() }
    : {
        name: byId("teamNameInput").value.trim(),
        owner: byId("teamOwnerInput").value.trim(),
        logo_url: byId("teamLogoUrlInput").value.trim(),
      };
  if (!isHomeTeam(team) && !payload.name) {
    alert("El nombre del equipo es obligatorio.");
    return;
  }
  const saved = team
    ? await api(`/api/teams/${team.id}`, { method: "PATCH", body: JSON.stringify(payload) })
    : await api("/api/teams", { method: "POST", body: JSON.stringify(payload) });
  const logoPayload = await readTeamLogoFile();
  if (logoPayload) {
    await api(`/api/teams/${saved.id}/logo`, { method: "POST", body: JSON.stringify(logoPayload) });
    byId("teamLogoFileInput").value = "";
  }
  await loadTeams(saved.id);
  renderTeams();
  if (activeView === "global") {
    await loadGlobalPlayers(globalPage);
    renderGlobalSummary();
    renderGlobalTable();
  }
}

async function deleteSelectedTeam() {
  const team = selectedTeam();
  if (!team) return;
  if (isHomeTeam(team)) {
    alert("Club Atletico Horizonte es tu equipo y no se puede borrar.");
    return;
  }
  if (!confirm(`Seguro que queres borrar ${team.name}? Sus jugadores quedaran libres de equipo.`)) return;
  await api(`/api/teams/${team.id}`, { method: "DELETE" });
  await loadTeams(null);
  renderTeams();
}

async function assignPlayerToTeam(teamId, playerIdentifier, selectedAfter = teamsState.selectedTeamId || teamId, force = false) {
  const result = await api(`/api/teams/${teamId}/players`, {
    method: "POST",
    body: JSON.stringify({ query: playerIdentifier, player_id: playerIdentifier, force }),
  });
  if (result.player?.id) {
    applyLocalTeamAssignment(result.player.id, teamId);
  }
  await loadTeams(selectedAfter);
  if (activeView === "global") {
    await loadGlobalPlayers(globalPage);
    renderGlobalSummary();
    renderGlobalTable();
  } else {
    renderTeams();
  }
  return result;
}

function findLoadedPlayer(playerId) {
  return globalState.players.find((item) => item.id === playerId)
    || teamsState.players.find((item) => item.id === playerId)
    || calculatorState.players.find((item) => item.id === playerId)
    || null;
}

function teamById(teamId) {
  return teamsState.teams.find((team) => team.id === teamId) || null;
}

function applyLocalTeamAssignment(playerId, teamId) {
  const team = teamById(teamId);
  const assigned = {
    assigned_team_id: team?.id || "",
    assigned_team_name: team?.name || "",
    assigned_team_owner: team?.owner || "",
    assigned_team_logo_url: team?.logo_url || "",
  };
  const applyTo = (player) => {
    if (!player) return player;
    return Object.assign(player, assigned);
  };
  globalState.players.forEach((player) => {
    if (player.id === playerId) applyTo(player);
  });
  teamsState.players.forEach((player) => {
    if (player.id === playerId) applyTo(player);
  });
}

function updateRenderedTeamPill(select, teamId) {
  const pill = select.closest("tr")?.querySelector(".team-pill");
  if (!pill) return;
  const team = teamById(teamId);
  pill.classList.toggle("empty", !team);
  pill.textContent = team ? `Equipo: ${team.name}` : "Sin equipo";
  pill.title = team ? `Pertenece a ${team.name}` : "";
}

function syncRenderedTeamAssignment(playerId, teamId) {
  document
    .querySelectorAll(`select[data-assign-global-team="${playerId}"], select[data-move-team-player="${playerId}"]`)
    .forEach((select) => {
      setTeamSelectValue(select, teamId);
      updateRenderedTeamPill(select, teamId);
    });
}

async function refreshAfterTeamAssignments(selectedTeamId = teamsState.selectedTeamId) {
  if (teamAssignmentInFlight > 0) {
    scheduleTeamAssignmentRefresh(selectedTeamId);
    return;
  }
  if (Date.now() - lastTeamAssignmentAt < TEAM_ASSIGNMENT_REFRESH_DELAY_MS) {
    scheduleTeamAssignmentRefresh(selectedTeamId);
    return;
  }
  await loadTeams(selectedTeamId);
  if (activeView === "global") {
    await loadGlobalPlayers(globalPage);
  }
  renderTeams();
  renderCalculator();
  renderActiveView();
}

function enqueueTeamAssignment(task) {
  const run = teamAssignmentQueue.catch(() => {}).then(task);
  teamAssignmentQueue = run.finally(() => {});
  return run;
}

function scheduleTeamAssignmentRefresh(selectedTeamId = teamsState.selectedTeamId) {
  clearTimeout(teamAssignmentRefreshTimer);
  teamAssignmentRefreshTimer = setTimeout(() => {
    refreshAfterTeamAssignments(selectedTeamId).catch((error) => {
      alert(`No se pudo refrescar la informacion de equipos: ${error.message}`);
    });
  }, TEAM_ASSIGNMENT_REFRESH_DELAY_MS);
}

function markTeamAssignmentSaving(select, saving) {
  select.disabled = saving;
  select.closest(".row-actions")?.classList.toggle("is-saving", saving);
}

async function changePlayerTeamFromSelect(select, playerId, teamId) {
  const player = findLoadedPlayer(playerId);
  const previousTeamId = select.dataset.savedTeamId ?? player?.assigned_team_id ?? "";
  const selectedAfter = teamsState.selectedTeamId;
  if (teamId === previousTeamId) return;

  teamAssignmentInFlight += 1;
  lastTeamAssignmentAt = Date.now();
  markTeamAssignmentSaving(select, true);
  try {
    setTeamSelectValue(select, teamId);
    applyLocalTeamAssignment(playerId, teamId);
    syncRenderedTeamAssignment(playerId, teamId);
    if (teamId) {
      await api(`/api/teams/${teamId}/players`, {
        method: "POST",
        body: JSON.stringify({ query: playerId, player_id: playerId, force: true }),
      });
    } else if (previousTeamId) {
      await api(`/api/teams/${previousTeamId}/players/${playerId}`, { method: "DELETE" });
    }
    applyLocalTeamAssignment(playerId, teamId);
    syncRenderedTeamAssignment(playerId, teamId);
  } catch (error) {
    setTeamSelectValue(select, previousTeamId);
    applyLocalTeamAssignment(playerId, previousTeamId);
    syncRenderedTeamAssignment(playerId, previousTeamId);
    alert(`No se pudo asignar el jugador: ${error.message}`);
  } finally {
    teamAssignmentInFlight -= 1;
    lastTeamAssignmentAt = Date.now();
    markTeamAssignmentSaving(select, false);
    scheduleTeamAssignmentRefresh(selectedAfter);
  }
}

async function addSelectedTeamPlayer() {
  const team = selectedTeam();
  const input = byId("teamPlayerSearchInput");
  const query = input.value.trim();
  const selectedPlayerId = input.dataset.selectedPlayerId || "";
  if (!team) {
    alert("Selecciona o crea un equipo primero.");
    return;
  }
  if (!query) {
    alert("Escribi un nombre o ID SoFIFA.");
    return;
  }
  try {
    await assignPlayerToTeam(team.id, selectedPlayerId || query);
    input.value = "";
    input.dataset.selectedPlayerId = "";
    hideAutocomplete("teamPlayerSuggestions");
  } catch (error) {
    alert(`No se pudo agregar el jugador: ${error.message}`);
  }
}

async function addCalculatorPlayer() {
  const input = byId("calculatorPlayerInput");
  const query = input.value.trim();
  const selectedPlayerId = input.dataset.selectedPlayerId || "";
  if (!query) {
    alert("Escribi un nombre o ID SoFIFA.");
    return;
  }
  try {
    const player = await findGlobalPlayerByInput(query, selectedPlayerId);
    if (!player) {
      alert("No encontre ese jugador en la Base Global.");
      return;
    }
    if (calculatorState.players.some((item) => item.id === player.id)) {
      alert(`${player.name} ya esta en la calculadora.`);
      return;
    }
    calculatorState.players.push({ ...player });
    saveCalculatorState();
    input.value = "";
    input.dataset.selectedPlayerId = "";
    hideAutocomplete("calculatorPlayerSuggestions");
    renderCalculator();
  } catch (error) {
    alert(`No se pudo agregar el jugador a la calculadora: ${error.message}`);
  }
}

function removeCalculatorPlayer(playerId) {
  calculatorState.players = calculatorState.players.filter((player) => player.id !== playerId);
  saveCalculatorState();
  renderCalculator();
}

function clearCalculator() {
  if (calculatorState.players.length && !confirm("Limpiar todos los jugadores de la calculadora?")) return;
  calculatorState.players = [];
  saveCalculatorState();
  renderCalculator();
}

async function removeSelectedTeamPlayer(playerId) {
  const team = selectedTeam();
  if (!team) return;
  const player = teamsState.players.find((item) => item.id === playerId);
  if (!confirm(`Quitar ${player?.name || "este jugador"} de ${team.name}?`)) return;
  await api(`/api/teams/${team.id}/players/${playerId}`, { method: "DELETE" });
  await loadTeams(team.id);
  renderTeams();
}

document.addEventListener("input", (event) => {
  const autocompleteLists = {
    globalSearchInput: "globalPlayerSuggestions",
    teamPlayerSearchInput: "teamPlayerSuggestions",
    calculatorPlayerInput: "calculatorPlayerSuggestions",
  };
  const autocompleteListId = autocompleteLists[event.target.id];
  if (autocompleteListId) {
    event.target.dataset.selectedPlayerId = "";
    clearTimeout(autocompleteTimers[event.target.id]);
    if (event.target.value.trim().length < 2) {
      hideAutocomplete(autocompleteListId);
      return;
    }
    autocompleteTimers[event.target.id] = setTimeout(async () => {
      try {
        renderAutocomplete(
          event.target.id,
          autocompleteListId,
          await searchGlobalPlayerSuggestions(event.target.value),
        );
      } catch {
        hideAutocomplete(autocompleteListId);
      }
    }, 180);
  }

  if (
    [
      "globalSearchInput",
      "globalPositionInput",
      "globalMinOverallInput",
      "globalMaxValueInput",
      "globalTmStatusInput",
    ].includes(event.target.id)
  ) {
    clearTimeout(globalSearchTimer);
    globalSearchTimer = setTimeout(async () => {
      await loadGlobalPlayers(1);
      renderGlobalSummary();
      renderGlobalTable();
    }, 220);
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.id === "globalTmStatusInput") {
    await loadGlobalPlayers(1);
    renderGlobalSummary();
    renderGlobalTable();
  }
  if (event.target.dataset.assignGlobalTeam) {
    await enqueueTeamAssignment(() => changePlayerTeamFromSelect(event.target, event.target.dataset.assignGlobalTeam, event.target.value));
  }
  if (event.target.dataset.moveTeamPlayer) {
    await enqueueTeamAssignment(() => changePlayerTeamFromSelect(event.target, event.target.dataset.moveTeamPlayer, event.target.value));
  }
});

document.addEventListener("click", async (event) => {
  const autocompleteOption = event.target.closest?.("[data-autocomplete-target]");
  if (autocompleteOption) {
    const input = byId(autocompleteOption.dataset.autocompleteTarget);
    if (input) {
      input.value = autocompleteOption.dataset.playerName || "";
      input.dataset.selectedPlayerId = autocompleteOption.dataset.playerId || "";
      input.focus();
      if (input.id === "globalSearchInput") {
        clearTimeout(globalSearchTimer);
        await loadGlobalPlayers(1);
        renderGlobalSummary();
        renderGlobalTable();
      }
    }
    hideAutocomplete(autocompleteOption.dataset.autocompleteList);
    return;
  }
  if (!event.target.closest?.(".autocomplete-field")) {
    hideAutocomplete("teamPlayerSuggestions");
    hideAutocomplete("globalPlayerSuggestions");
    hideAutocomplete("calculatorPlayerSuggestions");
  }
  const globalEditId = event.target.dataset?.editGlobal;
  const globalDeleteId = event.target.dataset?.deleteGlobal;
  const refreshSofifaId = event.target.dataset?.refreshSofifa;
  const refreshTransfermarktId = event.target.dataset?.refreshTransfermarkt;
  const selectTeamId = event.target.closest?.("[data-select-team]")?.dataset?.selectTeam;
  const removeTeamPlayerId = event.target.dataset?.removeTeamPlayer;
  const removeCalculatorPlayerId = event.target.dataset?.removeCalculatorPlayer;
  if (selectTeamId) {
    teamsState.selectedTeamId = selectTeamId;
    await loadSelectedTeamRoster();
    renderTeams();
    return;
  }
  if (globalEditId) openGlobalEdit(
    globalState.players.find((player) => player.id === globalEditId)
    || teamsState.players.find((player) => player.id === globalEditId)
    || calculatorState.players.find((player) => player.id === globalEditId)
  );
  if (globalDeleteId) await deleteGlobalPlayer(globalDeleteId);
  if (refreshSofifaId) await refreshGlobalPlayerFromSofifa(refreshSofifaId);
  if (refreshTransfermarktId) await refreshGlobalPlayerFromTransfermarkt(refreshTransfermarktId);
  if (removeTeamPlayerId) await removeSelectedTeamPlayer(removeTeamPlayerId);
  if (removeCalculatorPlayerId) removeCalculatorPlayer(removeCalculatorPlayerId);
});

byId("teamsTab").addEventListener("click", async () => {
  activeView = "teams";
  renderActiveView();
  await loadTeams();
  renderTeams();
});

byId("globalTab").addEventListener("click", async () => {
  activeView = "global";
  renderActiveView();
  await loadGlobalPlayers(globalPage);
  renderGlobalSummary();
  renderGlobalTable();
});

byId("calculatorTab").addEventListener("click", () => {
  activeView = "calculator";
  renderActiveView();
  renderCalculator();
});

byId("newTeam").addEventListener("click", () => {
  teamsState.selectedTeamId = null;
  teamsState.players = [];
  teamsState.summary = {};
  renderTeams();
});

byId("saveTeam").addEventListener("click", async () => {
  try {
    await saveSelectedTeam();
  } catch (error) {
    alert(`No se pudo guardar el equipo: ${error.message}`);
  }
});

byId("deleteTeam").addEventListener("click", async () => {
  try {
    await deleteSelectedTeam();
  } catch (error) {
    alert(`No se pudo borrar el equipo: ${error.message}`);
  }
});

byId("addTeamPlayer").addEventListener("click", addSelectedTeamPlayer);

byId("addCalculatorPlayer").addEventListener("click", addCalculatorPlayer);

byId("clearCalculator").addEventListener("click", clearCalculator);

byId("addGlobalPlayer").addEventListener("click", () => openGlobalEdit());

byId("importCsvPlayers").addEventListener("click", async () => {
  const result = await api("/api/global-players/import-csv", {
    method: "POST",
    body: JSON.stringify({
      csv_path: byId("csvPathInput").value.trim() || "data/raw/players.csv",
      source_dataset: byId("sourceDatasetInput").value.trim(),
      source_version: byId("sourceVersionInput").value.trim(),
    }),
  });
  await loadGlobalPlayers(1);
  renderGlobalSummary();
  renderGlobalTable();
  alert(`CSV importado: ${result.result.imported_rows} jugadores.`);
});

byId("refreshTransfermarktBatch").addEventListener("click", refreshTransfermarktBatch);

byId("prevGlobalPage").addEventListener("click", async () => {
  await loadGlobalPlayers(Math.max(1, globalPage - 1));
  renderGlobalSummary();
  renderGlobalTable();
});

byId("nextGlobalPage").addEventListener("click", async () => {
  const pages = globalState.pagination?.pages || 1;
  await loadGlobalPlayers(Math.min(pages, globalPage + 1));
  renderGlobalSummary();
  renderGlobalTable();
});

byId("closeGlobalDialog").addEventListener("click", () => byId("globalDialog").close());
byId("saveGlobalEdit").addEventListener("click", saveGlobalEdit);
byId("fetchSofifaForForm").addEventListener("click", fetchSofifaForForm);

initializeApp().catch((error) => {
  document.body.innerHTML = `<pre>${error.message}</pre>`;
});
