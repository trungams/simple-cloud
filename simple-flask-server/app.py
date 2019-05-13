import socket
from flask import Flask

app = Flask(__name__)


@app.route('/')
def hello():
    hostname = socket.gethostname()
    ip = socket.gethostbyname(hostname)
    return f"Hostname: {hostname}\n" + \
           f"IPv4: {ip}\n"


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, threaded=True)
