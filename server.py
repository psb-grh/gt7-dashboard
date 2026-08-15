#!/usr/bin/env python3
"""GT7 telemetry dashboard server.

Runs on the Orange Pi (LAN-only). It does three things concurrently:

  1. Sends a heartbeat to the PlayStation every second so GT7 keeps streaming
     telemetry, and receives the encrypted UDP packets on port 33740.
  2. Serves the static dashboard (index.html) over HTTP on the browser port.
  3. Pushes each decoded telemetry sample to every connected phone browser via
     a WebSocket at /ws (same port as the page).

HTTP and WebSocket share one port: the phone only needs one address,
http://192.168.0.9:8080/, and the dashboard auto-connects its WebSocket to
ws://192.168.0.9:8080/ws.

Run:
    python3 server.py
    python3 server.py --ps-ip 192.168.1.50      # fixed PS5 IP instead of broadcast

Then open http://192.168.0.9:8080/ on your phone.
"""

import argparse
import asyncio
import json
import logging
import socket
import time
from pathlib import Path
from urllib.parse import unquote

import websockets
from websockets.asyncio.server import serve
from websockets.http11 import Response

import gt7telemetry

log = logging.getLogger("gt7dash")

RECV_PORT = 33740
SEND_PORT = 33739
HEARTBEAT_INTERVAL = 1.0
HTTP_PORT = 8080
STATIC_DIR = Path(__file__).resolve().parent

latest_telemetry: dict = {}
last_packet_time: float = 0.0
connected_clients: set = set()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def ms_to_lap_string(ms: int) -> str:
    if ms <= 0:
        return "--:--.---"
    minutes, rem = divmod(ms, 60000)
    seconds, millis = divmod(rem, 1000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def serve_static(path: str) -> Response:
    if path in ("", "/"):
        path = "/index.html"
    target = (STATIC_DIR / unquote(path).lstrip("/")).resolve()
    if STATIC_DIR not in target.parents and target != STATIC_DIR / "index.html":
        return Response(403, "Forbidden", websockets.Headers(), b"Forbidden\n")
    try:
        body = target.read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return Response(404, "Not Found", websockets.Headers(), b"Not Found\n")
    ctype = MIME.get(target.suffix, "application/octet-stream")
    headers = websockets.Headers({
        "Content-Type": ctype,
        "Content-Length": str(len(body)),
        "Connection": "close",
        "Cache-Control": "no-cache",
    })
    return Response(200, "OK", headers, body)


async def receive_telemetry(ps_ip: str) -> None:
    global latest_telemetry, last_packet_time

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if ps_ip.endswith("255"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", RECV_PORT))
    sock.setblocking(False)

    loop = asyncio.get_running_loop()
    heartbeat = b"A"
    last_heartbeat = 0.0

    log.info("Listening for GT7 telemetry on UDP :%d (PS target %s:%d)",
             RECV_PORT, ps_ip, SEND_PORT)

    while True:
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                await loop.sock_sendto(sock, heartbeat, (ps_ip, SEND_PORT))
                last_heartbeat = now
            except OSError as exc:
                log.warning("Heartbeat send failed: %s", exc)

        try:
            data, _addr = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 4096), timeout=0.5
            )
        except asyncio.TimeoutError:
            continue
        except OSError as exc:
            log.warning("recv error: %s", exc)
            await asyncio.sleep(0.2)
            continue

        ddata = gt7telemetry.salsa20_dec(data)
        if not ddata:
            continue
        t = gt7telemetry.parse_packet(ddata)
        if t is None:
            continue

        latest_telemetry = t.to_dict()
        latest_telemetry["last_lap"] = ms_to_lap_string(t.last_lap_ms)
        latest_telemetry["best_lap"] = ms_to_lap_string(t.best_lap_ms)
        last_packet_time = now

        if connected_clients:
            payload = json.dumps(latest_telemetry)
            dead = set()
            for ws in connected_clients:
                try:
                    await ws.send(payload)
                except websockets.ConnectionClosed:
                    dead.add(ws)
            connected_clients.difference_update(dead)


async def process_request(connection, request):
    path = request.path
    if path.startswith("/ws"):
        return None
    return serve_static(path)


async def ws_handler(websocket) -> None:
    connected_clients.add(websocket)
    remote = websocket.remote_address if websocket.remote_address else "?"
    log.info("WebSocket client connected from %s (%d total)", remote, len(connected_clients))
    if latest_telemetry:
        try:
            await websocket.send(json.dumps(latest_telemetry))
        except websockets.ConnectionClosed:
            pass
    try:
        async for _ in websocket:
            pass
    except websockets.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        log.info("WebSocket client disconnected (%d remaining)", len(connected_clients))


async def main() -> None:
    parser = argparse.ArgumentParser(description="GT7 telemetry dashboard server")
    parser.add_argument("--ps-ip", default="255.255.255.255",
                        help="PlayStation IP (default: 255.255.255.255 broadcast)")
    parser.add_argument("--http-port", type=int, default=HTTP_PORT,
                        help=f"HTTP/WS port for browsers (default {HTTP_PORT})")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async with serve(
        ws_handler,
        "0.0.0.0",
        args.http_port,
        process_request=process_request,
    ):
        log.info("Dashboard + WebSocket on http://0.0.0.0:%d/  (ws at /ws)",
                 args.http_port)
        await receive_telemetry(args.ps_ip)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
