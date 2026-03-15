import socket                 # provides networking functionality
import threading              # allows multiple clients to connect simultaneously
from topics import TOPICS     # import the list of allowed topics

# 0.0.0.0 means accept connections from any machine on the network
HOST = "0.0.0.0"

# port number where the server listens, lets choose 5000 for ease??
PORT = 5000


# create a dictionary to store topic subscriptions
subscriptions = {topic: [] for topic in TOPICS}


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

    while True:
        try:
            # receive data from the client
            data = conn.recv(1024).decode()

            # if no data, connection closed
            if not data:
                break

            # split the command
            # format expected:
            # SUBSCRIBE|topic
            # POST|topic|message
            parts = data.split("|")

            command = parts[0]

            # -------------------------
            # handle SUBSCRIBE command
            # -------------------------
            if command == "SUBSCRIBE":

                topic = parts[1]

                # check if topic exists
                if topic in TOPICS:

                    # add client socket to the subscriber list
                    subscriptions[topic].append(conn)

                    # confirmation message
                    conn.send(f"Subscribed to {topic}".encode())


            # -------------------------
            # handle POST command
            # -------------------------
            elif command == "POST":

                topic = parts[1]
                message = parts[2]

                # format message for display
                full_message = f"[{topic.upper()}] {message}"

                # send message to all subscribers
                broadcast(topic, full_message)

        except:
            break

    # close connection if client disconnects
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