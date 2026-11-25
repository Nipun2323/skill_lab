"""Start SyncroMove with waitress (a production-grade WSGI server): python serve.py"""
from waitress import serve

from app import create_app

app = create_app()

if __name__ == "__main__":
    host, port = app.config["HOST"], int(app.config["PORT"])
    print(f"SyncroMove running at http://{host}:{port}  (Ctrl+C to stop)")
    serve(app, host=host, port=port)
