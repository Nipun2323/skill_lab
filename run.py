"""Start SyncroMove with Flask's development server: python run.py"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=app.config["HOST"], port=int(app.config["PORT"]), debug=False)
