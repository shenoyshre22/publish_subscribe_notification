import os               # lets us read environment variables (like PORT on Railway)
import time             # used to generate timestamps for comments
import uuid             # generates unique IDs for comments
import socket           # provides networking functionality (TCP sockets)
import threading        # allows multiple clients to connect simultaneously
import sqlite3          # built-in Python library for SQLite — no install needed

from topics import TOPICS     # import the list of allowed topics from topics.py

from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask import request as sio_request


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SETUP (SQLite)
# stores posts AND subscriptions so both survive page refreshes
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = "netthreads.db"


def get_db():
    """
    opens a connection to the SQLite database
    check_same_thread=False is required because Flask-SocketIO uses multiple threads
    row_factory=sqlite3.Row lets us access columns by name: row["username"] not row[0]
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    creates all tables if they don't already exist
    IF NOT EXISTS is safe to run on every startup — won't wipe existing data
    """
    conn = get_db()

    # posts table — stores every published post
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id  TEXT PRIMARY KEY,
            topic    TEXT NOT NULL,
            username TEXT NOT NULL,
            message  TEXT NOT NULL,
            ts       INTEGER NOT NULL
        )
    """)

    # subscriptions table — stores which username subscribed to which topic
    # so subscriptions survive page refreshes and reconnects
    # PRIMARY KEY (username, topic) prevents duplicate rows
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            username TEXT NOT NULL,
            topic    TEXT NOT NULL,
            PRIMARY KEY (username, topic)
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] SQLite database ready →", DB_PATH)


# ── Post DB helpers ───────────────────────────────────────────────────────────

def db_save_post(post_id, topic, username, message, ts):
    """
    inserts a new post — OR IGNORE skips silently if post_id already exists
    """
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO posts (post_id, topic, username, message, ts) VALUES (?,?,?,?,?)",
        (post_id, topic, username, message, ts)
    )
    conn.commit()
    conn.close()


def db_delete_post(post_id):
    """
    removes a post from the database permanently
    """
    conn = get_db()
    conn.execute("DELETE FROM posts WHERE post_id = ?", (post_id,))
    conn.commit()
    conn.close()


def db_get_all_posts():
    """
    fetches all posts ordered oldest first
    client reverses this so newest appears at top
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT post_id, topic, username, message, ts FROM posts ORDER BY ts ASC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ── Subscription DB helpers ───────────────────────────────────────────────────

def db_save_subscription(username, topic):
    """
    saves a subscription — OR IGNORE prevents duplicates
    called when a user subscribes to a topic
    """
    if username == "Anonymous":
        return   # don't persist anonymous subscriptions
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO subscriptions (username, topic) VALUES (?,?)",
        (username, topic)
    )
    conn.commit()
    conn.close()


def db_delete_subscription(username, topic):
    """
    removes a subscription from the database
    called when a user unsubscribes from a topic
    """
    if username == "Anonymous":
        return
    conn = get_db()
    conn.execute(
        "DELETE FROM subscriptions WHERE username = ? AND topic = ?",
        (username, topic)
    )
    conn.commit()
    conn.close()


def db_get_subscriptions(username):
    """
    returns all topics a username is subscribed to
    called on connect so the UI can restore the subscribed state
    """
    if username == "Anonymous":
        return []
    conn = get_db()
    rows = conn.execute(
        "SELECT topic FROM subscriptions WHERE username = ?", (username,)
    ).fetchall()
    conn.close()
    return [row["topic"] for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# RAW TCP SERVER SECTION
# ─────────────────────────────────────────────────────────────────────────────

HOST = "0.0.0.0"
PORT = 5000

tcp_subscriptions = {topic: [] for topic in TOPICS}
tcp_usernames     = {}
comments          = {}   # { post_id: [ {comment_id, username, message, ts} ] }
votes             = {}   # { target_id: { "up": set(sids), "down": set(sids) } }


def tcp_broadcast(topic, message):
    for client in list(tcp_subscriptions[topic]):
        try:
            client.send(message.encode())
        except:
            pass


def handle_tcp_client(conn, addr):
    """
    runs in a separate thread for each TCP client
    handles USER, SUBSCRIBE, UNSUBSCRIBE, POST commands
    """
    print("Connected:", addr)
    username = "Anonymous"
    tcp_usernames[conn] = username

    while True:
        try:
            data = conn.recv(1024).decode()
            if not data:
                break

            parts   = data.split("|")
            command = parts[0]

            # -------------------------
            # handle USER command
            # -------------------------
            if command == "USER":
                username = parts[1]
                tcp_usernames[conn] = username
                conn.send(f"Username set to {username}".encode())

            # -------------------------
            # handle SUBSCRIBE command
            # -------------------------
            elif command == "SUBSCRIBE":
                topic = parts[1]
                if topic in TOPICS:
                    # FIX: prevent duplicate subscriptions
                    if conn not in tcp_subscriptions[topic]:
                        tcp_subscriptions[topic].append(conn)
                    conn.send(f"Subscribed to {topic}".encode())
                else:
                    conn.send("Invalid topic".encode())

            # -------------------------
            # handle UNSUBSCRIBE command
            # -------------------------
            elif command == "UNSUBSCRIBE":
                topic = parts[1]
                if topic in TOPICS and conn in tcp_subscriptions[topic]:
                    tcp_subscriptions[topic].remove(conn)
                conn.send(f"Unsubscribed from {topic}".encode())

            # -------------------------
            # handle POST command
            # core pub-sub: server routes message only to topic subscribers
            # -------------------------
            elif command == "POST":
                topic   = parts[1]
                message = parts[2]

                if topic not in TOPICS:
                    conn.send("Invalid topic".encode())
                    continue

                username     = tcp_usernames.get(conn, "Anonymous")
                full_message = f"[{topic.upper()}] {username}: {message}"

                tcp_broadcast(topic, full_message)

                # cross-client bridge: push to web clients too
                socketio.emit("new_post", {
                    "topic":    topic,
                    "username": username,
                    "message":  message,
                    "post_id":  None,
                    "ts":       int(time.time() * 1000),
                }, room=topic)

            else:
                conn.send("Unknown command".encode())

        except:
            break

    # cleanup on disconnect — remove from all topic subscriber lists
    for topic in tcp_subscriptions:
        if conn in tcp_subscriptions[topic]:
            tcp_subscriptions[topic].remove(conn)

    if conn in tcp_usernames:
        del tcp_usernames[conn]

    conn.close()
    print("Disconnected:", addr)


def run_tcp_server():
    """
    creates and starts the raw TCP socket server in a background thread
    AF_INET = IPv4, SOCK_STREAM = TCP (guaranteed delivery, unlike UDP)
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR prevents "Address already in use" on quick restart
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print(f"[TCP] Listening on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        # one thread per client — enables concurrent connections (multithreading)
        thread = threading.Thread(target=handle_tcp_client, args=(conn, addr))
        thread.daemon = True
        thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# WEB SERVER SECTION
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static", template_folder="static")
app.config["SECRET_KEY"] = "netthreads_secret"

# cors_allowed_origins="*" allows connections from any domain — required for Railway
socketio = SocketIO(app, cors_allowed_origins="*")

# in-memory session tracking (resets on server restart — subscriptions now backed by DB)
web_subscriptions = {topic: set() for topic in TOPICS}
web_usernames     = {}


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── SocketIO event handlers ───────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    """
    fires when a browser tab connects
    sends full post history AND saved subscriptions so everything restores on refresh
    """
    web_usernames[sio_request.sid] = "Anonymous"
    emit("server_msg", {"text": "Connected to NetThreads!"})

    # send full post history — this is what makes posts survive page refreshes
    all_posts = db_get_all_posts()
    emit("load_posts", {"posts": all_posts})

@socketio.on("disconnect")
def on_disconnect():
    sid = sio_request.sid
    for topic in TOPICS:
        web_subscriptions[topic].discard(sid)
    web_usernames.pop(sid, None)

@socketio.on("set_username")
def on_set_username(data):
    """
    called when user sets their username
    also restores their saved subscriptions from the database
    so when a user logs in with the same name, all their topics come back
    """
    sid      = sio_request.sid
    username = data.get("username", "").strip() or "Anonymous"
    web_usernames[sid] = username
    emit("server_msg", {"text": f"Username set to {username}"})

    # restore saved subscriptions for this username from the database
    saved_topics = db_get_subscriptions(username)
    for topic in saved_topics:
        if topic in TOPICS:
            web_subscriptions[topic].add(sid)
            join_room(topic)

    # tell the client which topics to restore in the UI
    if saved_topics:
        emit("my_subscriptions", {"topics": saved_topics})

@socketio.on("subscribe")
def on_subscribe(data):
    """
    adds client to topic's SocketIO room and saves to database
    SocketIO rooms efficiently route broadcasts to only relevant subscribers
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic not in TOPICS:
        emit("server_msg", {"text": f"Unknown topic: {topic}"})
        return

    web_subscriptions[topic].add(sid)
    join_room(topic)

    # save to database so subscription survives page refresh
    username = web_usernames.get(sid, "Anonymous")
    db_save_subscription(username, topic)

    emit("subscribed", {"topic": topic})
    emit("server_msg", {"text": f"Subscribed to #{topic}"})

@socketio.on("unsubscribe")
def on_unsubscribe(data):
    """
    removes client from topic's SocketIO room and deletes from database
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic in TOPICS:
        web_subscriptions[topic].discard(sid)
        leave_room(topic)

    # remove from database so it stays gone after refresh
    username = web_usernames.get(sid, "Anonymous")
    db_delete_subscription(username, topic)

    emit("unsubscribed", {"topic": topic})
    emit("server_msg", {"text": f"Unsubscribed from #{topic}"})

@socketio.on("post")
def on_post(data):
    """
    core publish operation — saves to DB then broadcasts to all topic subscribers
    publisher doesn't know who subscribers are (decoupling — key pub-sub concept)
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

    # save to SQLite so post survives page refresh
    if post_id:
        db_save_post(post_id, topic, username, message, ts)
        comments[post_id] = []

    payload = {"topic": topic, "username": username, "message": message, "post_id": post_id, "ts": ts}

    # broadcast to all subscribers of this topic via SocketIO room
    socketio.emit("new_post", payload, room=topic)

    # cross-client bridge — TCP terminal users receive web posts too
    tcp_broadcast(topic, f"[{topic.upper()}] {username}: {message}")

@socketio.on("delete_post")
def on_delete_post(data):
    """
    deletes from SQLite and broadcasts deletion to all clients
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
        "post_id": post_id, "comment_id": comment_id,
        "username": username, "message": message, "ts": ts,
    })

@socketio.on("get_comments")
def on_get_comments(data):
    post_id = data.get("post_id")
    emit("comments_list", {"post_id": post_id, "comments": comments.get(post_id, [])})

# ── Vote handlers ─────────────────────────────────────────────────────────────

@socketio.on("cast_vote")
def on_cast_vote(data):
    """
    upvotes/downvotes using sets — guarantees one vote per session
    toggles: clicking same direction twice removes the vote
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
    gets machine's local network IP by briefly connecting to Google DNS
    doesn't send data — just lets the OS pick the correct outgoing interface
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

    # create tables if they don't exist
    init_db()

    # start TCP server in background daemon thread
    tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
    tcp_thread.start()

    # Railway injects PORT — we must listen on it or deployment fails
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
        print(f"  [TCP]     {local_ip}:{PORT}")
    else:
        print(f"  [Web UI]  Cloud — port {web_port}")

    print(f"  [DB]      {DB_PATH}")
    print("=" * 60)

    # host=0.0.0.0 accepts from any IP — required for cloud deployment
    socketio.run(app, host="0.0.0.0", port=web_port, debug=False)