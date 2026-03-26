"""WebSocket handler for game rooms."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import WebSocket, WebSocketDisconnect

from server.rooms import Player, Room, RoomState, get_room, remove_room
from server.problems import pick_random
from server.sandbox import run_code
from server.scoring import rank_players
from server.utils import fix_exponents, fix_subscripts

logger = logging.getLogger(__name__)

BREAK_DURATION_SECONDS = 30
MAX_CHAT_LENGTH = 200


def _reset_players(room: Room) -> None:
    """Clear all player submission state in preparation for a new round."""
    for player in room.players.values():
        player.submission = None
        player.best_submission = None
        player.locked_at = None
        player.resigned = False


async def broadcast(room: Room, message: dict) -> None:
    """Send a JSON message to all connected players in a room."""
    disconnected = []
    for name, player in room.players.items():
        if player.websocket:
            try:
                await player.websocket.send_json(message)
            except Exception:
                disconnected.append(name)
    for name in disconnected:
        player = room.players.get(name)
        if player:
            player.websocket = None


def room_state_msg(room: Room) -> dict:
    """Build a room_state message."""
    return {
        "type": "room_state",
        "room_id": room.id,
        "state": room.state.value,
        "host": room.host,
        "players": list(room.players.keys()),
        "time_limit": room.time_limit,
        "difficulty": room.difficulty,
        "current_round": room.current_round,
        "total_rounds": room.total_rounds,
    }


def scoreboard_msg(room: Room) -> dict:
    """Build a scoreboard message."""
    return {
        "type": "scoreboard",
        "rankings": rank_players(room.players),
    }


async def send_error(ws: WebSocket, msg: str) -> None:
    await ws.send_json({"type": "error", "message": msg})


async def timer_task(room: Room) -> None:
    """Background task that ticks every 5s and ends the game when time runs out."""
    start = room.start_time
    limit = room.time_limit

    while room.state == RoomState.PLAYING:
        elapsed = time.time() - start
        remaining = max(0, limit - elapsed)

        await broadcast(
            room,
            {
                "type": "tick",
                "remaining": int(remaining),
                "elapsed": int(elapsed),
            },
        )

        if remaining <= 0:
            await end_game(room)
            return

        await asyncio.sleep(5)


async def end_game(room: Room) -> None:
    """End the current round and either start a break or finish the game."""
    if room.state == RoomState.FINISHED:
        return

    rankings = rank_players(room.players, include_code=True)

    if room.current_round < room.total_rounds:
        # More rounds to play — show round results with a break
        room.state = (
            RoomState.FINISHED
        )  # temporarily, will change to PLAYING on next round
        await broadcast(
            room,
            {
                "type": "round_over",
                "rankings": rankings,
                "current_round": room.current_round,
                "total_rounds": room.total_rounds,
                "break_seconds": BREAK_DURATION_SECONDS,
            },
        )
        asyncio.create_task(break_task(room))
    else:
        # Final round — game is permanently over; record timestamp for GC.
        room.state = RoomState.FINISHED
        room.finished_at = time.time()
        await broadcast(
            room,
            {
                "type": "game_over",
                "rankings": rankings,
            },
        )


async def break_task(room: Room) -> None:
    """Countdown between rounds, then auto-start the next round."""
    for remaining in range(BREAK_DURATION_SECONDS - 1, -1, -1):
        await asyncio.sleep(1)
        if room.state != RoomState.FINISHED:
            return  # room was manually restarted or cleaned up
        await broadcast(
            room,
            {
                "type": "break_tick",
                "remaining": remaining,
            },
        )

    await start_next_round(room)


async def start_next_round(room: Room) -> None:
    """Pick a new problem and start the next round."""
    problem = pick_random(room.difficulty)
    if not problem:
        await broadcast(room, {"type": "error", "message": "No problems available."})
        return

    await _begin_round(room, problem, room.current_round + 1)


async def handle_lock(ws: WebSocket, room: Room, player_name: str) -> None:
    """Handle a player locking in their submission."""
    if room.state != RoomState.PLAYING:
        return

    player = room.players.get(player_name)
    if not player:
        return
    if player.locked_at is not None:
        await send_error(ws, "Already locked in")
        return
    if player.best_submission is None or not player.best_submission.get("solved"):
        await send_error(ws, "Solve the problem before locking in")
        return

    player.locked_at = time.time() - room.start_time

    if player.websocket:
        await player.websocket.send_json({"type": "locked"})

    await broadcast(room, scoreboard_msg(room))

    # If all players are locked in, end the round early
    if all(p.locked_at is not None for p in room.players.values()):
        await end_game(room)


async def handle_resign(ws: WebSocket, room: Room, player_name: str) -> None:
    """Handle a player voting to resign (end the round early)."""
    if room.state != RoomState.PLAYING:
        return

    player = room.players.get(player_name)
    if not player:
        return
    if player.resigned:
        await send_error(ws, "Already resigned")
        return

    player.resigned = True

    if player.websocket:
        await player.websocket.send_json({"type": "resigned"})

    total = len(room.players)
    count = sum(1 for p in room.players.values() if p.resigned)

    await broadcast(room, {"type": "resign_update", "count": count, "total": total})

    if all(p.resigned for p in room.players.values()):
        await end_game(room)


async def handle_join(ws: WebSocket, room: Room, data: dict) -> str | None:
    """Handle a player joining a room. Returns player name or None."""
    name = data.get("name", "").strip()
    if not name:
        await send_error(ws, "Name is required")
        return None
    if len(name) > 20:
        await send_error(ws, "Name too long (max 20 chars)")
        return None
    if name in room.players:
        await send_error(ws, f"Name '{name}' is already taken")
        return None
    if room.state != RoomState.LOBBY:
        await send_error(ws, "Game already in progress")
        return None

    room.players[name] = Player(name=name, websocket=ws)
    await broadcast(room, room_state_msg(room))
    return name


async def _begin_round(room: Room, problem: dict, round_number: int) -> None:
    """Reset players, assign the problem, update room state, broadcast, and start timer."""
    _reset_players(room)

    room.problem = problem
    room.state = RoomState.PLAYING
    room.start_time = time.time()
    room.current_round = round_number
    # Refresh created_at so multi-round games that span hours are not
    # mistakenly reaped by the GC catch-all during inter-round breaks.
    room.created_at = time.time()

    await broadcast(
        room,
        {
            "type": "game_start",
            "problem": {
                "id": problem["id"],
                "title": problem["title"],
                "difficulty": problem["difficulty"],
                "description": fix_subscripts(fix_exponents(problem["description"])),
                "entry_point": problem["entry_point"],
                "starter_code": problem["starter_code"],
            },
            "time_limit": room.time_limit,
            "current_round": room.current_round,
            "total_rounds": room.total_rounds,
        },
    )

    asyncio.create_task(timer_task(room))


async def handle_start(ws: WebSocket, room: Room, player_name: str) -> None:
    """Handle the host starting the game."""
    if player_name != room.host:
        await send_error(ws, "Only the host can start the game")
        return
    if room.state != RoomState.LOBBY:
        await send_error(ws, "Game has already started")
        return
    if len(room.players) < 1:
        await send_error(ws, "At least one player must be in the room")
        return

    problem = pick_random(room.difficulty)
    if not problem:
        await broadcast(
            room,
            {
                "type": "error",
                "message": "No problems available. Run `make rebuild` first.",
            },
        )
        return

    await _begin_round(room, problem, 1)


def _is_better(new: dict, old: dict) -> bool:
    """Return True if the new submission is better than the old one for scoring."""
    if new["solved"] and not old["solved"]:
        return True
    if not new["solved"] and old["solved"]:
        return False
    if new["solved"] and old["solved"]:
        return new["char_count"] < old["char_count"]
    # Both unsolved: more tests passed is better
    return new["passed"] > old["passed"]


async def handle_submit(room: Room, player_name: str, data: dict) -> None:
    """Handle a code submission."""
    if room.state != RoomState.PLAYING:
        return

    player = room.players.get(player_name)
    if not player:
        return
    if player.locked_at is not None:
        if player.websocket:
            await send_error(player.websocket, "You are locked in")
        return

    code = data.get("code", "")
    if not code.strip():
        if player.websocket:
            await send_error(player.websocket, "Empty submission")
        return

    char_count = len(code)
    submit_time = time.time() - room.start_time

    # Heuristic: detect problems where result order doesn't matter by scanning the
    # description for "any order". This can produce false positives for problems
    # that mention the phrase in a different context. A more robust approach would
    # store this as an explicit boolean field in the problem JSON at build time.
    any_order = "any order" in room.problem.get("description", "").lower()
    result = await run_code(
        code=code,
        entry_point=room.problem["entry_point"],
        test_cases=room.problem["test_cases"],
        preamble=room.problem.get("preamble", ""),
        any_order=any_order,
    )

    solved = result["passed"] == result["total"] and result["total"] > 0

    submission = {
        "code": code,
        "passed": result["passed"],
        "total": result["total"],
        "error": result.get("error"),
        "time_ms": result.get("time_ms", 0),
        "char_count": char_count,
        "solved": solved,
        "submit_time": round(submit_time, 2),
    }

    # Always store latest result
    player.submission = submission

    # Update best submission if this one is better
    best = player.best_submission
    if best is None or _is_better(submission, best):
        player.best_submission = submission

    # Notify the submitter of their result
    if player.websocket:
        await player.websocket.send_json(
            {
                "type": "submit_result",
                "passed": result["passed"],
                "total": result["total"],
                "error": result.get("error"),
                "solved": solved,
                "char_count": char_count,
                "submit_time": round(submit_time, 2),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "first_failure": result.get("first_failure"),
            }
        )

    # Broadcast updated scoreboard
    await broadcast(room, scoreboard_msg(room))


async def handle_chat(room: Room, player_name: str, data: dict) -> None:
    """Handle a chat message from a player and broadcast it to the room.

    Validates that the message is non-empty and within the character limit, then
    strips HTML tags to prevent XSS before broadcasting to all connected players.
    Chat is allowed in all game states so players can talk in lobby and after games.
    """
    text = data.get("message", "")

    # Reject non-string payloads so a malicious client sending e.g. an int or
    # list cannot crash re.sub() and trigger the connection error handler.
    if not isinstance(text, str):
        return

    # Strip HTML tags so injected markup cannot execute in other clients' browsers.
    # A simple regex is sufficient here because we never trust or render the raw
    # text as HTML — the frontend always assigns via textContent — but stripping on
    # the server provides defense-in-depth.
    text = re.sub(r"<[^>]*>", "", text)
    text = text.replace("<", "")  # catch unclosed tags like "<script"

    # Normalise whitespace: collapse runs and strip leading/trailing spaces.
    text = " ".join(text.split())

    if not text:
        return

    if len(text) > MAX_CHAT_LENGTH:
        text = text[:MAX_CHAT_LENGTH]

    await broadcast(
        room,
        {
            "type": "chat",
            "sender": player_name,
            "message": text,
        },
    )


async def handle_restart(ws: WebSocket, room: Room, player_name: str) -> None:
    """Handle the host restarting the game with a new problem."""
    if player_name != room.host:
        await send_error(ws, "Only the host can restart the game")
        return
    if room.state != RoomState.FINISHED:
        await send_error(ws, "Game is not finished yet")
        return

    room.state = RoomState.LOBBY
    room.problem = None
    room.start_time = None
    room.current_round = 0
    _reset_players(room)

    await broadcast(room, room_state_msg(room))


async def websocket_handler(ws: WebSocket, room_id: str) -> None:
    """Main WebSocket handler for a room."""
    await ws.accept()

    room = get_room(room_id)
    if not room:
        await send_error(ws, "Room not found")
        await ws.close()
        return

    player_name = None

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "join":
                player_name = await handle_join(ws, room, data)

            elif msg_type == "start" and player_name:
                await handle_start(ws, room, player_name)

            elif msg_type == "submit" and player_name:
                await handle_submit(room, player_name, data)

            elif msg_type == "lock" and player_name:
                await handle_lock(ws, room, player_name)

            elif msg_type == "resign" and player_name:
                await handle_resign(ws, room, player_name)

            elif msg_type == "skip_break" and player_name:
                if player_name != room.host:
                    await send_error(ws, "Only the host can skip the break")
                elif (
                    room.state != RoomState.FINISHED
                    or room.current_round >= room.total_rounds
                ):
                    await send_error(ws, "Cannot skip break right now")
                else:
                    await start_next_round(room)

            elif msg_type == "restart" and player_name:
                await handle_restart(ws, room, player_name)

            elif msg_type == "chat" and player_name:
                await handle_chat(room, player_name, data)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "WebSocket error in room %s for player %s", room_id, player_name
        )
        try:
            await send_error(ws, "Something went wrong. Please rejoin.")
        except Exception:
            pass
    finally:
        # Clean up player
        if player_name and player_name in room.players:
            room.players.pop(player_name)

            # If host left, assign new host
            if room.host == player_name and room.players:
                room.host = next(iter(room.players))

            # If room empty, remove it
            if not room.players:
                remove_room(room_id)
            else:
                await broadcast(room, room_state_msg(room))
