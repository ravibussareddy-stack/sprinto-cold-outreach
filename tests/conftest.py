"""Tests always use the default rubric, never the one stored in Neon."""
import rubric

rubric.use(rubric.defaults())
