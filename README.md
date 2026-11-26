# Lunch Hub

Static shared lunch-menu page plus Python scraper and scheduled GitHub Pages deployment.

## Run locally

```bash
pip install -r requirements.txt
python scraper.py
python -m http.server 8000
```
Open http://localhost:8000 (the page reads `data/menu.json`).

## Deploy

1. Create a GitHub repository and upload the contents of this folder.
2. In repository Settings → Pages, set the source to **GitHub Actions**.
3. Run the workflow manually once (`Actions → Update Lunch Hub menu and deploy → Run workflow`).
4. It deploys the page to a `github.io` URL. Scheduled runs check at both 08:00 and 09:00 UTC and proceed only when it is 10:00 in Europe/Prague, Monday–Friday (DST-aware).

Scraper extraction is heuristic and should be validated against live page markup. If a restaurant changes its page layout, its card will show a retrieval status instead of silently displaying stale sample data.
