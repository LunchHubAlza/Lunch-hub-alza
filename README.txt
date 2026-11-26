Lunch Hub - live-ready project

The website is driven by data/menu.json. scraper.py attempts to fetch and parse the four restaurant sources.

Run locally:
  pip install -r requirements.txt
  python scraper.py
  python -m http.server 8000

Deploy to GitHub Pages:
1. Create a GitHub repo and upload all files/folders in this package.
2. Settings > Pages > choose GitHub Actions.
3. Actions > Update Lunch Hub menu and deploy > Run workflow.

Scheduled workflow is configured for 10:00 Europe/Prague Monday-Friday. It checks at 08:00 and 09:00 UTC and gates execution on local Czech time, so it accounts for daylight saving time.

Scraping is heuristic and needs a live validation pass after initial deployment; the environment here cannot directly fetch the restaurant websites.
