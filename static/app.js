let ws = null;
let myId = null;
let myName = null;
let isHost = false;
let myRole = null;
let currentRoom = "Admin / Security";
let ventPairs = [];
let latestPlayers = [];
let hostPlayers = [];
let revealedRoles = new Set();

let currentQuestion = null;
let roundNumber = 0;
let roundEndsAt = 0;
let roundDurationSeconds = 15;
let answeredThisRound = false;
let totalCorrect = 0;
let targetCorrect = 0;
let countdownTimer = null;

let votingActive = false;
let votingEndsAt = 0;
let votingTimer = null;

let lastHostState = {
  state: "lobby",
  players: []
};

function $(sel) {
  return document.querySelector(sel);
}

function show(id) {
  document.querySelectorAll(".screen").forEach(s => {
    s.classList.remove("active");
  });

  const el = $(id);
  if (el) el.classList.add("active");
}

function toast(msg) {
  const t = $("#toast");
  if (!t) return;

  t.textContent = msg;
  t.classList.add("show");

  setTimeout(() => {
    t.classList.remove("show");
  }, 2200);
}

function setConnection(up) {
  const pill = $("#conn-pill");
  if (!pill) return;

  pill.textContent = up ? "● connected" : "◌ disconnected";
  pill.classList.toggle("up", up);
  pill.classList.toggle("down", !up);
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";

  ws = new WebSocket(
    `${proto}://${location.host}/ws`
  );

  ws.onopen = () => {
    setConnection(true);
  };

  ws.onmessage = ev => {
    handleMessage(JSON.parse(ev.data));
  };

  ws.onclose = () => {
    setConnection(false);
    toast("Disconnected from server");
  };

  ws.onerror = () => {
    setConnection(false);
  };
}

function connectAndJoin(payload) {
  const proto = location.protocol === "https:" ? "wss" : "ws";

  if (ws) {
    try {
      ws.close();
    } catch (_) {}
  }

  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    setConnection(true);
    ws.send(JSON.stringify(payload));
  };

  ws.onmessage = ev => {
    try {
      handleMessage(JSON.parse(ev.data));
    } catch (err) {
      console.error("Invalid server message", err);
    }
  };

  ws.onclose = () => {
    setConnection(false);
  };

  ws.onerror = () => {
    setConnection(false);
  };
}


/* =========================================================
   JOIN
   ========================================================= */

if ($("#join-btn")) {
  $("#join-btn").onclick = () => {
    const name = $("#p-name").value.trim();

    if (!name) {
      toast("Enter your name");
      return;
    }

    myName = name;
    isHost = false;

    connectAndJoin({
      type: "join",
      name: name
    });
  };
}


if ($("#host-join-btn")) {
  $("#host-join-btn").onclick = () => {
    const name = $("#h-name").value.trim();
    const password = $("#h-password").value;

    if (!name) {
      toast("Enter host name");
      return;
    }

    myName = name;
    isHost = true;

    connectAndJoin({
      type: "host_join",
      name: name,
      password: password
    });
  };
}


/* =========================================================
   HOST / GAME BUTTONS
   ========================================================= */

if ($("#start-btn")) {
  $("#start-btn").onclick = () => {
    if (!ws) return;

    ws.send(
      JSON.stringify({
        type: "start_game"
      })
    );
  };
}


if ($("#host-start-btn")) {
  $("#host-start-btn").onclick = () => {
    if (!ws) return;

    ws.send(
      JSON.stringify({
        type: "start_game"
      })
    );
  };
}


if ($("#restart-btn")) {
  $("#restart-btn").onclick = () => {
    if (!ws) return;

    ws.send(
      JSON.stringify({
        type: "restart"
      })
    );
  };
}


if ($("#host-restart-btn")) {
  $("#host-restart-btn").onclick = () => {
    if (!ws) return;

    ws.send(
      JSON.stringify({
        type: "restart"
      })
    );
  };
}


/* =========================================================
   TRAITOR TOOLS
   ========================================================= */

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

    ws?.send(
      JSON.stringify({
        type: "sabotage",
        kind: kind
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

    ws?.send(
      JSON.stringify({
        type: "kill",
        target_id: target.id
      })
    );
  };
}


/* =========================================================
   MESSAGE HANDLER
   ========================================================= */

function handleMessage(msg) {

  switch (msg.type) {

    case "joined":

      myId = msg.your_id;
      isHost = false;

      show("#screen-lobby");

      break;


    case "host_joined":

      myId = msg.your_id;
      isHost = true;

      show("#screen-host");

      renderHostDashboard({
        state: "lobby",
        players: []
      });

      break;


    case "lobby_update":

      if (!isHost) {
        renderLobby(msg.players || []);
      }

      break;


    case "host_state":

      if (isHost) {

        lastHostState = {
          ...lastHostState,
          ...msg,
          players:
            msg.players ||
            lastHostState.players ||
            []
        };

        hostPlayers =
          lastHostState.players;

        renderHostDashboard(
          lastHostState
        );
      }

      break;


    case "error":

      toast(msg.message);

      break;


    /* =====================================================
       PLAYER GAME START
       ===================================================== */

    case "game_started":

      myRole = msg.your_role;

      ventPairs =
        msg.vent_pairs || [];

      currentRoom =
        "Admin / Security";

      currentQuestion = null;
      answeredThisRound = false;
      votingActive = false;

      show("#screen-game");

      renderRoleBadge();

      if ($("#traitor-panel")) {
        $("#traitor-panel").classList.toggle(
          "hidden",
          myRole !== "traitor"
        );
      }

      if ($("#question-panel")) {
        $("#question-panel").classList.remove(
          "hidden"
        );
      }

      break;


    /* =====================================================
       NEW ROUND
       ===================================================== */

    case "round_started":

      votingActive = false;

      if (votingTimer) {
        clearInterval(votingTimer);
        votingTimer = null;
      }

      roundNumber =
        Number(msg.round) || 0;

      currentQuestion =
        msg.question || null;

      answeredThisRound = false;

      roundDurationSeconds =
        Number(msg.seconds) || 15;

      roundEndsAt =
        Number(msg.round_ends_at || 0) * 1000;

      const votingResult = $("#voting-result");

      if (votingResult) {
        votingResult.textContent = "";
      }

      renderQuestion();

      startCountdown();

      updateProgressLine();

      show("#screen-game");

      break;


    /* =====================================================
       NORMAL PLAYER STATE
       ===================================================== */

    case "state_update":

      latestPlayers =
        msg.players || [];

      renderRooms(
        msg.rooms || []
      );

      renderPlayers(
        latestPlayers
      );

      renderSabotage(
        msg.sabotage_active
      );

      if (
        typeof msg.round === "number"
      ) {
        roundNumber =
          msg.round;
      }

      if (
        typeof msg.total_correct === "number"
      ) {
        totalCorrect =
          msg.total_correct;
      }

      if (
        typeof msg.target_correct === "number"
      ) {
        targetCorrect =
          msg.target_correct;
      }

      if (msg.round_ends_at) {
        roundEndsAt =
          msg.round_ends_at * 1000;
      }

      if (
        msg.round_question &&
        (
          !currentQuestion ||
          currentQuestion.id !==
            msg.round_question.id
        )
      ) {

        currentQuestion =
          msg.round_question;

        answeredThisRound =
          false;

        renderQuestion();
      }

      updateProgressLine();

      if (
        msg.state === "voting" &&
        !votingActive
      ) {

        votingActive = true;

        renderVotingScreen();

        show("#screen-voting");

      } else if (
        msg.state === "playing" &&
        currentQuestion
      ) {

        show("#screen-game");
      }

      break;


    /* =====================================================
       TASK RESULT
       ===================================================== */

    case "task_result":

      handleTaskResult(msg);

      break;


    /* =====================================================
       VOTING STARTED
       ===================================================== */

    case "voting_started":

      votingActive = true;

      votingEndsAt =
        (
          Number(msg.voting_ends_at) ||
          (
            Date.now() / 1000 +
            Number(msg.voting_seconds || 20)
          )
        ) * 1000;

      const votingResultEl =
        $("#voting-result");

      if (votingResultEl) {
        votingResultEl.textContent = "";
      }

      renderVotingScreen();

      startVotingCountdown();

      show("#screen-voting");

      break;


    /* =====================================================
       VOTE CAST
       ===================================================== */

    case "vote_cast":

      if ($("#voting-timer")) {

        $("#voting-timer").textContent =
          `Votes: ${msg.num_votes}/${msg.num_alive}`;
      }

      break;


    /* =====================================================
       VOTING RESULT
       ===================================================== */

    case "voting_result":

      votingActive = false;

      renderVotingResult(msg);

      break;


    /* =====================================================
       GAME OVER
       ===================================================== */

    case "game_over":

      renderGameOver(msg);

      break;
  }
}


/* =========================================================
   LOBBY
   ========================================================= */

function renderLobby(players) {

  const list =
    $("#lobby-players");

  if (!list) return;

  list.innerHTML = "";

  players.forEach(p => {

    const li =
      document.createElement("li");

    li.textContent =
      p.name;

    list.appendChild(li);
  });

  if ($("#start-btn")) {
    $("#start-btn").style.display =
      "none";
  }

  if ($("#lobby-hint")) {

    $("#lobby-hint").textContent =
      players.length < 4
        ? `Need at least 4 players to start (${players.length}/4).`
        : `${players.length} players ready. Waiting for host to start.`;
  }
}


/* =========================================================
   ROLE BADGE
   ========================================================= */

function renderRoleBadge() {

  const badge =
    $("#role-badge");

  if (!badge) return;

  badge.textContent =
    myRole === "traitor"
      ? "You are a Traitor"
      : "You are an Engineer";

  badge.className =
    myRole;
}


/* =========================================================
   ROOMS
   ========================================================= */

function renderRooms(rooms) {

  const el =
    $("#room-list");

  if (!el) return;

  el.innerHTML = "";

  rooms.forEach(room => {

    const btn =
      document.createElement("button");

    btn.className =
      "room-btn" +
      (
        room === currentRoom
          ? " current"
          : ""
      );

    btn.textContent =
      room;

    btn.onclick = () => {

      currentRoom =
        room;

      ws?.send(
        JSON.stringify({
          type: "move_room",
          room: room
        })
      );
    };

    el.appendChild(btn);
  });


  /* Traitor vents */

  if (myRole === "traitor") {

    ventPairs.forEach(
      ([a, b]) => {

        if (
          currentRoom === a ||
          currentRoom === b
        ) {

          const dest =
            currentRoom === a
              ? b
              : a;

          const vbtn =
            document.createElement(
              "button"
            );

          vbtn.textContent =
            `Vent to ${dest}`;

          vbtn.className =
            "vent-btn";

          vbtn.onclick = () => {

            currentRoom =
              dest;

            ws?.send(
              JSON.stringify({
                type: "vent",
                room: dest
              })
            );
          };

          el.appendChild(vbtn);
        }
      }
    );
  }
}


/* =========================================================
   PLAYERS
   ========================================================= */

function renderPlayers(players) {

  const el =
    $("#player-list");

  if (!el) return;

  el.innerHTML = "";

  players.forEach(p => {

    const row =
      document.createElement("div");

    row.className =
      "player-row" +
      (
        p.alive
          ? ""
          : " dead"
      );

    row.innerHTML =
      `
      <span>
        ${escapeHtml(p.name)}
        ${p.id === myId ? " (you)" : ""}
      </span>

      <span class="room-tag">
        ${
          p.alive
            ? escapeHtml(p.room)
            : "removed"
        }
      </span>
      `;

    el.appendChild(row);
  });
}


/* =========================================================
   ROUND TIMER
   ========================================================= */

function updateProgressLine() {

  const secs =
    Math.max(
      0,
      Math.ceil(
        (
          roundEndsAt -
          Date.now()
        ) / 1000
      )
    );

  if ($("#progress-pill")) {

    $("#progress-pill").textContent =
      `Round ${roundNumber} • ${secs}s`;
  }

  if ($("#timer-label")) {

    $("#timer-label").textContent =
      `${secs}.0s`;
  }

  if ($("#timer-fill")) {

    const duration =
      Math.max(
        1,
        roundDurationSeconds
      );

    const percent =
      Math.max(
        0,
        Math.min(
          100,
          (
            (
              roundEndsAt -
              Date.now()
            ) /
            (duration * 1000)
          ) * 100
        )
      );

    $("#timer-fill").style.width =
      `${percent}%`;
  }
}


function startCountdown() {

  if (countdownTimer) {
    clearInterval(
      countdownTimer
    );
  }

  updateProgressLine();

  countdownTimer =
    setInterval(
      updateProgressLine,
      100
    );
}


/* =========================================================
   VOTING TIMER
   ========================================================= */

function startVotingCountdown() {

  if (votingTimer) {
    clearInterval(
      votingTimer
    );
  }

  const tick = () => {

    const secs =
      Math.max(
        0,
        Math.ceil(
          (
            votingEndsAt -
            Date.now()
          ) / 1000
        )
      );

    if ($("#voting-timer")) {

      $("#voting-timer").textContent =
        `Voting: ${secs}s`;
    }

    if (secs <= 0) {

      clearInterval(
        votingTimer
      );

      votingTimer = null;
    }
  };

  tick();

  votingTimer =
    setInterval(
      tick,
      250
    );
}


/* =========================================================
   SABOTAGE
   ========================================================= */

function renderSabotage(sabotage) {

  const banner =
    $("#sabotage-banner");

  if (!banner) return;

  if (sabotage) {

    banner.textContent =
      `SABOTAGE ACTIVE: ${sabotage.kind} — tasks blocked until the round ends`;

    banner.classList.remove(
      "hidden"
    );

  } else {

    banner.classList.add(
      "hidden"
    );
  }
}
/* =========================================================
   QUESTION
   ========================================================= */

function renderQuestion() {

  const el =
    $("#question-card");

  if (!el) return;

  el.innerHTML = "";

  if (!currentQuestion) return;

  const card =
    document.createElement(
      "div"
    );

  card.className =
    "task-card" +
    (
      answeredThisRound
        ? " done"
        : ""
    );


  const prompt =
    document.createElement(
      "div"
    );

  prompt.className =
    "prompt";

  prompt.textContent =
    currentQuestion.prompt;

  card.appendChild(
    prompt
  );


  const options =
    document.createElement(
      "div"
    );

  options.className =
    "options";


  currentQuestion.options.forEach(
    option => {

      const btn =
        document.createElement(
          "button"
        );

      btn.textContent =
        option;

      btn.disabled =
        answeredThisRound ||
        votingActive;

      btn.onclick = () =>
        submitAnswer(option);

      options.appendChild(
        btn
      );
    }
  );


  card.appendChild(
    options
  );

  el.appendChild(
    card
  );
}


function submitAnswer(answer) {

  if (
    !currentQuestion ||
    answeredThisRound ||
    votingActive
  ) {
    return;
  }

  ws?.send(
    JSON.stringify({
      type: "do_task",
      task_id:
        currentQuestion.id,
      answer:
        answer
    })
  );
}

window.submitAnswer =
  submitAnswer;


/* =========================================================
   TASK RESULT
   ========================================================= */

function handleTaskResult(msg) {

  if (
    !currentQuestion ||
    msg.task_id !==
      currentQuestion.id
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

    answeredThisRound =
      true;

    toast(
      "Correct!"
    );

  } else {

    toast(
      "Incorrect, try again"
    );
  }

  renderQuestion();
}


/* =========================================================
   VOTING SCREEN
   ========================================================= */

function renderVotingScreen() {

  const el =
    $("#voting-players");

  if (!el) return;

  el.innerHTML = "";


  latestPlayers
    .filter(
      p => p.alive
    )
    .forEach(
      p => {

        const row =
          document.createElement(
            "div"
          );

        row.className =
          "vote-row";


        const info =
          document.createElement(
            "div"
          );

        info.className =
          "vote-player-info";


        const name =
          document.createElement(
            "strong"
          );

        name.textContent =
          p.name +
          (
            p.id === myId
              ? " (you)"
              : ""
          );


        const unsolved =
          document.createElement(
            "small"
          );

        unsolved.textContent =
          `Unsolved questions: ${
            p.unsolved_questions || 0
          }`;


        info.appendChild(
          name
        );

        info.appendChild(
          unsolved
        );


        const button =
          document.createElement(
            "button"
          );

        button.textContent =
          "Vote";

        button.disabled =
          !votingActive ||
          p.id === myId;

        button.onclick = () =>
          castVote(p.id);


        row.appendChild(
          info
        );

        row.appendChild(
          button
        );

        el.appendChild(
          row
        );
      }
    );
}


function castVote(targetId) {

  if (!votingActive) {
    return;
  }

  ws?.send(
    JSON.stringify({
      type: "vote",
      target_id: targetId
    })
  );

  votingActive =
    false;

  toast(
    "Vote cast"
  );
}

window.castVote =
  castVote;


/* =========================================================
   VOTING RESULT
   ========================================================= */

function renderVotingResult(msg) {

  const result =
    $("#voting-result");

  if (result) {

    result.textContent =
      msg.eliminated_name
        ? `${msg.eliminated_name} was removed from the game. Starting next round...`
        : "No player was removed. Starting next round...";
  }

  show(
    "#screen-voting"
  );
}


/* =========================================================
   GAME OVER
   ========================================================= */

function renderGameOver(msg) {

  if (countdownTimer) {
    clearInterval(
      countdownTimer
    );
  }

  if (votingTimer) {
    clearInterval(
      votingTimer
    );
  }

  votingActive =
    false;

  show(
    "#screen-gameover"
  );


  if ($("#gameover-title")) {

    $("#gameover-title").textContent =
      msg.winner === "engineers"
        ? "Engineers win"
        : "Traitors win";
  }


  if ($("#gameover-reason")) {

    $("#gameover-reason").textContent =
      msg.reason;
  }


  const rolesEl =
    $("#gameover-roles");

  if (rolesEl) {

    rolesEl.innerHTML = "";

    const row =
      document.createElement(
        "div"
      );

    row.className =
      "player-row";

    const left =
      document.createElement(
        "span"
      );

    left.textContent =
      "Your role";


    const right =
      document.createElement(
        "span"
      );

    right.textContent =
      msg.your_role || myRole || "";


    row.appendChild(
      left
    );

    row.appendChild(
      right
    );

    rolesEl.appendChild(
      row
    );
  }


  if ($("#restart-btn")) {

    $("#restart-btn").style.display =
      "none";
  }
}


/* =========================================================
   HOST DASHBOARD
   ========================================================= */

function renderHostDashboard(msg = {}) {

  if (!isHost) {
    return;
  }


  if (
    msg &&
    (
      msg.players ||
      msg.state
    )
  ) {

    lastHostState = {
      ...lastHostState,
      ...msg,
      players:
        msg.players ||
        lastHostState.players ||
        []
    };
  }


  const state =
    msg.state ||
    lastHostState.state ||
    "lobby";


  const players =
    msg.players ||
    lastHostState.players ||
    [];


  hostPlayers =
    players;


  show(
    "#screen-host"
  );


  /* =====================================================
     SUMMARY
     ===================================================== */

  const active =
    players.filter(
      p => p.alive
    ).length;


  const removed =
    players.filter(
      p => !p.alive
    ).length;


  if ($("#host-summary")) {

    $("#host-summary").textContent =
      `${players.length} participants • ` +
      `${active} active • ` +
      `${removed} removed • ` +
      `Game: ${state}`;
  }


  /* =====================================================
     TABLE
     ===================================================== */

  const rows =
    $("#host-rows");

  if (!rows) return;

  rows.innerHTML = "";


  players.forEach(
    p => {

      const tr =
        document.createElement(
          "tr"
        );


      /* PLAYER NAME */

      const nameTd =
        document.createElement(
          "td"
        );

      nameTd.textContent =
        p.name;


      /* ROLE */

      const roleTd =
        document.createElement("td");

      roleTd.className =
        "role-cell";

      const roleVisible =
        revealedRoles.has(p.id);

      if (roleVisible) {

        roleTd.classList.add(
          p.role === "traitor"
            ? "role-traitor"
            : "role-engineer"
        );

        roleTd.textContent =
          p.role === "traitor"
            ? "Traitor"
            : "Engineer";

      } else {

        roleTd.textContent =
          "Hidden";
      }


      const roleButton =
        document.createElement(
          "button"
        );

      roleButton.type =
        "button";

      roleButton.className =
        "role-toggle";

      roleButton.textContent =
        roleVisible
          ? "Hide"
          : "Reveal";

      roleButton.onclick = () =>
        toggleHostRole(p.id);

      roleTd.appendChild(
        roleButton
      );


      /* ROOM */

      const roomTd =
        document.createElement(
          "td"
        );

      roomTd.textContent =
        p.room || "-";


      /* STATUS */

      const statusTd =
        document.createElement(
          "td"
        );

      statusTd.textContent =
        p.alive
          ? "Active"
          : "Removed";

      statusTd.className =
        p.alive
          ? "status-active"
          : "status-removed";


      /* SOLVED */

      const solvedTd =
        document.createElement(
          "td"
        );

      solvedTd.textContent =
        p.solved_questions || 0;


      /* UNSOLVED */

      const unsolvedTd =
        document.createElement(
          "td"
        );

      unsolvedTd.textContent =
        p.unsolved_questions || 0;


      tr.appendChild(
        nameTd
      );

      tr.appendChild(
        roleTd
      );

      tr.appendChild(
        roomTd
      );

      tr.appendChild(
        statusTd
      );

      tr.appendChild(
        solvedTd
      );

      tr.appendChild(
        unsolvedTd
      );


      rows.appendChild(
        tr
      );
    }
  );


  /* =====================================================
     HOST START BUTTON
     ===================================================== */

  const start =
    $("#host-start-btn");

  if (start) {

    if (state === "lobby") {

      start.style.display =
        "block";

      start.disabled =
        players.length < 4;

      start.textContent =
        players.length < 4
          ? `Need ${4 - players.length} more player${
              4 - players.length === 1
                ? ""
                : "s"
            }`
          : "Start game";

    } else {

      start.style.display =
        "none";
    }
  }


  /* =====================================================
     HOST RESTART
     ===================================================== */

  const restart =
    $("#host-restart-btn");

  if (restart) {

    restart.style.display =
      state === "ended"
        ? "block"
        : "none";
  }


  /* =====================================================
     HOST LIVE STATUS
     ===================================================== */

  const live =
    $("#host-live");

  if (live) {

    live.innerHTML = "";

    players.forEach(
      p => {

        const line =
          document.createElement(
            "div"
          );

        line.className =
          "live-line";


        const dot =
          document.createElement(
            "span"
          );

        dot.className =
          "live-dot " +
          (
            p.alive
              ? "up"
              : "down"
          );

        dot.textContent =
          "●";


        const name =
          document.createElement(
            "span"
          );

        name.className =
          "live-name";

        name.textContent =
          p.name;


        const status =
          document.createElement(
            "span"
          );

        status.className =
          "muted";

        status.textContent =
          p.alive
            ? "Active"
            : "Removed";


        line.appendChild(
          dot
        );

        line.appendChild(
          name
        );

        line.appendChild(
          status
        );


        live.appendChild(
          line
        );
      }
    );
  }


  /* =====================================================
     HOST LOG
     ===================================================== */

  const log =
    $("#host-log");

  if (log) {

    if (state === "voting") {

      log.textContent =
        "Voting is in progress. Player removal will appear here automatically.";

    } else if (state === "playing") {

      log.textContent =
        `Round ${
          lastHostState.round || 0
        } is in progress.`;

    } else if (state === "ended") {

      log.textContent =
        "Game ended.";

    } else {

      log.textContent =
        "Waiting for players.";
    }
  }
}


/* =========================================================
   HOST ROLE SHOW / HIDE
   ========================================================= */

function toggleHostRole(id) {

  if (
    revealedRoles.has(id)
  ) {

    revealedRoles.delete(id);

  } else {

    revealedRoles.add(id);
  }


  renderHostDashboard(
    lastHostState
  );
}

window.toggleHostRole =
  toggleHostRole;


/* =========================================================
   HTML ESCAPING
   ========================================================= */

function escapeHtml(value) {

  return String(
    value ?? ""
  ).replace(
    /[&<>'"]/g,
    c => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;"
    }[c])
  );
}