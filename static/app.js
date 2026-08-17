let ws = null;
let myId = null;
let myName = null;
let isHost = false;
let myRole = null;
let currentRoom = "Admin / Security";
let ventPairs = [];
let latestPlayers = [];

// Round state
let currentQuestion = null;
let roundNumber = 0;
let roundEndsAt = 0;
let answeredThisRound = false;
let totalCorrect = 0;
let targetCorrect = 0;
let countdownTimer = null;
let votingActive = false;

function $(sel) {
  return document.querySelector(sel);
}

function show(id) {
  document.querySelectorAll(".screen").forEach(s => {
    s.classList.remove("active");
  });

  $(id).classList.add("active");
}

function toast(msg) {
  const t = $("#toast");

  t.textContent = msg;
  t.classList.add("show");

  setTimeout(() => {
    t.classList.remove("show");
  }, 2200);
}


// ------------------------------------------------------------
// CONNECTION
// ------------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";

  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    const pill = $("#conn-pill");

    if (pill) {
      pill.textContent = "● connected";
      pill.classList.remove("down");
      pill.classList.add("up");
    }
  };

  ws.onmessage = (ev) => {
    handleMessage(JSON.parse(ev.data));
  };

  ws.onclose = () => {
    const pill = $("#conn-pill");

    if (pill) {
      pill.textContent = "◌ disconnected";
      pill.classList.remove("up");
      pill.classList.add("down");
    }

    toast("Disconnected from server");
  };
}


// ------------------------------------------------------------
// PLAYER JOIN
// ------------------------------------------------------------

$("#join-btn").onclick = () => {
  const name = $("#p-name").value.trim();

  if (!name) {
    toast("Enter your name");
    return;
  }

  myName = name;

  connect();

  ws.onopen = () => {
    const pill = $("#conn-pill");

    if (pill) {
      pill.textContent = "● connected";
      pill.classList.remove("down");
      pill.classList.add("up");
    }

    ws.send(
      JSON.stringify({
        type: "join",
        name
      })
    );
  };
};


// ------------------------------------------------------------
// HOST JOIN
// ------------------------------------------------------------

if ($("#host-join-btn")) {
  $("#host-join-btn").onclick = () => {
    const name = $("#h-name").value.trim();
    const password = $("#h-password").value;

    if (!name) {
      toast("Enter host name");
      return;
    }

    myName = name;

    connect();

    ws.onopen = () => {
      const pill = $("#conn-pill");

      if (pill) {
        pill.textContent = "● connected";
        pill.classList.remove("down");
        pill.classList.add("up");
      }

      ws.send(
        JSON.stringify({
          type: "join",
          name,
          host: true,
          password
        })
      );
    };
  };
}


// ------------------------------------------------------------
// GAME BUTTONS
// ------------------------------------------------------------

if ($("#start-btn")) {
  $("#start-btn").onclick = () => {
    ws.send(
      JSON.stringify({
        type: "start_game"
      })
    );
  };
}

if ($("#host-start-btn")) {
  $("#host-start-btn").onclick = () => {
    ws.send(
      JSON.stringify({
        type: "start_game"
      })
    );
  };
}

if ($("#restart-btn")) {
  $("#restart-btn").onclick = () => {
    ws.send(
      JSON.stringify({
        type: "restart"
      })
    );
  };
}

if ($("#host-restart-btn")) {
  $("#host-restart-btn").onclick = () => {
    ws.send(
      JSON.stringify({
        type: "restart"
      })
    );
  };
}


// ------------------------------------------------------------
// TRAITOR TOOLS
// ------------------------------------------------------------

if ($("#sabotage-btn")) {
  $("#sabotage-btn").onclick = () => {
    const kinds = [
      "Power Failure",
      "Communication Failure",
      "Server Crash",
      "Fake Alarm"
    ];

    const kind =
      kinds[Math.floor(Math.random() * kinds.length)];

    ws.send(
      JSON.stringify({
        type: "sabotage",
        kind
      })
    );
  };
}

if ($("#kill-btn")) {
  $("#kill-btn").onclick = () => {
    const target = latestPlayers.find(
      p =>
        p.alive &&
        p.room === currentRoom &&
        p.id !== myId
    );

    if (!target) {
      toast("No one else here");
      return;
    }

    ws.send(
      JSON.stringify({
        type: "kill",
        target_id: target.id
      })
    );
  };
}


// ------------------------------------------------------------
// SERVER MESSAGE HANDLER
// ------------------------------------------------------------

function handleMessage(msg) {

  switch (msg.type) {

    case "joined":

      myId = msg.your_id;

      if (typeof msg.is_host === "boolean") {
        isHost = msg.is_host;
      }

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

      votingActive = false;

      show("#screen-game");

      renderRoleBadge();

      $("#traitor-panel").classList.toggle(
        "hidden",
        myRole !== "traitor"
      );

      $("#question-panel").classList.remove("hidden");

      break;


    case "round_started":

      votingActive = false;

      roundNumber = msg.round;

      currentQuestion = msg.question;

      answeredThisRound = false;

      roundEndsAt = msg.round_ends_at * 1000;

      renderQuestion();

      startCountdown();

      show("#screen-game");

      break;


    case "state_update":

      latestPlayers = msg.players;

      renderRooms(msg.rooms);

      renderPlayers(msg.players);

      renderSabotage(msg.sabotage_active);

      if (typeof msg.round === "number") {
        roundNumber = msg.round;
      }

      if (typeof msg.total_correct === "number") {
        totalCorrect = msg.total_correct;
      }

      if (typeof msg.target_correct === "number") {
        targetCorrect = msg.target_correct;
      }

      if (msg.round_ends_at) {
        roundEndsAt = msg.round_ends_at * 1000;
      }

      if (
        msg.round_question &&
        (
          !currentQuestion ||
          currentQuestion.id !== msg.round_question.id
        )
      ) {
        currentQuestion = msg.round_question;

        answeredThisRound = false;

        renderQuestion();
      }

      updateProgressLine();

      if (msg.state === "voting") {
        votingActive = true;
        renderVotingScreen();
        show("#screen-voting");
      }

      break;


    case "task_result":

      handleTaskResult(msg);

      break;


    case "voting_started":

      votingActive = true;

      renderVotingScreen();

      $("#voting-timer").textContent =
        `Voting: ${msg.voting_seconds}s`;

      show("#screen-voting");

      break;


    case "vote_cast":

      $("#voting-timer").textContent =
        `Votes: ${msg.num_votes}/${msg.num_alive}`;

      break;


    case "voting_result":

      renderVotingResult(msg);

      break;


    case "game_over":

      renderGameOver(msg);

      break;
  }
}


// ------------------------------------------------------------
// LOBBY
// ------------------------------------------------------------

function renderLobby(players) {

  const list = $("#lobby-players");

  list.innerHTML = "";

  players.forEach(p => {

    const li = document.createElement("li");

    li.textContent =
      p.name + (p.host ? " (host)" : "");

    list.appendChild(li);

    if (p.id === myId) {
      isHost = p.host;
    }
  });

  if ($("#start-btn")) {
    $("#start-btn").style.display =
      isHost ? "block" : "none";
  }

  if ($("#host-start-btn")) {
    $("#host-start-btn").style.display =
      isHost ? "block" : "none";
  }

  $("#lobby-hint").textContent =
    players.length < 4
      ? `Need at least 4 players to start (${players.length}/4).`
      : `${players.length} players ready.`;
}


// ------------------------------------------------------------
// ROLE
// ------------------------------------------------------------

function renderRoleBadge() {

  const badge = $("#role-badge");

  badge.textContent =
    myRole === "traitor"
      ? "You are a Traitor"
      : "You are an Engineer";

  badge.className = myRole;
}


// ------------------------------------------------------------
// ROOMS
// ------------------------------------------------------------

function renderRooms(rooms) {

  const el = $("#room-list");

  el.innerHTML = "";

  rooms.forEach(room => {

    const btn =
      document.createElement("button");

    btn.className =
      "room-btn" +
      (room === currentRoom ? " current" : "");

    btn.textContent = room;

    btn.onclick = () => {

      currentRoom = room;

      ws.send(
        JSON.stringify({
          type: "move_room",
          room
        })
      );
    };

    el.appendChild(btn);
  });


  if (myRole === "traitor") {

    ventPairs.forEach(([a, b]) => {

      if (
        currentRoom === a ||
        currentRoom === b
      ) {

        const dest =
          currentRoom === a ? b : a;

        const vbtn =
          document.createElement("button");

        vbtn.textContent =
          `Vent to ${dest}`;

        vbtn.style.borderColor =
          "#f0a63a";

        vbtn.style.color =
          "#f0a63a";

        vbtn.onclick = () => {

          currentRoom = dest;

          ws.send(
            JSON.stringify({
              type: "vent",
              room: dest
            })
          );
        };

        el.appendChild(vbtn);
      }
    });
  }
}


// ------------------------------------------------------------
// PLAYERS
// ------------------------------------------------------------

function renderPlayers(players) {

  const el = $("#player-list");

  el.innerHTML = "";

  players.forEach(p => {

    const row =
      document.createElement("div");

    row.className =
      "player-row" +
      (p.alive ? "" : " dead");

    row.innerHTML = `
      <span>
        ${p.name}${p.id === myId ? " (you)" : ""}
      </span>

      <span class="room-tag">
        ${p.alive ? p.room : "eliminated"}
      </span>
    `;

    el.appendChild(row);
  });
}


// ------------------------------------------------------------
// ROUND TIMER
// ------------------------------------------------------------

function updateProgressLine() {

  const secs =
    Math.max(
      0,
      Math.ceil(
        (roundEndsAt - Date.now()) / 1000
      )
    );

  $("#progress-pill").textContent =
    `Round ${roundNumber} • ${secs}s`;

  if ($("#timer-label")) {
    $("#timer-label").textContent =
      `${secs}.0s`;
  }

  if ($("#timer-fill")) {

    const percent =
      Math.max(
        0,
        Math.min(
          100,
          ((roundEndsAt - Date.now()) /
            (15 * 1000)) * 100
        )
      );

    $("#timer-fill").style.width =
      `${percent}%`;
  }
}


function startCountdown() {

  if (countdownTimer) {
    clearInterval(countdownTimer);
  }

  updateProgressLine();

  countdownTimer =
    setInterval(
      updateProgressLine,
      100
    );
}


// ------------------------------------------------------------
// SABOTAGE DISPLAY
// ------------------------------------------------------------

function renderSabotage(sabotage) {

  const banner =
    $("#sabotage-banner");

  if (sabotage) {

    banner.textContent =
      `SABOTAGE ACTIVE: ${sabotage.kind} — tasks blocked until the round ends`;

    banner.classList.remove("hidden");

  } else {

    banner.classList.add("hidden");
  }
}


// ------------------------------------------------------------
// QUESTION
// ------------------------------------------------------------

function renderQuestion() {

  const el =
    $("#question-card");

  el.innerHTML = "";

  if (!currentQuestion) {
    return;
  }

  const card =
    document.createElement("div");

  card.className =
    "task-card" +
    (answeredThisRound ? " done" : "");


  const opts =
    currentQuestion.options
      .map(o => `
        <button
          ${answeredThisRound ? "disabled" : ""}
          onclick='submitAnswer(${JSON.stringify(o)})'
        >
          ${o}
        </button>
      `)
      .join("");


  card.innerHTML = `
    <div class="prompt">
      ${currentQuestion.prompt}
    </div>

    <div class="options">
      ${opts}
    </div>
  `;

  el.appendChild(card);
}


function submitAnswer(answer) {

  if (!currentQuestion) {
    return;
  }

  if (answeredThisRound) {
    return;
  }

  ws.send(
    JSON.stringify({
      type: "do_task",
      task_id: currentQuestion.id,
      answer
    })
  );
}

window.submitAnswer = submitAnswer;


// ------------------------------------------------------------
// TASK RESULT
// ------------------------------------------------------------

function handleTaskResult(msg) {

  if (
    !currentQuestion ||
    msg.task_id !== currentQuestion.id
  ) {
    return;
  }

  if (msg.blocked) {

    toast(
      "Sabotage active — answers blocked"
    );

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


// ------------------------------------------------------------
// VOTING SCREEN
// ------------------------------------------------------------

function renderVotingScreen() {

  const el =
    $("#voting-players");

  if (!el) {
    return;
  }

  el.innerHTML = "";

  const alivePlayers =
    latestPlayers.filter(
      p => p.alive
    );


  alivePlayers.forEach(p => {

    const row =
      document.createElement("div");

    row.className =
      "vote-row";


    const info =
      document.createElement("div");

    info.className =
      "vote-player-info";

    info.innerHTML = `
      <strong>
        ${p.name}${p.id === myId ? " (you)" : ""}
      </strong>

      <small>
        Unsolved questions:
        ${p.unsolved_questions || 0}
      </small>
    `;


    const button =
      document.createElement("button");

    button.textContent =
      "Vote";

    button.onclick = () => {
      castVote(p.id);
    };


    row.appendChild(info);

    row.appendChild(button);

    el.appendChild(row);
  });


  const skipRow =
    document.createElement("div");

  skipRow.className =
    "vote-row skip-vote";

  skipRow.innerHTML = `
    <div class="vote-player-info">
      <strong>Skip vote</strong>
    </div>

    <button onclick="castVote('skip')">
      Vote
    </button>
  `;

  el.appendChild(skipRow);
}


function castVote(targetId) {

  if (!votingActive) {
    return;
  }

  ws.send(
    JSON.stringify({
      type: "vote",
      target_id: targetId
    })
  );

  toast("Vote cast");

  votingActive = false;
}

window.castVote = castVote;


// ------------------------------------------------------------
// VOTING RESULT
// ------------------------------------------------------------

function renderVotingResult(msg) {

  const result =
    $("#voting-result");

  if (msg.eliminated) {

    const p =
      latestPlayers.find(
        p => p.id === msg.eliminated
      );

    result.textContent =
      `${p ? p.name : "Someone"} was eliminated.`;

  } else {

    result.textContent =
      "No one was eliminated (everyone skipped).";
  }


  show("#screen-voting");


  setTimeout(() => {

    if (
      document
        .querySelector("#screen-voting")
        .classList
        .contains("active")
    ) {
      show("#screen-game");
    }

  }, 2000);
}


// ------------------------------------------------------------
// GAME OVER
// ------------------------------------------------------------

function renderGameOver(msg) {

  if (countdownTimer) {

    clearInterval(countdownTimer);

    countdownTimer = null;
  }

  votingActive = false;

  show("#screen-gameover");

  $("#gameover-title").textContent =
    msg.winner === "engineers"
      ? "Engineers win"
      : "Traitors win";

  $("#gameover-reason").textContent =
    msg.reason;


  const rolesEl =
    $("#gameover-roles");

  rolesEl.innerHTML = "";


  latestPlayers.forEach(p => {

    const div =
      document.createElement("div");

    div.className =
      "player-row";

    div.textContent =
      `${p.name}: ${msg.roles[p.id]}`;

    rolesEl.appendChild(div);
  });


  if ($("#restart-btn")) {

    $("#restart-btn").style.display =
      isHost ? "block" : "none";
  }
}