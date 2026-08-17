"""
Traitor Engineer - prototype server
------------------------------------
A minimal but fully playable implementation of the core loop from the GDD:
lobby -> role assignment -> rounds (question + traitor tools) -> meetings & voting -> win check.

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

TASKS_PER_ENGINEER = 3  # multiplier used to compute how many correct answers are needed to win
DISCUSSION_SECONDS = 45
VOTING_SECONDS = 20
ROUND_SECONDS = 15  # one round = one shared question, visible for this long

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


def public_question(q: dict) -> dict:
    """Strip the answer field before sending a question to clients."""
    return {k: v for k, v in q.items() if k != "answer"}


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
        self.host = False
        # Per-round state
        self.round_choice: Optional[str] = None  # None | "question" | "tool" (traitors only)
        self.answered_this_round = False

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
        self.meeting: Optional[dict] = None
        self.winner: Optional[str] = None
        self.next_task_id = 1
        # Round state
        self.round_number = 0
        self.round_question: Optional[dict] = None
        self.round_ends_at = 0.0
        self.total_correct = 0
        self.target_correct = 0

    def alive_players(self):
        return [p for p in self.players.values() if p.alive]

    def alive_engineers(self):
        return [p for p in self.alive_players() if p.role == "engineer"]

    def alive_traitors(self):
        return [p for p in self.alive_players() if p.role == "traitor"]


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
        "round": game.round_number,
        "round_question": public_question(game.round_question) if game.round_question else None,
        "round_ends_at": game.round_ends_at,
        "total_correct": game.total_correct,
        "target_correct": game.target_correct,
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
        p.round_choice = None
        p.answered_this_round = False

    n_engineers = len(players) - len(traitors)

    game.state = "playing"
    game.sabotage_active = None
    game.meeting = None
    game.winner = None
    game.round_number = 0
    game.total_correct = 0
    game.target_correct = TASKS_PER_ENGINEER * max(1, n_engineers)

    for p in players:
        await send(p.id, {
            "type": "game_started",
            "your_role": p.role,
            "rooms": ROOMS,
            "vent_pairs": VENT_PAIRS if p.role == "traitor" else [],
        })

    await start_round()


async def start_round():
    """Begin a new round: fresh shared question, tools/choices refresh."""
    game.round_number += 1
    game.round_question = make_task(game.next_task_id)
    game.next_task_id += 1
    game.round_ends_at = time.time() + ROUND_SECONDS
    game.sabotage_active = None  # sabotage never carries across a round refresh

    for p in game.players.values():
        p.round_choice = None
        p.answered_this_round = False

    await broadcast({
        "type": "round_started",
        "round": game.round_number,
        "question": public_question(game.round_question),
        "seconds": ROUND_SECONDS,
        "round_ends_at": game.round_ends_at,
    })
    await broadcast_state()
    asyncio.create_task(round_timer(game.round_number))


async def round_timer(round_no: int):
    await asyncio.sleep(ROUND_SECONDS)
    # Bail if a meeting/new round/game end already happened while we slept.
    if game.round_number != round_no or game.state != "playing":
        return
    await start_round()


async def check_win():
    if game.state != "playing":
        return
    engineers = game.alive_engineers()
    traitors = game.alive_traitors()

    if game.target_correct > 0 and game.total_correct >= game.target_correct:
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
    if game.state == "playing":
        await start_round()  # resume with a fresh round after the meeting


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

            elif mtype == "do_task" and game.state == "playing" and player.alive:
                q = game.round_question
                if not q or msg.get("task_id") != q["id"]:
                    continue  # stale question from a previous round, ignore

                if player.role == "engineer":
                    if game.sabotage_active:
                        await send(pid, {"type": "task_result", "task_id": q["id"], "correct": False, "blocked": True})
                        continue
                    if player.answered_this_round:
                        continue
                    answer = msg.get("answer")
                    correct = answer == q["answer"]
                    if correct:
                        player.answered_this_round = True
                        game.total_correct += 1
                    await send(pid, {"type": "task_result", "task_id": q["id"], "correct": correct})
                    if correct:
                        await broadcast_state()
                        await check_win()

                elif player.role == "traitor":
                    if player.round_choice == "tool":
                        await send(pid, {"type": "error", "message": "You already used your action this round"})
                        continue
                    if player.answered_this_round:
                        continue
                    player.round_choice = "question"
                    answer = msg.get("answer")
                    correct = answer == q["answer"]
                    if correct:
                        player.answered_this_round = True
                        game.total_correct += 1
                    await send(pid, {"type": "task_result", "task_id": q["id"], "correct": correct})
                    if correct:
                        await broadcast_state()
                        await check_win()

            elif mtype == "sabotage" and game.state == "playing" and player.alive and player.role == "traitor":
                if player.round_choice is not None:
                    await send(pid, {"type": "error", "message": "You already used your action this round"})
                else:
                    kind = msg.get("kind", "Power Failure")
                    player.round_choice = "tool"
                    game.sabotage_active = {"kind": kind, "ends_at": game.round_ends_at}
                    await broadcast_state()

            elif mtype == "kill" and game.state == "playing" and player.alive and player.role == "traitor":
                if player.round_choice is not None:
                    await send(pid, {"type": "error", "message": "You already used your action this round"})
                else:
                    target_id = msg.get("target_id")
                    target = game.players.get(target_id)
                    if target and target.alive and target.role == "engineer" and target.room == player.room:
                        target.alive = False
                        player.round_choice = "tool"
                        await broadcast_state()
                        await check_win()

            elif mtype == "report_body" and game.state == "playing" and player.alive:
                target_id = msg.get("target_id")
                target = game.players.get(target_id)
                if target and not target.alive:
                    await start_meeting(f"{player.name} reported {target.name}'s body", pid)

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