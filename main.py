from flask import Flask, jsonify, request
import requests
import os
import time

app = Flask(__name__)

# ===== Config WBuy =====
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "")  # defina no Render (Environment Variables)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

TIMEOUT = 30


# ===== Helpers =====
def wbuy_get(path, params=None):
    """
    Faz GET na API da WBuy e retorna JSON.
    Lança erro se status != 200.
    """
    url = f"{API_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=TIMEOUT)

    # Se der 401/403 fica fácil entender
    if r.status_code in (401, 403):
        raise RuntimeError(f"Sem autorização (status {r.status_code}). Token inválido/ausente.")

    r.raise_for_status()
    return r.json()


def list_produtos_raw(limit=None, max_pages=1):
    """
    Puxa a lista de produtos.
    - Se a WBuy devolver tudo em uma chamada, max_pages=1 já resolve.
    - Se houver paginação, tente page/limit. (Se não existir, a API ignorará.)
    """
    all_items = []
    page = 1

    # Tenta paginação "page/limit" de forma compatível (se a API suportar).
    while page <= max_pages:
        params = {}
        if limit:
            params["limit"] = int(limit)
        params["page"] = page

        data = wbuy_get("/product/", params=params)
        items = data.get("data") or []

        if not items:
            break

        all_items.extend(items)

        # Se a API não for paginada, normalmente ela retorna tudo no page=1.
        # Nesse caso, para aqui para não duplicar.
        if page == 1 and (data.get("total") == len(items)):
            break

        page += 1

    # Pode ter duplicados se a API não usa page e sempre retorna igual.
    # Então dedup por id.
    seen = set()
    dedup = []
    for it in all_items:
        pid = str(it.get("id", ""))
        if pid and pid not in seen:
            seen.add(pid)
            dedup.append(it)

    # Recalcula total (o total “real” vem no campo total da API, mas aqui é a lista que pegamos)
    return dedup


def get_produto_detalhe_by_id(pid):
    """
    Busca um produto por id via querystring (?id=...),
    conforme você testou no Postman e funcionou.
    """
    data = wbuy_get("/product/", params={"id": str(pid)})
    items = data.get("data") or []
    if not items:
        return None
    return items[0]


def extrair_skus_ativos(produto_detalhe):
    """
    Recebe o JSON completo de 1 produto (detalhe) e devolve lista de SKUs ativos.
    """
    out = []
    if not produto_detalhe:
        return out

    estoque = produto_detalhe.get("estoque") or []
    for sku in estoque:
        if str(sku.get("ativo", "")) == "1":
            # Pega um preço (tabela 1 varejo normalmente). Se tiver mais, você pode ajustar.
            valores = sku.get("valores") or []
            preco = ""
            if valores:
                preco = valores[0].get("valor", "")

            out.append({
                "produto_id": str(produto_detalhe.get("id", "")),
                "cod_produto": produto_detalhe.get("cod", ""),
                "produto_nome": produto_detalhe.get("produto", ""),
                "produto_url": produto_detalhe.get("produto_url", ""),

                "sku_id": str(sku.get("id", "")),
                "sku": sku.get("sku", ""),
                "cod_estoque": sku.get("cod_estoque", ""),
                "erp_id": sku.get("erp_id", ""),

                "quantidade_em_estoque": sku.get("quantidade_em_estoque", ""),
                "preco": preco,

                "tamanho": (sku.get("variacao") or {}).get("valor", ""),
                "cor_nome": ((sku.get("cor") or {}).get("nome", "") if isinstance(sku.get("cor"), dict) else ""),
                "ativo": str(sku.get("ativo", "")),
            })

    return out


def get_all_active_skus(limit_produtos=None, sleep_ms=0):
    """
    Puxa todos os produtos e para cada produto busca o detalhe e extrai SKUs ativos.
    - limit_produtos: limita quantos produtos processar (bom para teste).
    - sleep_ms: pausa entre requisições (evita rate-limit).
    """
    produtos = list_produtos_raw()
    if limit_produtos:
        produtos = produtos[:int(limit_produtos)]

    skus_ativos = []

    for idx, p in enumerate(produtos, start=1):
        pid = p.get("id")
        if not pid:
            continue

        detalhe = get_produto_detalhe_by_id(pid)
        skus_ativos.extend(extrair_skus_ativos(detalhe))

        if sleep_ms and sleep_ms > 0:
            time.sleep(float(sleep_ms) / 1000.0)

    return skus_ativos


# ===== Rotas =====
@app.get("/")
def home():
    return jsonify({"ok": True, "msg": "Martier API rodando"}), 200


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "token_loaded": bool(TOKEN),
    }), 200


@app.get("/wbuy/produtos")
def wbuy_produtos():
    """
    Lista produtos (resumido).
    Query opcional:
    - limit (int)
    - max_pages (int)
    """
    limit = request.args.get("limit")
    max_pages = int(request.args.get("max_pages", "1"))
    produtos = list_produtos_raw(limit=limit, max_pages=max_pages)
    return jsonify({
        "ok": True,
        "total": len(produtos),
        "data": produtos
    }), 200


@app.get("/wbuy/produto/<pid>")
def wbuy_produto_detalhe(pid):
    """
    Detalhe de produto por id.
    """
    detalhe = get_produto_detalhe_by_id(pid)
    if not detalhe:
        return jsonify({"ok": False, "message": "Produto não encontrado"}), 404
    return jsonify({"ok": True, "data": detalhe}), 200


@app.get("/skus/ativos")
def skus_ativos():
    """
    Retorna todos os SKUs ativos.
    Query opcional:
    - limit_produtos (int) -> para testar com poucos
    - sleep_ms (int) -> pausa entre chamadas
    """
    limit_produtos = request.args.get("limit_produtos")
    sleep_ms = request.args.get("sleep_ms", "0")

    skus = get_all_active_skus(limit_produtos=limit_produtos, sleep_ms=float(sleep_ms))

    return jsonify({
        "ok": True,
        "total": len(skus),
        "data": skus
    }), 200


@app.get("/skus/ativos/resumo")
def skus_ativos_resumo():
    """
    Resumo leve:
    - total de produtos processados
    - total de skus ativos
    - 10 exemplos
    Query opcional:
    - limit_produtos
    - sleep_ms
    """
    limit_produtos = request.args.get("limit_produtos")
    sleep_ms = request.args.get("sleep_ms", "0")

    skus = get_all_active_skus(limit_produtos=limit_produtos, sleep_ms=float(sleep_ms))

    return jsonify({
        "ok": True,
        "skus_ativos_total": len(skus),
        "exemplos": skus[:10]
    }), 200


# ===== Run local =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
