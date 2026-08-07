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
cd traitor-engineer
pip install -r requirements.txt
python server.py
```

Open `http://localhost:8000` in a browser tab per player (or share your IP
on the same wifi network so friends can join from their phones/laptops:
`http://YOUR_LOCAL_IP:8000`). Need at least 4 players to start.

## What's actually implemented

- Lobby with host controls
- Role assignment (traitor count scales with player count, section 8 of GDD)
- 6 rooms from the map design, connected as a grid, with a vent shortcut
  between Electrical Bay and Storage
- 2 task types (logic gates, binary decoding) — see "Adding a task type" below
- Sabotage: any traitor can trigger one (25s cooldown, blocks task
  completion for 15s) — no repair minigame yet, it just times out
- Traitor kill (20s cooldown, must share a room with the target)
- Body reporting and emergency meetings, discussion + voting timers
- Win conditions from section 9 of the GDD

## What's intentionally NOT implemented (and why)

I scoped this down hard so you'd have something *working* rather than
something *ambitious and broken*. These are the natural next steps, roughly
in order of how much they'd improve the game:

1. **Repair minigame for sabotage** — right now sabotage just expires.
   Real Among Us-style repair (an engineer has to go to the sabotaged room
   and solve a puzzle) would replace `clear_sabotage_after()`.
2. **Real 2D movement** — right now "moving" is picking a room from a list,
   not walking around a map. Adding real movement means a canvas/sprite
   client and broadcasting x/y positions — a genuinely bigger project.
3. **Multiple concurrent lobbies** — right now there's one global game.
   `game = Game()` would become a `dict[str, Game]` keyed by a room code.
4. **Persistence / accounts** — currently everything is in-memory and resets
   when the server restarts.
5. **Mobile app wrapper** — once the web version feels good, wrapping it as
   a mobile app (e.g. with Capacitor, which just wraps a web app — no Flutter
   needed) is a much smaller step than building native from scratch.

## How the code is organized

- `server.py` — all game logic. One `Game` class holds state in memory;
  one `websocket` endpoint handles every message type as a big
  if/elif dispatcher. Read this top to bottom, it's ~350 lines.
- `static/index.html` — the page skeleton, several `<section class="screen">`
  blocks, one shown at a time.
- `static/app.js` — connects the websocket, sends messages, and re-renders
  the DOM whenever a message comes in from the server.
- `static/style.css` — visuals, nothing game-logic-related.

## Adding a task type (the pattern to copy)

Look at `make_task()` in `server.py`. Each task is just a dict with a
`prompt`, some `options`, and the correct `answer`. To add "circuit
matching," write a new branch that generates a prompt and answer, add it
to `TASK_TYPES`, and the client will render it automatically — `app.js`
doesn't know or care what the task *is*, it just shows `prompt` and turns
each `option` into a button.

## Adding a room

Add the name to the `ROOMS` list in `server.py`. That's it — the client
pulls the room list from the server on `game_started`. If you want it
vent-connected to another room, add a tuple to `VENT_PAIRS`.

## Questions to think about before you extend this

- Do you want repair-the-sabotage to be a *race* (whoever gets there
  first) or does it need a specific role/tool?
- Should dead engineers' unfinished tasks count against the team, or just
  freeze?
- Right now votes only resolve when everyone alive has voted OR the timer
  runs out — is that the pacing you want, or should there be a way to end
  discussion early by consensus?
