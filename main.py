from flask import Flask, jsonify, request
import requests
import os
import time
import traceback

app = Flask(__name__)

API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "45"))

def headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def safe_error(msg, status=500, extra=None):
    payload = {"ok": False, "error": msg}
    if extra:
        payload["extra"] = extra
    return jsonify(payload), status

def wbuy_get_raw(path, params=None):
    url = f"{API_URL}{path}"
    r = requests.get(url, headers=headers(), params=params or {}, timeout=TIMEOUT)
    return r

def wbuy_get_json(path, params=None):
    r = wbuy_get_raw(path, params=params)
    # tenta json mesmo em erro
    try:
        j = r.json()
    except Exception:
        j = None
    return r, j

@app.get("/")
def home():
    return jsonify({"ok": True, "msg": "Martier API rodando"}), 200

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "token_loaded": bool(TOKEN),
        "timeout": TIMEOUT
    }), 200

# --- debug pra ver se WBuy está respondendo ---
@app.get("/debug/wbuy")
def debug_wbuy():
    try:
        r, j = wbuy_get_json("/product/", params={"page": 1, "limit": 1})
        return jsonify({
            "ok": True,
            "status_code": r.status_code,
            "content_type": r.headers.get("content-type", ""),
            "sample_json_keys": list(j.keys()) if isinstance(j, dict) else None,
            "sample_body": (r.text[:500] if r.text else "")
        }), 200
    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})

@app.get("/wbuy/produtos")
def wbuy_produtos():
    """
    Retorna lista de produtos.
    Query:
      - limit (int) opcional: se quiser só primeiros N itens da lista
    """
    try:
        r, j = wbuy_get_json("/product/")
        if r.status_code != 200:
            return safe_error("WBuy respondeu com erro", 502, {
                "status_code": r.status_code,
                "body": r.text[:800]
            })

        if not isinstance(j, dict):
            return safe_error("Resposta da WBuy não veio em JSON esperado", 502, {
                "body": r.text[:800]
            })

        data = j.get("data") or []
        total = j.get("total")

        # opcional: cortar para não enviar resposta gigantesca no browser
        limit = request.args.get("limit")
        if limit:
            data = data[:int(limit)]

        return jsonify({
            "ok": True,
            "total": total,
            "retornados": len(data),
            "data": data
        }), 200

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})

@app.get("/wbuy/produto/<pid>")
def wbuy_produto(pid):
    try:
        r, j = wbuy_get_json("/product/", params={"id": str(pid)})
        if r.status_code != 200:
            return safe_error("WBuy respondeu com erro", 502, {
                "status_code": r.status_code,
                "body": r.text[:800]
            })

        itens = (j or {}).get("data") or []
        if not itens:
            return safe_error("Produto não encontrado", 404)

        return jsonify({"ok": True, "data": itens[0]}), 200

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})

def extrair_skus_ativos(prod):
    skus = []
    for sku in (prod.get("estoque") or []):
        if str(sku.get("ativo", "")) == "1":
            valores = sku.get("valores") or []
            preco = valores[0].get("valor") if valores else ""
            skus.append({
                "produto_id": str(prod.get("id", "")),
                "produto": prod.get("produto", ""),
                "cod_produto": prod.get("cod", ""),
                "sku_id": str(sku.get("id", "")),
                "sku": sku.get("sku", ""),
                "tamanho": (sku.get("variacao") or {}).get("valor", ""),
                "quantidade": sku.get("quantidade_em_estoque", ""),
                "preco": preco
            })
    return skus

@app.get("/skus/ativos")
def skus_ativos():
    """
    Query:
      - limit_produtos (int) default 200
      - sleep_ms (int) default 50
    """
    limit_produtos = int(request.args.get("limit_produtos", "200"))
    sleep_ms = int(request.args.get("sleep_ms", "50"))

    try:
        # pega lista geral
        r, j = wbuy_get_json("/product/")
        if r.status_code != 200:
            return safe_error("Erro ao listar produtos na WBuy", 502, {
                "status_code": r.status_code,
                "body": r.text[:800]
            })

        produtos = (j or {}).get("data") or []
        produtos = produtos[:limit_produtos]

        skus = []
        erros = []

        for p in produtos:
            pid = p.get("id")
            if not pid:
                continue

            try:
                rr, jj = wbuy_get_json("/product/", params={"id": str(pid)})
                if rr.status_code != 200:
                    erros.append({"produto_id": str(pid), "status": rr.status_code, "body": rr.text[:200]})
                    continue

                itens = (jj or {}).get("data") or []
                if not itens:
                    continue

                skus.extend(extrair_skus_ativos(itens[0]))

            except Exception as ie:
                erros.append({"produto_id": str(pid), "erro": str(ie)})

            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)

        return jsonify({
            "ok": True,
            "produtos_processados": len(produtos),
            "skus_ativos": len(skus),
            "erros_count": len(erros),
            "erros": erros[:20],  # não explode resposta
            "data": skus
        }), 200

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})

@app.get("/skus/ativos/resumo")
def skus_ativos_resumo():
    # só chama o endpoint completo mas reduz output
    r = skus_ativos()
    # r pode ser (json, status)
    return r

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
