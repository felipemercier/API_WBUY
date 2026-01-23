from flask import Flask, jsonify, request
import requests
import os
import time

app = Flask(__name__)

# ================= CONFIG =================
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

TIMEOUT = 30


# ================= HELPERS =================
def wbuy_get(path, params=None):
    url = f"{API_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ================= HEALTH =================
@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "token_loaded": bool(TOKEN)
    })


# ================= PRODUTOS (LISTA) =================
@app.route("/wbuy/produtos")
def produtos():
    try:
        data = wbuy_get("/product/")
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ================= PRODUTO DETALHE =================
@app.route("/wbuy/produto/<produto_id>")
def produto_detalhe(produto_id):
    try:
        data = wbuy_get("/product/", params={"id": produto_id})
        if data.get("data"):
            return jsonify(data["data"][0])
        return jsonify({"ok": False, "message": "Produto não encontrado"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ================= SKUS ATIVOS =================
@app.route("/skus/ativos")
def skus_ativos():
    """
    Query params:
      limit_produtos (int) -> quantos produtos processar
      sleep_ms (int)       -> delay entre requests
    """
    limit_produtos = int(request.args.get("limit_produtos", 50))
    sleep_ms = int(request.args.get("sleep_ms", 150))

    try:
        produtos = wbuy_get("/product/").get("data", [])
        produtos = produtos[:limit_produtos]

        skus = []
        erros = []

        for idx, p in enumerate(produtos, start=1):
            pid = p.get("id")
            if not pid:
                continue

            try:
                detalhe = wbuy_get("/product/", params={"id": pid})
                itens = detalhe.get("data", [])

                for prod in itens:
                    for sku in prod.get("estoque", []):
                        if sku.get("ativo") == "1":
                            skus.append({
                                "produto_id": prod.get("id"),
                                "produto": prod.get("produto"),
                                "cod_produto": prod.get("cod"),
                                "sku_id": sku.get("id"),
                                "sku": sku.get("sku"),
                                "tamanho": sku.get("variacao", {}).get("valor"),
                                "quantidade": sku.get("quantidade_em_estoque"),
                                "preco": (sku.get("valores") or [{}])[0].get("valor")
                            })

            except Exception as e:
                erros.append({
                    "produto_id": pid,
                    "erro": str(e)
                })

            time.sleep(sleep_ms / 1000)

        return jsonify({
            "ok": True,
            "produtos_processados": len(produtos),
            "skus_ativos": len(skus),
            "erros": erros,
            "data": skus
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ================= START =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
