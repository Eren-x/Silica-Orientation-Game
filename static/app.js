function makePalette(n = 48) {
  const colors = [];
  for (let i = 0; i < n; i++) {
    const hue = (i * 0.618033988749895) % 1.0;
    colors.push(hlsToHex(hue, 0.55, 0.85));
  }
  return colors;
}

function hlsToHex(h, l, s) {
  let r, g, b;
  if (s === 0) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  const toHex = v => Math.round(v * 255).toString(16).padStart(2, "0");
  return "#" + toHex(r) + toHex(g) + toHex(b);
}

function hue2rgb(p, q, t) {
  if (t < 0) t += 1;
  if (t > 1) t -= 1;
  if (t < 1 / 6) return p + (q - p) * 6 * t;
  if (t < 1 / 2) return q;
  if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
  return p;
}

const PLAYER_COLORS = makePalette();

const ROOM_COLORS = {
  "Reactor Core":     "#e0524a",
  "Workshop":         "#f0a63a",
  "Admin / Security": "#4f8cff",
  "Electrical Bay":   "#c77dff",
  "Storage":          "#ff6bd6",
  "Assembly Line":    "#3ecf8e",
};

let ws = null;
let myId = null;
let myName = null;
let myColor = PLAYER_COLORS[0];
let myRole = null;
let myTarget = 3;
let isHost = false;
let lastJoinAttempt = null;
let reconnectAttempts = 0;
let mapLayout = {};
let adjacentPairs = [];
let ventPairs = [];
let currentRoom = "Admin / Security";
let latestPlayers = [];
let myQuestion = null;
let myDeadline = 0;
let traitorView = "tools";
let timerHandle = null;
let pauseTimerHandle = null;
let meetingTickerHandle = null;
let leftIntentionally = false;
let takenColors = new Set();
let takenNames = new Set();

function $(sel) { return document.querySelector(sel); }

function show(id) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  $(id).classList.add("active");
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

function buildColorWheel() {
  const wheel = $("#color-wheel");
  wheel.innerHTML = "";
  const n = PLAYER_COLORS.length;
  const SIZE = 280;
  const RADIUS = (SIZE / 2) - 26;
  const cx = SIZE / 2;
  const cy = SIZE / 2;
  wheel.style.width = SIZE + "px";
  wheel.style.height = SIZE + "px";
  PLAYER_COLORS.forEach((c, i) => {
    const ang = (i / n) * Math.PI * 2 - Math.PI / 2;
    const x = cx + RADIUS * Math.cos(ang) - 17;
    const y = cy + RADIUS * Math.sin(ang) - 17;
    const sw = document.createElement("button");
    const taken = takenColors.has(c);
    sw.className = "swatch" + (c === myColor ? " selected" : "") + (taken ? " taken" : "");
    sw.style.background = c;
    sw.style.left = x + "px";
    sw.style.top = y + "px";
    sw.disabled = taken;
    sw.title = taken ? "Already taken" : c;
    sw.dataset.color = c;
    sw.onclick = () => {
      myColor = c;
      document.querySelectorAll("#color-wheel .swatch").forEach(b => b.classList.remove("selected"));
      sw.classList.add("selected");
    };
    wheel.appendChild(sw);
  });
}

function getClientId() {
  let cid = sessionStorage.getItem("traitor_client_id");
  if (!cid) {
    cid = Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem("traitor_client_id", cid);
  }
  return cid;
}

function setConnectionStatus(ok) {
  const el = $("#conn-pill");
  if (!el) return;
  el.textContent = ok ? "● connected" : "◌ reconnecting";
  el.className = "conn-pill " + (ok ? "up" : "down");
}

function buildJoinPayload() {
  const payload = { type: "join", name: myName, color: myColor, client_id: getClientId() };
  if (lastJoinAttempt === "host") {
    payload.as_host = true;
    payload.password = $("#h-password").value;
  }
  return payload;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
  ws.onopen = () => {
    setConnectionStatus(true);
    if (lastJoinAttempt) ws.send(JSON.stringify(buildJoinPayload()));
  };
  ws.onclose = () => {
    setConnectionStatus(false);
    if (leftIntentionally) return;
    if (lastJoinAttempt && reconnectAttempts < 5) {
      reconnectAttempts += 1;
      toast("Connection lost — reconnecting…");
      setTimeout(connect, 1000);
      return;
    }
    toast("Disconnected from server");
  };
}

function tryJoin(asHost) {
  const nameInput = asHost ? $("#h-name") : $("#p-name");
  const errEl = asHost ? $("#host-error") : $("#join-error");
  errEl.classList.add("hidden");
  const name = nameInput.value.trim();
  if (!name) {
    errEl.textContent = "Enter a name";
    errEl.classList.remove("hidden");
    return;
  }
  myName = name;
  leftIntentionally = false;
  lastJoinAttempt = asHost ? "host" : "player";
  reconnectAttempts = 0;
  connect();
}

function resetToJoin() {
  if (timerHandle) clearInterval(timerHandle);
  timerHandle = null;
  if (pauseTimerHandle) clearInterval(pauseTimerHandle);
  pauseTimerHandle = null;
  myId = null;
  isHost = false;
  myRole = null;
  latestPlayers = [];
  myQuestion = null;
  lastJoinAttempt = null;
  reconnectAttempts = 5;
  leftIntentionally = true;
  hidePauseOverlay();
  show("#screen-join");
  buildColorWheel();
}

function leaveGame(confirmLeave) {
  if (confirmLeave && !confirm("Leave the game? You cannot rejoin this round.")) return;
  leftIntentionally = true;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "leave" }));
  }
  resetToJoin();
}

function endGame() {
  if (!confirm("End the game for everyone? Everyone will return to the join screen.")) return;
  leftIntentionally = true;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end_game" }));
  }
}

function showPauseOverlay(deadline) {
  const overlay = $("#pause-overlay");
  if (!overlay) return;
  overlay.classList.add("visible");
  if (pauseTimerHandle) clearInterval(pauseTimerHandle);
  const update = () => {
    const left = Math.max(0, (deadline || 0) - Date.now() / 1000);
    const count = $("#pause-count");
    if (count) count.textContent = Math.ceil(left) + "s";
    if (left <= 0) {
      clearInterval(pauseTimerHandle);
      pauseTimerHandle = null;
    }
  };
  update();
  pauseTimerHandle = setInterval(update, 500);
}

function hidePauseOverlay() {
  if (pauseTimerHandle) clearInterval(pauseTimerHandle);
  pauseTimerHandle = null;
  const overlay = $("#pause-overlay");
  if (overlay) overlay.classList.remove("visible");
}

$("#join-btn").onclick = () => tryJoin(false);
$("#host-join-btn").onclick = () => tryJoin(true);

$("#leave-lobby-btn").onclick = () => leaveGame(false);
document.querySelectorAll(".leave-game").forEach(b => b.onclick = () => leaveGame(true));
$("#end-game-btn").onclick = () => endGame();
$("#host-leave-btn").onclick = () => leaveGame(false);

$("#start-btn") && ($("#start-btn").onclick = () => ws.send(JSON.stringify({ type: "start_game" })));
$("#restart-btn").onclick = () => ws.send(JSON.stringify({ type: "restart" }));
$("#host-restart-btn").onclick = () => ws.send(JSON.stringify({ type: "restart" }));
$("#host-start-btn").onclick = () => ws.send(JSON.stringify({ type: "start_game" }));

$("#report-btn").onclick = () => {
  const dead = latestPlayers.find(p => !p.alive && p.room === currentRoom);
  if (!dead) { toast("No body here to report"); return; }
  ws.send(JSON.stringify({ type: "report_body", target_id: dead.id }));
};
$("#meeting-btn").onclick = () => ws.send(JSON.stringify({ type: "call_meeting" }));
$("#sabotage-btn").onclick = () => {
  const kinds = ["Power Failure", "Communication Failure", "Server Crash", "Fake Alarm"];
  const kind = kinds[Math.floor(Math.random() * kinds.length)];
  ws.send(JSON.stringify({ type: "sabotage", kind }));
};
$("#kill-btn").onclick = () => {
  const target = latestPlayers.find(p => p.alive && p.room === currentRoom && p.id !== myId);
  if (!target) { toast("No one else here"); return; }
  ws.send(JSON.stringify({ type: "kill", target_id: target.id }));
};

$("#toggle-view").onclick = () => {
  traitorView = traitorView === "tools" ? "question" : "tools";
  applyTraitorView();
};

function applyTraitorView() {
  if (myRole !== "traitor") return;
  if (traitorView === "tools") {
    $("#question-panel").classList.add("hidden");
    $("#traitor-panel").classList.remove("hidden");
    $("#toggle-view").textContent = "Switch to question";
  } else {
    $("#traitor-panel").classList.add("hidden");
    $("#question-panel").classList.remove("hidden");
    $("#toggle-view").textContent = "Switch to tools";
  }
}

function handleMessage(msg) {
  switch (msg.type) {
    case "joined":
      myId = msg.your_id;
      isHost = !!msg.is_host;
      reconnectAttempts = 0;
      if (isHost) {
        show("#screen-host");
      } else if (!msg.reconnected) {
        show("#screen-lobby");
      }
      break;
    case "lobby_update":
      renderLobby(msg);
      if (myId && !isHost) show("#screen-lobby");
      break;
    case "error":
      toast(msg.message);
      if (lastJoinAttempt && !myId) {
        const el = lastJoinAttempt === "host" ? $("#host-error") : $("#join-error");
        if (el) {
          el.textContent = msg.message;
          el.classList.remove("hidden");
        }
      }
      break;
    case "host_disconnected":
      toast("Host disconnected — game paused");
      showPauseOverlay(msg.deadline);
      break;
    case "host_rejoined":
      toast("Host is back — game resumed");
      hidePauseOverlay();
      break;
    case "session_ended":
      toast(msg.reason || "Session ended");
      resetToJoin();
      break;
    case "game_started":
      myRole = msg.your_role;
      myTarget = msg.your_target || 0;
      mapLayout = msg.map_layout || {};
      adjacentPairs = msg.adjacent || [];
      ventPairs = msg.vent_pairs || [];
      currentRoom = "Admin / Security";
      traitorView = "tools";
      show("#screen-game");
      renderRoleBadge();
      renderColorChip();
      renderProgress();
      $("#minimap-panel").classList.remove("hidden");
      $("#room-panel").classList.remove("hidden");
      $("#players-panel").classList.remove("hidden");
      $("#actions-panel").classList.remove("hidden");
      $("#toggle-row").classList.toggle("hidden", myRole !== "traitor");
      $("#traitor-panel").classList.toggle("hidden", myRole !== "traitor");
      $("#question-panel").classList.remove("hidden");
      if (myRole === "engineer") {
        $("#question-panel").classList.remove("hidden");
      } else {
        $("#question-panel").classList.remove("hidden");
      }
      break;
    case "state_update":
      latestPlayers = msg.players;
      renderMinimap(msg);
      renderRooms(msg.rooms);
      renderPlayers(msg.players);
      if (msg.host_pending) {
        showPauseOverlay(msg.host_deadline);
      } else {
        hidePauseOverlay();
      }
      if (msg.state === "meeting") show("#screen-meeting");
      else if (msg.state === "playing" && document.querySelector("#screen-meeting").classList.contains("active")) {
        show("#screen-game");
      }
      break;
    case "question":
      setQuestion(msg.question, msg.deadline, msg.seconds);
      break;
    case "task_result":
      handleTaskResult(msg);
      break;
    case "meeting_started":
      show("#screen-meeting");
      $("#meeting-reason").textContent = msg.reason;
      startMeetingCountdown("Discussion", msg.discussion_seconds);
      $("#meeting-result").textContent = "";
      renderMeetingVoting(false);
      break;
    case "voting_started":
      startMeetingCountdown("Voting", msg.voting_seconds);
      renderMeetingVoting(true);
      break;
    case "vote_cast":
      stopMeetingCountdown();
      $("#meeting-timer").textContent = `Votes: ${msg.num_votes}/${msg.num_alive}`;
      break;
    case "meeting_result":
      renderMeetingResult(msg);
      break;
    case "game_over":
      renderGameOver(msg);
      break;
    case "host_state":
      renderHostDashboard(msg);
      break;
  }
}

function renderLobby(msg) {
  const list = $("#lobby-players");
  list.innerHTML = "";
  takenColors = new Set();
  takenNames = new Set();
  msg.players.forEach(p => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="dot" style="background:${p.color}"></span>${p.name}`;
    list.appendChild(li);
    takenColors.add(p.color);
    takenNames.add(p.name.toLowerCase());
  });
  const count = msg.players.length;
  $("#lobby-hint").textContent = count < 4
    ? `Need at least 4 players to start (${count}/4).`
    : `${count} players ready.`;
  if ($("#screen-join").classList.contains("active")) {
    buildColorWheel();
  }
}

function renderRoleBadge() {
  const badge = $("#role-badge");
  badge.textContent = myRole === "traitor" ? "Traitor" : "Engineer";
  badge.className = myRole;
}

function renderColorChip() {
  $("#color-chip").innerHTML = `<span class="dot" style="background:${myColor}"></span>${myName}`;
}

function renderProgress() {
  if (myRole === "engineer") {
    $("#progress-pill").textContent = `Engineer target: ${myTarget}`;
  } else {
    $("#progress-pill").textContent = "";
  }
}

const ROOM_ICONS = {
  "Reactor Core":     "☢️",
  "Workshop":         "🔧",
  "Admin / Security": "🛡️",
  "Electrical Bay":   "⚡",
  "Storage":          "📦",
  "Assembly Line":    "🏭",
};

function renderMinimap(msg) {
  const map = $("#minimap");
  map.innerHTML = "";
  map.style.position = "relative";
  const layout = msg.map_layout || mapLayout;
  const adjacent = msg.adjacent || adjacentPairs;

  const NODE_W = 120;
  const NODE_H = 46;
  const STEP_X = 150;
  const STEP_Y = 96;
  const PAD_X = 10;
  const PAD_Y = 10;

  const minX = Math.min(...Object.values(layout).map(p => p.x));
  const maxX = Math.max(...Object.values(layout).map(p => p.x));
  const minY = Math.min(...Object.values(layout).map(p => p.y));
  const maxY = Math.max(...Object.values(layout).map(p => p.y));
  const w = (maxX - minX) * STEP_X + 2 * PAD_X + NODE_W;
  const h = (maxY - minY) * STEP_Y + 2 * PAD_Y + NODE_H;
  map.style.width = w + "px";
  map.style.height = h + "px";

  const cx = room => PAD_X + (layout[room].x - minX) * STEP_X + NODE_W / 2;
  const cy = room => PAD_Y + (layout[room].y - minY) * STEP_Y + NODE_H / 2;

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", w);
  svg.setAttribute("height", h);
  svg.style.position = "absolute";
  svg.style.left = "0";
  svg.style.top = "0";
  svg.style.pointerEvents = "none";

  for (const [a, b] of adjacent) {
    if (!layout[a] || !layout[b]) continue;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", cx(a));
    line.setAttribute("y1", cy(a));
    line.setAttribute("x2", cx(b));
    line.setAttribute("y2", cy(b));
    line.setAttribute("stroke", "#2b3245");
    line.setAttribute("stroke-width", "4");
    line.setAttribute("stroke-linecap", "round");
    svg.appendChild(line);
  }
  for (const [a, b] of (msg.vent_pairs || ventPairs)) {
    if (!layout[a] || !layout[b]) continue;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", cx(a));
    line.setAttribute("y1", cy(a));
    line.setAttribute("x2", cx(b));
    line.setAttribute("y2", cy(b));
    line.setAttribute("stroke", "#f0a63a");
    line.setAttribute("stroke-width", "3");
    line.setAttribute("stroke-dasharray", "8 6");
    line.setAttribute("class", "vent-line");
    svg.appendChild(line);
  }
  map.appendChild(svg);

  for (const room of (msg.rooms || Object.keys(layout))) {
    if (!layout[room]) continue;
    const node = document.createElement("div");
    const color = ROOM_COLORS[room] || "#4f8cff";
    node.className = "map-node" + (room === currentRoom ? " current" : "");
    node.style.left = (PAD_X + (layout[room].x - minX) * STEP_X) + "px";
    node.style.top = (PAD_Y + (layout[room].y - minY) * STEP_Y) + "px";
    node.style.borderColor = color;
    node.innerHTML = `<span class="node-icon">${ROOM_ICONS[room] || ""}</span><span class="node-name">${room}</span>`;
    node.onclick = () => {
      currentRoom = room;
      ws.send(JSON.stringify({ type: "move_room", room }));
    };
    map.appendChild(node);

    const here = latestPlayers.filter(p => p.alive && p.room === room);
    here.forEach((p, i) => {
      const dot = document.createElement("div");
      dot.className = "map-player-dot";
      dot.style.background = p.color;
      dot.style.bottom = "4px";
      dot.style.left = (8 + i * 13) + "px";
      dot.title = p.name;
      node.appendChild(dot);
    });
  }
}

function renderRooms(rooms) {
  const el = $("#room-list");
  el.innerHTML = "";
  rooms.forEach(room => {
    const btn = document.createElement("button");
    btn.className = "room-btn" + (room === currentRoom ? " current" : "");
    btn.textContent = room;
    btn.onclick = () => {
      currentRoom = room;
      ws.send(JSON.stringify({ type: "move_room", room }));
    };
    el.appendChild(btn);

    if (myRole === "traitor") {
      ventPairs.forEach(([a, b]) => {
        let dest = null;
        if (a === currentRoom && room === b) dest = b;
        if (b === currentRoom && room === a) dest = a;
      });
    }
  });

  if (myRole === "traitor") {
    ventPairs.forEach(([a, b]) => {
      if (currentRoom === a || currentRoom === b) {
        const dest = currentRoom === a ? b : a;
        const vbtn = document.createElement("button");
        vbtn.textContent = `Vent to ${dest}`;
        vbtn.style.borderColor = "#f0a63a";
        vbtn.style.color = "#f0a63a";
        vbtn.onclick = () => {
          currentRoom = dest;
          ws.send(JSON.stringify({ type: "vent", room: dest }));
        };
        el.appendChild(vbtn);
      }
    });
  }
}

function renderPlayers(players) {
  const el = $("#player-list");
  el.innerHTML = "";
  players.forEach(p => {
    const row = document.createElement("div");
    row.className = "player-row" + (p.alive ? "" : " dead");
    row.innerHTML = `<span><span class="dot" style="background:${p.color}"></span>${p.name}${p.id === myId ? " (you)" : ""}</span><span class="room-tag">${p.alive ? p.room : "eliminated"}</span>`;
    el.appendChild(row);
  });
}

function setQuestion(question, deadline, seconds) {
  if (timerHandle) clearInterval(timerHandle);
  myQuestion = question;
  myDeadline = deadline || 0;
  if (!question) {
    $("#question-card").innerHTML = `<div class="prompt">No active question — move rooms or switch to question view.</div>`;
    $("#timer-label").textContent = "";
    $("#timer-fill").style.width = "0%";
    if (myRole === "traitor") {
      if (traitorView === "question") {
        $("#question-panel").classList.remove("hidden");
        $("#traitor-panel").classList.add("hidden");
      } else {
        $("#question-panel").classList.add("hidden");
        $("#traitor-panel").classList.remove("hidden");
      }
    } else {
      $("#question-panel").classList.add("hidden");
    }
    return;
  }
  const opts = question.options.map(o =>
    `<button onclick="submitTask(${question.id}, ${JSON.stringify(o)})">${o}</button>`
  ).join("");
  $("#question-card").innerHTML = `<div class="prompt">${question.prompt}</div><div class="options">${opts}</div>`;

  if (myRole === "traitor" && traitorView === "tools") {
    $("#question-panel").classList.add("hidden");
    $("#traitor-panel").classList.remove("hidden");
  } else {
    $("#question-panel").classList.remove("hidden");
    if (myRole === "traitor") {
      $("#traitor-panel").classList.add("hidden");
    }
  }

  const total = seconds || 10;
  timerHandle = setInterval(() => {
    const left = Math.max(0, myDeadline - Date.now() / 1000);
    const pct = Math.max(0, Math.min(100, (left / total) * 100));
    $("#timer-fill").style.width = pct + "%";
    $("#timer-label").textContent = left.toFixed(1) + "s";
    if (left <= 0) {
      clearInterval(timerHandle);
      timerHandle = null;
    }
  }, 100);
}

function submitTask(taskId, answer) {
  ws.send(JSON.stringify({ type: "do_task", task_id: taskId, answer }));
}
window.submitTask = submitTask;

function startMeetingCountdown(label, seconds) {
  if (meetingTickerHandle) clearInterval(meetingTickerHandle);
  const endAt = Date.now() + seconds * 1000;
  const el = $("#meeting-timer");
  const tick = () => {
    const left = Math.max(0, Math.ceil((endAt - Date.now()) / 1000));
    el.textContent = `${label}: ${left}s`;
    if (left <= 0) {
      clearInterval(meetingTickerHandle);
      meetingTickerHandle = null;
    }
  };
  tick();
  meetingTickerHandle = setInterval(tick, 250);
}

function stopMeetingCountdown() {
  if (meetingTickerHandle) clearInterval(meetingTickerHandle);
  meetingTickerHandle = null;
}

function handleTaskResult(msg) {
  let text = msg.correct ? "Correct!" : (msg.reason === "timeout" ? "Time's up!" : (msg.reason === "sabotage" ? "Blocked by sabotage" : "Incorrect"));
  toast(text);
  if (msg.correct) {
    const card = $("#question-card");
    if (card) card.innerHTML = `<div class="prompt">${myQuestion && myQuestion.prompt || ""} ✓</div>`;
  }
}

function renderMeetingVoting(enabled) {
  const el = $("#meeting-players");
  el.innerHTML = "";
  latestPlayers.filter(p => p.alive).forEach(p => {
    const row = document.createElement("div");
    row.className = "vote-row";
    const btn = enabled
      ? `<button onclick="castVote('${p.id}')">Vote</button>`
      : "";
    row.innerHTML = `<span><span class="dot" style="background:${p.color}"></span>${p.name}${p.id === myId ? " (you)" : ""}</span>${btn}`;
    el.appendChild(row);
  });
  if (enabled) {
    const skipRow = document.createElement("div");
    skipRow.className = "vote-row";
    skipRow.innerHTML = `<span>Skip vote</span><button onclick="castVote('skip')">Vote</button>`;
    el.appendChild(skipRow);
  }
}

function castVote(targetId) {
  ws.send(JSON.stringify({ type: "vote", target_id: targetId }));
  toast("Vote cast");
}
window.castVote = castVote;

function renderMeetingResult(msg) {
  stopMeetingCountdown();
  const el = $("#meeting-result");
  if (msg.eliminated) {
    const p = latestPlayers.find(p => p.id === msg.eliminated);
    el.textContent = `${p ? p.name : "Someone"} was eliminated. They were a ${msg.eliminated_role}.`;
  } else {
    el.textContent = "No one was eliminated (tie or skip).";
  }
  setTimeout(() => {
    if (document.querySelector("#screen-meeting").classList.contains("active")) {
      show("#screen-game");
    }
  }, 3000);
}

function renderGameOver(msg) {
  show("#screen-gameover");
  $("#gameover-title").textContent = msg.winner === "engineers" ? "Engineers win" : "Traitors win";
  $("#gameover-reason").textContent = msg.reason;
  const rolesEl = $("#gameover-roles");
  rolesEl.innerHTML = "";
  latestPlayers.forEach(p => {
    const div = document.createElement("div");
    div.className = "player-row";
    div.innerHTML = `<span><span class="dot" style="background:${p.color}"></span>${p.name}</span><span>${msg.roles[p.id]}</span>`;
    rolesEl.appendChild(div);
  });
  $("#restart-btn").style.display = isHost ? "block" : "none";
}

function renderHostDashboard(msg) {
  if (!isHost) return;
  const inActiveGame = msg.state === "playing" || msg.state === "meeting";
  if (!inActiveGame) show("#screen-host");
  renderHostLive(msg);
  const summary = $("#host-summary");
  const playerCount = msg.players.length;
  const stateLabel = msg.state === "ended"
    ? `Game over — ${msg.winner} win · ${playerCount} players`
    : msg.state === "lobby"
      ? `Lobby — ${playerCount} player${playerCount === 1 ? "" : "s"} ready (need 4 to start)`
      : `Game state: ${msg.state} · ${playerCount} player${playerCount === 1 ? "" : "s"}`;
  summary.textContent = stateLabel + (msg.sabotage_active ? ` · SABOTAGE: ${msg.sabotage_active.kind}` : "");

  const startBtn = $("#host-start-btn");
  startBtn.style.display = msg.state === "lobby" ? "block" : "none";
  if (msg.state === "lobby") {
    startBtn.disabled = msg.players.length < 4;
    startBtn.textContent = msg.players.length < 4
      ? `Start game (need ${4 - msg.players.length} more)`
      : "Start game";
  }
  $("#host-restart-btn").style.display = msg.state === "lobby" ? "none" : "block";
  $("#end-game-btn").style.display = msg.state === "lobby" ? "none" : "block";
  $("#host-leave-btn").style.display = msg.state === "lobby" ? "block" : "none";

  const lobbyList = $("#host-ready");
  if (lobbyList) lobbyList.style.display = msg.state === "lobby" ? "block" : "none";
  const tableWrap = $("#host-table-wrap");
  if (tableWrap) tableWrap.style.display = msg.state === "lobby" ? "none" : "block";

  if (msg.state === "lobby") {
    if (lobbyList) renderHostReady(msg.players);
    return;
  }

  const tbody = $("#host-rows");
  tbody.innerHTML = "";
  msg.players.forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="dot" style="background:${p.color}"></span>${p.name}</td>
      <td class="role-${p.role}">${p.role}</td>
      <td>${p.alive ? p.room : "—"}</td>
      <td>${p.alive ? "yes" : "no"}</td>
      <td>${p.stats.attempted} / ${p.target || "—"}</td>
      <td>${p.stats.correct}</td>
      <td>${p.stats.incorrect}</td>
    `;
    tbody.appendChild(tr);
  });

  renderHostRooms(msg);
}

function renderHostLive(msg) {
  const el = $("#host-live");
  if (!el) return;
  const connected = msg.players || [];
  const disconnected = msg.disconnected || [];
  let html = `<div class="live-line"><span class="live-dot up">●</span><b>${connected.length} connected:</b> `;
  if (connected.length) {
    html += connected.map(p => `<span class="live-name"><span class="dot" style="background:${p.color}"></span>${p.name}</span>`).join("");
  } else {
    html += `<span class="muted">none</span>`;
  }
  html += "</div>";
  if (disconnected.length) {
    html += `<div class="live-line"><span class="live-dot down">●</span><b>${disconnected.length} disconnected:</b> `;
    html += disconnected.map(d => `<span class="live-name"><span class="dot" style="background:${d.color};opacity:.45"></span>${d.name}</span>`).join("");
    html += "</div>";
  }
  el.innerHTML = html;
}

function renderHostReady(players) {
  const el = $("#host-ready");
  if (!el) return;
  el.innerHTML = "";
  if (!players.length) {
    el.innerHTML = `<div class="hint">No one in the lobby yet. Share the URL so people can join.</div>`;
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "ready-list";
  players.forEach(p => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="dot" style="background:${p.color}"></span>${p.name}`;
    ul.appendChild(li);
  });
  el.appendChild(ul);
  const prev = document.createElement("div");
  prev.className = "hint";
  prev.textContent = `${players.length} ready`;
  el.appendChild(prev);
}

function renderHostRooms(msg) {
  const el = $("#host-rooms");
  if (!el) return;
  el.innerHTML = "";
  const alive = msg.players.filter(p => p.alive);
  if (!alive.length) { el.textContent = "No players."; return; }
  const groups = {};
  alive.forEach(p => {
    (groups[p.room] = groups[p.room] || []).push(p);
  });
  Object.entries(groups).forEach(([room, list]) => {
    const line = document.createElement("div");
    line.className = "host-room-line";
    const names = list.map(p =>
      `<span class="dot" style="background:${p.color}"></span>${p.name}`
    ).join("");
    line.innerHTML = `<span class="room-tag">${room}</span>${names}`;
    el.appendChild(line);
  });
}

buildColorWheel();