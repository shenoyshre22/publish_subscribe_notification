import os               # lets us read environment variables (like PORT on Railway)
import time             # used to generate timestamps for comments
import uuid             # generates unique IDs for comments
import socket           # provides networking functionality (TCP sockets)
import threading        # allows multiple clients to connect simultaneously

from topics import TOPICS     # import the list of allowed topics from topics.py
                              # both server and client import from the same file
                              # so topics are always in sync

# NEW: Flask is a lightweight web framework — it lets us serve the HTML page
# and handle HTTP requests (like when someone opens localhost:8000 in a browser)
from flask import Flask, send_from_directory

# SocketIO extends Flask with WebSocket support
# WebSockets are different from regular HTTP — they keep a persistent two-way
# connection open between browser and server, which is how real-time messaging works
# without WebSockets the browser would have to keep refreshing to check for new posts
from flask_socketio import SocketIO, emit, join_room, leave_room

# Flask's request object gives us info about the current connection
# we use sio_request.sid — the unique session ID for each browser tab
from flask import request as sio_request


# ─────────────────────────────────────────────────────────────────────────────
# RAW TCP SERVER SECTION
# this is the original pub-sub system using raw TCP sockets
# kept intact so client.py still works on a local network
# ─────────────────────────────────────────────────────────────────────────────

# 0.0.0.0 means "listen on all available network interfaces"
# this is different from 127.0.0.1 (localhost) which only accepts connections
# from the same machine — 0.0.0.0 accepts from any IP on the network
HOST = "0.0.0.0"

# port number where the TCP server listens
# port 5000 is commonly used for development servers
# ports below 1024 are reserved for system services so we avoid those
PORT = 5000


# dictionary that maps each topic to a list of connected TCP client sockets
# when a client subscribes, their socket is added to that topic's list
# when a message is posted, we loop through this list to broadcast it
# example: { "sports": [<socket1>, <socket3>], "gaming": [<socket2>] }
tcp_subscriptions = {topic: [] for topic in TOPICS}

# dictionary that maps each connected socket to its username
# key = socket object, value = username string
# used when formatting broadcast messages like "[SPORTS] alice: hello"
tcp_usernames = {}

# ── in-memory data stores for comments and votes ──────────────────────────────

# comments store — maps each post_id to a list of comment objects
# each comment object has: comment_id, username, message, ts (timestamp)
# example: { "post_abc": [ {"comment_id": "xyz", "username": "alice", ...} ] }
comments = {}

# votes store — maps each target_id (post or comment) to vote counts
# each entry has two sets: "up" (upvoters' sids) and "down" (downvoters' sids)
# using sets ensures one vote per user — adding the same sid twice has no effect
# example: { "post_abc": { "up": {"sid1", "sid2"}, "down": {"sid3"} } }
votes = {}


def tcp_broadcast(topic, message):
    """
    sends a formatted message string to every TCP client subscribed to a topic
    called when a TCP client posts a message, and also when a web client posts
    (so terminal clients receive web posts too — cross-client broadcasting)
    """

    # loop through every socket in this topic's subscriber list
    for client in list(tcp_subscriptions[topic]):
        # we use list() to make a copy before iterating
        # this prevents crashes if a client disconnects mid-loop
        try:
            # encode converts the Python string to bytes (required for socket.send)
            client.send(message.encode())

        except:
            # if sending fails (client disconnected), ignore it
            # the client will be properly cleaned up when handle_tcp_client detects the disconnect
            pass


def handle_tcp_client(conn, addr):
    """
    this function runs in a separate thread for each connected TCP client
    it loops forever, reading commands from the client and acting on them
    the thread ends when the client disconnects
    
    conn = the socket object for this specific client connection
    addr = (ip_address, port) tuple identifying who connected
    """

    print("Connected:", addr)

    # set a default username — client can change this with the USER command
    username = "Anonymous"
    tcp_usernames[conn] = username

    # keep reading data from this client until they disconnect
    while True:
        try:
            # recv(1024) reads up to 1024 bytes from the client
            # .decode() converts the bytes back to a Python string
            data = conn.recv(1024).decode()

            # if recv returns empty bytes, the client has closed the connection
            if not data:
                break

            # all commands use pipe | as a separator
            # split("| ") breaks the string into a list of parts
            # format expected:
            # USER|name                 → set username
            # SUBSCRIBE|topic           → subscribe to a topic
            # UNSUBSCRIBE|topic         → unsubscribe from a topic
            # POST|topic|message        → broadcast a message to a topic
            parts = data.split("|")

            # first part is always the command type
            command = parts[0]


            # -------------------------
            # handle USER command
            # saves the username so it appears in broadcast messages
            # -------------------------
            if command == "USER":

                username = parts[1]
                tcp_usernames[conn] = username

                # send a confirmation back to the client
                conn.send(f"Username set to {username}".encode())


            # -------------------------
            # handle SUBSCRIBE command
            # adds this client's socket to the topic's subscriber list
            # -------------------------
            elif command == "SUBSCRIBE":

                topic = parts[1]

                # check if topic exists in our allowed topics list
                if topic in TOPICS:

                    # FIX: prevent duplicate subscriptions
                    # without this check, the client would receive duplicate messages
                    if conn not in tcp_subscriptions[topic]:
                        tcp_subscriptions[topic].append(conn)

                    # send a confirmation message back to the client
                    conn.send(f"Subscribed to {topic}".encode())

                else:
                    # topic doesn't exist — tell the client
                    conn.send("Invalid topic".encode())


            # -------------------------
            # handle UNSUBSCRIBE command
            # removes this client's socket from the topic's subscriber list
            # -------------------------
            elif command == "UNSUBSCRIBE":

                topic = parts[1]

                # only remove if they were actually subscribed
                # avoids a ValueError if the socket isn't in the list
                if topic in TOPICS and conn in tcp_subscriptions[topic]:
                    tcp_subscriptions[topic].remove(conn)

                conn.send(f"Unsubscribed from {topic}".encode())


            # -------------------------
            # handle POST command
            # broadcasts a message to all subscribers of a topic
            # this is the core pub-sub operation — the server acts as a broker
            # the publisher (poster) doesn't need to know who the subscribers are
            # -------------------------
            elif command == "POST":

                topic   = parts[1]
                message = parts[2]

                # validate topic before broadcasting
                if topic not in TOPICS:
                    conn.send("Invalid topic".encode())
                    continue

                # look up the sender's username
                username = tcp_usernames.get(conn, "Anonymous")

                # format the message with topic and username for display
                full_message = f"[{topic.upper()}] {username}: {message}"

                # broadcast to all TCP clients subscribed to this topic
                tcp_broadcast(topic, full_message)

                # cross-client bridge: also push to web (browser) clients
                # subscribed to this topic via SocketIO rooms
                # this means a terminal user posting reaches browser users too
                socketio.emit("new_post", {
                    "topic":    topic,
                    "username": username,
                    "message":  message,
                    "post_id":  None,   # TCP posts don't have a post_id so deletion won't work
                }, room=topic)

            else:
                # unknown command — send an error back
                conn.send("Unknown command".encode())

        except:
            # any error (connection reset, broken pipe, etc.) breaks the loop
            # and triggers the cleanup code below
            break

    # ── CLEANUP on disconnect ─────────────────────────────────────────────────
    # when a client disconnects, remove them from ALL topic subscriber lists
    # without this, broadcasting would try to send to a closed socket and crash
    for topic in tcp_subscriptions:
        if conn in tcp_subscriptions[topic]:
            tcp_subscriptions[topic].remove(conn)

    # remove the username entry to free memory
    if conn in tcp_usernames:
        del tcp_usernames[conn]

    # close the socket to release the OS-level resource
    conn.close()
    print("Disconnected:", addr)


def run_tcp_server():
    """
    creates and starts the raw TCP socket server
    runs in a background daemon thread so the Flask web server can
    run on the main thread — both servers run simultaneously
    """

    # AF_INET = IPv4 addressing (as opposed to AF_INET6 for IPv6)
    # SOCK_STREAM = TCP (as opposed to SOCK_DGRAM which is UDP)
    # TCP is used here because it guarantees delivery and ordering of messages
    # UDP would be faster but messages could be lost or arrive out of order
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # SOL_SOCKET = socket-level option (as opposed to protocol-level)
    # SO_REUSEADDR = allow reuse of the port immediately after the server restarts
    # without this, if you stop and restart the server quickly you get
    # "Address already in use" error because the OS holds the port for a few seconds
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # bind tells the OS "this socket owns this address+port"
    # HOST = 0.0.0.0 means listen on all network interfaces (WiFi, ethernet, etc.)
    server.bind((HOST, PORT))

    # listen() puts the socket into passive mode — ready to accept connections
    # the OS will queue incoming connection requests until we call accept()
    server.listen()

    print(f"[TCP] Listening on {HOST}:{PORT}")

    # server loop — runs forever, accepting one client at a time
    while True:

        # accept() blocks until a client connects
        # returns a new socket (conn) for that specific client
        # and the client's address (addr) as (ip, port)
        conn, addr = server.accept()

        # create a new thread to handle this client
        # this is what makes the server handle multiple clients simultaneously
        # without threading, the server could only talk to one client at a time
        # each client gets their own thread that runs handle_tcp_client()
        thread = threading.Thread(target=handle_tcp_client, args=(conn, addr))

        # daemon=True means this thread will be killed automatically
        # when the main program exits (no need to manually clean up)
        thread.daemon = True

        # start the thread — handle_tcp_client now runs in parallel
        thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# WEB SERVER SECTION
# Flask + SocketIO serves the browser-based UI (index.html)
# WebSockets handle real-time pub-sub for browser clients
# this runs on port 8000 (or whatever PORT Railway assigns)
# ─────────────────────────────────────────────────────────────────────────────

# Flask app — static_folder tells it where to find index.html
app = Flask(__name__, static_folder="static", template_folder="static")

# SECRET_KEY is required by Flask for session security
# in production this should be a long random string stored in an environment variable
app.config["SECRET_KEY"] = "netthreads_secret"

# SocketIO wraps our Flask app to add WebSocket support
# cors_allowed_origins="*" means accept WebSocket connections from any domain
# this is needed for Railway deployment where the domain changes
socketio = SocketIO(app, cors_allowed_origins="*")

# web equivalent of tcp_subscriptions
# maps each topic to a set of connected session IDs (sids)
# sets automatically prevent duplicates (no need to check like we did for TCP)
web_subscriptions = {topic: set() for topic in TOPICS}

# web equivalent of tcp_usernames
# maps each session ID (sid) to the user's chosen username
web_usernames = {}


# ── HTTP route ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    # when someone opens http://localhost:8000 in their browser
    # Flask sends back the index.html file from the static/ folder
    # the browser then loads it, connects via SocketIO, and the app starts
    return send_from_directory("static", "index.html")


# ── SocketIO event handlers ───────────────────────────────────────────────────
# each @socketio.on("event_name") decorator registers a handler function
# SocketIO calls the right handler whenever that event arrives from any client
# this is event-driven programming — the server reacts to events, not polls

@socketio.on("connect")
def on_connect():
    """
    fires automatically when a browser tab opens the app and connects via SocketIO
    sio_request.sid is a unique string ID for this specific browser tab/session
    """
    web_usernames[sio_request.sid] = "Anonymous"
    emit("server_msg", {"text": "Connected to NetThreads!"})

@socketio.on("disconnect")
def on_disconnect():
    """
    fires automatically when a browser tab closes or loses connection
    cleans up all subscriptions and the username entry for this session
    """
    sid = sio_request.sid

    # remove this session from every topic's subscriber set
    for topic in TOPICS:
        web_subscriptions[topic].discard(sid)

    # remove username entry to free memory
    web_usernames.pop(sid, None)

@socketio.on("set_username")
def on_set_username(data):
    """
    called when the user types their name in the username modal
    updates the server-side mapping so their name appears on posts
    """
    sid      = sio_request.sid
    username = data.get("username", "").strip() or "Anonymous"
    web_usernames[sid] = username
    emit("server_msg", {"text": f"Username set to {username}"})

@socketio.on("subscribe")
def on_subscribe(data):
    """
    called when a user clicks Subscribe on a topic pill
    adds them to the topic's subscriber set and to the SocketIO room for that topic
    SocketIO rooms are how we efficiently broadcast only to relevant subscribers
    without rooms we'd have to loop through ALL connected clients and filter
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    # validate the topic
    if topic not in TOPICS:
        emit("server_msg", {"text": f"Unknown topic: {topic}"})
        return

    # add to our subscription tracking set
    # sets handle duplicate prevention automatically
    web_subscriptions[topic].add(sid)

    # join_room registers this session in a SocketIO room named after the topic
    # later when we do socketio.emit("new_post", ..., room=topic)
    # SocketIO automatically delivers it only to sessions in that room
    join_room(topic)

    # confirm back to the client so the UI can update
    emit("subscribed", {"topic": topic})
    emit("server_msg", {"text": f"Subscribed to #{topic}"})

@socketio.on("unsubscribe")
def on_unsubscribe(data):
    """
    called when a user clicks Unsubscribe on a topic
    removes them from the subscriber set and leaves the SocketIO room
    they will no longer receive broadcasts for this topic
    """
    sid   = sio_request.sid
    topic = data.get("topic", "")

    if topic in TOPICS:
        # discard (vs remove) won't raise an error if the sid wasn't in the set
        web_subscriptions[topic].discard(sid)

        # leave_room removes this session from the SocketIO room
        # future broadcasts to this room won't reach them
        leave_room(topic)

    emit("unsubscribed", {"topic": topic})
    emit("server_msg", {"text": f"Unsubscribed from #{topic}"})

@socketio.on("post")
def on_post(data):
    """
    called when a user submits a new post from the web UI
    this is the core publish operation:
      - the publisher emits "post" with topic + message
      - the server (broker) looks up who is subscribed
      - the server broadcasts to all subscribers via SocketIO rooms
      - the publisher doesn't need to know who the subscribers are (decoupling)
    """
    sid     = sio_request.sid
    topic   = data.get("topic", "")
    message = data.get("message", "").strip()

    # validate the topic
    if topic not in TOPICS:
        emit("server_msg", {"text": "Invalid topic."})
        return

    # don't broadcast empty messages
    if not message:
        emit("server_msg", {"text": "Empty message not sent."})
        return

    username = web_usernames.get(sid, "Anonymous")

    # post_id is generated on the client side (see app.js submitPost())
    # it's a unique string used to identify this post for deletion later
    post_id = data.get("post_id")

    # initialise an empty comment list for this post in memory
    if post_id:
        comments[post_id] = []

    # build the payload to broadcast
    payload = {
        "topic":    topic,
        "username": username,
        "message":  message,
        "post_id":  post_id,
    }

    # broadcast to ALL browser clients in this topic's SocketIO room
    # room=topic means only subscribers of this topic receive it
    # this is the subscribe-dispatch (broker) pattern in action
    socketio.emit("new_post", payload, room=topic)

    # cross-client bridge: also forward to raw TCP subscribers
    # so terminal client.py users receive web posts too
    formatted = f"[{topic.upper()}] {username}: {message}"
    tcp_broadcast(topic, formatted)

@socketio.on("delete_post")
def on_delete_post(data):
    """
    called when a user clicks the delete button on their own post
    broadcasts the deletion to ALL connected clients (not just subscribers)
    so the post disappears from everyone's feed simultaneously
    also cleans up any comments and votes stored for that post
    """
    sid     = sio_request.sid
    post_id = data.get("post_id")
    username = web_usernames.get(sid, "Anonymous")

    # clean up all comments for this post from memory
    post_comments = comments.pop(post_id, [])

    # clean up the vote bucket for the post itself
    votes.pop(post_id, None)

    # also clean up vote buckets for each comment on this post
    for cm in post_comments:
        votes.pop(cm.get("comment_id"), None)

    # broadcast deletion to ALL clients (no room filter)
    # so regardless of who is subscribed, the post disappears everywhere
    socketio.emit("post_deleted", {
        "post_id":  post_id,
        "username": username,
    })


# ── Comment handlers ──────────────────────────────────────────────────────────

@socketio.on("post_comment")
def on_post_comment(data):
    """
    called when a user submits a comment on a post
    generates a unique comment_id and timestamp server-side
    broadcasts the new comment to ALL connected clients
    so it appears live on everyone's screen who has that post visible
    """
    sid     = sio_request.sid
    post_id = data.get("post_id")
    message = data.get("message", "").strip()

    # don't process empty comments or comments without a parent post
    if not post_id or not message:
        return

    username = web_usernames.get(sid, "Anonymous")

    # int(time.time() * 1000) gives milliseconds since epoch
    # this matches JavaScript's Date.now() format used on the frontend
    ts = int(time.time() * 1000)

    # uuid4 generates a random universally unique identifier
    # virtually impossible to collide even with thousands of comments
    comment_id = str(uuid.uuid4())

    # build the comment object
    comment = {
        "comment_id": comment_id,
        "username":   username,
        "message":    message,
        "ts":         ts,
    }

    # store in our in-memory comments dictionary
    if post_id not in comments:
        comments[post_id] = []
    comments[post_id].append(comment)

    # broadcast to ALL connected clients (no room filter)
    # so the comment appears live for everyone who can see the post
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
    called when a client wants to load existing comments for a post
    (e.g. when they first open the comments section)
    returns the full list of stored comments for that post
    """
    post_id = data.get("post_id")

    # emit only back to the requesting client (no broadcast)
    emit("comments_list", {
        "post_id":  post_id,
        "comments": comments.get(post_id, []),
    })


# ── Vote handlers ─────────────────────────────────────────────────────────────

@socketio.on("cast_vote")
def on_cast_vote(data):
    """
    called when a user upvotes or downvotes a post or comment
    uses sets to ensure one vote per user — adding the same sid twice has no effect
    toggling: if you upvote something you already upvoted, the vote is removed
    broadcasts updated vote counts to ALL clients so scores update live
    """
    sid         = sio_request.sid
    target_type = data.get("target_type")   # "post" or "comment"
    target_id   = data.get("target_id")     # post_id or comment_id
    direction   = data.get("direction")     # "up", "down", or None (toggle off)

    # validate inputs
    if not target_id or target_type not in ("post", "comment"):
        return

    # initialise vote bucket for this target if it doesn't exist yet
    if target_id not in votes:
        votes[target_id] = {"up": set(), "down": set()}

    bucket = votes[target_id]

    # remove any previous vote by this user (handles switching from up to down etc.)
    bucket["up"].discard(sid)
    bucket["down"].discard(sid)

    # apply the new vote direction
    # if direction is None it means the user is toggling their vote off — no new vote added
    if direction in ("up", "down"):
        bucket[direction].add(sid)

    # count the votes — len(set) gives the number of unique voters
    up   = len(bucket["up"])
    down = len(bucket["down"])

    # broadcast updated counts to ALL clients so vote scores update live everywhere
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
    called when the page first loads (or after a reconnect)
    returns the list of topics this session is currently subscribed to
    allows the UI to restore the subscribed state after a page refresh
    """
    sid     = sio_request.sid
    my_subs = [t for t in TOPICS if sid in web_subscriptions[t]]
    emit("my_subscriptions", {"topics": my_subs})


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def get_local_ip():
    """
    figures out the machine's local network IP address (e.g. 192.168.1.5)
    this is what other devices on the same WiFi network use to connect
    
    the trick: create a UDP socket and "connect" to an external address
    this doesn't actually send any data — it just makes the OS pick
    the correct outgoing network interface, which tells us our local IP
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # 8.8.8.8 is Google's DNS — just used for routing
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # if anything fails, fall back to localhost
        return "127.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# everything above is just function/variable definitions
# this block runs when you execute: python server.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # start the raw TCP server in a background daemon thread
    # daemon=True means this thread dies automatically when the main program exits
    # it runs run_tcp_server() in parallel with the Flask web server below
    tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
    tcp_thread.start()

    # ── PORT DETECTION ────────────────────────────────────────────────────────
    # when running locally:
    #   os.environ.get("PORT") returns None → we use our default 8000
    #
    # when deployed on Railway (or Heroku, Render, etc.):
    #   the platform sets the PORT environment variable automatically
    #   we MUST listen on this port or the deployment fails
    #   Railway routes external traffic to whatever port this variable says
    #
    # int() converts the string "8000" or "3000" etc. to an integer
    web_port = int(os.environ.get("PORT", 8000))

    # get local IP for the startup message
    local_ip = get_local_ip()

    # ── STARTUP INFO ──────────────────────────────────────────────────────────
    print("=" * 60)
    print("  NetThreads Server")
    print("=" * 60)
    print()

    if web_port == 8000:
        # running locally — show both localhost and LAN addresses
        print(f"  [Web UI]  http://localhost:{web_port}        ← this machine only")
        print(f"  [Web UI]  http://{local_ip}:{web_port}  ← anyone on same WiFi")
        print()
        print(f"  [TCP]     localhost:{PORT}               ← this machine only")
        print(f"  [TCP]     {local_ip}:{PORT}        ← anyone on same WiFi")
        print()
        print("  For global access (different cities):")
        print("    push to GitHub → deploy on Railway → share the Railway URL")
    else:
        # running on Railway — PORT was injected by the platform
        print(f"  [Web UI]  Running on cloud — port {web_port}")
        print(f"  [TCP]     localhost:{PORT} (LAN only — cloud doesn't expose raw TCP)")

    print()
    print("=" * 60)

    # ── START THE WEB SERVER ──────────────────────────────────────────────────
    # host="0.0.0.0" — accept connections from any IP address (required for cloud)
    # port=web_port  — use Railway's assigned port, or 8000 locally
    # debug=False    — debug mode is dangerous in production (exposes internals)
    socketio.run(app, host="0.0.0.0", port=web_port, debug=False)