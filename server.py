"""
Traitor Engineer - prototype server
------------------------------------
Lobby -> role assignment -> one-question-at-a-time tasks (10s timer)
-> sabotage/kill -> meetings -> voting -> win check.

Run with:  python server.py
Then open http://localhost:8000 in a browser tab per player.
"""

import asyncio
import json
import os
import random
import string
import time
from typing import Optional
import colorsys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()

ROOMS = [
    "Reactor Core",
    "Workshop",
    "Admin / Security",
    "Electrical Bay",
    "Storage",
    "Assembly Line",
]

VENT_PAIRS = [("Electrical Bay", "Storage")]

ROOM_POSITIONS = {
    "Reactor Core":      {"x": 0, "y": 0},
    "Workshop":          {"x": 2, "y": 0},
    "Admin / Security":  {"x": 0, "y": 1},
    "Electrical Bay":    {"x": 2, "y": 1},
    "Assembly Line":     {"x": 0, "y": 2},
    "Storage":           {"x": 2, "y": 2},
}

ADJACENT_PAIRS = [
    ("Reactor Core",     "Workshop"),
    ("Reactor Core",     "Admin / Security"),
    ("Workshop",         "Electrical Bay"),
    ("Admin / Security", "Electrical Bay"),
    ("Admin / Security", "Assembly Line"),
    ("Electrical Bay",   "Storage"),
    ("Assembly Line",    "Storage"),
]

def make_palette(n: int = 48) -> list:
    """Evenly spaced colours using the golden angle for max distinctness."""
    colors = []
    for i in range(n):
        hue = (i * 0.618033988749895) % 1.0
        r, g, b = colorsys.hls_to_rgb(hue, 0.55, 0.85)
        colors.append("#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return colors


PLAYER_COLORS = make_palette()

HOST_PASSWORD = "1234"
QUESTIONS_PER_ENGINEER = 3
QUESTION_SECONDS = 10
QUESTION_AFTER_ANSWER_DELAY = 0.0
DISCUSSION_SECONDS = int(os.environ.get("SILICA_DISCUSSION_SECONDS", "45"))
VOTING_SECONDS = int(os.environ.get("SILICA_VOTING_SECONDS", "20"))
SABOTAGE_COOLDOWN = 25
SABOTAGE_DURATION = 15
KILL_COOLDOWN = 20
HOST_PAUSE_SECONDS = int(os.environ.get("SILICA_HOST_PAUSE_SECONDS", "60"))

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
        prompt = f"{a} {gate} {b} = ?"
        options = [0, 1]
    else:
        n = random.randint(1, 31)
        prompt = f"Decode binary {format(n, '05b')} to decimal"
        options = sorted({n, n + 1, max(0, n - 1), n + 4})[:4]
        answer = n
    return {
        "id": task_id,
        "type": kind,
        "prompt": prompt,
        "options": options,
        "answer": answer,
    }


def public_task(task: dict) -> dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "prompt": task["prompt"],
        "options": task["options"],
    }


class Player:
    def __init__(self, pid: str, ws: WebSocket, name: str, color: str, client_id: str = ""):
        self.id = pid
        self.ws = ws
        self.name = name
        self.color = color
        self.client_id = client_id
        self.role = "engineer"
        self.alive = True
        self.room = "Admin / Security"
        self.host = False
        self.target_questions = 0
        self.stats = {"attempted": 0, "correct": 0, "incorrect": 0}
        self.current_question: Optional[dict] = None
        self.question_deadline: float = 0.0
        self.question_timeout_task: Optional[asyncio.Task] = None
        self.mock_question: Optional[dict] = None
        self.mock_deadline: float = 0.0
        self.mock_timeout_task: Optional[asyncio.Task] = None
        self.question_id_seq: int = 0
        self.last_kill_time = 0.0

    def public(self):
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "alive": self.alive,
            "room": self.room,
            "host": self.host,
        }

    def host_view(self):
        return {
            **self.public(),
            "role": self.role,
            "target": self.target_questions,
            "stats": self.stats,
            "has_question": self.current_question is not None,
        }


class Game:
    def __init__(self):
        self.reset()

    def reset(self, keep_host: bool = False):
        existing = list(self.players.values()) if hasattr(self, "players") else []
        existing += [self.host] if getattr(self, "host", None) else []
        for p in existing:
            if p and p.question_timeout_task and not p.question_timeout_task.done():
                p.question_timeout_task.cancel()
            if p and p.mock_timeout_task and not p.mock_timeout_task.done():
                p.mock_timeout_task.cancel()
        host_timeout = getattr(self, "host_timeout_task", None)
        if host_timeout and not host_timeout.done():
            host_timeout.cancel()
        host = self.host if keep_host else None
        self.players: dict[str, Player] = {}
        self.host = None
        self.state = "lobby"
        self.sabotage_active: Optional[dict] = None
        self.last_sabotage_time = 0.0
        self.meeting: Optional[dict] = None
        self.winner: Optional[str] = None
        self.next_task_id = 1
        self.recent_leavers: list = getattr(self, "recent_leavers", [])
        self.disconnected_players: dict[str, Player] = {}
        self.retired_host_cid: Optional[str] = None
        self.host_pending: bool = False
        self.host_deadline: Optional[float] = None
        self.host_timeout_task: Optional[asyncio.Task] = None
        self.host = host

    def find_by_client_id(self, client_id: str):
        for p in self.players.values():
            if p.client_id and p.client_id == client_id:
                return p
        return self.disconnected_players.get(client_id)

    def alive_players(self):
        return [p for p in self.players.values() if p.alive]

    def alive_engineers(self):
        return [p for p in self.alive_players() if p.role == "engineer"]

    def alive_traitors(self):
        return [p for p in self.alive_players() if p.role == "traitor"]


game = Game()


def gen_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


async def broadcast(msg: dict, exclude: Optional[str] = None):
    dead = []
    payload = json.dumps(msg)
    for pid, p in list(game.players.items()):
        if pid == exclude:
            continue
        try:
            await p.ws.send_text(payload)
        except Exception:
            dead.append(pid)
    for pid in dead:
        p = game.players.pop(pid, None)
        if p:
            await cancel_question(p)
            await cancel_mock_question(p)
            if p.client_id:
                game.disconnected_players[p.client_id] = p
            game.recent_leavers.append({"client_id": p.client_id, "name": p.name, "color": p.color, "left_at": time.time()})
            print(f"[SERVER] send failed -> dropped {p.name}", flush=True)


async def send(pid: str, msg: dict):
    p = game.players.get(pid) or (game.host if game.host and game.host.id == pid else None)
    if not p:
        return
    try:
        await p.ws.send_text(json.dumps(msg))
    except Exception:
        if game.host and game.host.id == p.id:
            game.recent_leavers.append({"client_id": p.client_id, "name": p.name, "color": p.color, "left_at": time.time()})
            print(f"[SERVER] send failed -> host dropped {p.name}", flush=True)
            game.host = None
            return
        if p.id in game.players:
            game.players.pop(p.id)
            await cancel_question(p)
            await cancel_mock_question(p)
            if p.client_id:
                game.disconnected_players[p.client_id] = p
            game.recent_leavers.append({"client_id": p.client_id, "name": p.name, "color": p.color, "left_at": time.time()})
            print(f"[SERVER] send failed -> dropped {p.name}", flush=True)


def recent_leavers():
    now = time.time()
    keep = [l for l in game.recent_leavers if now - l["left_at"] < 30]
    connected = {p.client_id for p in game.players.values() if p.client_id}
    if game.host and game.host.client_id:
        connected.add(game.host.client_id)
    keep = [l for l in keep if l.get("client_id") not in connected]
    game.recent_leavers = keep
    return keep


def lobby_state():
    return {
        "type": "lobby_update",
        "players": [p.public() for p in game.players.values()],
        "host_present": game.host is not None,
        "ready": len(game.players),
    }


async def broadcast_lobby():
    state = lobby_state()
    await broadcast(state)
    if game.host and game.host.id:
        await send(game.host.id, state)
        await send(game.host.id, host_state())


def public_state():
    return {
        "type": "state_update",
        "state": game.state,
        "players": [p.public() for p in game.players.values()],
        "rooms": ROOMS,
        "map_layout": ROOM_POSITIONS,
        "adjacent": ADJACENT_PAIRS,
        "sabotage_active": game.sabotage_active,
        "host_pending": game.host_pending,
        "host_deadline": game.host_deadline,
    }


async def broadcast_state():
    await broadcast(public_state())
    await broadcast_host_state()


async def broadcast_host_state():
    if not game.host:
        return
    await send(game.host.id, host_state())


def host_state():
    return {
        "type": "host_state",
        "state": game.state,
        "winner": game.winner,
        "sabotage_active": game.sabotage_active,
        "map_layout": ROOM_POSITIONS,
        "adjacent": ADJACENT_PAIRS,
        "players": [p.host_view() for p in game.players.values()],
        "disconnected": recent_leavers(),
        "host_pending": game.host_pending,
        "host_deadline": game.host_deadline,
    }


async def resend_game_state(player: Player):
    await send(player.id, {
        "type": "game_started",
        "your_id": player.id,
        "your_role": player.role,
        "your_color": player.color,
        "your_target": player.target_questions,
        "rooms": ROOMS,
        "map_layout": ROOM_POSITIONS,
        "adjacent": ADJACENT_PAIRS,
        "vent_pairs": VENT_PAIRS if player.role == "traitor" else [],
    })
    await send(player.id, public_state())
    if player.role == "engineer" and player.alive:
        await issue_question(player)
    elif player.role == "traitor" and player.alive:
        await issue_mock_question(player)


async def cancel_question(player: Player):
    if player.question_timeout_task and not player.question_timeout_task.done():
        player.question_timeout_task.cancel()
    player.question_timeout_task = None
    player.current_question = None
    player.question_deadline = 0.0


async def issue_question(player: Player):
    if not player.alive or game.state != "playing":
        return
    if game.host_pending:
        return
    if player.role != "engineer":
        return
    await cancel_question(player)
    task = make_task(game.next_task_id)
    game.next_task_id += 1
    player.current_question = task
    player.question_deadline = time.time() + QUESTION_SECONDS
    await send(player.id, {
        "type": "question",
        "question": public_task(task),
        "deadline": player.question_deadline,
        "seconds": QUESTION_SECONDS,
    })
    player.question_timeout_task = asyncio.create_task(
        question_timeout(player, task["id"], player.question_deadline)
    )


async def question_timeout(player: Player, qid: int, deadline: float):
    try:
        await asyncio.sleep(max(0.0, deadline - time.time()))
    except asyncio.CancelledError:
        return
    if game.state != "playing":
        return
    if not player.alive:
        return
    if player.current_question is None or player.current_question["id"] != qid:
        return
    player.stats["attempted"] += 1
    player.stats["incorrect"] += 1
    await send(player.id, {
        "type": "task_result",
        "task_id": qid,
        "correct": False,
        "reason": "timeout",
    })
    player.current_question = None
    player.question_deadline = 0.0
    player.question_timeout_task = None
    await broadcast_state()
    await issue_question(player)


async def cancel_mock_question(player: Player):
    if player.mock_timeout_task and not player.mock_timeout_task.done():
        player.mock_timeout_task.cancel()
    player.mock_timeout_task = None
    player.mock_question = None
    player.mock_deadline = 0.0


async def issue_mock_question(player: Player):
    if not player.alive or game.state != "playing":
        return
    if game.host_pending:
        return
    if player.role != "traitor":
        return
    await cancel_mock_question(player)
    task = make_task(game.next_task_id)
    game.next_task_id += 1
    player.mock_question = task
    player.mock_deadline = time.time() + QUESTION_SECONDS
    await send(player.id, {
        "type": "question",
        "question": public_task(task),
        "deadline": player.mock_deadline,
        "seconds": QUESTION_SECONDS,
    })
    player.mock_timeout_task = asyncio.create_task(
        mock_question_timeout(player, task["id"], player.mock_deadline)
    )


async def mock_question_timeout(player: Player, qid: int, deadline: float):
    try:
        await asyncio.sleep(max(0.0, deadline - time.time()))
    except asyncio.CancelledError:
        return
    if game.state != "playing":
        return
    if not player.alive:
        return
    if game.host_pending:
        return
    if player.mock_question is None or player.mock_question["id"] != qid:
        return
    await issue_mock_question(player)


async def start_game():
    players = list(game.players.values())
    n_traitors = traitor_count(len(players))
    traitors = random.sample(players, n_traitors)
    traitor_ids = {p.id for p in traitors}

    for p in players:
        p.alive = True
        p.room = "Admin / Security"
        p.role = "traitor" if p.id in traitor_ids else "engineer"
        p.target_questions = QUESTIONS_PER_ENGINEER if p.role == "engineer" else 0
        p.stats = {"attempted": 0, "correct": 0, "incorrect": 0}
        p.last_kill_time = 0.0

    game.state = "playing"
    game.sabotage_active = None
    game.meeting = None
    game.winner = None

    for p in players:
        await send(p.id, {
            "type": "game_started",
            "your_id": p.id,
            "your_role": p.role,
            "your_color": p.color,
            "your_target": p.target_questions,
            "rooms": ROOMS,
            "map_layout": ROOM_POSITIONS,
            "adjacent": ADJACENT_PAIRS,
            "vent_pairs": VENT_PAIRS if p.role == "traitor" else [],
        })

    await broadcast_state()

    for p in players:
        if p.role == "engineer":
            await issue_question(p)
        else:
            await issue_mock_question(p)


async def check_win():
    if game.state != "playing":
        return
    engineers = game.alive_engineers()
    traitors = game.alive_traitors()

    if engineers and all(p.stats["correct"] >= p.target_questions for p in engineers):
        await end_game("engineers", "All engineers completed their questions")
    elif len(traitors) == 0:
        await end_game("engineers", "All traitors eliminated")
    elif len(traitors) >= len(engineers):
        await end_game("traitors", "Traitors equal or outnumber engineers")


async def end_game(winner: str, reason: str):
    for p in list(game.players.values()):
        await cancel_question(p)
        await cancel_mock_question(p)
    game.state = "ended"
    game.winner = winner
    payload = {
        "type": "game_over",
        "winner": winner,
        "reason": reason,
        "roles": {p.id: p.role for p in list(game.players.values())},
    }
    await broadcast(payload)
    await broadcast_host_state()


async def start_meeting(reason: str, caller_id: str):
    for p in list(game.players.values()):
        await cancel_question(p)
        await cancel_mock_question(p)
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
    remaining = m["ends_at"] - time.time()
    while remaining > 0 and game.meeting is m and game.state == "meeting":
        if game.host_pending:
            await asyncio.sleep(0.5)
            continue
        await asyncio.sleep(min(1.0, remaining))
        remaining = m["ends_at"] - time.time()
    if game.meeting is not m or game.state != "meeting":
        return
    m["phase"] = "voting"
    m["ends_at"] = time.time() + VOTING_SECONDS
    await broadcast({"type": "voting_started", "voting_seconds": VOTING_SECONDS})
    remaining = m["ends_at"] - time.time()
    while remaining > 0 and game.meeting is m and game.state == "meeting":
        if game.host_pending:
            await asyncio.sleep(0.5)
            continue
        await asyncio.sleep(min(1.0, remaining))
        remaining = m["ends_at"] - time.time()
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
    await resume_questions()
    await check_win()


async def pause_for_host_rejoin():
    if game.host_pending:
        return
    for p in list(game.players.values()):
        await cancel_question(p)
        await cancel_mock_question(p)
    game.host_pending = True
    game.host_deadline = time.time() + HOST_PAUSE_SECONDS
    print(f"[SERVER] host disconnected mid-game -> paused for {HOST_PAUSE_SECONDS}s", flush=True)
    await broadcast({
        "type": "host_disconnected",
        "deadline": game.host_deadline,
        "seconds": HOST_PAUSE_SECONDS,
    })
    await broadcast_state()
    game.host_timeout_task = asyncio.create_task(host_timeout())


async def host_timeout():
    deadline = game.host_deadline or 0.0
    try:
        await asyncio.sleep(max(0.0, deadline - time.time()))
    except asyncio.CancelledError:
        return
    if not game.host_pending or game.host is not None:
        return
    print("[SERVER] host did not rejoin in time -> ending session", flush=True)
    await end_session("Host did not rejoin in time")


async def end_session(reason: str):
    if game.host_timeout_task and not game.host_timeout_task.done():
        game.host_timeout_task.cancel()
    game.host_timeout_task = None
    game.host_pending = False
    game.host_deadline = None
    for p in list(game.players.values()):
        await cancel_question(p)
        await cancel_mock_question(p)
    payload = {"type": "session_ended", "reason": reason}
    await broadcast(payload)
    if game.host:
        await send(game.host.id, payload)
    print(f"[SERVER] session ended: {reason}", flush=True)
    game.reset()


async def resume_questions():
    if game.state != "playing":
        return
    for p in game.players.values():
        if not p.alive:
            continue
        if p.role == "engineer":
            if p.stats["correct"] < p.target_questions:
                await issue_question(p)
        else:
            await issue_mock_question(p)


async def remove_player(pid: str, voluntary: bool = False):
    host_left = False
    if game.host and game.host.id == pid:
        host_left = True
        host = game.host
        game.host = None
        if voluntary:
            game.retired_host_cid = None
            game.disconnected_players.pop(host.client_id, None)
        else:
            game.retired_host_cid = host.client_id
            if host.client_id:
                game.disconnected_players[host.client_id] = host
        game.recent_leavers.append({"client_id": host.client_id, "name": host.name, "color": host.color, "left_at": time.time()})
        print(f"[SERVER] host left: {host.name} (voluntary={voluntary})", flush=True)

    if pid in game.players:
        p = game.players.pop(pid)
        await cancel_question(p)
        await cancel_mock_question(p)
        if voluntary:
            game.disconnected_players.pop(p.client_id, None)
        elif p.client_id:
            game.disconnected_players[p.client_id] = p
        game.recent_leavers.append({"client_id": p.client_id, "name": p.name, "color": p.color, "left_at": time.time()})
        print(f"[SERVER] {p.name} left (voluntary={voluntary}, players={len(game.players)})", flush=True)

    if not game.players:
        if game.host is None:
            game.reset()
        else:
            game.reset(keep_host=True)
        await broadcast_lobby()
        return

    if game.state == "lobby":
        await broadcast_lobby()
        return

    if host_left and game.host is None:
        if voluntary:
            await end_session("The host left the game")
        else:
            await pause_for_host_rejoin()
        return

    await broadcast_state()
    if game.state == "playing":
        await check_win()


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
                client_id = msg.get("client_id") or ""
                name = (msg.get("name") or "Player")[:16]
                color = msg.get("color") if msg.get("color") in PLAYER_COLORS else PLAYER_COLORS[0]
                as_host = bool(msg.get("as_host"))
                existing = game.find_by_client_id(client_id) if client_id else None
                if existing is None and game.host and game.host.client_id and game.host.client_id == client_id:
                    existing = game.host
                is_reconnect = existing is not None

                if game.state != "lobby" and game.players and not is_reconnect:
                    await send_to_ws(ws, {"type": "error", "message": "Game already in progress"})
                    continue
                if game.state != "lobby" and not game.players:
                    game.reset()

                if as_host and is_reconnect and existing.client_id == game.retired_host_cid and game.host is None:
                    if game.host_timeout_task and not game.host_timeout_task.done():
                        game.host_timeout_task.cancel()
                    game.host_timeout_task = None
                    resumed = game.host_pending
                    game.host_pending = False
                    game.host_deadline = None
                    game.retired_host_cid = None
                    game.disconnected_players.pop(client_id, None)
                    player = Player(pid, ws, existing.name, existing.color, client_id)
                    player.host = True
                    player.target_questions = 0
                    game.host = player
                    print(f"[SERVER] host reclaimed, game {'resumed' if resumed else 'back to lobby'} (host_pending={resumed}): {player.name}", flush=True)
                    await send_to_ws(ws, {"type": "joined", "your_id": pid, "is_host": True, "reconnected": True})
                    await broadcast_host_state()
                    if resumed:
                        await broadcast({"type": "host_rejoined"})
                        await broadcast_state()
                        print(f"[SERVER] calling resume_questions, state={game.state}, alive_eng={len([p for p in game.players.values() if p.role=='engineer' and p.alive])}", flush=True)
                        await resume_questions()
                    else:
                        await broadcast_lobby()
                    continue

                if as_host and game.host_pending:
                    await send_to_ws(ws, {"type": "error", "message": "Host disconnected - the game is paused"})
                    continue

                if is_reconnect:
                    if existing is game.host:
                        if not as_host:
                            await send_to_ws(ws, {"type": "error", "message": "Host identity cannot join as a player"})
                            continue
                        player = Player(pid, ws, existing.name, existing.color, client_id)
                        player.host = True
                        player.target_questions = 0
                        game.host = player
                        print(f"[SERVER] host reconnected: {player.name}", flush=True)
                        await send_to_ws(ws, {"type": "joined", "your_id": pid, "is_host": True, "reconnected": True})
                        await broadcast_lobby()
                        continue
                    if not as_host and game.host_pending and existing.client_id == game.retired_host_cid:
                        await send_to_ws(ws, {"type": "error", "message": "Host identity cannot join as a player"})
                        continue
                    await cancel_question(existing)
                    await cancel_mock_question(existing)
                    if existing.id in game.players:
                        del game.players[existing.id]
                    game.disconnected_players.pop(client_id, None)
                    print(f"[SERVER] {existing.name} reconnected (replaced stale entry)", flush=True)
                    player = Player(pid, ws, existing.name, existing.color, client_id)
                    player.role = existing.role
                    player.alive = existing.alive
                    player.room = existing.room
                    player.target_questions = existing.target_questions
                    player.stats = existing.stats
                    player.last_kill_time = existing.last_kill_time
                    game.players[pid] = player
                    await send_to_ws(ws, {"type": "joined", "your_id": pid, "is_host": False, "reconnected": True})
                    if game.state in ("lobby", "ended"):
                        await broadcast_lobby()
                    else:
                        await resend_game_state(player)
                        await broadcast_host_state()
                    continue

                taken_names = {p.name.lower() for p in game.players.values()}
                taken_colors = {p.color for p in game.players.values()}
                if not as_host:
                    if name.lower() in taken_names:
                        await send_to_ws(ws, {"type": "error", "message": "That name is already taken"})
                        continue
                    if color in taken_colors:
                        await send_to_ws(ws, {"type": "error", "message": "That colour is already taken"})
                        continue
                if as_host:
                    if game.host_pending:
                        await send_to_ws(ws, {"type": "error", "message": "Host disconnected - the game is paused"})
                        continue
                    if game.host is not None:
                        await send_to_ws(ws, {"type": "error", "message": "A host already exists"})
                        continue
                    if msg.get("password") != HOST_PASSWORD:
                        await send_to_ws(ws, {"type": "error", "message": "Wrong host password"})
                        continue
                    if not game.players:
                        game.recent_leavers = []
                    game.retired_host_cid = None
                    player = Player(pid, ws, name, color, client_id)
                    player.host = True
                    player.target_questions = 0
                    game.host = player
                    print(f"[SERVER] host joined: {name}", flush=True)
                    await send_to_ws(ws, {"type": "joined", "your_id": pid, "is_host": True})
                    await broadcast_lobby()
                    continue
                player = Player(pid, ws, name, color, client_id)
                game.players[pid] = player
                print(f"[SERVER] {name} joined (players={len(game.players)})", flush=True)
                await send_to_ws(ws, {"type": "joined", "your_id": pid, "is_host": False})
                await broadcast_lobby()
                continue

            if player is None:
                continue

            if mtype == "start_game":
                if not player.host:
                    continue
                if game.state != "lobby":
                    continue
                if len(game.players) < 4:
                    await send_to_ws(ws, {"type": "error", "message": "Need at least 4 players"})
                else:
                    await start_game()

            elif mtype == "move_room" and game.state == "playing" and player.alive and not game.host_pending:
                room = msg.get("room")
                if room in ROOMS:
                    player.room = room
                    await broadcast_state()
                    if player.role == "engineer":
                        await issue_question(player)
                    else:
                        await issue_mock_question(player)

            elif mtype == "vent" and game.state == "playing" and player.alive and player.role == "traitor" and not game.host_pending:
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

            elif mtype == "do_task" and game.state == "playing" and player.alive and not game.host_pending:
                if player.current_question is None:
                    continue
                task_id = msg.get("task_id")
                if task_id != player.current_question["id"]:
                    continue
                if time.time() > player.question_deadline:
                    continue
                answer = msg.get("answer")
                correct = answer == player.current_question["answer"] and not game.sabotage_active
                player.stats["attempted"] += 1
                if correct:
                    player.stats["correct"] += 1
                else:
                    player.stats["incorrect"] += 1
                await send_to_ws(ws, {
                    "type": "task_result",
                    "task_id": task_id,
                    "correct": correct,
                    "reason": "sabotage" if game.sabotage_active else ("wrong" if not correct else None),
                })
                await cancel_question(player)
                await broadcast_state()
                if correct:
                    await check_win()
                    if game.state == "playing" and player.alive and player.role == "engineer":
                        if player.stats["correct"] < player.target_questions:
                            await issue_question(player)
                elif game.state == "playing" and player.alive and player.role == "engineer":
                    if player.stats["correct"] < player.target_questions:
                        await issue_question(player)

            elif mtype == "sabotage" and game.state == "playing" and player.alive and player.role == "traitor" and not game.host_pending:
                now = time.time()
                if game.sabotage_active:
                    await send_to_ws(ws, {"type": "error", "message": "A sabotage is already active"})
                elif now - game.last_sabotage_time < SABOTAGE_COOLDOWN:
                    await send_to_ws(ws, {"type": "error", "message": "Sabotage on cooldown"})
                else:
                    kind = msg.get("kind", "Power Failure")
                    game.sabotage_active = {"kind": kind, "ends_at": now + SABOTAGE_DURATION}
                    game.last_sabotage_time = now
                    await broadcast_state()
                    asyncio.create_task(clear_sabotage_after(SABOTAGE_DURATION))

            elif mtype == "kill" and game.state == "playing" and player.alive and player.role == "traitor" and not game.host_pending:
                now = time.time()
                if now - player.last_kill_time < KILL_COOLDOWN:
                    await send_to_ws(ws, {"type": "error", "message": "Kill on cooldown"})
                else:
                    target_id = msg.get("target_id")
                    target = game.players.get(target_id)
                    if target and target.alive and target.role == "engineer" and target.room == player.room:
                        await cancel_question(target)
                        target.alive = False
                        target.alive = False
                        player.last_kill_time = now
                        await broadcast_state()
                        await check_win()

            elif mtype == "report_body" and game.state == "playing" and player.alive and not game.host_pending:
                target_id = msg.get("target_id")
                target = game.players.get(target_id)
                if target and not target.alive:
                    await start_meeting(f"{player.name} reported {target.name}'s body", pid)

            elif mtype == "call_meeting" and game.state == "playing" and player.alive and not game.host_pending:
                await start_meeting(f"{player.name} called an emergency meeting", pid)

            elif mtype == "vote" and game.state == "meeting" and player.alive and not game.host_pending:
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
                old_host = game.host
                game.reset()
                game.host = old_host
                if game.host:
                    await send_to_ws(game.host.ws, {"type": "joined", "your_id": game.host.id, "is_host": True})
                await broadcast_lobby()

            elif mtype == "leave":
                await remove_player(pid, voluntary=True)
                await send_to_ws(ws, {"type": "session_ended", "reason": "You left"})

            elif mtype == "end_game" and player.host:
                if game.state == "lobby":
                    await send_to_ws(ws, {"type": "error", "message": "No game in progress"})
                else:
                    await end_session("The host ended the game")

    except WebSocketDisconnect:
        await remove_player(pid)


async def send_to_ws(ws, msg: dict):
    try:
        await ws.send_text(json.dumps(msg))
    except Exception:
        pass


async def clear_sabotage_after(seconds: float):
    remaining = seconds
    while remaining > 0 and game.sabotage_active:
        if game.host_pending:
            await asyncio.sleep(0.5)
            continue
        await asyncio.sleep(min(1.0, remaining))
        remaining = game.sabotage_active["ends_at"] - time.time()
    if game.sabotage_active and game.sabotage_active["ends_at"] <= time.time() + 0.5:
        game.sabotage_active = None
        await broadcast_state()


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)