import socket

HOST = "127.0.0.1"
PORT = 5000

client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

client.connect((HOST, PORT))

client.send("Hello server".encode())

response = client.recv(1024)

print("Server says:", response.decode())

client.close()