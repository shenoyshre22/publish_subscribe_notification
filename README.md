# Publish–Subscribe Notification System (Python Sockets)

A simple TCP socket-based publish/subscribe system where each client can act as:
- **Subscriber** (subscribe to topics and receive updates)
- **Publisher** (post messages to topics)

The server routes published messages to all subscribers of the selected topic.

---

## Features

- Multi-client support using threads
- Topic-based message distribution
- Shared topic definitions via `topics.py`
- Real-time notifications to subscribed clients
- Single client app that can both publish and subscribe

---

## Project Structure

```text
publish_subscribe_notification/
├── server.py      # TCP server, subscription store, broadcast logic
├── client.py      # Interactive publisher/subscriber client
├── topics.py      # Allowed topics shared by server and client
└── README.md
```

---

## Requirements

- Python 3.8+
- No external dependencies (uses standard library only)

---

## How to Run

### 1) Start the server

```bash
python server.py
```

The server listens on:
- Host: `0.0.0.0`
- Port: `5000`

### 2) Start one or more clients

Open a new terminal for each client:

```bash
python client.py
```

By default, the client connects to `127.0.0.1:5000`.

If your server is on another machine, update `SERVER_HOST` in `client.py`.

---

## Client Commands

Inside the client prompt:

- `topics` → list all available topics
- `subscribe <topic>` → subscribe to a topic
- `unsubscribe <topic>` → request unsubscribe from a topic
- `post <topic> <message>` → publish a message to a topic
- `help` → show command help
- `quit` → close client

Example:

```text
> subscribe sports
> post sports Messi scores in stoppage time!
```

---

## Message Protocol (Client ↔ Server)

The system uses a simple `|`-separated command protocol:

- Subscribe: `SUBSCRIBE|<topic>`
- Unsubscribe: `UNSUBSCRIBE|<topic>`
- Post: `POST|<topic>|<message>`

Broadcast format from server to subscribers:

```text
[TOPIC] message text
```

---

## Topics

Defined in `topics.py`:

- sports
- world_news
- national_news
- pesu_live
- music
- gaming
- hollywood
- bollybuzz
- stocks

---

## Demo Walkthrough (2 Subscribers + 1 Publisher)

Open 4 terminals total:
- Terminal 1: server
- Terminal 2: Client A (subscriber)
- Terminal 3: Client B (subscriber)
- Terminal 4: Client C (publisher)

### Terminal 1 (Server)

```text
python server.py
Connected: ('127.0.0.1', 54321)
Connected: ('127.0.0.1', 54322)
Connected: ('127.0.0.1', 54323)
```

### Terminal 2 (Client A)

```text
python client.py
> subscribe sports
[NOTIFICATION] Subscribed to sports
```

### Terminal 3 (Client B)

```text
python client.py
> subscribe sports
[NOTIFICATION] Subscribed to sports
```

### Terminal 4 (Client C)

```text
python client.py
> post sports Match starts at 7 PM today
```

### What subscribers see

Both Client A and Client B receive:

```text
[NOTIFICATION] [SPORTS] Match starts at 7 PM today
```

This confirms the publish/subscribe flow is working.

---

## Current Notes / Limitations

- The client sends `UNSUBSCRIBE`, but the server must implement handling for this command to fully support unsubscribe behavior.
- Current server message parsing is minimal and assumes valid command format.
- No authentication or encryption (for learning/demo use).

---

## Suggested Improvements

- Add `UNSUBSCRIBE` handling in server logic
- Prevent duplicate subscriptions per client/topic
- Improve input validation and error responses
- Add graceful shutdown and cleanup of disconnected sockets
- Add logging and unit tests

---

## License

This project is for educational use.
