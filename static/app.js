let ws = null;
let myId = null;
let myName = null;
let isHost = false;
let myRole = null;
let currentRoom = "Admin / Security";
let ventPairs = [];
let latestPlayers = [];

// Round state
let currentQuestion = null;   // {id, type, prompt, options}
let roundNumber = 0;
let roundEndsAt = 0;          // ms epoch
let answeredThisRound = false;
let totalCorrect = 0;
let targetCorrect = 0;
let countdownTimer = null;

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

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
  ws.onclose = () => toast("Disconnected from server");
}

$("#join-btn").onclick = () => {
  const name = $("#name-input").value.trim();
  if (!name) return;
  myName = name;
  connect();
  ws.onopen = () => ws.send(JSON.stringify({ type: "join", name }));
};

$("#start-btn").onclick = () => ws.send(JSON.stringify({ type: "start_game" }));
$("#restart-btn").onclick = () => ws.send(JSON.stringify({ type: "restart" }));
$("#report-btn").onclick = () => {
  const dead = latestPlayers.find(p => !p.alive && p.room === currentRoom);
  if (!dead) { toast("No body here to report"); return; }
  ws.send(JSON.stringify({ type: "report_body", target_id: dead.id }));
};
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

function handleMessage(msg) {
  switch (msg.type) {
    case "joined":
      myId = msg.your_id;
      show("#screen-lobby");
      break;
    case "lobby_update":
      renderLobby(msg.players);
      break;
    case "error":
      toast(msg.message);
      break;
    case "game_started":
      myRole = msg.your_role;
      ventPairs = msg.vent_pairs || [];
      currentRoom = "Admin / Security";
      currentQuestion = null;
      answeredThisRound = false;
      show("#screen-game");
      renderRoleBadge();
      $("#traitor-panel").classList.toggle("hidden", myRole !== "traitor");
      $("#task-panel").classList.remove("hidden"); // both roles see the round question
      break;
    case "round_started":
      roundNumber = msg.round;
      currentQuestion = msg.question;
      answeredThisRound = false;
      roundEndsAt = msg.round_ends_at * 1000;
      renderQuestion();
      startCountdown();
      break;
    case "state_update":
      latestPlayers = msg.players;
      renderRooms(msg.rooms);
      renderPlayers(msg.players);
      renderSabotage(msg.sabotage_active);
      if (typeof msg.round === "number") roundNumber = msg.round;
      if (typeof msg.total_correct === "number") totalCorrect = msg.total_correct;
      if (typeof msg.target_correct === "number") targetCorrect = msg.target_correct;
      if (msg.round_ends_at) roundEndsAt = msg.round_ends_at * 1000;
      if (msg.round_question && (!currentQuestion || currentQuestion.id !== msg.round_question.id)) {
        currentQuestion = msg.round_question;
        answeredThisRound = false;
        renderQuestion();
      }
      updateProgressLine();
      if (msg.state === "meeting") show("#screen-meeting");
      else if (msg.state === "playing" && document.querySelector("#screen-meeting").classList.contains("active")) {
        show("#screen-game");
      }
      break;
    case "task_result":
      handleTaskResult(msg);
      break;
    case "meeting_started":
      show("#screen-meeting");
      $("#meeting-reason").textContent = msg.reason;
      $("#meeting-timer").textContent = `Discussion: ${msg.discussion_seconds}s`;
      $("#meeting-result").textContent = "";
      renderMeetingVoting(false);
      break;
    case "voting_started":
      $("#meeting-timer").textContent = `Voting: ${msg.voting_seconds}s`;
      renderMeetingVoting(true);
      break;
    case "vote_cast":
      $("#meeting-timer").textContent = `Votes: ${msg.num_votes}/${msg.num_alive}`;
      break;
    case "meeting_result":
      renderMeetingResult(msg);
      break;
    case "game_over":
      renderGameOver(msg);
      break;
  }
}

function renderLobby(players) {
  const list = $("#lobby-players");
  list.innerHTML = "";
  players.forEach(p => {
    const li = document.createElement("li");
    li.textContent = p.name + (p.host ? " (host)" : "");
    list.appendChild(li);
    if (p.id === myId) isHost = p.host;
  });
  $("#start-btn").style.display = isHost ? "block" : "none";
  $("#lobby-hint").textContent = players.length < 4
    ? `Need at least 4 players to start (${players.length}/4).`
    : `${players.length} players ready.`;
}

function renderRoleBadge() {
  const badge = $("#role-badge");
  badge.textContent = myRole === "traitor" ? "You are a Traitor" : "You are an Engineer";
  badge.className = myRole;
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
    row.innerHTML = `<span>${p.name}${p.id === myId ? " (you)" : ""}</span><span class="room-tag">${p.alive ? p.room : "eliminated"}</span>`;
    el.appendChild(row);
  });
}

function updateProgressLine() {
  const secs = Math.max(0, Math.round((roundEndsAt - Date.now()) / 1000));
  $("#task-progress").textContent = `Round ${roundNumber} • Solved ${totalCorrect}/${targetCorrect} • ${secs}s left`;
}

function startCountdown() {
  if (countdownTimer) clearInterval(countdownTimer);
  updateProgressLine();
  countdownTimer = setInterval(updateProgressLine, 1000);
}

function renderSabotage(sabotage) {
  const banner = $("#sabotage-banner");
  if (sabotage) {
    banner.textContent = `SABOTAGE ACTIVE: ${sabotage.kind} — tasks blocked until the round ends`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function renderQuestion() {
  const el = $("#task-list");
  el.innerHTML = "";
  if (!currentQuestion) return;
  const card = document.createElement("div");
  card.className = "task-card" + (answeredThisRound ? " done" : "");
  const opts = currentQuestion.options.map(o =>
    `<button ${answeredThisRound ? "disabled" : ""} onclick="submitAnswer(${JSON.stringify(o)})">${o}</button>`
  ).join("");
  card.innerHTML = `<div class="prompt">${currentQuestion.prompt}</div><div class="options">${opts}</div>`;
  el.appendChild(card);
}

function submitAnswer(answer) {
  if (!currentQuestion) return;
  ws.send(JSON.stringify({ type: "do_task", task_id: currentQuestion.id, answer }));
}
window.submitAnswer = submitAnswer;

function handleTaskResult(msg) {
  if (!currentQuestion || msg.task_id !== currentQuestion.id) return;
  if (msg.blocked) {
    toast("Sabotage active — answers blocked");
    return;
  }
  if (msg.correct) {
    answeredThisRound = true;
    toast("Correct!");
  } else {
    toast("Incorrect, try again");
  }
  renderQuestion();
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
    row.innerHTML = `<span>${p.name}${p.id === myId ? " (you)" : ""}</span>${btn}`;
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
  if (countdownTimer) clearInterval(countdownTimer);
  show("#screen-gameover");
  $("#gameover-title").textContent = msg.winner === "engineers" ? "Engineers win" : "Traitors win";
  $("#gameover-reason").textContent = msg.reason;
  const rolesEl = $("#gameover-roles");
  rolesEl.innerHTML = "";
  latestPlayers.forEach(p => {
    const div = document.createElement("div");
    div.className = "player-row";
    div.textContent = `${p.name}: ${msg.roles[p.id]}`;
    rolesEl.appendChild(div);
  });
  $("#restart-btn").style.display = isHost ? "block" : "none";
}