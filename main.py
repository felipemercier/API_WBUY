from flask import Flask, jsonify, request
import requests
import os

app = Flask(__name__)

API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

TIMEOUT = 30


def wbuy_get(path, params=None):
    url = f"{API_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def paginate_products(limit=100, max_pages=500):
    """Busca todos os produtos paginando (ajuste page/limit conforme WBuy)."""
    page = 1
    all_items = []

    while page <= max_pages:
        data = wbuy_get("/product", params={"limit": limit, "page": page})
        items = data.get("data") or []
        if not items:
            break
        all_items.extend(items)
        page += 1

    return all_items


@app.get("/wbuy/produtos")
def api_produtos():
    limit = int(request.args.get("limit", 100))
    produtos = paginate_products(limit=limit)
    return jsonify({"ok": True, "total": len(produtos), "data": produtos})


@app.get("/wbuy/estoque")
def api_estoque():
    # WBuy: /product/stock/ (conforme sua doc)
    pid = request.args.get("pid")
    params = {}
    if pid:
        params["pid"] = pid

    data = wbuy_get("/product/stock/", params=params)
    return jsonify(data)


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
