# test_server.py
from flask import Flask
app = Flask(__name__)

@app.route('/')
def hello():
    return "OK"

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=False)  # ← порт 5000