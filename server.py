"""
Traitor Engineer - prototype server

Game flow:

Lobby
    ↓
Role assignment
    ↓
15-second round
    ↓
Question + traitor tools
    ↓
Automatic voting
    ↓
One player eliminated
    ↓
Next 15-second round
    ↓
Automatic voting
    ↓
...

Run with:

    python server.py
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
# GAME CONFIGURATION
# ---------------------------------------------------------------------------

ROOMS = [
    "Reactor Core",
    "Workshop",
    "Admin / Security",
    "Electrical Bay",
    "Storage",
    "Assembly Line",
]

VENT_PAIRS = [
    ("Electrical Bay", "Storage")
]

TASKS_PER_ENGINEER = 3

ROUND_SECONDS = 15
VOTING_SECONDS = 20

TASK_TYPES = [
    "logic_gate",
    "binary_decode"
]


def traitor_count(num_players: int) -> int:
    """
    Minimum 1 traitor.
    Roughly 1 traitor per 5 players.
    """

    if num_players < 5:
        return 1

    return max(1, num_players // 5)


# ---------------------------------------------------------------------------
# TASK GENERATION
# ---------------------------------------------------------------------------

def make_task(task_id: int) -> dict:

    kind = random.choice(TASK_TYPES)

    if kind == "logic_gate":

        gate = random.choice([
            "AND",
            "OR",
            "XOR"
        ])

        a = random.randint(0, 1)
        b = random.randint(0, 1)

        answer = {
            "AND": a & b,
            "OR": a | b,
            "XOR": a ^ b
        }[gate]

        return {
            "id": task_id,
            "type": "logic_gate",
            "prompt": f"{a} {gate} {b} = ?",
            "options": [0, 1],
            "answer": answer,
            "done": False
        }

    else:

        n = random.randint(1, 31)

        options = sorted({
            n,
            n + 1,
            max(0, n - 1),
            n + 4
        })[:4]

        return {
            "id": task_id,
            "type": "binary_decode",
            "prompt": (
                f"Decode binary "
                f"{format(n, '05b')} to decimal"
            ),
            "options": options,
            "answer": n,
            "done": False
        }


def public_question(q: dict) -> dict:
    """
    Remove the answer before sending the question
    to players.
    """

    return {
        k: v
        for k, v in q.items()
        if k != "answer"
    }


# ---------------------------------------------------------------------------
# PLAYER
# ---------------------------------------------------------------------------

class Player:

    def __init__(
        self,
        pid: str,
        ws: WebSocket,
        name: str
    ):

        self.id = pid
        self.ws = ws
        self.name = name

        self.role = "engineer"

        self.alive = True

        self.room = "Admin / Security"

        self.host = False

        # None:
        #     Player has not selected an action.
        #
        # question:
        #     Player chose to answer the question.
        #
        # tool:
        #     Traitor used a tool.
        self.round_choice: Optional[str] = None

        self.answered_this_round = False

        # Number of rounds where this player failed
        # to solve the question.
        self.unsolved_questions = 0

    def public(self):

        return {
            "id": self.id,
            "name": self.name,
            "alive": self.alive,
            "room": self.room,
            "host": self.host,
            "unsolved_questions": self.unsolved_questions
        }


# ---------------------------------------------------------------------------
# GAME
# ---------------------------------------------------------------------------

class Game:

    def __init__(self):
        self.reset()

    def reset(self):

        self.players: dict[str, Player] = {}

        # lobby | playing | voting | ended
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

    def alive_players(self):

        return [
            p
            for p in self.players.values()
            if p.alive
        ]

    def alive_engineers(self):

        return [
            p
            for p in self.alive_players()
            if p.role == "engineer"
        ]

    def alive_traitors(self):

        return [
            p
            for p in self.alive_players()
            if p.role == "traitor"
        ]


game = Game()


# ---------------------------------------------------------------------------
# ID
# ---------------------------------------------------------------------------

def gen_id():

    return "".join(
        random.choices(
            string.ascii_lowercase + string.digits,
            k=8
        )
    )


# ---------------------------------------------------------------------------
# BROADCASTING
# ---------------------------------------------------------------------------

async def broadcast(
    msg: dict,
    exclude: Optional[str] = None
):

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

        game.players.pop(pid, None)


async def send(pid: str, msg: dict):

    p = game.players.get(pid)

    if not p:
        return

    try:

        await p.ws.send_text(
            json.dumps(msg)
        )

    except Exception:
        pass


def lobby_state():

    return {
        "type": "lobby_update",
        "players": [
            p.public()
            for p in game.players.values()
        ]
    }


async def broadcast_lobby():

    await broadcast(lobby_state())


def public_state():

    return {
        "type": "state_update",

        "state": game.state,

        "players": [
            p.public()
            for p in game.players.values()
        ],

        "rooms": ROOMS,

        "sabotage_active":
            game.sabotage_active,

        "round":
            game.round_number,

        "round_question":
            (
                public_question(game.round_question)
                if game.round_question
                else None
            ),

        "round_ends_at":
            game.round_ends_at,

        "total_correct":
            game.total_correct,

        "target_correct":
            game.target_correct
    }


async def broadcast_state():

    await broadcast(public_state())


# ---------------------------------------------------------------------------
# START GAME
# ---------------------------------------------------------------------------

async def start_game():

    players = list(
        game.players.values()
    )

    n_traitors = traitor_count(
        len(players)
    )

    traitors = random.sample(
        players,
        n_traitors
    )

    traitor_ids = {
        p.id
        for p in traitors
    }

    for p in players:

        p.alive = True

        p.room = "Admin / Security"

        p.role = (
            "traitor"
            if p.id in traitor_ids
            else "engineer"
        )

        p.round_choice = None

        p.answered_this_round = False

        p.unsolved_questions = 0

    n_engineers = (
        len(players) - n_traitors
    )

    game.state = "playing"

    game.sabotage_active = None

    game.voting = None

    game.winner = None

    game.round_number = 0

    game.total_correct = 0

    game.target_correct = (
        TASKS_PER_ENGINEER *
        max(1, n_engineers)
    )

    for p in players:

        await send(
            p.id,
            {
                "type": "game_started",
                "your_role": p.role,
                "rooms": ROOMS,
                "vent_pairs": (
                    VENT_PAIRS
                    if p.role == "traitor"
                    else []
                )
            }
        )

    await start_round()


# ---------------------------------------------------------------------------
# START ROUND
# ---------------------------------------------------------------------------

async def start_round():

    if game.state == "ended":
        return

    game.state = "playing"

    game.round_number += 1

    game.round_question = make_task(
        game.next_task_id
    )

    game.next_task_id += 1

    game.round_ends_at = (
        time.time() + ROUND_SECONDS
    )

    # Sabotage only lasts for this round.
    game.sabotage_active = None

    game.voting = None

    for p in game.players.values():

        p.round_choice = None

        p.answered_this_round = False

    await broadcast(
        {
            "type": "round_started",

            "round":
                game.round_number,

            "question":
                public_question(
                    game.round_question
                ),

            "seconds":
                ROUND_SECONDS,

            "round_ends_at":
                game.round_ends_at
        }
    )

    await broadcast_state()

    asyncio.create_task(
        round_timer(
            game.round_number
        )
    )


# ---------------------------------------------------------------------------
# ROUND TIMER
# ---------------------------------------------------------------------------

async def round_timer(round_no: int):

    await asyncio.sleep(
        ROUND_SECONDS
    )

    if game.round_number != round_no:
        return

    if game.state != "playing":
        return

    # Every alive player who did not solve
    # the current question gets one unsolved question.
    for p in game.alive_players():

        if not p.answered_this_round:

            p.unsolved_questions += 1

    await broadcast_state()

    # Automatically go to voting.
    await start_voting()


# ---------------------------------------------------------------------------
# WIN CHECK
# ---------------------------------------------------------------------------

async def check_win():

    if game.state == "ended":
        return

    engineers = game.alive_engineers()

    traitors = game.alive_traitors()

    if (
        game.target_correct > 0
        and game.total_correct >= game.target_correct
    ):

        await end_game(
            "engineers",
            "All project tasks completed"
        )

        return

    if len(traitors) == 0:

        await end_game(
            "engineers",
            "All traitors eliminated"
        )

        return

    if len(traitors) >= len(engineers):

        await end_game(
            "traitors",
            "Traitors equal or outnumber engineers"
        )


# ---------------------------------------------------------------------------
# GAME OVER
# ---------------------------------------------------------------------------

async def end_game(
    winner: str,
    reason: str
):

    game.state = "ended"

    game.winner = winner

    await broadcast(
        {
            "type": "game_over",

            "winner":
                winner,

            "reason":
                reason,

            "roles": {
                p.id: p.role
                for p in game.players.values()
            }
        }
    )


# ---------------------------------------------------------------------------
# START VOTING
# ---------------------------------------------------------------------------

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
        "ends_at":
            time.time() + VOTING_SECONDS
    }

    await broadcast(
        {
            "type": "voting_started",

            "voting_seconds":
                VOTING_SECONDS,

            "voting_ends_at":
                game.voting["ends_at"]
        }
    )

    await broadcast_state()

    asyncio.create_task(
        voting_timer(
            game.voting
        )
    )


# ---------------------------------------------------------------------------
# VOTING TIMER
# ---------------------------------------------------------------------------

async def voting_timer(voting_ref):

    await asyncio.sleep(
        VOTING_SECONDS
    )

    if (
        game.voting is not voting_ref
        or game.state != "voting"
    ):
        return

    await resolve_votes()


# ---------------------------------------------------------------------------
# RESOLVE VOTES
# ---------------------------------------------------------------------------

async def resolve_votes():

    voting = game.voting

    if not voting:
        return

    tally: dict[str, int] = {}

    for target in voting["votes"].values():

        tally[target] = (
            tally.get(target, 0) + 1
        )

    eliminated = None

    # Ignore skip when choosing who has the most votes.
    non_skip_tally = {
        target: count
        for target, count in tally.items()
        if target != "skip"
    }

    if non_skip_tally:

        top = max(
            non_skip_tally.values()
        )

        top_targets = [
            target
            for target, count
            in non_skip_tally.items()
            if count == top
        ]

        # If there is a tie, randomly choose one
        # of the tied players so that one player
        # is removed.
        eliminated = random.choice(
            top_targets
        )

    eliminated_role = None

    if eliminated:

        target = game.players.get(
            eliminated
        )

        if target and target.alive:

            target.alive = False

            eliminated_role = target.role

    await broadcast(
        {
            "type": "voting_result",

            "tally":
                tally,

            "eliminated":
                eliminated,

            "eliminated_role":
                eliminated_role
        }
    )

    game.voting = None

    game.state = "playing"

    await broadcast_state()

    await check_win()

    # Automatically begin the next round.
    if game.state == "playing":

        await start_round()


# ---------------------------------------------------------------------------
# WEBSOCKET
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


            # ------------------------------------------------------------
            # JOIN
            # ------------------------------------------------------------

            if mtype == "join":

                name = (
                    msg.get("name")
                    or "Player"
                )[:16]

                player = Player(
                    pid,
                    ws,
                    name
                )

                if not game.players:

                    player.host = True

                game.players[pid] = player

                await send(
                    pid,
                    {
                        "type": "joined",
                        "your_id": pid,
                        "is_host": player.host
                    }
                )

                await broadcast_lobby()

                continue


            if player is None:

                continue


            # ------------------------------------------------------------
            # START GAME
            # ------------------------------------------------------------

            if (
                mtype == "start_game"
                and player.host
                and game.state == "lobby"
            ):

                if len(game.players) < 4:

                    await send(
                        pid,
                        {
                            "type": "error",
                            "message":
                                "Need at least 4 players"
                        }
                    )

                else:

                    await start_game()


            # ------------------------------------------------------------
            # MOVE ROOM
            # ------------------------------------------------------------

            elif (
                mtype == "move_room"
                and game.state == "playing"
                and player.alive
            ):

                room = msg.get("room")

                if room in ROOMS:

                    player.room = room

                    await broadcast_state()


            # ------------------------------------------------------------
            # VENT
            # ------------------------------------------------------------

            elif (
                mtype == "vent"
                and game.state == "playing"
                and player.alive
                and player.role == "traitor"
            ):

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


            # ------------------------------------------------------------
            # ANSWER QUESTION
            # ------------------------------------------------------------

            elif (
                mtype == "do_task"
                and game.state == "playing"
                and player.alive
            ):

                q = game.round_question

                if not q:
                    continue

                if msg.get("task_id") != q["id"]:
                    continue


                # --------------------------------------------------------
                # ENGINEER
                # --------------------------------------------------------

                if player.role == "engineer":

                    if game.sabotage_active:

                        await send(
                            pid,
                            {
                                "type": "task_result",
                                "task_id": q["id"],
                                "correct": False,
                                "blocked": True
                            }
                        )

                        continue

                    if player.answered_this_round:
                        continue

                    answer = msg.get("answer")

                    correct = (
                        answer == q["answer"]
                    )

                    if correct:

                        player.answered_this_round = True

                        game.total_correct += 1

                    await send(
                        pid,
                        {
                            "type": "task_result",
                            "task_id": q["id"],
                            "correct": correct
                        }
                    )

                    if correct:

                        await broadcast_state()

                        await check_win()


                # --------------------------------------------------------
                # TRAITOR
                # --------------------------------------------------------

                elif player.role == "traitor":

                    # A traitor can either answer the question
                    # OR use one traitor tool in the round.

                    if player.round_choice == "tool":

                        await send(
                            pid,
                            {
                                "type": "error",
                                "message":
                                    "You already used your traitor tool this round"
                            }
                        )

                        continue

                    if player.answered_this_round:
                        continue

                    player.round_choice = "question"

                    answer = msg.get("answer")

                    correct = (
                        answer == q["answer"]
                    )

                    if correct:

                        player.answered_this_round = True

                        game.total_correct += 1

                    await send(
                        pid,
                        {
                            "type": "task_result",
                            "task_id": q["id"],
                            "correct": correct
                        }
                    )

                    if correct:

                        await broadcast_state()

                        await check_win()


            # ------------------------------------------------------------
            # SABOTAGE
            # ------------------------------------------------------------

            elif (
                mtype == "sabotage"
                and game.state == "playing"
                and player.alive
                and player.role == "traitor"
            ):

                if player.round_choice is not None:

                    await send(
                        pid,
                        {
                            "type": "error",
                            "message":
                                "You already used your action this round"
                        }
                    )

                else:

                    kind = msg.get(
                        "kind",
                        "Power Failure"
                    )

                    player.round_choice = "tool"

                    game.sabotage_active = {
                        "kind": kind,
                        "ends_at":
                            game.round_ends_at
                    }

                    await broadcast_state()


            # ------------------------------------------------------------
            # KILL
            # ------------------------------------------------------------

            elif (
                mtype == "kill"
                and game.state == "playing"
                and player.alive
                and player.role == "traitor"
            ):

                if player.round_choice is not None:

                    await send(
                        pid,
                        {
                            "type": "error",
                            "message":
                                "You already used your action this round"
                        }
                    )

                else:

                    target_id = msg.get(
                        "target_id"
                    )

                    target = game.players.get(
                        target_id
                    )

                    if (
                        target
                        and target.alive
                        and target.role == "engineer"
                        and target.room == player.room
                    ):

                        target.alive = False

                        player.round_choice = "tool"

                        await broadcast_state()

                        await check_win()


            # ------------------------------------------------------------
            # VOTE
            # ------------------------------------------------------------

            elif (
                mtype == "vote"
                and game.state == "voting"
                and player.alive
            ):

                if not game.voting:
                    continue

                target_id = msg.get(
                    "target_id",
                    "skip"
                )

                # Validate selected player.
                if target_id != "skip":

                    target = game.players.get(
                        target_id
                    )

                    if not target or not target.alive:

                        await send(
                            pid,
                            {
                                "type": "error",
                                "message":
                                    "That player is no longer available"
                            }
                        )

                        continue

                # One vote per player.
                game.voting["votes"][pid] = target_id

                await broadcast(
                    {
                        "type": "vote_cast",

                        "voter":
                            pid,

                        "num_votes":
                            len(
                                game.voting["votes"]
                            ),

                        "num_alive":
                            len(
                                game.alive_players()
                            )
                    }
                )

                # If everyone alive has voted,
                # resolve immediately.
                if (
                    len(game.voting["votes"])
                    >= len(game.alive_players())
                ):

                    await resolve_votes()


            # ------------------------------------------------------------
            # RESTART
            # ------------------------------------------------------------

            elif (
                mtype == "restart"
                and player.host
            ):

                game.reset()

                game.players[pid] = player

                player.host = True

                player.role = "engineer"

                player.alive = True

                player.room = "Admin / Security"

                player.round_choice = None

                player.answered_this_round = False

                player.unsolved_questions = 0

                await send(
                    pid,
                    {
                        "type": "joined",
                        "your_id": pid,
                        "is_host": True
                    }
                )

                await broadcast_lobby()


    except WebSocketDisconnect:

        if pid in game.players:

            was_host = game.players[pid].host

            del game.players[pid]

            # Transfer host status if host disconnects.
            if was_host and game.players:

                next_host = next(
                    iter(
                        game.players.values()
                    )
                )

                next_host.host = True

            if game.state == "lobby":

                await broadcast_lobby()

            else:

                await broadcast_state()


# ---------------------------------------------------------------------------
# STATIC FILES
# ---------------------------------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)


@app.get("/")
async def index():

    return FileResponse(
        "static/index.html"
    )


# ---------------------------------------------------------------------------
# RUN
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )