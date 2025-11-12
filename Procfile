web: gunicorn -k uvicorn.workers.UvicornWorker -w 3 -t 120 app.main:app
# Auto-run migrations on each deploy (release phase)
release: echo "skip release"
