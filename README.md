# Navy Glider Recovery Tracker

Deploy using GitHub Pages (Settings > Pages > Source: GitHub Actions).
Edit `config.json` on GitHub and commit to `main` to automatically regenerate the map.
Manual trigger: Actions > Update glider recovery map > Run workflow.
Scheduled refreshes: approximately every 30 minutes (may be delayed).
Do not publish sensitive location information without authorization.

Local test: `python3 -m pip install -r requirements.txt && python3 tracker.py` then open `docs/index.html`.
