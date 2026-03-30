import time
import socket
import threading

from topics import TOPICS

# ─────────────────────────────────────────────────────────────────────────────
# tcp_server.py — raw TCP socket server for NetThreads
# keeps the original terminal client (client.py) working alongside the web UI
# ─────────────────────────────────────────────────────────────────────────────

HOST = "0.0.0.0"
PORT = 5000

# in-memory subscription lists for TCP clients — { topic: [socket, socket, ...] }
tcp_subscriptions = {topic: [] for topic in TOPICS}

# maps each TCP socket connection to its username
tcp_usernames = {}

# socketio reference — injected from server.py after Flask app is created
# this allows TCP posts to be forwarded to web clients (cross-client bridge)
_socketio = None

def set_socketio(sio):
    """
    called by server.py after socketio is created so TCP handlers
    can emit events to web clients without a circular import
    """
    global _socketio
    _socketio = sio


def tcp_broadcast(topic, message):
    """
    sends a message to every TCP client subscribed to a topic
    uses list() to avoid RuntimeError if a client disconnects mid-iteration
    """
    for client in list(tcp_subscriptions[topic]):
        try:
            client.send(message.encode())
        except:
            # client disconnected — ignore, cleanup happens in handle_tcp_client
            pass


def handle_tcp_client(conn, addr):
    """
    runs in a separate thread for each connected TCP client
    parses pipe-delimited commands: USER|name, SUBSCRIBE|topic,
    UNSUBSCRIBE|topic, POST|topic|message
    """
    print("Connected:", addr)
    username = "Anonymous"
    tcp_usernames[conn] = username

    while True:
        try:
            data = conn.recv(1024).decode()
            if not data:
                break  # empty string means client closed the connection gracefully

            parts   = data.split("|")
            command = parts[0]

            # ── USER ─────────────────────────────────────────────────────────
            if command == "USER":
                username = parts[1]
                tcp_usernames[conn] = username
                conn.send(f"Username set to {username}".encode())

            # ── SUBSCRIBE ─────────────────────────────────────────────────────
            elif command == "SUBSCRIBE":
                topic = parts[1]
                if topic in TOPICS:
                    if conn not in tcp_subscriptions[topic]:
                        tcp_subscriptions[topic].append(conn)
                    conn.send(f"Subscribed to {topic}".encode())
                else:
                    conn.send("Invalid topic".encode())

            # ── UNSUBSCRIBE ───────────────────────────────────────────────────
            elif command == "UNSUBSCRIBE":
                topic = parts[1]
                if topic in TOPICS and conn in tcp_subscriptions[topic]:
                    tcp_subscriptions[topic].remove(conn)
                conn.send(f"Unsubscribed from {topic}".encode())

            # ── POST ──────────────────────────────────────────────────────────
            # core pub-sub: server routes message only to subscribers of that topic
            # publisher does not know who or how many subscribers exist (decoupling)
            elif command == "POST":
                topic   = parts[1]
                message = parts[2]

                if topic not in TOPICS:
                    conn.send("Invalid topic".encode())
                    continue

                username     = tcp_usernames.get(conn, "Anonymous")
                full_message = f"[{topic.upper()}] {username}: {message}"

                # deliver to all TCP subscribers of this topic
                tcp_broadcast(topic, full_message)

                # cross-client bridge: forward to web clients via SocketIO room
                if _socketio:
                    _socketio.emit("new_post", {
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

    # ── cleanup on disconnect ─────────────────────────────────────────────────
    for topic in tcp_subscriptions:
        if conn in tcp_subscriptions[topic]:
            tcp_subscriptions[topic].remove(conn)

    if conn in tcp_usernames:
        del tcp_usernames[conn]

    conn.close()
    print("Disconnected:", addr)


def run_tcp_server():
    """
    creates the TCP server socket and loops forever accepting new clients
    each client gets its own daemon thread via handle_tcp_client

    AF_INET  = IPv4 addressing
    SOCK_STREAM = TCP — reliable, ordered, connection-based (unlike UDP/SOCK_DGRAM)
    SO_REUSEADDR = prevents "Address already in use" error on quick restart
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen()
    print(f"[TCP] Listening on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        thread = threading.Thread(target=handle_tcp_client, args=(conn, addr))
        thread.daemon = True  # killed automatically when main process exits
        thread.start()