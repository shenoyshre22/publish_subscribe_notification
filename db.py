import sqlite3

# ─────────────────────────────────────────────────────────────────────────────
# db.py — all SQLite database logic for NetThreads
# handles posts and subscriptions — both need to survive server restarts
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

    # posts table — stores every published post permanently
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id  TEXT PRIMARY KEY,
            topic    TEXT NOT NULL,
            username TEXT NOT NULL,
            message  TEXT NOT NULL,
            ts       INTEGER NOT NULL
        )
    """)

    # subscriptions table — stores which username is subscribed to which topic
    # PRIMARY KEY (username, topic) prevents duplicate rows at the database level
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


# ── Post helpers ──────────────────────────────────────────────────────────────

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
    client sorts newest-first after receiving
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT post_id, topic, username, message, ts FROM posts ORDER BY ts ASC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def db_get_posts_by_topics(topics):
    """
    fetches all posts for a list of topics, ordered oldest first
    used to send history when a user subscribes or restores subscriptions
    placeholders are generated dynamically to safely handle any number of topics
    """
    if not topics:
        return []
    conn = get_db()
    placeholders = ",".join("?" for _ in topics)
    rows = conn.execute(
        f"SELECT post_id, topic, username, message, ts FROM posts WHERE topic IN ({placeholders}) ORDER BY ts ASC",
        topics
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ── Subscription helpers ──────────────────────────────────────────────────────

def db_save_subscription(username, topic):
    """
    saves a subscription — OR IGNORE prevents duplicates
    anonymous users are not persisted since multiple users share that label
    """
    if username == "Anonymous":
        return
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO subscriptions (username, topic) VALUES (?,?)",
        (username, topic)
    )
    conn.commit()
    conn.close()


def db_delete_subscription(username, topic):
    """
    removes a subscription — called when a user explicitly unsubscribes
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
    called on set_username so the UI can restore subscriptions on reconnect
    """
    if username == "Anonymous":
        return []
    conn = get_db()
    rows = conn.execute(
        "SELECT topic FROM subscriptions WHERE username = ?", (username,)
    ).fetchall()
    conn.close()
    return [row["topic"] for row in rows]