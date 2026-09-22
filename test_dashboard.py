from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

r = client.get("/")
assert r.status_code in (200, 307)

r = client.get("/app/")
assert r.status_code == 200
assert "Job Agent" in r.text

r = client.get("/profile/current")
assert r.status_code == 200
profile = r.json()
assert profile["graduation_year"] == 2025
assert "Python" in profile["skills"]

r = client.get("/auth/google/status")
assert r.status_code == 200

print("Dashboard smoke tests passed.")
