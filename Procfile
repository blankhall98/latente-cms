web: bash -lc 'printf "%s" "$FIREBASE_SERVICE_ACCOUNT_JSON" > /tmp/firebase.json && export FIREBASE_CREDENTIALS_PATH=/tmp/firebase.json && exec gunicorn -k uvicorn.workers.UvicornWorker -w 3 -t 120 app.main:app'
# Auto-run migrations on each deploy (release phase)
release: echo "skip release"
