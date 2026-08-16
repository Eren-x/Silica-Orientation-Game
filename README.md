# Traitor Engineer — playable prototype

A real, working implementation of the core loop from your GDD: lobby, role
assignment, tasks, sabotage, meetings, voting, win conditions. Python backend,
plain HTML/JS browser client. No Flutter, no app store, no build tools —
just `python server.py`.

This is deliberately a **prototype**, not the finished mobile game. It exists
so you can (a) actually play a round tonight, and (b) read code you already
understand (Python) to see how a real-time multiplayer game is structured,
before you decide how much further to take it.

## Run it

```bash
cd Silica-Orientation-Game
pip install -r requirements.txt   # fastapi, uvicorn, websockets
python server.py
```

The server listens on `0.0.0.0:8000`. Open `http://localhost:8000` in a
browser tab per player, or share your local IP on the same wifi network so
friends can join from their phones/laptops: `http://YOUR_LOCAL_IP:8000`.
Need at least 4 players to start.

## How a round works

- The **host** joins the separate host card (password `1234`). The host is
  **not** a player — they watch a dashboard showing every player's role,
  their questions attempted, and their running correct/incorrect totals.
- Players join from the player card: pick a name and a colour from the
  circular 48-colour wheel. Duplicate names (case-insensitive) and taken
  colours are rejected server-side, and the wheel greys out taken colours.
- The host hits **Start**. Traitors are assigned (1 for <5 players, else
  `players // 5`), everyone gets a role and a colour.
- **Engineers** solve questions. Each player has one question at a time with
  a 10-second server-side deadline; a fresh question arrives after each
  answer or timeout — and again whenever they move to a new room. Correct
  answers require no active sabotage.
- **Traitors** don't answer questions. They flip between task view and a
  tools panel (kill / sabotage / vent) and the question timer keeps running
  while they're on tools. Vent shortcuts (`VENT_PAIRS`), kill (20s cooldown,
  same room), sabotage (25s cooldown, blocks task completion for 15s).
- **Meetings**: report a body or call an emergency meeting — discussion
  (45s) then voting (20s), most votes ejected.
- **Win conditions**: engineers win when every alive engineer has 3 correct
  answers, or all traitors are eliminated. Traitors win when they equal or
  outnumber the alive engineers.
- If the host disconnects, the first remaining player is **auto-promoted to
  host** (mid-lobby or mid-game) so the round can keep going and be restarted.
  Host restart returns everyone to the lobby.

## What's actually implemented

- Lobby with host controls; separate player/host join cards with inline errors
- Host dashboard with roles and per-player question stats; no host questions
- Role assignment (traitor count scales with player count, section 8 of GDD)
- Server-authoritative single-question flow: 10s deadline, fresh question
  after answer/timeout and on room move; answers never sent to the client
- A live minimap driven by `ROOM_POSITIONS` / `ADJACENT_PAIRS`, showing player
  colours, room tints, and animated vent connections
- 6 rooms from the map design, connected as a grid, with a vent shortcut
  between Electrical Bay and Storage
- 2 task types (logic gates, binary decoding) — see "Adding a task type" below
- Sabotage: any traitor can trigger one (25s cooldown, blocks task
  completion for 15s) — no repair minigame yet, it just times out
- Traitor tools view (kill / sabotage / vent) toggled against the task timer
- Body reporting and emergency meetings, discussion + voting timers
- Win conditions from section 9 of the GDD
- Auto host promotion on disconnect; duplicate name/colour rejection;
  golden-angle colour palette shared byte-for-byte between server and client

## What's intentionally NOT implemented (and why)

I scoped this down hard so you'd have something *working* rather than
something *ambitious and broken*. These are the natural next steps, roughly
in order of how much they'd improve the game:

1. **Repair minigame for sabotage** — right now sabotage just expires.
   Real Among Us-style repair (an engineer has to go to the sabotaged room
   and solve a puzzle) would replace `clear_sabotage_after()`.
2. **Real 2D movement** — right now "moving" is picking a room on the map,
   not walking around with sprites. Real movement means a canvas/sprite
   client and broadcasting x/y positions — a genuinely bigger project.
3. **Multiple concurrent lobbies** — right now there's one global game.
   `game = Game()` would become a `dict[str, Game]` keyed by a room code.
4. **Persistence / accounts** — currently everything is in-memory and resets
   when the server restarts.
5. **Mobile app wrapper** — once the web version feels good, wrapping it as
   a mobile app (e.g. with Capacitor, which just wraps a web app — no Flutter
   needed) is a much smaller step than building native from scratch.

## How the code is organized

- `server.py` — all game logic in ~680 lines. One `Game` class holds state
  in memory; constants at the top (`ROOMS`, `ROOM_POSITIONS`,
  `ADJACENT_PAIRS`, `VENT_PAIRS`, cooldowns, `HOST_PASSWORD`) and one
  `websocket` endpoint that dispatches every message type. Read it top to
  bottom — the dispatcher is the wire contract.
- `static/index.html` — the page skeleton, several `<section class="screen">`
  blocks (join, lobby, game, meeting, gameover, host), one shown at a time.
- `static/app.js` — connects the websocket, sends messages, re-renders the
  DOM on each server message. Contains the palette generator, the circular
  colour wheel renderer, and the host dashboard renderer. The `?v=` query
  parameter on the script tag is a cache-buster — bump it whenever you edit
  the client.
- `static/style.css` — visuals, nothing game-logic-related.

## Adding a task type (the pattern to copy)

Look at `make_task()` in `server.py`. Each task is just a dict with a
`prompt`, some `options`, and the correct `answer`. To add "circuit
matching," write a new branch that generates a prompt and answer, add it
to `TASK_TYPES`, and the client will render it automatically — `app.js`
doesn't know or care what the task *is*, it just shows `prompt` and turns
each `option` into a button.

## Changing the map layout

The minimap is data-driven, so you can restructure the map without touching
the renderer:

- `ROOMS` — the list of room names (the client pulls this at game start)
- `ROOM_POSITIONS` — a `{room: {"x": n, "y": n}}` grid: the minimap is
  layout-adaptive and will re-flow automatically
- `ADJACENT_PAIRS` — which rooms are connected by corridors
- `VENT_PAIRS` — which rooms are linked by vent shortcuts (start and end swap
  freely; the client renders the shortcut)

## Questions to think about before you extend this

- Do you want repair-the-sabotage to be a *race* (whoever gets there
  first) or does it need a specific role/tool?
- Should dead engineers' unfinished tasks count against the team, or just
  freeze?
- Right now votes only resolve when everyone alive has voted OR the timer
  runs out — is that the pacing you want, or should there be a way to end
  discussion early by consensus?