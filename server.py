import socket                 # provides networking functionality
import threading              # allows multiple clients to connect simultaneously
from topics import TOPICS     # import the list of allowed topics

# Flask + SocketIO so the browser (index.html) can connect via WebSockets (to integrate frontend and backend in one server)
from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask import request as sio_request

# RAW TCP SERVER (original — keeps client.py working)


# 0.0.0.0 means accept connections from any machine on the network
HOST = "0.0.0.0"

# port number where the server listens, lets choose 5000 for ease??
PORT = 5000


# create a dictionary to store topic subscriptions
tcp_subscriptions = {topic: [] for topic in TOPICS}

# NEW: store usernames of clients
tcp_usernames = {}


def tcp_broadcast(topic, message):

    # send a message to every client subscribed to a topic

    # get the list of subscribers for this topic
    for client in list(tcp_subscriptions[topic]):
        try:
            # send message to the client
            client.send(message.encode())

        except:
            # if sending fails (client disconnected), ignore it
            pass


def handle_tcp_client(conn, addr):
    """
    this function runs in a separate thread for each client
    """

    print("Connected:", addr)

    # NEW: default username
    username = "Anonymous"
    tcp_usernames[conn] = username

    while True:
        try:
            # receive data from the client
            data = conn.recv(1024).decode()

            # if no data, connection closed
            if not data:
                break

            # split the command
            # format expected:
            # USER|name
            # SUBSCRIBE|topic
            # POST|topic|message
            parts = data.split("|")

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

                # check if topic exists
                if topic in TOPICS:

                    # FIX: prevent duplicate subscriptions
                    if conn not in tcp_subscriptions[topic]:
                        tcp_subscriptions[topic].append(conn)

                    # confirmation message
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
            # -------------------------
            elif command == "POST":

                topic = parts[1]
                message = parts[2]

                # validate topic
                if topic not in TOPICS:
                    conn.send("Invalid topic".encode())
                    continue

                # get username of sender
                username = tcp_usernames.get(conn, "Anonymous")

                # format message for display
                full_message = f"[{topic.upper()}] {username}: {message}"

                # send message to all TCP subscribers
                tcp_broadcast(topic, full_message)

                # NEW: also push to web (browser) clients subscribed to this topic
                socketio.emit("new_post", {
                    "topic": topic,
                    "username": username,
                    "message": message,
                }, room=topic)

            else:
                conn.send("Unknown command".encode())

        except:
            break

    # FIX: remove client from all topics on disconnect
    for topic in tcp_subscriptions:
        if conn in tcp_subscriptions[topic]:
            tcp_subscriptions[topic].remove(conn)

    # remove username entry
    if conn in tcp_usernames:
        del tcp_usernames[conn]

    # close connection if client disconnects
    conn.close()
    print("Disconnected:", addr)


def run_tcp_server():
    """
    creates the raw TCP socket server and keeps accepting new clients
    runs in a background thread so Flask can run on the main thread
    """

    # create a TCP socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # allows reuse of the port immediately after restart
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # bind the server to HOST and PORT
    server.bind((HOST, PORT))

    # start listening for incoming connections
    server.listen()

    print(f"[TCP] Listening on {HOST}:{PORT}")

    # server loop that constantly accepts new clients
    while True:

        # wait for a client to connect
        conn, addr = server.accept()

        # create a new thread to handle that client
        thread = threading.Thread(target=handle_tcp_client, args=(conn, addr))

        # start the thread
        thread.start()

# WEB SERVER  (NEW addd on — this serves index.html and handles browser WebSocket clients)


# Flask serves the static files (index.html, style.css, app.js)
app = Flask(__name__, static_folder="static", template_folder="static")
app.config["SECRET_KEY"] = "netthreads_secret"

# SocketIO replaces the raw TCP layer for browser clients
socketio = SocketIO(app, cors_allowed_origins="*")

# topic → set of socket-io session ids (one per browser tab)
web_subscriptions = {topic: set() for topic in TOPICS}

# sid → username  (browser equivalent of tcp_usernames)
web_usernames = {}


# serve index.html at the root URL
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── SocketIO event handlers (browser clients) ─────────────────────────────────

@socketio.on("connect")
def on_connect():
    # called when a browser tab connects
    web_usernames[sio_request.sid] = "Anonymous"
    emit("server_msg", {"text": "Connected to NetThreads!"})

@socketio.on("disconnect")
def on_disconnect():
    # clean up subscriptions and username when browser tab closes
    sid = sio_request.sid
    for topic in TOPICS:
        web_subscriptions[topic].discard(sid)
    web_usernames.pop(sid, None)

@socketio.on("set_username")
def on_set_username(data):
    sid = sio_request.sid
    username = data.get("username", "").strip() or "Anonymous"
    web_usernames[sid] = username
    emit("server_msg", {"text": f"Username set to {username}"})

@socketio.on("subscribe")
def on_subscribe(data):
    sid = sio_request.sid
    topic = data.get("topic", "")

    # check if topic exists in the topics.py file
    if topic not in TOPICS:
        emit("server_msg", {"text": f"Unknown topic: {topic}"})
        return

    # FIX: prevent duplicate subscriptions (same as the TCP side)
    web_subscriptions[topic].add(sid)

    # join_room makes SocketIO route broadcasts only to those who have been subscribers of this topic
    join_room(topic)

    emit("subscribed", {"topic": topic})
    emit("server_msg", {"text": f"Subscribed to #{topic}"})

@socketio.on("unsubscribe")
def on_unsubscribe(data):
    sid = sio_request.sid
    topic = data.get("topic", "")

    if topic in TOPICS:
        web_subscriptions[topic].discard(sid)
        leave_room(topic)

    emit("unsubscribed", {"topic": topic})
    emit("server_msg", {"text": f"Unsubscribed from #{topic}"})

@socketio.on("post")
def on_post(data):
    sid = sio_request.sid
    topic = data.get("topic", "")
    message = data.get("message", "").strip()

    # validate topic
    if topic not in TOPICS:
        emit("server_msg", {"text": "Invalid topic."})
        return

    if not message:
        emit("server_msg", {"text": "Empty message not sent."})
        return

    username = web_usernames.get(sid, "Anonymous")

    # broadcast to all browser clients subscribed to this topic
    payload = {"topic": topic, "username": username, "message": message}
    socketio.emit("new_post", payload, room=topic)

    # also forward to raw TCP subscribers so both client types receive it
    formatted = f"[{topic.upper()}] {username}: {message}"
    tcp_broadcast(topic, formatted)

@socketio.on("get_subscriptions")
def on_get_subscriptions():
    # called on reconnect so the UI can restore the subscribed state
    sid = sio_request.sid
    my_subs = [t for t in TOPICS if sid in web_subscriptions[t]]
    emit("my_subscriptions", {"topics": my_subs})

# ENTRY POINT from heree


if __name__ == "__main__":

    # start raw TCP server in a background daemon thread
    tcp_thread = threading.Thread(target=run_tcp_server, daemon=True)
    tcp_thread.start()

    # start Flask + SocketIO web server (this blocks, so it runs on main thread)
    print("[Web] NetThreads UI → http://localhost:8000")
    socketio.run(app, host="0.0.0.0", port=8000, debug=False)