"""Tests always use the default rubric, never the one stored in Neon."""
import rubric

rubric.use(rubric.defaults())

# Unit tests never read answers from the live Questions table; tests that need one stub it.
import questions  # noqa: E402
questions.answer = lambda key: None
