import requests, json

r = requests.post("http://127.0.0.1:8000/api/admin/token",
                   data={"username": "Tsikhilovk", "password": "Haker05dag$"})
token = r.json()["access_token"]

u = requests.get("http://127.0.0.1:8000/api/user/kamil-b58c047f",
                  headers={"Authorization": "Bearer " + token})
data = u.json()
for k, v in data.items():
    print(f"{k}: {repr(v)}")
