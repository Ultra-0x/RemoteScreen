"""ScreenShare — consent-based remote viewing + control (Python server).

How it works
------------
1. Run this server:         python server.py
2. Open http://localhost:8000 on your computer -> "Create session".
3. Send the SHARE link to the person whose screen you need.
4. They open it: tick the consent box, click OK, then pick a screen to
   share in the browser prompt. Screen video streams peer-to-peer (WebRTC),
   so the server never sees the actual video.
5. For CONTROL, they also download and run agent.py on that same computer
   (pip install pyautogui websockets; python agent.py <room>). The agent
   stays idle until consent is received — it refuses to move the mouse or
   type before the box is ticked. After consent, the viewer's mouse,
   keyboard and scroll are relayed through the server to the agent.
6. You open the WATCH link and see their screen live; once they consent
   you can also control it.

Consent cannot be bypassed: the server marks a room "consented" only on a
consent message from the share page, and it only forwards control commands
to the agent after that. The agent also enforces the same rule itself.

For links to work between machines over the internet you need HTTPS + a
tunnel (e.g. Cloudflare) or a public IP — see README notes in chat.
"""

import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="ScreenShare")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# --------------------------------------------------------------------------
# Sanity-check the layout on startup. A 500 on the pages almost always means
# server.py can't find its static HTML folder (wrong working directory, or
# the static/ folder didn't get saved next to server.py). Catch it early.
# --------------------------------------------------------------------------
_MISSING = [p for p in ("index.html", "share.html", "watch.html")
            if not (STATIC_DIR / p).exists()]
if _MISSING:
    print("\n[!] SCREENSHARE SETUP PROBLEM: static/ folder is missing these files:")
    for p in _MISSING:
        print(f"    MISSING: {STATIC_DIR / p}")
    print("    Expected folder: {0}".format(STATIC_DIR))
    print("    Make sure static/ sits in the same folder as server.py\n")


def _serve(page: str) -> FileResponse:
    """Return a page, or a clear 500 body instead of a bare traceback."""
    path = STATIC_DIR / page
    if not path.exists():
        return Response(
            "<h1>Internal Server Error</h1>"
            f"<p>Cannot find {path}. Put the <code>static</code> folder next to "
            "server.py and restart.</p>",
            status_code=500, media_type="text/html",
        )
    return FileResponse(path)

# room_id -> {"share": ws|None, "viewer": ws|None, "agent": ws|None, "consented": bool}
rooms: dict[str, dict] = {}


# --------------------------------------------------------------------------
# The native control agent, served for download so the guest can grab it
# straight from the share page. It is a normal Python program the guest
# runs on the computer they want controlled.
# --------------------------------------------------------------------------
AGENT_SOURCE = r'''"""ScreenShare control agent — run this ON the computer to be controlled.

Install:
    pip install pyautogui websockets

Run:
    python agent.py <room>

<room> is the id from your share link, e.g. http://host/r/abcdefgh
-> room = abcdefgh. If your server is not at localhost:8000, set SERVER
below (use wss://... when the site is https).

SECURITY: the agent does nothing until the person on THIS computer ticks
the consent box on the share page. After that the viewer can move your
mouse, click, scroll and type. Push the mouse into a screen corner at any
time to abort (pyautogui failsafe). Only run this if you trust the person
who sent you the link — they can control your computer.
"""
import asyncio
import json
import sys

import pyautogui
import websockets

SERVER = "ws://localhost:8000"  # change to your server (wss://... for https)

# Browser KeyboardEvent.key -> pyautogui key name
KEYMAP = {
    "Enter": "enter", "Tab": "tab", "Escape": "esc",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Backspace": "backspace", "Delete": "delete", "Home": "home", "End": "end",
    "PageUp": "pageup", "PageDown": "pagedown",
    "Control": "ctrl", "Shift": "shift", "Alt": "alt", "Meta": "win",
    "CapsLock": "capslock", " ": "space",
}


def norm_key(k):
    if k in KEYMAP:
        return KEYMAP[k]
    if len(k) == 1:
        return k.lower()
    return k


async def main():
    if len(sys.argv) < 2:
        print("Usage:  python agent.py <room>")
        return
    room = sys.argv[1]
    url = f"{SERVER}/ws/{room}?role=agent"

    pyautogui.FAILSAFE = True      # move mouse to a corner to abort
    pyautogui.PAUSE = 0.01
    consented = False

    print(f"Connecting to {url} ...")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "agent", "status": "connected"}))
        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")

            if t == "consent":
                consented = True
                print(">> Consent received. Remote control is now ACTIVE.")
                print("   (push the mouse to a corner anytime to abort)")
                await ws.send(json.dumps({"type": "agent", "status": "controlling"}))

            elif t == "control":
                if not consented:
                    continue  # refuse: no consent yet
                act = msg.get("action")
                w, h = pyautogui.size()
                try:
                    if act == "mousemove":
                        pyautogui.moveTo(int(msg["x"] * w), int(msg["y"] * h))
                    elif act == "click":
                        pyautogui.click(int(msg["x"] * w), int(msg["y"] * h),
                                        button=msg.get("button", "left"))
                    elif act == "mousedown":
                        pyautogui.mouseDown(int(msg["x"] * w), int(msg["y"] * h),
                                            button=msg.get("button", "left"))
                    elif act == "mouseup":
                        pyautogui.mouseUp(int(msg["x"] * w), int(msg["y"] * h),
                                          button=msg.get("button", "left"))
                    elif act == "scroll":
                        pyautogui.scroll(-int(msg.get("dy", 0) * 50))
                    elif act == "keydown":
                        pyautogui.keyDown(norm_key(msg.get("key", "")))
                    elif act == "keyup":
                        pyautogui.keyUp(norm_key(msg.get("key", "")))
                    elif act == "text":
                        pyautogui.write(msg.get("text", ""))
                except Exception as exc:  # noqa: BLE001
                    print("action error:", exc)


if __name__ == "__main__":
    asyncio.run(main())
'''


@app.get("/")
async def index():
    """Host console: create a session, get share + watch links, download agent."""
    return _serve("index.html")


@app.get("/r/{room}")
async def share_page(room: str):
    """The page the person whose screen is being shared opens."""
    return _serve("share.html")


@app.get("/watch/{room}")
async def watch_page(room: str):
    """The page the viewer opens to see (and control) the shared screen."""
    return _serve("watch.html")


@app.get("/agent.py")
async def agent_download():
    """Serve the native control agent for download."""
    return Response(
        AGENT_SOURCE,
        media_type="text/x-python",
        headers={"Content-Disposition": "attachment; filename=agent.py"},
    )


@app.websocket("/ws/{room}")
async def signalling(room: str, websocket: WebSocket):
    """Relays WebRTC signalling, consent, and control commands."""
    role = websocket.query_params.get("role", "viewer")
    if role not in ("share", "viewer", "agent"):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    conns = rooms.setdefault(
        room, {"share": None, "viewer": None, "agent": None, "consented": False}
    )
    conns[role] = websocket

    # Tell the viewer on connect whether control is permitted yet.
    if role == "viewer":
        await websocket.send_text(
            json.dumps({"type": "state", "consented": conns["consented"]})
        )

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            t = msg.get("type")

            if role == "share":
                if t == "consent":
                    conns["consented"] = True
                    if conns["agent"] is not None:
                        await conns["agent"].send_text(
                            json.dumps({"type": "consent"})
                        )
                else:
                    # WebRTC signalling -> viewer
                    if conns["viewer"] is not None:
                        await conns["viewer"].send_text(data)

            elif role == "viewer":
                if t == "control":
                    # Only forward control commands after consent.
                    if conns["consented"] and conns["agent"] is not None:
                        await conns["agent"].send_text(data)
                else:
                    # WebRTC signalling -> share page
                    if conns["share"] is not None:
                        await conns["share"].send_text(data)

            elif role == "agent":
                # Agent status/state -> viewer
                if conns["viewer"] is not None:
                    await conns["viewer"].send_text(data)

    except WebSocketDisconnect:
        pass
    finally:
        if conns.get(role) is websocket:
            conns[role] = None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)