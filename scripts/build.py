"""Assemble index.html from src/app.html and src/scene.svg.

Edit the page in src/app.html, then run: python scripts/build.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
page = open(os.path.join(ROOT, "src", "app.html"), encoding="utf-8").read()
scene = open(os.path.join(ROOT, "src", "scene.svg"), encoding="utf-8").read()
assert "<!--SCENE-->" in page
out = page.replace("<!--SCENE-->", scene)
with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
    f.write(out)
print("index.html", len(out), "bytes")
