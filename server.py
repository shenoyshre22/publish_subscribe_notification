import os
import time
import uuid
import socket
import threading

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask import request as sio_request

from topics import TOPICS

# import database helpers from db.py
from db import (
    init_db,
    db_save_post, db_delete_post, db_get_posts_by_topics,
    db_save_subscription, db_delete_subscription, db_get_subscriptions,
)

# import TCP server from tcp_server.py
from tcp_server import run_tcp_server, tcp_broadcast, set_socketio

# ─────────────────────────────────────────────────────────────────────────────
# server.py — Flask web server + SocketIO handlers for NetThreads
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static", template_folder="static")
app.config["SECRET_KEY"] = "netthreads_secret"

# cors_allowed_origins="*" allows connections from any domain — required for Railway
socketio = SocketIO(app, cors_allowed_origins="*")

# give the TCP server a reference to socketio so it can forward posts to web clients
set_socketio(socketio)

# in-memory session tracking — resets on server restart
# subscriptions are now persisted in SQLite so this is just a runtime routing cache
web_subscriptions = {topic: set() for topic in TOPICS}
web_usernames     = {}

# in-memory stores for comments and votes (ephemeral by design)
comments = {}   # { post_id: [ {comment_id, username, message, ts} ] }
votes    = {}   # { target_id: { "up": set(sids), "down": set(sids) } }


# ── Static file serving ───────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


# ── SocketIO event handlers ───────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    web_usernames[sio_request.sid] = "Anonymous"
    emit("server_msg", {"text": "Connected to NetThreads!"})
    # posts are NOT sent here — sent in on_set_username and on_subscribe
    # this prevents the empty-feed bug where posts load before subscriptions are known


@socketio.on("disconnect")
def on_disconnect():
    sid = sio_request.sid
    for topic in TOPICS:
        web_subscriptions[topic].discard(sid)
    web_usernames.pop(sid, None)


@socketio.on("set_username")
def on_set_username(data):
    """
    called on every connect (from app.js localStorage restore or modal save)
    restores saved subscriptions from SQLite and sends post history for those topics
    this is what makes the feed survive page refreshes
    """
    sid      = sio_request.sid
    username = data.get("username", "").strip() or "Anonymous"
    web_usernames[sid] = username
    emit("server_msg", {"text": f"Username set to {username}"})

    # look up saved subscriptions for this username
    saved_topics = db_get_subscriptions(username)
    for topic in saved_topics:
        if topic in TOPICS:
            web_subscriptions[topic].add(sid)
            join_room(topic)

    if saved_topics:
        emit("my_subscriptions", {"topics": saved_topics})
        # send post history for all restored topics so feed populates on refresh
        history = db_get_posts_by_topics(saved_topics)
        emit("load_posts", {"posts": history})


@socketio.on("subscribe")
def on_subscribe(data):
    """
    adds client to SocketIO room and persists subscription to SQLite
    also sends existing posts for this topic so history appears immediately
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic not in TOPICS:
        emit("server_msg", {"text": f"Unknown topic: {topic}"})
        return

    web_subscriptions[topic].add(sid)
    join_room(topic)

    username = web_usernames.get(sid, "Anonymous")
    db_save_subscription(username, topic)

    emit("subscribed",   {"topic": topic})
    emit("server_msg",   {"text": f"Subscribed to #{topic}"})

    # send this topic's post history to just this client
    history = db_get_posts_by_topics([topic])
    if history:
        emit("load_posts", {"posts": history})


@socketio.on("unsubscribe")
def on_unsubscribe(data):
    """
    removes client from SocketIO room and deletes subscription from SQLite
    so the unsubscription persists after page refresh
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic in TOPICS:
        web_subscriptions[topic].discard(sid)
        leave_room(topic)

    username = web_usernames.get(sid, "Anonymous")
    db_delete_subscription(username, topic)

    emit("unsubscribed", {"topic": topic})
    emit("server_msg",   {"text": f"Unsubscribed from #{topic}"})


@socketio.on("post")
def on_post(data):
    """
    core publish operation — saves to SQLite then broadcasts to all topic subscribers
    publisher doesn't know who subscribers are — that's the pub-sub pattern
    """
    sid     = sio_request.sid
    topic   = data.get("topic", "")
    message = data.get("message", "").strip()

    if topic not in TOPICS:
        emit("server_msg", {"text": "Invalid topic."})
        return

    if not message:
        emit("server_msg", {"text": "Empty message not sent."})
        return

    username = web_usernames.get(sid, "Anonymous")
    post_id  = data.get("post_id")
    ts       = int(time.time() * 1000)

    # save to SQLite so post survives page refresh and server restart
    if post_id:
        db_save_post(post_id, topic, username, message, ts)
        comments[post_id] = []

    payload = {
        "topic": topic, "username": username,
        "message": message, "post_id": post_id, "ts": ts
    }

    # broadcast to all web clients subscribed to this topic via SocketIO room
    socketio.emit("new_post", payload, room=topic)

    # cross-client bridge — TCP terminal users receive web posts too
    tcp_broadcast(topic, f"[{topic.upper()}] {username}: {message}")


@socketio.on("delete_post")
def on_delete_post(data):
    """
    deletes from SQLite, cleans up in-memory comments/votes,
    then broadcasts deletion to all connected clients
    """
    sid      = sio_request.sid
    post_id  = data.get("post_id")
    username = web_usernames.get(sid, "Anonymous")

    if post_id:
        db_delete_post(post_id)

    post_comments = comments.pop(post_id, [])
    votes.pop(post_id, None)
    for cm in post_comments:
        votes.pop(cm.get("comment_id"), None)

    socketio.emit("post_deleted", {"post_id": post_id, "username": username})


# ── Comment handlers ──────────────────────────────────────────────────────────

@socketio.on("post_comment")
def on_post_comment(data):
    """
    stores comment in memory and broadcasts to all clients
    comments are intentionally ephemeral — not saved to SQLite
    """
    sid     = sio_request.sid
    post_id = data.get("post_id")
    message = data.get("message", "").strip()

    if not post_id or not message:
        return

    username   = web_usernames.get(sid, "Anonymous")
    ts         = int(time.time() * 1000)
    comment_id = str(uuid.uuid4())

    comment = {"comment_id": comment_id, "username": username, "message": message, "ts": ts}

    if post_id not in comments:
        comments[post_id] = []
    comments[post_id].append(comment)

    socketio.emit("new_comment", {
        "post_id":    post_id,
        "comment_id": comment_id,
        "username":   username,
        "message":    message,
        "ts":         ts,
    })


@socketio.on("get_comments")
def on_get_comments(data):
    post_id = data.get("post_id")
    emit("comments_list", {
        "post_id":  post_id,
        "comments": comments.get(post_id, []),
    })


# ── Vote handlers ─────────────────────────────────────────────────────────────

@socketio.on("cast_vote")
def on_cast_vote(data):
    """
    votes stored as sets of session IDs — guarantees one vote per session
    discarding from both sets before adding ensures you can't upvote and downvote simultaneously
    sending None as direction removes the vote (toggle off)
    """
    sid         = sio_request.sid
    target_type = data.get("target_type")
    target_id   = data.get("target_id")
    direction   = data.get("direction")

    if not target_id or target_type not in ("post", "comment"):
        return

    if target_id not in votes:
        votes[target_id] = {"up": set(), "down": set()}

    bucket = votes[target_id]
    bucket["up"].discard(sid)
    bucket["down"].discard(sid)

    if direction in ("up", "down"):
        bucket[direction].add(sid)

    socketio.emit("vote_updated", {
        "target_type": target_type,
        "target_id":   target_id,
        "up":          len(bucket["up"]),
        "down":        len(bucket["down"]),
    })


@socketio.on("get_subscriptions")
def on_get_subscriptions():
    # legacy handler kept for compatibility
    sid     = sio_request.sid
    my_subs = [t for t in TOPICS if sid in web_subscriptions[t]]
    emit("my_subscriptions", {"topics": my_subs})


# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────

def get_local_ip():
    """
    gets the machine's local network IP by briefly connecting to Google DNS (8.8.8.8)
    no data is sent — the OS just picks the correct outgoing network interface
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # initialise SQLite tables (safe to run every startup — IF NOT EXISTS)
    init_db()

    # start TCP server in a background daemon thread
    # daemon=True means it's killed automatically when the main process exits
    tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
    tcp_thread.start()

    # Railway injects PORT as an environment variable — must use it or deployment fails
    # locally defaults to 8000
    web_port = int(os.environ.get("PORT", 8000))
    local_ip = get_local_ip()

    print("=" * 60)
    print("  NetThreads Server")
    print("=" * 60)
    print()

    if web_port == 8000:
        print(f"  [Web UI]  http://localhost:{web_port}        ← this machine")
        print(f"  [Web UI]  http://{local_ip}:{web_port}  ← same WiFi")
        print(f"  [TCP]     {local_ip}:5000")
    else:
        print(f"  [Web UI]  Cloud — port {web_port}")

    print(f"  [DB]      netthreads.db")
    print("=" * 60)

    # host=0.0.0.0 means accept connections from any IP — required for cloud deployment
    socketio.run(app, host="0.0.0.0", port=web_port, debug=False)