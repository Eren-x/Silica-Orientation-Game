"""
Traitor Engineer - prototype server
------------------------------------
A minimal but fully playable implementation of the core loop from the GDD:
lobby -> role assignment -> tasks & sabotage -> meetings & voting -> win check.

Run with:  python server.py
Then open http://localhost:8000 in a browser tab per player.

This is a SINGLE global lobby (one game at a time) to keep the code readable.
See README.md for how to extend it to multiple concurrent lobbies, add more
task types, add a repair minigame for sabotage, etc.
"""

import asyncio
import json
import random
import string
import time
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()

# ---------------------------------------------------------------------------
# Game configuration (tune these -- see section 8/9 of the GDD)
# ---------------------------------------------------------------------------

ROOMS = [
    "Reactor Core",
    "Workshop",
    "Admin / Security",
    "Electrical Bay",
    "Storage",
    "Assembly Line",
]

# Vent pairs: traitors standing in one of a pair can vent to the other instantly.
VENT_PAIRS = [("Electrical Bay", "Storage")]

TASKS_PER_ENGINEER = 3
DISCUSSION_SECONDS = 45
VOTING_SECONDS = 20
SABOTAGE_COOLDOWN = 25
SABOTAGE_DURATION = 15
KILL_COOLDOWN = 20

TASK_TYPES = ["logic_gate", "binary_decode"]


def traitor_count(num_players: int) -> int:
    """Simple scaling curve -- roughly 1 traitor per 5 players, min 1."""
    if num_players < 5:
        return 1
    return max(1, num_players // 5)


def make_task(task_id: int) -> dict:
    kind = random.choice(TASK_TYPES)
    if kind == "logic_gate":
        gate = random.choice(["AND", "OR", "XOR"])
        a, b = random.randint(0, 1), random.randint(0, 1)
        answer = {"AND": a & b, "OR": a | b, "XOR": a ^ b}[gate]
        return {
            "id": task_id,
            "type": "logic_gate",
            "prompt": f"{a} {gate} {b} = ?",
            "options": [0, 1],
            "answer": answer,
            "done": False,
        }
    else:  # binary_decode
        n = random.randint(1, 31)
        return {
            "id": task_id,
            "type": "binary_decode",
            "prompt": f"Decode binary {format(n, '05b')} to decimal",
            "options": sorted({n, n + 1, max(0, n - 1), n + 4})[:4],
            "answer": n,
            "done": False,
        }


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

class Player:
    def __init__(self, pid: str, ws: WebSocket, name: str):
        self.id = pid
        self.ws = ws
        self.name = name
        self.role = "engineer"  # or "traitor"
        self.alive = True
        self.room = "Admin / Security"
        self.tasks = []
        self.host = False
        self.last_kill_time = 0.0

    def public(self):
        return {
            "id": self.id,
            "name": self.name,
            "alive": self.alive,
            "room": self.room,
            "host": self.host,
        }


class Game:
    def __init__(self):
        self.reset()

    def reset(self):
        self.players: dict[str, Player] = {}
        self.state = "lobby"  # lobby | playing | meeting | ended
        self.sabotage_active: Optional[dict] = None
        self.last_sabotage_time = 0.0
        self.meeting: Optional[dict] = None
        self.winner: Optional[str] = None
        self.next_task_id = 1

    def alive_players(self):
        return [p for p in self.players.values() if p.alive]

    def alive_engineers(self):
        return [p for p in self.alive_players() if p.role == "engineer"]

    def alive_traitors(self):
        return [p for p in self.alive_players() if p.role == "traitor"]

    def total_tasks(self):
        return sum(len(p.tasks) for p in self.players.values() if p.role == "engineer")

    def done_tasks(self):
        return sum(
            1
            for p in self.players.values()
            if p.role == "engineer"
            for t in p.tasks
            if t["done"]
        )


game = Game()


def gen_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


# ---------------------------------------------------------------------------
# Broadcasting
# ---------------------------------------------------------------------------

async def broadcast(msg: dict, exclude: Optional[str] = None):
    dead = []
    payload = json.dumps(msg)
    for pid, p in game.players.items():
        if pid == exclude:
            continue
        try:
            await p.ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        game.players.pop(pid, None)


async def send(pid: str, msg: dict):
    p = game.players.get(pid)
    if not p:
        return
    try:
        await p.ws.send_text(json.dumps(msg))
    except Exception:
        pass


def lobby_state():
    return {
        "type": "lobby_update",
        "players": [p.public() for p in game.players.values()],
    }


async def broadcast_lobby():
    await broadcast(lobby_state())


def public_state():
    return {
        "type": "state_update",
        "state": game.state,
        "players": [p.public() for p in game.players.values()],
        "rooms": ROOMS,
        "sabotage_active": game.sabotage_active,
        "tasks_done": game.done_tasks(),
        "tasks_total": game.total_tasks(),
    }


async def broadcast_state():
    await broadcast(public_state())


# ---------------------------------------------------------------------------
# Game flow
# ---------------------------------------------------------------------------

async def start_game():
    players = list(game.players.values())
    n_traitors = traitor_count(len(players))
    traitors = random.sample(players, n_traitors)
    traitor_ids = {p.id for p in traitors}

    for p in players:
        p.alive = True
        p.room = "Admin / Security"
        p.role = "traitor" if p.id in traitor_ids else "engineer"
        p.tasks = []
        if p.role == "engineer":
            for _ in range(TASKS_PER_ENGINEER):
                p.tasks.append(make_task(game.next_task_id))
                game.next_task_id += 1

    game.state = "playing"
    game.sabotage_active = None
    game.meeting = None
    game.winner = None

    for p in players:
        await send(p.id, {
            "type": "game_started",
            "your_role": p.role,
            "your_tasks": p.tasks,
            "rooms": ROOMS,
            "vent_pairs": VENT_PAIRS if p.role == "traitor" else [],
        })
    await broadcast_state()


async def check_win():
    if game.state != "playing":
        return
    engineers = game.alive_engineers()
    traitors = game.alive_traitors()

    if game.done_tasks() >= game.total_tasks() and game.total_tasks() > 0:
        await end_game("engineers", "All project tasks completed")
    elif len(traitors) == 0:
        await end_game("engineers", "All traitors eliminated")
    elif len(traitors) >= len(engineers):
        await end_game("traitors", "Traitors equal or outnumber engineers")


async def end_game(winner: str, reason: str):
    game.state = "ended"
    game.winner = winner
    await broadcast({
        "type": "game_over",
        "winner": winner,
        "reason": reason,
        "roles": {p.id: p.role for p in game.players.values()},
    })


async def start_meeting(reason: str, caller_id: str):
    game.state = "meeting"
    game.meeting = {
        "reason": reason,
        "caller": caller_id,
        "votes": {},
        "phase": "discussion",
        "ends_at": time.time() + DISCUSSION_SECONDS,
    }
    await broadcast({
        "type": "meeting_started",
        "reason": reason,
        "caller": caller_id,
        "discussion_seconds": DISCUSSION_SECONDS,
    })
    await broadcast_state()
    asyncio.create_task(meeting_timer())


async def meeting_timer():
    m = game.meeting
    if not m:
        return
    await asyncio.sleep(DISCUSSION_SECONDS)
    if game.meeting is not m or game.state != "meeting":
        return
    m["phase"] = "voting"
    m["ends_at"] = time.time() + VOTING_SECONDS
    await broadcast({"type": "voting_started", "voting_seconds": VOTING_SECONDS})
    await asyncio.sleep(VOTING_SECONDS)
    if game.meeting is not m or game.state != "meeting":
        return
    await resolve_votes()


async def resolve_votes():
    m = game.meeting
    tally: dict[str, int] = {}
    for target in m["votes"].values():
        tally[target] = tally.get(target, 0) + 1

    eliminated = None
    if tally:
        top = max(tally.values())
        top_targets = [t for t, v in tally.items() if v == top]
        if len(top_targets) == 1 and top_targets[0] != "skip":
            eliminated = top_targets[0]

    if eliminated and eliminated in game.players:
        game.players[eliminated].alive = False

    await broadcast({
        "type": "meeting_result",
        "tally": tally,
        "eliminated": eliminated,
        "eliminated_role": game.players[eliminated].role if eliminated else None,
    })

    game.meeting = None
    game.state = "playing"
    await broadcast_state()
    await check_win()


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    pid = gen_id()
    player: Optional[Player] = None

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "join":
                name = (msg.get("name") or "Player")[:16]
                player = Player(pid, ws, name)
                if not game.players:
                    player.host = True
                game.players[pid] = player
                await send(pid, {"type": "joined", "your_id": pid})
                await broadcast_lobby()
                continue

            if player is None:
                continue  # ignore messages before join

            if mtype == "start_game" and player.host and game.state == "lobby":
                if len(game.players) < 4:
                    await send(pid, {"type": "error", "message": "Need at least 4 players"})
                else:
                    await start_game()

            elif mtype == "move_room" and game.state == "playing" and player.alive:
                room = msg.get("room")
                if room in ROOMS:
                    player.room = room
                    await broadcast_state()

            elif mtype == "vent" and game.state == "playing" and player.alive and player.role == "traitor":
                target_room = msg.get("room")
                valid_targets = set()
                for a, b in VENT_PAIRS:
                    if player.room == a:
                        valid_targets.add(b)
                    if player.room == b:
                        valid_targets.add(a)
                if target_room in valid_targets:
                    player.room = target_room
                    await broadcast_state()

            elif mtype == "do_task" and game.state == "playing" and player.alive and player.role == "engineer":
                task_id = msg.get("task_id")
                answer = msg.get("answer")
                for t in player.tasks:
                    if t["id"] == task_id and not t["done"]:
                        if answer == t["answer"] and not game.sabotage_active:
                            t["done"] = True
                            await send(pid, {"type": "task_result", "task_id": task_id, "correct": True})
                            await broadcast_state()
                            await check_win()
                        else:
                            await send(pid, {"type": "task_result", "task_id": task_id, "correct": False})
                        break

            elif mtype == "sabotage" and game.state == "playing" and player.alive and player.role == "traitor":
                now = time.time()
                if game.sabotage_active:
                    await send(pid, {"type": "error", "message": "A sabotage is already active"})
                elif now - game.last_sabotage_time < SABOTAGE_COOLDOWN:
                    await send(pid, {"type": "error", "message": "Sabotage on cooldown"})
                else:
                    kind = msg.get("kind", "Power Failure")
                    game.sabotage_active = {"kind": kind, "ends_at": now + SABOTAGE_DURATION}
                    game.last_sabotage_time = now
                    await broadcast_state()
                    asyncio.create_task(clear_sabotage_after(SABOTAGE_DURATION))

            elif mtype == "kill" and game.state == "playing" and player.alive and player.role == "traitor":
                now = time.time()
                if now - player.last_kill_time < KILL_COOLDOWN:
                    await send(pid, {"type": "error", "message": "Kill on cooldown"})
                else:
                    target_id = msg.get("target_id")
                    target = game.players.get(target_id)
                    if target and target.alive and target.role == "engineer" and target.room == player.room:
                        target.alive = False
                        player.last_kill_time = now
                        await broadcast_state()
                        await check_win()

            elif mtype == "report_body" and game.state == "playing" and player.alive:
                target_id = msg.get("target_id")
                target = game.players.get(target_id)
                if target and not target.alive:
                    await start_meeting(f"{player.name} reported {target.name}'s body", pid)

            elif mtype == "call_meeting" and game.state == "playing" and player.alive:
                await start_meeting(f"{player.name} called an emergency meeting", pid)

            elif mtype == "vote" and game.state == "meeting" and player.alive:
                if game.meeting and game.meeting["phase"] == "voting":
                    game.meeting["votes"][pid] = msg.get("target_id", "skip")
                    await broadcast({
                        "type": "vote_cast",
                        "voter": pid,
                        "num_votes": len(game.meeting["votes"]),
                        "num_alive": len(game.alive_players()),
                    })
                    if len(game.meeting["votes"]) >= len(game.alive_players()):
                        await resolve_votes()

            elif mtype == "restart" and player.host:
                game.reset()
                game.players[pid] = player
                player.host = True
                await broadcast_lobby()

    except WebSocketDisconnect:
        if pid in game.players:
            was_host = game.players[pid].host
            del game.players[pid]
            if was_host and game.players:
                next_host = next(iter(game.players.values()))
                next_host.host = True
            if game.state == "lobby":
                await broadcast_lobby()
            else:
                await broadcast_state()


async def clear_sabotage_after(seconds: float):
    await asyncio.sleep(seconds)
    if game.sabotage_active and game.sabotage_active["ends_at"] <= time.time() + 0.5:
        game.sabotage_active = None
        await broadcast_state()


# ---------------------------------------------------------------------------
# Static files (the browser client)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
