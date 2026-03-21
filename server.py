import os               # lets us read environment variables (like PORT on Railway)
import time             # used to generate timestamps for comments
import uuid             # generates unique IDs for comments
import socket           # provides networking functionality (TCP sockets)
import threading        # allows multiple clients to connect simultaneously
import sqlite3          # built-in Python library for SQLite — no install needed

from topics import TOPICS     # import the list of allowed topics from topics.py
                              # both server and client import from the same file
                              # so topics are always in sync

# Flask is a lightweight web framework — serves the HTML page and HTTP requests
from flask import Flask, send_from_directory

# SocketIO extends Flask with WebSocket support
# WebSockets keep a persistent two-way connection open between browser and server
# which is how real-time messaging works without the browser needing to refresh
from flask_socketio import SocketIO, emit, join_room, leave_room

# Flask's request object — sio_request.sid is the unique session ID per browser tab
from flask import request as sio_request


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SETUP (SQLite)
# SQLite stores data in a single file — netthreads.db
# this file persists on disk so posts survive page refreshes
# sqlite3 is built into Python — no pip install needed
# NOTE: on Railway free tier the filesystem resets on redeploy
#       for true permanence across redeploys you'd need PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────

# path to the SQLite database file — created automatically if it doesn't exist
DB_PATH = "netthreads.db"


def get_db():
    """
    opens a connection to the SQLite database
    check_same_thread=False is required because Flask-SocketIO uses multiple threads
    row_factory=sqlite3.Row makes rows behave like dicts: row["username"] not row[0]
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    creates the posts table if it doesn't already exist
    IF NOT EXISTS means this is safe to run on every startup — won't wipe existing data
    called once at the bottom in the entry point block
    """
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id  TEXT PRIMARY KEY,   -- unique ID generated client-side
            topic    TEXT NOT NULL,      -- which topic this post belongs to
            username TEXT NOT NULL,      -- who posted it
            message  TEXT NOT NULL,      -- the post content
            ts       INTEGER NOT NULL    -- unix timestamp in milliseconds (matches JS Date.now())
        )
    """)
    conn.commit()
    conn.close()
    print("[DB] SQLite database ready →", DB_PATH)


def db_save_post(post_id, topic, username, message, ts):
    """
    inserts a new post into the database
    OR IGNORE means if a post with the same post_id already exists, skip silently
    called every time a web client successfully posts a message
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
    called when a user clicks the delete button on their own post
    """
    conn = get_db()
    conn.execute("DELETE FROM posts WHERE post_id = ?", (post_id,))
    conn.commit()
    conn.close()


def db_get_all_posts():
    """
    fetches all posts ordered by timestamp ascending (oldest first)
    called on connect so every new browser client gets the full post history
    the client reverses this so newest posts appear at the top of the feed
    returns a list of plain dicts ready for JSON serialisation over SocketIO
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT post_id, topic, username, message, ts FROM posts ORDER BY ts ASC"
    ).fetchall()
    conn.close()

    # convert sqlite3.Row objects to plain dicts for JSON serialisation
    return [dict(row) for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# RAW TCP SERVER SECTION
# the original pub-sub system using raw TCP sockets
# kept intact so client.py still works on a local network
# ─────────────────────────────────────────────────────────────────────────────

# 0.0.0.0 = listen on all available network interfaces
# different from 127.0.0.1 (localhost) which only accepts from the same machine
HOST = "0.0.0.0"

# port number where the TCP server listens
# port 5000 is commonly used for development; ports below 1024 are system-reserved
PORT = 5000


# maps each topic to a list of connected TCP client sockets
# { "sports": [<socket1>, <socket3>], "gaming": [<socket2>] }
tcp_subscriptions = {topic: [] for topic in TOPICS}

# maps each connected socket to its username
# { <socket>: "alice", <socket>: "bob" }
tcp_usernames = {}

# comments store — maps post_id to list of comment objects
# { "post_abc": [ {"comment_id": "xyz", "username": "alice", "message": "...", "ts": 123} ] }
comments = {}

# votes store — maps target_id (post or comment) to vote sets
# using sets ensures one vote per user — adding the same sid twice has no effect
# { "post_abc": { "up": {"sid1", "sid2"}, "down": {"sid3"} } }
votes = {}


def tcp_broadcast(topic, message):
    """
    sends a formatted message to every TCP client subscribed to a topic
    also called when a web client posts, so terminal users receive web posts too
    """
    for client in list(tcp_subscriptions[topic]):
        # list() makes a copy before iterating to avoid crashes on mid-loop disconnects
        try:
            client.send(message.encode())
        except:
            # if sending fails (client disconnected), ignore it
            # the client gets cleaned up properly when handle_tcp_client detects the disconnect
            pass


def handle_tcp_client(conn, addr):
    """
    runs in a separate thread for each connected TCP client
    loops forever reading commands until the client disconnects
    conn = the socket for this specific client
    addr = (ip, port) identifying who connected
    """
    print("Connected:", addr)

    # set a default username — client can change this with the USER command
    username = "Anonymous"
    tcp_usernames[conn] = username

    while True:
        try:
            # recv(1024) reads up to 1024 bytes; .decode() converts bytes to string
            data = conn.recv(1024).decode()

            # empty bytes = client closed the connection
            if not data:
                break

            # all commands use pipe | as a separator
            # format expected:
            # USER|name
            # SUBSCRIBE|topic
            # UNSUBSCRIBE|topic
            # POST|topic|message
            parts   = data.split("|")
            command = parts[0]

            # -------------------------
            # handle USER command
            # saves username so it appears in broadcast messages
            # -------------------------
            if command == "USER":
                username = parts[1]
                tcp_usernames[conn] = username
                conn.send(f"Username set to {username}".encode())

            # -------------------------
            # handle SUBSCRIBE command
            # adds this socket to the topic's subscriber list
            # -------------------------
            elif command == "SUBSCRIBE":
                topic = parts[1]
                if topic in TOPICS:
                    # FIX: prevent duplicate subscriptions
                    # without this check the client would receive duplicate messages
                    if conn not in tcp_subscriptions[topic]:
                        tcp_subscriptions[topic].append(conn)
                    conn.send(f"Subscribed to {topic}".encode())
                else:
                    conn.send("Invalid topic".encode())

            # -------------------------
            # handle UNSUBSCRIBE command
            # removes this socket from the topic's subscriber list
            # -------------------------
            elif command == "UNSUBSCRIBE":
                topic = parts[1]
                # only remove if actually subscribed — avoids ValueError
                if topic in TOPICS and conn in tcp_subscriptions[topic]:
                    tcp_subscriptions[topic].remove(conn)
                conn.send(f"Unsubscribed from {topic}".encode())

            # -------------------------
            # handle POST command
            # core pub-sub operation — server acts as the broker
            # publisher doesn't need to know who the subscribers are (decoupling)
            # -------------------------
            elif command == "POST":
                topic   = parts[1]
                message = parts[2]

                if topic not in TOPICS:
                    conn.send("Invalid topic".encode())
                    continue

                username     = tcp_usernames.get(conn, "Anonymous")
                full_message = f"[{topic.upper()}] {username}: {message}"

                # broadcast to all TCP subscribers of this topic
                tcp_broadcast(topic, full_message)

                # cross-client bridge: also push to browser clients via SocketIO
                # so terminal client.py users' posts appear in the web UI too
                socketio.emit("new_post", {
                    "topic":    topic,
                    "username": username,
                    "message":  message,
                    "post_id":  None,   # TCP posts don't have a post_id so deletion won't work
                    "ts":       int(time.time() * 1000),
                }, room=topic)

            else:
                conn.send("Unknown command".encode())

        except:
            # any error (connection reset, broken pipe) breaks the loop
            break

    # ── CLEANUP on disconnect ──────────────────────────────────────────────────
    # remove from ALL topic subscriber lists — prevents sending to a closed socket
    for topic in tcp_subscriptions:
        if conn in tcp_subscriptions[topic]:
            tcp_subscriptions[topic].remove(conn)

    # free memory
    if conn in tcp_usernames:
        del tcp_usernames[conn]

    # release the OS-level socket resource
    conn.close()
    print("Disconnected:", addr)


def run_tcp_server():
    """
    creates and starts the raw TCP socket server
    runs in a background daemon thread so Flask can run on the main thread
    both servers run simultaneously
    """

    # AF_INET = IPv4 addressing, SOCK_STREAM = TCP
    # TCP guarantees delivery and ordering — UDP would be faster but unreliable
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # SO_REUSEADDR prevents "Address already in use" error on quick restart
    # without this the OS holds the port for a few seconds after shutdown
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # bind = "this socket owns this address+port"
    server.bind((HOST, PORT))

    # listen() puts socket into passive mode — ready to accept connections
    server.listen()

    print(f"[TCP] Listening on {HOST}:{PORT}")

    while True:
        # accept() blocks until a client connects
        # returns a new socket (conn) for that specific client
        conn, addr = server.accept()

        # one thread per client = handle multiple clients simultaneously
        # without threading the server could only talk to one client at a time
        thread = threading.Thread(target=handle_tcp_client, args=(conn, addr))
        thread.daemon = True   # killed automatically when main program exits
        thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# WEB SERVER SECTION
# Flask + SocketIO serves the browser-based UI
# WebSockets handle real-time pub-sub for browser clients
# ─────────────────────────────────────────────────────────────────────────────

# Flask app — static_folder tells it where to find style.css, app.js etc.
app = Flask(__name__, static_folder="static", template_folder="static")
app.config["SECRET_KEY"] = "netthreads_secret"

# SocketIO wraps Flask to add WebSocket support
# cors_allowed_origins="*" allows connections from any domain — required for Railway
socketio = SocketIO(app, cors_allowed_origins="*")

# topic → set of session IDs currently subscribed (sets prevent duplicates automatically)
web_subscriptions = {topic: set() for topic in TOPICS}

# sid → username
web_usernames = {}


@app.route("/")
def index():
    # when someone opens the URL, Flask sends back index.html
    # the browser loads it, connects via SocketIO, and the app starts
    return send_from_directory("static", "index.html")


# ── SocketIO event handlers ───────────────────────────────────────────────────
# @socketio.on("event") registers a handler that fires when that event arrives
# this is event-driven programming — server reacts to events, doesn't poll

@socketio.on("connect")
def on_connect():
    """
    fires automatically when a browser tab connects via SocketIO
    sends the full post history from SQLite so the feed is populated immediately
    this is what makes posts survive page refreshes
    """
    web_usernames[sio_request.sid] = "Anonymous"
    emit("server_msg", {"text": "Connected to NetThreads!"})

    # load all posts from the database and send to this client only
    # emit() without broadcast= sends only to the connecting client, not everyone
    all_posts = db_get_all_posts()
    emit("load_posts", {"posts": all_posts})

@socketio.on("disconnect")
def on_disconnect():
    """
    fires when a browser tab closes — clean up subscriptions and username
    """
    sid = sio_request.sid
    for topic in TOPICS:
        web_subscriptions[topic].discard(sid)
    web_usernames.pop(sid, None)

@socketio.on("set_username")
def on_set_username(data):
    sid      = sio_request.sid
    username = data.get("username", "").strip() or "Anonymous"
    web_usernames[sid] = username
    emit("server_msg", {"text": f"Username set to {username}"})

@socketio.on("subscribe")
def on_subscribe(data):
    """
    adds client to topic's subscriber set and SocketIO room
    SocketIO rooms let us broadcast only to relevant subscribers
    without rooms we'd loop through ALL clients and filter — inefficient
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic not in TOPICS:
        emit("server_msg", {"text": f"Unknown topic: {topic}"})
        return

    # sets handle duplicate prevention automatically
    web_subscriptions[topic].add(sid)

    # join_room registers this session so socketio.emit(..., room=topic) reaches them
    join_room(topic)

    emit("subscribed", {"topic": topic})
    emit("server_msg", {"text": f"Subscribed to #{topic}"})

@socketio.on("unsubscribe")
def on_unsubscribe(data):
    """
    removes client from topic's subscriber set and SocketIO room
    future broadcasts to this topic won't reach them
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic in TOPICS:
        # discard (vs remove) won't raise an error if sid wasn't in the set
        web_subscriptions[topic].discard(sid)
        leave_room(topic)

    emit("unsubscribed", {"topic": topic})
    emit("server_msg", {"text": f"Unsubscribed from #{topic}"})

@socketio.on("post")
def on_post(data):
    """
    core publish operation — this is the heart of the pub-sub pattern:
    publisher emits "post" with topic + message
    server (broker) saves to DB then broadcasts to all subscribers via rooms
    publisher doesn't need to know who subscribers are (decoupling)
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

    # current time in milliseconds — matches JS Date.now() on the frontend
    ts = int(time.time() * 1000)

    # ── SAVE TO DATABASE ──────────────────────────────────────────────────────
    # this is what makes the post survive a page refresh
    # the next time any client connects, db_get_all_posts() will return this post
    if post_id:
        db_save_post(post_id, topic, username, message, ts)
        comments[post_id] = []

    # broadcast to all browser clients subscribed to this topic
    # room=topic means only subscribers receive it — this is the broker pattern
    payload = {
        "topic":    topic,
        "username": username,
        "message":  message,
        "post_id":  post_id,
        "ts":       ts,
    }
    socketio.emit("new_post", payload, room=topic)

    # cross-client bridge — forward to raw TCP subscribers too
    formatted = f"[{topic.upper()}] {username}: {message}"
    tcp_broadcast(topic, formatted)

@socketio.on("delete_post")
def on_delete_post(data):
    """
    deletes a post from SQLite and broadcasts deletion to all clients
    also cleans up in-memory comments and votes for that post
    """
    sid      = sio_request.sid
    post_id  = data.get("post_id")
    username = web_usernames.get(sid, "Anonymous")

    # ── DELETE FROM DATABASE ──────────────────────────────────────────────────
    if post_id:
        db_delete_post(post_id)

    # clean up comments and votes for this post from memory
    post_comments = comments.pop(post_id, [])
    votes.pop(post_id, None)
    for cm in post_comments:
        votes.pop(cm.get("comment_id"), None)

    # broadcast to ALL clients (no room filter) so it disappears everywhere
    socketio.emit("post_deleted", {
        "post_id":  post_id,
        "username": username,
    })

# ── Comment handlers ──────────────────────────────────────────────────────────

@socketio.on("post_comment")
def on_post_comment(data):
    """
    stores a new comment in memory and broadcasts it live to all clients
    """
    sid     = sio_request.sid
    post_id = data.get("post_id")
    message = data.get("message", "").strip()

    if not post_id or not message:
        return

    username = web_usernames.get(sid, "Anonymous")

    # int(time.time() * 1000) = milliseconds since epoch — matches JS Date.now()
    ts = int(time.time() * 1000)

    # uuid4 = random universally unique identifier — virtually impossible to collide
    comment_id = str(uuid.uuid4())

    comment = {"comment_id": comment_id, "username": username, "message": message, "ts": ts}

    if post_id not in comments:
        comments[post_id] = []
    comments[post_id].append(comment)

    # broadcast to ALL clients (no room filter) so comments appear live everywhere
    socketio.emit("new_comment", {
        "post_id":    post_id,
        "comment_id": comment_id,
        "username":   username,
        "message":    message,
        "ts":         ts,
    })

@socketio.on("get_comments")
def on_get_comments(data):
    """
    returns stored comments for a post to the requesting client only
    """
    post_id = data.get("post_id")
    emit("comments_list", {
        "post_id":  post_id,
        "comments": comments.get(post_id, []),
    })

# ── Vote handlers ─────────────────────────────────────────────────────────────

@socketio.on("cast_vote")
def on_cast_vote(data):
    """
    handles upvotes and downvotes on posts and comments
    uses sets to guarantee one vote per user
    toggling: upvoting something you already upvoted removes your vote
    broadcasts updated counts to ALL clients so scores update live
    """
    sid         = sio_request.sid
    target_type = data.get("target_type")   # "post" or "comment"
    target_id   = data.get("target_id")     # post_id or comment_id
    direction   = data.get("direction")     # "up", "down", or None (toggle off)

    if not target_id or target_type not in ("post", "comment"):
        return

    # initialise vote bucket if this target hasn't been voted on yet
    if target_id not in votes:
        votes[target_id] = {"up": set(), "down": set()}

    bucket = votes[target_id]

    # remove any previous vote by this user first
    # this handles switching from up to down, or toggling off
    bucket["up"].discard(sid)
    bucket["down"].discard(sid)

    # apply the new direction (None = toggle off, no new vote added)
    if direction in ("up", "down"):
        bucket[direction].add(sid)

    # len(set) = number of unique voters
    up   = len(bucket["up"])
    down = len(bucket["down"])

    # broadcast updated counts to ALL clients so scores update live everywhere
    socketio.emit("vote_updated", {
        "target_type": target_type,
        "target_id":   target_id,
        "up":          up,
        "down":        down,
    })

# ── Subscriptions restore ─────────────────────────────────────────────────────

@socketio.on("get_subscriptions")
def on_get_subscriptions():
    """
    called on page load — returns which topics this session is subscribed to
    allows the UI to restore subscribed state after a page refresh
    """
    sid     = sio_request.sid
    my_subs = [t for t in TOPICS if sid in web_subscriptions[t]]
    emit("my_subscriptions", {"topics": my_subs})


# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────

def get_local_ip():
    """
    figures out the machine's local network IP address
    trick: "connect" to an external address via UDP — no data is sent
    the OS picks the correct outgoing interface, which reveals our local IP
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # Google's DNS — just used for routing
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# everything above is definitions — this block runs when you do: python server.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # initialise SQLite — creates netthreads.db and the posts table if needed
    init_db()

    # start raw TCP server in a background daemon thread
    tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
    tcp_thread.start()

    # ── PORT DETECTION ────────────────────────────────────────────────────────
    # locally:   os.environ.get("PORT") returns None → defaults to 8000
    # on Railway: the platform injects PORT automatically
    #             we MUST listen on this port or the deployment fails
    web_port = int(os.environ.get("PORT", 8000))

    local_ip = get_local_ip()

    print("=" * 60)
    print("  NetThreads Server")
    print("=" * 60)
    print()

    if web_port == 8000:
        print(f"  [Web UI]  http://localhost:{web_port}        ← this machine only")
        print(f"  [Web UI]  http://{local_ip}:{web_port}  ← anyone on same WiFi")
        print()
        print(f"  [TCP]     localhost:{PORT}               ← this machine only")
        print(f"  [TCP]     {local_ip}:{PORT}        ← anyone on same WiFi")
    else:
        print(f"  [Web UI]  Running on cloud — port {web_port}")
        print(f"  [TCP]     localhost:{PORT} (LAN only — cloud doesn't expose raw TCP)")

    print()
    print("  [DB]      Posts stored in →", DB_PATH)
    print("=" * 60)

    # host="0.0.0.0" accepts connections from any IP — required for cloud
    # port=web_port uses Railway's assigned port, or 8000 locally
    # debug=False — never use debug mode in production (exposes internals)
    socketio.run(app, host="0.0.0.0", port=web_port, debug=False)