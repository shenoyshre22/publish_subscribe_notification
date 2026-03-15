import socket

HOST = "127.0.0.1"
PORT = 5000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

server.bind((HOST, PORT))
server.listen()

print("just testing to check server is running...")

conn, addr = server.accept()

print("Connected by", addr)

data = conn.recv(1024)

print("Message received:", data.decode())

conn.send("Hello client".encode())

conn.close()