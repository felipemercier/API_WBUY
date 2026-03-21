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

# =========================================================
# ================= CACHE SIMPLES EM MEMÓRIA ==============
# =========================================================
CACHE = {}

def cache_get(key, ttl_sec=600):
    item = CACHE.get(key)
    if not item:
        return None
    ts, data = item
    if time.time() - ts > ttl_sec:
        return None
    return data

def cache_set(key, data):
    CACHE[key] = (time.time(), data)


# =========================================================
# ======================== HELPERS ========================
# =========================================================
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

    ct = (r.headers.get("content-type") or "").lower()
    if "application/json" not in ct:
        raise RuntimeError(f"WBuy retornou não-JSON ({r.status_code}). Body: {r.text[:300]}")

    data = r.json()

    rc = str(data.get("responseCode", ""))
    code = str(data.get("code", ""))

    # aceita formatos comuns de sucesso
    if rc not in ("200", "201", "") and code not in ("010", "1", ""):
        raise RuntimeError(f"WBuy erro: {data}")

    return data


def get_nested(obj, path, default=""):
    try:
        cur = obj
        for p in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(p)
        return cur if cur is not None else default
    except Exception:
        return default


# =========================================================
# ====================== ESTOQUE WBUY =====================
# =========================================================
def normalize_stock_item(item):
    produto_obj = item.get("produto") or {}
    produto_nome = (produto_obj.get("produto") or produto_obj.get("nome") or "SEM_PRODUTO").strip()

    variacao = item.get("variacao") or {}
    tamanho = (variacao.get("valor") or variacao.get("nome") or "SEM_TAMANHO").strip()

    cor_obj = item.get("cor") or {}
    cor_nome = (cor_obj.get("nome") or "SEM_COR").strip()

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


# =========================================================
# ====================== PEDIDOS WBUY =====================
# =========================================================
def extract_order_list(data):
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("orders"), list):
            return data.get("orders")
        if isinstance(data.get("pedidos"), list):
            return data.get("pedidos")
        if isinstance(data.get("result"), list):
            return data.get("result")
    if isinstance(data, list):
        return data
    return []


def paginate_orders(page_size=100, sleep_ms=0, status_filter=None, max_pages=20):
    offset = 0
    total = None
    out = []
    pages = 0

    while True:
        params = {"limit": f"{offset},{page_size}"}
        if status_filter:
            params["status"] = status_filter

        data = wbuy_get("/order/", params=params)

        if total is None:
            total = to_int(data.get("total", 0), 0)

        items = extract_order_list(data)
        if not items:
            break

        out.extend(items)

        offset += page_size
        pages += 1

        if total and offset >= total:
            break

        if max_pages and pages >= max_pages:
            break

        if sleep_ms and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    return out, total or len(out)


def normalize_order_item(item):
    cliente_obj = item.get("cliente") or item.get("customer") or {}
    endereco_obj = item.get("endereco_entrega") or item.get("shipping_address") or {}
    frete_obj = item.get("frete") or {}

    pedido_id = (
        item.get("pedido_id")
        or item.get("id")
        or item.get("order_id")
        or item.get("codigo")
        or ""
    )

    numero = (
        item.get("numero")
        or item.get("identificacao")
        or item.get("pedido")
        or item.get("order_number")
        or pedido_id
        or ""
    )

    cliente_nome = (
        get_nested(item, ["cliente", "nome"], "")
        or get_nested(item, ["customer", "name"], "")
        or item.get("cliente")
        or item.get("customer_name")
        or ""
    )

    status = (
        item.get("status")
        or item.get("status_descricao")
        or item.get("situacao")
        or item.get("order_status")
        or ""
    )

    data_pedido = (
        item.get("data")
        or item.get("data_pedido")
        or item.get("created_at")
        or item.get("date_created")
        or ""
    )

    valor_total = (
        item.get("total")
        or item.get("valor_total")
        or item.get("total_venda")
        or item.get("order_total")
        or ""
    )

    forma_envio = (
        frete_obj.get("nome")
        or item.get("forma_envio")
        or item.get("servico_frete")
        or item.get("shipping_method")
        or ""
    )

    transportadora = (
        frete_obj.get("tipo_envio_nome")
        or item.get("transportadora")
        or item.get("nome_transportadora")
        or ""
    )

    codigo_rastreio = (
        frete_obj.get("rastreio")
        or item.get("codigo_rastreio")
        or item.get("rastreamento")
        or item.get("tracking")
        or item.get("tracking_code")
        or ""
    )

    rastreio_url = (
        frete_obj.get("rastreio_url")
        or item.get("rastreio_url")
        or ""
    )

    prazo = (
        frete_obj.get("prazo")
        or item.get("prazo_entrega")
        or ""
    )

    cpf_cnpj = (
        get_nested(item, ["cliente", "cpf_cnpj"], "")
        or get_nested(item, ["cliente", "cpf"], "")
        or get_nested(item, ["cliente", "doc1"], "")
        or get_nested(item, ["customer", "document"], "")
        or ""
    )

    email = (
        get_nested(item, ["cliente", "email"], "")
        or get_nested(item, ["customer", "email"], "")
        or ""
    )

    telefone = (
        get_nested(item, ["cliente", "telefone"], "")
        or get_nested(item, ["cliente", "celular"], "")
        or get_nested(item, ["cliente", "fone"], "")
        or get_nested(item, ["customer", "phone"], "")
        or ""
    )

    cidade = (
        endereco_obj.get("cidade")
        or endereco_obj.get("city")
        or get_nested(cliente_obj, ["cidade"], "")
        or ""
    )

    uf = (
        endereco_obj.get("estado")
        or endereco_obj.get("uf")
        or endereco_obj.get("state")
        or get_nested(cliente_obj, ["estado"], "")
        or ""
    )

    return {
        "pedido_id": str(pedido_id),
        "numero": str(numero),
        "cliente": str(cliente_nome),
        "status": str(status),
        "data": str(data_pedido),
        "valor_total": valor_total,
        "forma_envio": str(forma_envio),
        "transportadora": str(transportadora),
        "codigo_rastreio": str(codigo_rastreio),
        "rastreio_url": str(rastreio_url),
        "prazo": str(prazo),
        "cpf_cnpj": str(cpf_cnpj),
        "email": str(email),
        "telefone": str(telefone),
        "cidade": str(cidade),
        "uf": str(uf),
        "raw": item
    }


def contains_jt_shipping(item, normalized_row=None):
    frete = item.get("frete") or {}
    row = normalized_row or normalize_order_item(item)

    nome = (frete.get("nome") or row.get("forma_envio") or "").lower()
    tipo = (frete.get("tipo_envio_nome") or row.get("transportadora") or "").lower()
    rastreio = (frete.get("rastreio") or row.get("codigo_rastreio") or "").strip()

    if "j&t" in nome or "j&t" in tipo:
        return True

    if rastreio.startswith("888"):
        return True

    return False


def row_matches_status(row, status_param):
    if not status_param:
        return True

    status_txt = (row.get("status") or "").strip().lower()
    return status_param in status_txt


# =========================================================
# ========================= ROTAS =========================
# =========================================================
@app.get("/health")
def health():
    return jsonify({"ok": True, "token_loaded": bool(TOKEN)})


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "message": "API da Martier rodando com todas as rotas!"
    })


@app.get("/wbuy/estoque-grade")
def estoque_grade():
    try:
        sizes_param = (request.args.get("sizes") or "").strip()
        expected_sizes = [s.strip() for s in sizes_param.split(",") if s.strip()]

        min_qty = to_int(request.args.get("min_qty", 0), 0)
        page_size = to_int(request.args.get("page_size", 200), 200)

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


@app.get("/wbuy/skus/ativos")
def wbuy_skus_ativos():
    try:
        page_size = to_int(request.args.get("page_size", 200), 200)

        cache_key = f"skus_ativos_ps{page_size}"
        cached = cache_get(cache_key, ttl_sec=600)
        if cached:
            return jsonify(cached)

        rows, total = paginate_stock(page_size=page_size, only_active=True, only_sale=True)

        payload = {"ok": True, "total": total, "data": rows}
        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


@app.get("/wbuy/skus/ativos-fast")
def wbuy_skus_ativos_fast():
    try:
        page_size = to_int(request.args.get("page_size", 200), 200)
        data = wbuy_get("/product/stock/", params={"limit": f"0,{page_size}"})
        items = data.get("data") or []

        out = []
        for it in items:
            row = normalize_stock_item(it)
            if row["ativo"] != "1":
                continue
            if row["venda"] != "1":
                continue
            out.append(row)

        return jsonify({"ok": True, "total": len(out), "data": out})

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


# =========================================================
# ================== NOVAS ROTAS DE PEDIDOS ===============
# =========================================================

@app.get("/wbuy/pedidos/formas-envio")
def wbuy_pedidos_formas_envio():
    try:
        page_size = to_int(request.args.get("page_size", 100), 100)
        max_pages = to_int(request.args.get("max_pages", 20), 20)
        status_param = (request.args.get("status") or "").strip().lower()

        raw_items, total_api = paginate_orders(
            page_size=page_size,
            sleep_ms=0,
            status_filter=status_param if status_param else None,
            max_pages=max_pages
        )

        mapa = {}

        for it in raw_items:
            row = normalize_order_item(it)

            forma_envio = (row.get("forma_envio") or "").strip()
            transportadora = (row.get("transportadora") or "").strip()
            status = (row.get("status") or "").strip()

            chave = f"{forma_envio}|||{transportadora}|||{status}"

            if chave not in mapa:
                mapa[chave] = {
                    "forma_envio": forma_envio,
                    "transportadora": transportadora,
                    "status": status,
                    "quantidade": 0,
                    "exemplo_pedido": row.get("numero", ""),
                    "codigo_rastreio": row.get("codigo_rastreio", "")
                }

            mapa[chave]["quantidade"] += 1

        data = sorted(
            mapa.values(),
            key=lambda x: (-x["quantidade"], x["forma_envio"], x["transportadora"])
        )

        return jsonify({
            "ok": True,
            "total_api": total_api,
            "total_formas": len(data),
            "data": data
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


@app.get("/wbuy/pedidos/jt")
def wbuy_pedidos_jt():
    try:
        page_size = to_int(request.args.get("page_size", 100), 100)
        max_pages = to_int(request.args.get("max_pages", 20), 20)
        status_param = (request.args.get("status") or "nota fiscal emitida").strip().lower()

        cache_key = f"pedidos_jt_ps{page_size}_mp{max_pages}_st{status_param}"
        cached = cache_get(cache_key, ttl_sec=180)
        if cached:
            return jsonify(cached)

        raw_items, total_api = paginate_orders(
            page_size=page_size,
            sleep_ms=0,
            status_filter=status_param,
            max_pages=max_pages
        )

        out = []
        for it in raw_items:
            row = normalize_order_item(it)

            if not row_matches_status(row, status_param):
                continue

            if not contains_jt_shipping(it, row):
                continue

            out.append(row)

        payload = {
            "ok": True,
            "filtro": {
                "status": status_param,
                "transportadora": "J&T"
            },
            "total_api": total_api,
            "total": len(out),
            "data": out
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


@app.get("/wbuy/pedidos/jt-fast")
def wbuy_pedidos_jt_fast():
    try:
        page_size = to_int(request.args.get("page_size", 100), 100)
        status_param = (request.args.get("status") or "nota fiscal emitida").strip().lower()

        data = wbuy_get("/order/", params={"limit": f"0,{page_size}"})
        items = extract_order_list(data)

        out = []
        for it in items:
            row = normalize_order_item(it)

            if not row_matches_status(row, status_param):
                continue

            if not contains_jt_shipping(it, row):
                continue

            out.append(row)

        return jsonify({
            "ok": True,
            "filtro": {
                "status": status_param,
                "transportadora": "J&T"
            },
            "total": len(out),
            "data": out
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
