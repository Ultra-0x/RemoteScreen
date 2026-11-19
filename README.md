Flow:

Host runs python server.py → starts server, can create a room → gets link like https://<host>/r/abc123
Host shares the link with the guest.
Guest clicks link → sees "Share your screen" page → clicks button → browser asks "Choose what to share" (native consent, equivalent to their checkbox+OK) → guest picks screen/window and clicks Share.
Guest's browser (via getDisplayMedia) captures the screen; WebRTC peer connection is established through the WebSocket signaling relay.
Host opens the watch page in a browser (or the host's browser is already on the watch page) and sees the guest's screen live.
Signaling details:

WebSocket endpoint /ws/{room}
Rooms: a dict mapping room id → set of connections, with roles (share/viewer)
When the sharer sends an SDP offer, relay it to the viewer; ICE candidates relayed both ways.
STUN server for NAT traversal: stun:stun.l.google.com:19302 (public, fine). For real TURN you'd need a TURN server, but STUN works for many cases.

cd remote-screen
python -m venv .venv
# Windows: .venv\Scripts\activate  |  macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python server.py
