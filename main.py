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


# -------------------------
# Helpers
# -------------------------
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
        raise RuntimeError("WBUY_TOKEN ausente no Environment.")

    url = f"{API_URL}{path}"
    r = requests.get(url, headers=headers, params=params or {}, timeout=TIMEOUT)

    # Se a WBuy devolver HTML em erro, isso evita explodir no .json()
    ct = (r.headers.get("content-type") or "").lower()
    if "application/json" not in ct:
        raise RuntimeError(f"WBuy retornou não-JSON ({r.status_code}). Body: {r.text[:200]}")

    data = r.json()

    # Alguns erros vêm em JSON mesmo com 200
    response_code = data.get("responseCode")
    code = data.get("code")

    ok_by_response_code = str(response_code) in ("200", "201")
    ok_by_code = str(code) in ("010",)  # docs costumam indicar sucesso como "010"

    if not (ok_by_response_code or ok_by_code):
        raise RuntimeError(f"WBuy erro: {data}")

    return data


def normalize_stock_item(item: dict) -> dict:
    """
    Normaliza um item do /product/stock/ para:
      - sku, produto, produto_url
      - tamanho (variação)
      - cor
      - qty (quantidade em estoque)  <-- essencial para grade
      - ativo, venda
    """
    variacao = item.get("variacao") or {}
    cor = item.get("cor") or {}

    # A WBuy pode variar o nome do campo de quantidade.
    # Mantemos fallback para cobrir variações mais comuns.
    qty_raw = (
        item.get("estoque")
        if item.get("estoque") is not None else
        item.get("quantidade")
        if item.get("quantidade") is not None else
        item.get("saldo")
        if item.get("saldo") is not None else
        item.get("qtd")
    )
    qty = to_int(qty_raw, 0)

    tamanho = (variacao.get("valor") or variacao.get("nome") or "").strip()
    cor_nome = (cor.get("nome") or "").strip()

    return {
        "sku": item.get("sku") or "",
        "produto": item.get("produto") or item.get("produto_nome") or "",
        "produto_url": item.get("produto_url") or "",
        "tamanho": tamanho,
        "cor": cor_nome,
        "qty": qty,
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

        # Para quando já passou do total
        if total and offset >= total:
            break

        if sleep_ms and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    return out, total or len(out)


# -------------------------
# Endpoints (mantidos)
# -------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "token_loaded": bool(TOKEN)})


@app.get("/wbuy/skus")
def wbuy_skus_all():
    """
    Retorna TODOS os SKUs (produto + variação + cor) paginando automático.

    Mantém o formato do seu projeto atual (sem quebrar):
      - sku, produto, variacao, cor, produto_url

    Query:
      - page_size (default 200)
      - sleep_ms (default 0)
      - include_qty=1 (opcional) -> se quiser incluir qty sem mudar o padrão
    """
    try:
        page_size = int(request.args.get("page_size", 200))
        sleep_ms = int(request.args.get("sleep_ms", 0))
        include_qty = request.args.get("include_qty", "0") in ("1", "true", "True")

        rows, total = paginate_stock(
            page_size=page_size,
            sleep_ms=sleep_ms,
            only_active=False,
            only_sale=False
        )

        data_out = []
        for r in rows:
            base = {
                "sku": r["sku"],
                "produto": r["produto"],
                "variacao": r["tamanho"],  # compatível: antes era variacao_valor/nome
                "cor": r["cor"],
                "produto_url": r["produto_url"],
            }
            if include_qty:
                base["qty"] = r["qty"]
            data_out.append(base)

        return jsonify({
            "ok": True,
            "total": total,
            "retornados": len(data_out),
            "data": data_out
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


@app.get("/wbuy/skus/ativos")
def wbuy_skus_active():
    """
    Retorna somente SKUs ativos (opcionalmente apenas vendáveis).

    Mantém o formato do seu projeto atual (sem quebrar):
      - sku, produto, variacao, cor, produto_url

    Query:
      - page_size (default 200)
      - sleep_ms (default 0)
      - only_sale=1 (default 0) -> filtra venda == 1
      - include_qty=1 (opcional) -> se quiser incluir qty sem mudar o padrão
    """
    try:
        page_size = int(request.args.get("page_size", 200))
        sleep_ms = int(request.args.get("sleep_ms", 0))
        only_sale = request.args.get("only_sale", "0") in ("1", "true", "True")
        include_qty = request.args.get("include_qty", "0") in ("1", "true", "True")

        rows, total = paginate_stock(
            page_size=page_size,
            sleep_ms=sleep_ms,
            only_active=True,
            only_sale=only_sale
        )

        data_out = []
        for r in rows:
            base = {
                "sku": r["sku"],
                "produto": r["produto"],
                "variacao": r["tamanho"],
                "cor": r["cor"],
                "produto_url": r["produto_url"],
            }
            if include_qty:
                base["qty"] = r["qty"]
            data_out.append(base)

        return jsonify({
            "ok": True,
            "total_estoques_api": total,
            "retornados": len(data_out),
            "data": data_out
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


# -------------------------
# Novo endpoint: Grade (sem quebrar o resto)
# -------------------------
@app.get("/wbuy/estoque-grade")
def estoque_grade():
    """
    Retorna estoque agrupado por grade (produto -> cor -> tamanho -> qty),
    e marca "desgradiado" quando faltar algum tamanho esperado.

    Query:
      - only_active=1 (default 1)
      - only_sale=1 (default 1)
      - min_qty=0 (default 0) -> filtra só SKUs com qty >= min_qty
      - sizes=PP,P,M,G,GG (opcional) -> tamanhos esperados para marcar desgradiado
      - page_size (default 200)
      - sleep_ms (default 0)
    """
    try:
        page_size = int(request.args.get("page_size", 200))
        sleep_ms = int(request.args.get("sleep_ms", 0))

        only_active = request.args.get("only_active", "1") in ("1", "true", "True")
        only_sale = request.args.get("only_sale", "1") in ("1", "true", "True")

        min_qty = int(request.args.get("min_qty", 0))

        sizes_param = (request.args.get("sizes") or "").strip()
        expected_sizes = [s.strip() for s in sizes_param.split(",") if s.strip()]

        rows, total = paginate_stock(
            page_size=page_size,
            sleep_ms=sleep_ms,
            only_active=only_active,
            only_sale=only_sale
        )

        if min_qty > 0:
            rows = [r for r in rows if int(r.get("qty", 0)) >= min_qty]

        # Agrupa grade: produto -> cor -> tamanhos
        grid = {}
        for r in rows:
            prod = r["produto"] or "SEM_PRODUTO"
            cor = r["cor"] or "SEM_COR"
            tam = r["tamanho"] or "SEM_TAMANHO"

            grid.setdefault(prod, {
                "produto": prod,
                "produto_url": r.get("produto_url", ""),
                "cores": {}
            })
            grid[prod]["cores"].setdefault(cor, {
                "cor": cor,
                "tamanhos": {}
            })

            grid[prod]["cores"][cor]["tamanhos"][tam] = int(r.get("qty", 0))

        # Monta saída e marca desgradiado (se sizes foi informado)
        out = []
        for prod_name, prod_obj in grid.items():
            cores_list = []
            for cor_name, cor_obj in prod_obj["cores"].items():
                tamanhos = cor_obj["tamanhos"]

                faltando = []
                if expected_sizes:
                    for s in expected_sizes:
                        if tamanhos.get(s, 0) <= 0:
                            faltando.append(s)

                cores_list.append({
                    "cor": cor_name,
                    "tamanhos": tamanhos,           # ex: {"P": 3, "M": 0, "G": 2}
                    "desgradiado": bool(faltando),
                    "faltando": faltando
                })

            out.append({
                "produto": prod_name,
                "produto_url": prod_obj.get("produto_url", ""),
                "cores": cores_list
            })

        return jsonify({
            "ok": True,
            "total_estoques_api": total,
            "produtos": len(out),
            "expected_sizes": expected_sizes,
            "data": out
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


if __name__ == "__main__":
    # Em produção (Render/Hostinger), geralmente você não usa isso,
    # mas manter não quebra e ajuda local.
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
