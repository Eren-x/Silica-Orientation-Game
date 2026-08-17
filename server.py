"""Traitor Engineer - prototype server."""
import os
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

ROOMS = [
    "Reactor Core", "Workshop", "Admin / Security",
    "Electrical Bay", "Storage", "Assembly Line",
]
VENT_PAIRS = [("Electrical Bay", "Storage")]
TASKS_PER_ENGINEER = 3
ROUND_SECONDS = 15
VOTING_SECONDS = 20
TASK_TYPES = ["logic_gate", "binary_decode"]


def traitor_count(num_players: int) -> int:
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
            "id": task_id, "type": "logic_gate",
            "prompt": f"{a} {gate} {b} = ?",
            "options": [0, 1], "answer": answer, "done": False,
        }

    n = random.randint(1, 31)
    options = sorted({n, n + 1, max(0, n - 1), n + 4})[:4]
    return {
        "id": task_id, "type": "binary_decode",
        "prompt": f"Decode binary {format(n, '05b')} to decimal",
        "options": options, "answer": n, "done": False,
    }


def public_question(q: dict) -> dict:
    return {k: v for k, v in q.items() if k != "answer"}


class Player:
    def __init__(self, pid: str, ws: WebSocket, name: str):
        self.id = pid
        self.ws = ws
        self.name = name
        self.role = "engineer"
        self.alive = True
        self.room = "Admin / Security"
        self.round_choice: Optional[str] = None
        self.answered_this_round = False
        self.solved_questions = 0
        self.unsolved_questions = 0

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "alive": self.alive,
            "room": self.room,
            "unsolved_questions": self.unsolved_questions,
            "solved_questions": self.solved_questions,
        }


class Game:
    def __init__(self):
        self.players: dict[str, Player] = {}
        self.host_ws: Optional[WebSocket] = None
        self.host_name: str = ""
        self.host_connected = False
        self.state = "lobby"
        self.sabotage_active: Optional[dict] = None
        self.voting: Optional[dict] = None
        self.winner: Optional[str] = None
        self.next_task_id = 1
        self.round_number = 0
        self.round_question: Optional[dict] = None
        self.round_ends_at = 0.0
        self.total_correct = 0
        self.target_correct = 0

    def reset_game(self):
        self.players = {}
        self.state = "lobby"
        self.sabotage_active = None
        self.voting = None
        self.winner = None
        self.next_task_id = 1
        self.round_number = 0
        self.round_question = None
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


async def send_ws(ws: Optional[WebSocket], msg: dict):
    if not ws:
        return False
    try:
        await ws.send_text(json.dumps(msg))
        return True
    except Exception:
        return False


async def send_player(pid: str, msg: dict):
    p = game.players.get(pid)
    if p:
        await send_ws(p.ws, msg)


async def broadcast(msg: dict):
    payload = json.dumps(msg)
    dead = []
    for pid, p in list(game.players.items()):
        try:
            await p.ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        game.players.pop(pid, None)


async def broadcast_lobby():
    msg = {
        "type": "lobby_update",
        "players": [p.public() for p in game.players.values()],
    }
    await broadcast(msg)
    await send_host_state()


def player_state() -> dict:
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


def host_state() -> dict:
    return {
        "type": "host_state",
        "state": game.state,
        "round": game.round_number,
        "round_ends_at": game.round_ends_at,
        "voting_ends_at": game.voting.get("ends_at") if game.voting else 0,
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "role": p.role,
                "room": p.room,
                "solved_questions": p.solved_questions,
                "unsolved_questions": p.unsolved_questions,
                "alive": p.alive,
                "status": "Active" if p.alive else "Removed",
            }
            for p in game.players.values()
        ],
        "host_name": game.host_name,
        "player_count": len(game.players),
        "alive_count": len(game.alive_players()),
    }


async def broadcast_state():
    await broadcast(player_state())
    await send_host_state()


async def send_host_state():
    await send_ws(game.host_ws, host_state())


async def start_game():
    players = list(game.players.values())
    if len(players) < 4:
        await send_host_state()
        return

    n_traitors = traitor_count(len(players))
    traitors = random.sample(players, n_traitors)
    traitor_ids = {p.id for p in traitors}

    for p in players:
        p.alive = True
        p.room = "Admin / Security"
        p.role = "traitor" if p.id in traitor_ids else "engineer"
        p.round_choice = None
        p.answered_this_round = False
        p.solved_questions = 0
        p.unsolved_questions = 0

    n_engineers = len(players) - n_traitors
    game.state = "playing"
    game.sabotage_active = None
    game.voting = None
    game.winner = None
    game.round_number = 0
    game.total_correct = 0
    game.target_correct = TASKS_PER_ENGINEER * max(1, n_engineers)

    for p in players:
        await send_player(p.id, {
            "type": "game_started",
            "your_role": p.role,
            "rooms": ROOMS,
            "vent_pairs": VENT_PAIRS if p.role == "traitor" else [],
        })

    await send_host_state()
    await start_round()


async def start_round():
    if game.state == "ended":
        return

    game.state = "playing"
    game.round_number += 1
    game.round_question = make_task(game.next_task_id)
    game.next_task_id += 1
    game.round_ends_at = time.time() + ROUND_SECONDS
    game.sabotage_active = None
    game.voting = None

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
    await send_host_state()
    asyncio.create_task(round_timer(game.round_number))


async def round_timer(round_no: int):
    await asyncio.sleep(ROUND_SECONDS)
    if game.round_number != round_no or game.state != "playing":
        return

    for p in game.alive_players():
        if not p.answered_this_round:
            p.unsolved_questions += 1

    await broadcast_state()
    await start_voting()


async def check_win():
    if game.state == "ended":
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

    for p in game.players.values():
        await send_player(p.id, {
            "type": "game_over",
            "winner": winner,
            "reason": reason,
            "your_role": p.role,
        })
    await send_host_state()


async def start_voting():
    if game.state == "ended":
        return

    alive = game.alive_players()
    if len(alive) <= 1:
        await check_win()
        return

    game.state = "voting"
    game.voting = {
        "votes": {},
        "ends_at": time.time() + VOTING_SECONDS,
    }

    await broadcast({
        "type": "voting_started",
        "voting_seconds": VOTING_SECONDS,
        "voting_ends_at": game.voting["ends_at"],
    })
    await send_host_state()
    asyncio.create_task(voting_timer(game.voting))


async def voting_timer(voting_ref):
    await asyncio.sleep(VOTING_SECONDS)
    if game.voting is not voting_ref or game.state != "voting":
        return
    await resolve_votes()


async def resolve_votes():
    voting = game.voting
    if not voting:
        return

    alive = game.alive_players()
    tally: dict[str, int] = {}
    for target in voting["votes"].values():
        if target in {p.id for p in alive}:
            tally[target] = tally.get(target, 0) + 1

    if tally:
        top = max(tally.values())
        top_targets = [target for target, count in tally.items() if count == top]
        eliminated = random.choice(top_targets)
    else:
        eliminated = random.choice(alive).id if alive else None

    eliminated_role = None
    eliminated_name = None
    if eliminated:
        target = game.players.get(eliminated)
        if target and target.alive:
            target.alive = False
            eliminated_role = target.role
            eliminated_name = target.name

    await broadcast({
        "type": "voting_result",
        "tally": tally,
        "eliminated": eliminated,
        "eliminated_name": eliminated_name,
    })
    await send_host_state()

    game.voting = None
    await check_win()
    if game.state == "playing":
        await start_round()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    connection_id = gen_id()
    player: Optional[Player] = None
    is_host_connection = False

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "join":
                name = (msg.get("name") or "Player")[:16]
                requested_host = bool(msg.get("host"))

                if requested_host:
                    if game.host_connected:
                        await send_ws(ws, {"type": "error", "message": "A host is already connected"})
                        continue
                    game.host_ws = ws
                    game.host_name = name
                    game.host_connected = True
                    is_host_connection = True
                    await send_ws(ws, {
                        "type": "host_joined",
                        "your_id": connection_id,
                        "is_host": True,
                    })
                    await send_host_state()
                    continue

                if game.state != "lobby":
                    await send_ws(ws, {"type": "error", "message": "The game has already started"})
                    continue

                player = Player(connection_id, ws, name)
                game.players[connection_id] = player
                await send_player(connection_id, {
                    "type": "joined",
                    "your_id": connection_id,
                    "is_host": False,
                })
                await broadcast_lobby()
                continue

            if is_host_connection:
                if mtype == "start_game" and game.state == "lobby":
                    if len(game.players) < 4:
                        await send_ws(ws, {"type": "error", "message": "Need at least 4 players"})
                    else:
                        await start_game()
                elif mtype == "restart":
                    game.reset_game()
                    game.host_ws = ws
                    game.host_name = name_from_host(game.host_name, "Host")
                    game.host_connected = True
                    await send_ws(ws, {"type": "host_joined", "your_id": connection_id, "is_host": True})
                    await broadcast_lobby()
                continue

            if player is None:
                continue

            if mtype == "start_game":
                await send_player(player.id, {"type": "error", "message": "Only the host can start the game"})

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
                    continue

                if player.round_choice == "tool":
                    await send_player(player.id, {"type": "error", "message": "You already used your traitor tool this round"})
                    continue
                if player.answered_this_round:
                    continue

                if player.role == "engineer" and game.sabotage_active:
                    await send_player(player.id, {"type": "task_result", "task_id": q["id"], "correct": False, "blocked": True})
                    continue

                player.round_choice = "question"
                answer = msg.get("answer")
                correct = answer == q["answer"]
                if correct:
                    player.answered_this_round = True
                    player.solved_questions += 1
                    game.total_correct += 1

                await send_player(player.id, {"type": "task_result", "task_id": q["id"], "correct": correct})
                if correct:
                    await broadcast_state()
                    await check_win()

            elif mtype == "sabotage" and game.state == "playing" and player.alive and player.role == "traitor":
                if player.round_choice is not None:
                    await send_player(player.id, {"type": "error", "message": "You already used your action this round"})
                else:
                    kind = msg.get("kind", "Power Failure")
                    player.round_choice = "tool"
                    game.sabotage_active = {"kind": kind, "ends_at": game.round_ends_at}
                    await broadcast_state()

            elif mtype == "kill" and game.state == "playing" and player.alive and player.role == "traitor":
                if player.round_choice is not None:
                    await send_player(player.id, {"type": "error", "message": "You already used your action this round"})
                else:
                    target = game.players.get(msg.get("target_id"))
                    if target and target.alive and target.role == "engineer" and target.room == player.room:
                        target.alive = False
                        player.round_choice = "tool"
                        await broadcast_state()
                        await check_win()
                    else:
                        await send_player(player.id, {"type": "error", "message": "That player cannot be killed"})

            elif mtype == "vote" and game.state == "voting" and player.alive:
                if not game.voting:
                    continue
                target_id = msg.get("target_id")
                target = game.players.get(target_id)
                if not target or not target.alive:
                    await send_player(player.id, {"type": "error", "message": "Choose an active player"})
                    continue
                game.voting["votes"][player.id] = target_id
                await broadcast({
                    "type": "vote_cast",
                    "voter": player.id,
                    "num_votes": len(game.voting["votes"]),
                    "num_alive": len(game.alive_players()),
                })
                if len(game.voting["votes"]) >= len(game.alive_players()):
                    await resolve_votes()

            elif mtype == "restart":
                await send_player(player.id, {"type": "error", "message": "Only the host can restart the game"})

    except WebSocketDisconnect:
        if is_host_connection:
            if game.host_ws is ws:
                game.host_ws = None
                game.host_connected = False
            return

        if player and player.id in game.players:
            del game.players[player.id]
            if game.state == "lobby":
                await broadcast_lobby()
            else:
                await broadcast_state()


def name_from_host(current: str, fallback: str) -> str:
    return current or fallback


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)