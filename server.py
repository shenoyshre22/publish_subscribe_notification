import socket                 # provides networking functionality
import threading              # allows multiple clients to connect simultaneously
from topics import TOPICS     # import the list of allowed topics

# 0.0.0.0 means accept connections from any machine on the network
HOST = "0.0.0.0"

# port number where the server listens, lets choose 5000 for ease??
PORT = 5000


# create a dictionary to store topic subscriptions
subscriptions = {topic: [] for topic in TOPICS}

# NEW: store usernames of clients
usernames = {}


# create a TCP socket
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# bind the server to HOST and PORT
server.bind((HOST, PORT))

# start listening for incoming connections
server.listen()

def broadcast(topic, message):
    
    #send a message to every client subscribed to a topic
   
    # get the list of subscribers for this topic
    for client in subscriptions[topic]:
        try:
            # send message to the client
            client.send(message.encode())

        except:
            # if sending fails (client disconnected), ignore it
            pass


def handle_client(conn, addr):
    """
    this function runs in a separate thread for each client
    """

    print("Connected:", addr)

    # NEW: default username
    username = "Anonymous"
    usernames[conn] = username

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
                usernames[conn] = username

                conn.send(f"Username set to {username}".encode())


            # -------------------------
            # handle SUBSCRIBE command
            # -------------------------
            elif command == "SUBSCRIBE":

                topic = parts[1]

                # check if topic exists
                if topic in TOPICS:

                    # FIX: prevent duplicate subscriptions
                    if conn not in subscriptions[topic]:
                        subscriptions[topic].append(conn)

                    # confirmation message
                    conn.send(f"Subscribed to {topic}".encode())

                else:
                    conn.send("Invalid topic".encode())


            # -------------------------
            # handle UNSUBSCRIBE command
            # -------------------------
            elif command == "UNSUBSCRIBE":

                topic = parts[1]

                if topic in TOPICS and conn in subscriptions[topic]:
                    subscriptions[topic].remove(conn)

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
                username = usernames.get(conn, "Anonymous")

                # format message for display
                full_message = f"[{topic.upper()}] {username}: {message}"

                # send message to all subscribers
                broadcast(topic, full_message)

            # -------------------------
            # handle UNSUBSCRIBE command
            # -------------------------
            elif command == "UNSUBSCRIBE":

                topic = parts[1]

             # check if topic exists and client is subscribed
                if topic in TOPICS and conn in subscriptions[topic]:

             # remove client from subscriber list
                    subscriptions[topic].remove(conn)

                    conn.send(f"Unsubscribed from {topic}".encode())

            else:
                    conn.send(f"You are not subscribed to {topic}".encode())

        except:
            break

<<<<<<< HEAD
    # FIX: remove client from all topics on disconnect
    for topic in subscriptions:
        if conn in subscriptions[topic]:
            subscriptions[topic].remove(conn)

    # remove username entry
    if conn in usernames:
        del usernames[conn]

    # close connection if client disconnects
=======
    # remove client from ALL topics on disconnect
    for topic in TOPICS:
        if conn in subscriptions[topic]:
            subscriptions[topic].remove(conn)

>>>>>>> eb72a323b66e9856ea9ebe3419390c1c99cc79a5
    conn.close()
    print("Disconnected:", addr)


# server loop that constantly accepts new clients
while True:

    # wait for a client to connect
    conn, addr = server.accept()

    # create a new thread to handle that client
    thread = threading.Thread(target=handle_client, args=(conn, addr))

    # start the thread
    thread.start()