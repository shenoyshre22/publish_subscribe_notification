import socket
import threading
from topics import TOPICS
# this is client.py
HOST = "127.0.0.1"
PORT = 5000

def receive_messages(sock):
    """
    Runs in a background thread — listens for incoming messages
    (i.e. posts from topics this client is subscribed to)
    """
    while True:
        try:
            message = sock.recv(1024).decode()
            if not message:
                break
            print(f"\n{message}\n> ", end="")
        except:
            print("\n[Disconnected from server]")
            break


def show_topics():
    print("\nAvailable topics:")
    for i, topic in enumerate(TOPICS, 1):
        print(f"  {i}. {topic}")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect((HOST, PORT))
        print(f"Connected to server at {HOST}:{PORT}")
    except ConnectionRefusedError:
        print("Could not connect to server. Is it running?")
        return

    # NEW: username setup
    username = input("Enter your username: ").strip()
    if username:
        sock.send(f"USER|{username}".encode())

    # start background thread to receive incoming broadcast messages
    thread = threading.Thread(target=receive_messages, args=(sock,), daemon=True)
    thread.start()
     #added 6 socket event handlers 
    print("\nCommands:")
    print("  sub   → subscribe to a topic")
    print("  unsub → unsubscribe from a topic")
    print("  post  → post a message to a topic")
    print("  quit  → disconnect\n")

    while True:
        try:
            command = input("> ").strip().lower()

            if command == "quit":
                print("Disconnecting...")
                break

            elif command == "sub":
                show_topics()
                topic = input("Enter topic name to subscribe: ").strip()
                if topic in TOPICS:
                    sock.send(f"SUBSCRIBE|{topic}".encode())
                else:
                    print(f"Unknown topic '{topic}'.")

            elif command == "unsub":
                show_topics()
                topic = input("Enter topic name to unsubscribe: ").strip()
                if topic in TOPICS:
                    sock.send(f"UNSUBSCRIBE|{topic}".encode())
                else:
                    print(f"Unknown topic '{topic}'.")

            elif command == "post":
                show_topics()
                topic = input("Enter topic to post to: ").strip()
                if topic not in TOPICS:
                    print(f"Unknown topic '{topic}'.")
                    continue

                message = input("Enter your message: ").strip()
                if message:
                    sock.send(f"POST|{topic}|{message}".encode())
                else:
                    print("Empty message not sent.")

            else:
                print("Unknown command. Use: sub, unsub, post, quit")

        except KeyboardInterrupt:
            print("\nDisconnecting...")
            break

    sock.close()


if __name__ == "__main__":
    main()