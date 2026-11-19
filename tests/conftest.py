"""Tests always use the default rubric, never the one stored in Neon."""
import rubric

rubric.use(rubric.defaults())

# Unit tests never read rulings from the live Questions table; tests that need one set questions._cache.
import questions  # noqa: E402
questions._cache = {}
questions.load = lambda: questions._cache
questions.open_rulings = lambda: []
questions.ask = lambda *a, **k: None

# ...and never fetch real sitemaps.
import scraper  # noqa: E402
scraper.sitemap_urls = lambda *a, **k: []
