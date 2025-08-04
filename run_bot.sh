#!/bin/bash
source venv/bin/activate
while true; do
    python3 petezah_bot.py >> bot.log 2>&1
    echo "Bot crashed with exit code $?. Restarting in 5 seconds..."
    sleep 5
done
