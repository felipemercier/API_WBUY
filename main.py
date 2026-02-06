import os
import time
import traceback
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
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


def to_int(v, default=0):
    try:
        return int(float(str(v).replace(",", ".")))
    except Exception:
        return default


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
        raise RuntimeError("WBUY_TOKEN ausente no Environment (Render).")

    url = f"{API_URL}{path}"
    r = requests.get(url, headers=headers, params=params or {}, timeout=TIMEOUT)

    ct = (r.headers.get("content-type") or "").lower()
    if "application/json" not in ct:
        raise RuntimeError(f"WBuy retornou não-JSON ({r.status_code}). Body: {r.text[:200]}")

    data = r.json()

    # considera sucesso por responseCode/code
    rc = str(data.get("responseCode", ""))
    code = str(data.get("code", ""))
    if rc not in ("200", "201") and code not in ("010",):
        raise RuntimeError(f"WBuy erro: {data}")

    return data


def normalize_stock_item(item):
    # produto vem como dict na sua conta
    produto_obj = item.get("produto") or {}
    produto_nome = (produto_obj.get("produto") or produto_obj.get("nome") or "SEM_PRODUTO").strip()

    variacao = item.get("variacao") or {}
    tamanho = (variacao.get("valor") or variacao.get("nome") or "SEM_TAMANHO").strip()

    cor_obj = item.get("cor") or {}
    cor_nome = (cor_obj.get("nome") or "SEM_COR").strip()

    # campo real do estoque na sua conta
    qty = to_int(item.get("quantidade_em_estoque"), 0)

    return {
        "sku": item.get("sku") or "",
        "produto": produto_nome or "SEM_PRODUTO",
        "tamanho": tamanho or "SEM_TAMANHO",
        "cor": cor_nome or "SEM_COR",
        "qty": qty,
        "produto_url": item.get("produto_url") or "",
        "ativo": str(item.get("ativo", "")),
        "venda": str(item.get("venda", "")),
    }


def paginate_stock(page_size=200, sleep_ms=0, only_active=False, only_sale=False):
    offset = 0
    total = None
    out = []

    while True:
        data = wbuy_get("/product/stock/", params={"limit": f"{offset},{page_size}"})

        if total is None:
            total = to_int(data.get("total", 0), 0)

        items = data.get("data") or []
        if not items:
            break

        for it in items:
            row = normalize_stock_item(it)

            if only_active and row["ativo"] != "1":
                continue
            if only_sale and row["venda"] != "1":
                continue

            out.append(row)

        offset += page_size
        if total and offset >= total:
            break

        if sleep_ms and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    return out, total or len(out)


@app.get("/health")
def health():
    return jsonify({"ok": True, "token_loaded": bool(TOKEN)})


@app.get("/wbuy/estoque-grade")
def estoque_grade():
    try:
        sizes_param = (request.args.get("sizes") or "").strip()
        expected_sizes = [s.strip() for s in sizes_param.split(",") if s.strip()]

        min_qty = to_int(request.args.get("min_qty", 0), 0)
        page_size = to_int(request.args.get("page_size", 200), 200)

        # padrão: ativos + vendáveis (pra tráfego)
        only_active = request.args.get("only_active", "1") in ("1", "true", "True")
        only_sale = request.args.get("only_sale", "1") in ("1", "true", "True")

        rows, total = paginate_stock(page_size=page_size, only_active=only_active, only_sale=only_sale)

        if min_qty > 0:
            rows = [r for r in rows if int(r.get("qty", 0)) >= min_qty]

        grid = {}
        for r in rows:
            prod = r["produto"]
            cor = r["cor"]
            tam = r["tamanho"]
            qty = int(r.get("qty", 0))

            grid.setdefault(prod, {"produto": prod, "cores": {}})
            grid[prod]["cores"].setdefault(cor, {"cor": cor, "tamanhos": {}})
            grid[prod]["cores"][cor]["tamanhos"][tam] = qty

        out = []
        for prod_obj in grid.values():
            cores_list = []
            for cor_obj in prod_obj["cores"].values():
                tamanhos = cor_obj["tamanhos"]
                faltando = []
                if expected_sizes:
                    for s in expected_sizes:
                        if tamanhos.get(s, 0) <= 0:
                            faltando.append(s)

                cores_list.append({
                    "cor": cor_obj["cor"],
                    "tamanhos": tamanhos,
                    "desgradiado": bool(faltando),
                    "faltando": faltando
                })

            out.append({"produto": prod_obj["produto"], "cores": cores_list})

        return jsonify({"ok": True, "total_estoques_api": total, "data": out})

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


@app.get("/wbuy/skus")
def wbuy_skus():
    try:
        page_size = to_int(request.args.get("page_size", 200), 200)
        rows, total = paginate_stock(page_size=page_size, only_active=False, only_sale=False)
        return jsonify({"ok": True, "total": total, "data": rows})
    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
