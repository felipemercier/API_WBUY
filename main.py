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

def safe_error(message, status=500, extra=None):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status

def wbuy_headers():
    if not TOKEN:
        return None
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def wbuy_get(path, params=None):
    headers = wbuy_headers()
    if headers is None:
        raise RuntimeError("WBUY_TOKEN ausente no Environment.")

    url = f"{API_URL}{path}"
    r = requests.get(url, headers=headers, params=params or {}, timeout=TIMEOUT)

    # Se a WBuy devolver HTML em erro, isso evita explodir no .json()
    ct = (r.headers.get("content-type") or "").lower()
    if "application/json" not in ct:
        raise RuntimeError(f"WBuy retornou não-JSON ({r.status_code}). Body: {r.text[:200]}")

    data = r.json()
    # alguns erros vem em JSON mesmo com 200
    if str(data.get("responseCode", "")) not in ("200", 200) and data.get("code") not in ("010", "010"):
        raise RuntimeError(f"WBuy erro: {data}")

    return data

def normalize_stock_item(item: dict) -> dict:
    """
    Queremos: produto + variação (e opcional cor), por SKU.
    O /product/stock/ costuma trazer campos como:
      - sku
      - produto / produto_url
      - variacao: { nome, valor }
      - cor: { nome }
      - ativo / venda
    """
    variacao = item.get("variacao") or {}
    cor = item.get("cor") or {}

    return {
        "sku": item.get("sku") or "",
        "produto": item.get("produto") or item.get("produto_nome") or "",
        "produto_url": item.get("produto_url") or "",
        "variacao_nome": variacao.get("nome") or "",
        "variacao_valor": variacao.get("valor") or "",
        "cor_nome": cor.get("nome") or "",
        "ativo": str(item.get("ativo", "")),
        "venda": str(item.get("venda", "")),
    }

def paginate_stock(page_size=200, sleep_ms=0, only_active=False, only_sale=False):
    """
    Pagina automaticamente no /product/stock/ usando limit=offset,page_size
    Retorna lista de itens normalizados.
    """
    offset = 0
    total = None
    out = []

    while True:
        params = {"limit": f"{offset},{page_size}"}
        data = wbuy_get("/product/stock/", params=params)

        if total is None:
            try:
                total = int(data.get("total", 0))
            except Exception:
                total = 0

        items = data.get("data") or []
        if not items:
            break

        for it in items:
            row = normalize_stock_item(it)

            if only_active and row["ativo"] != "1":
                continue
            if only_sale and row["venda"] != "1":
                continue

            # você disse que não precisa de estoque, então não retornamos qty
            out.append({
                "sku": row["sku"],
                "produto": row["produto"],
                "variacao": row["variacao_valor"] or row["variacao_nome"],  # fallback
                "cor": row["cor_nome"],
                "produto_url": row["produto_url"],
            })

        offset += page_size

        # para quando já passou do total
        if total and offset >= total:
            break

        if sleep_ms and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    return out, total or len(out)

@app.get("/health")
def health():
    return jsonify({"ok": True, "token_loaded": bool(TOKEN)})

@app.get("/wbuy/skus")
def wbuy_skus_all():
    """
    Retorna TODOS os SKUs (produto + variação + cor) paginando automático.
    Query:
      - page_size (default 200)
      - sleep_ms (default 0) -> se quiser desacelerar
    """
    try:
        page_size = int(request.args.get("page_size", 200))
        sleep_ms = int(request.args.get("sleep_ms", 0))

        rows, total = paginate_stock(page_size=page_size, sleep_ms=sleep_ms, only_active=False, only_sale=False)

        return jsonify({
            "ok": True,
            "total": total,
            "retornados": len(rows),
            "data": rows
        })
    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})

@app.get("/wbuy/skus/ativos")
def wbuy_skus_active():
    """
    Retorna somente SKUs ativos (opcionalmente apenas vendáveis).
    Query:
      - page_size (default 200)
      - sleep_ms (default 0)
      - only_sale=1 (default 0) -> filtra venda == 1
    """
    try:
        page_size = int(request.args.get("page_size", 200))
        sleep_ms = int(request.args.get("sleep_ms", 0))
        only_sale = request.args.get("only_sale", "0") in ("1", "true", "True")

        rows, total = paginate_stock(page_size=page_size, sleep_ms=sleep_ms, only_active=True, only_sale=only_sale)

        return jsonify({
            "ok": True,
            "total_estoques_api": total,      # total que a WBuy reporta no endpoint
            "retornados": len(rows),
            "data": rows
        })
    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})
