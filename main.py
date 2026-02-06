import os
import time
import traceback
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "").strip()
TIMEOUT = 30


# ---------------- HELPERS ----------------

def safe_error(message, status=500, extra=None):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def to_int(v, default=0):
    try:
        return int(v)
    except:
        return default


def wbuy_headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def wbuy_get(path, params=None):
    url = f"{API_URL}{path}"
    r = requests.get(url, headers=wbuy_headers(), params=params or {}, timeout=TIMEOUT)

    if "application/json" not in (r.headers.get("content-type") or ""):
        raise RuntimeError("Resposta não JSON da WBuy")

    return r.json()


# ---------------- NORMALIZAÇÃO ----------------

def normalize_stock_item(item):

    produto_obj = item.get("produto") or {}

    produto_nome = (
        produto_obj.get("produto") or
        produto_obj.get("nome") or
        "SEM_PRODUTO"
    )

    variacao = item.get("variacao") or {}
    tamanho = variacao.get("valor") or variacao.get("nome") or "SEM_TAMANHO"

    cor = item.get("cor") or {}
    cor_nome = cor.get("nome") or "SEM_COR"

    qty = to_int(item.get("quantidade_em_estoque"), 0)

    return {
        "sku": item.get("sku"),
        "produto": produto_nome,
        "tamanho": tamanho,
        "cor": cor_nome,
        "qty": qty,
        "produto_url": item.get("produto_url") or ""
    }


# ---------------- PAGINAÇÃO ----------------

def paginate_stock(page_size=200):

    offset = 0
    out = []

    while True:
        data = wbuy_get("/product/stock/", params={"limit": f"{offset},{page_size}"})
        items = data.get("data") or []

        if not items:
            break

        for it in items:
            out.append(normalize_stock_item(it))

        offset += page_size

        if offset >= int(data.get("total", 0)):
            break

    return out


# ---------------- ENDPOINT SKUS ----------------

@app.get("/wbuy/skus")
def wbuy_skus():

    rows = paginate_stock()

    return jsonify({
        "ok": True,
        "data": rows
    })


# ---------------- ENDPOINT GRADE ----------------

@app.get("/wbuy/estoque-grade")
def estoque_grade():

    sizes = request.args.get("sizes", "")
    sizes = [s.strip() for s in sizes.split(",") if s]

    rows = paginate_stock()

    grid = {}

    for r in rows:

        prod = r["produto"]
        cor = r["cor"]
        tam = r["tamanho"]
        qty = r["qty"]

        grid.setdefault(prod, {"produto": prod, "cores": {}})
        grid[prod]["cores"].setdefault(cor, {"cor": cor, "tamanhos": {}})

        grid[prod]["cores"][cor]["tamanhos"][tam] = qty

    out = []

    for prod in grid.values():

        cores_list = []

        for cor in prod["cores"].values():

            tamanhos = cor["tamanhos"]

            faltando = []
            if sizes:
                for s in sizes:
                    if tamanhos.get(s, 0) <= 0:
                        faltando.append(s)

            cores_list.append({
                "cor": cor["cor"],
                "tamanhos": tamanhos,
                "desgradiado": bool(faltando),
                "faltando": faltando
            })

        out.append({
            "produto": prod["produto"],
            "cores": cores_list
        })

    return jsonify({
        "ok": True,
        "data": out
    })


# ---------------- HEALTH ----------------

@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run()
