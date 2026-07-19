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
        or get_nested(item, ["cliente", "telefone1"], "")
        or get_nested(item, ["cliente", "telefone2"], "")
        or get_nested(item, ["cliente", "telefone3"], "")
        or get_nested(item, ["cliente", "celular"], "")
        or get_nested(item, ["cliente", "fone"], "")
        or get_nested(item, ["customer", "phone"], "")
        or get_nested(item, ["customer", "phone1"], "")
        or get_nested(item, ["customer", "phone2"], "")
        or get_nested(item, ["customer", "phone3"], "")
        or item.get("telefone1")
        or item.get("telefone2")
        or item.get("telefone3")
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


@app.get("/wbuy/produtos")
def wbuy_produtos():
    try:
        q = (request.args.get("q") or "").strip().lower()
        page_size = to_int(request.args.get("page_size", 200), 200)
        max_pages = to_int(request.args.get("max_pages", 20), 20)

        offset = 0
        total_api = 0
        pages = 0
        out = []

        while True:
            data = wbuy_get("/product/stock/", params={"limit": f"{offset},{page_size}"})

            if not total_api:
                total_api = to_int(data.get("total", 0), 0)

            items = data.get("data") or []
            if not items:
                break

            for item in items:
                produto_obj = item.get("produto") or {}
                variacao = item.get("variacao") or {}
                cor = item.get("cor") or {}

                nome_produto = str(
                    produto_obj.get("produto")
                    or produto_obj.get("nome")
                    or item.get("produto_nome")
                    or "SEM NOME"
                ).strip()

                cor_nome = str(cor.get("nome") or "").strip()
                valor_variacao = str(variacao.get("valor") or variacao.get("nome") or "").strip()

                partes_nome = [nome_produto]
                if cor_nome and cor_nome != ".":
                    partes_nome.append(cor_nome)
                if valor_variacao:
                    partes_nome.append(valor_variacao)

                nome_completo = " ".join(partes_nome).strip()

                id_variacao = str(item.get("id") or "").strip()
                codigo_variacao = str(item.get("cod_estoque") or item.get("erp_id") or "").strip()
                sku = str(item.get("sku") or "").strip()
                gtin = str(item.get("gtin") or "").strip()

                texto_busca = f"{id_variacao} {codigo_variacao} {sku} {gtin} {nome_completo} {valor_variacao}".lower()

                if q and q not in texto_busca:
                    continue

                if not codigo_variacao:
                    continue

                out.append({
                    "id": id_variacao,
                    "nome": nome_completo,
                    "variacao": valor_variacao,
                    "codigo_variacao": codigo_variacao,
                    "sku": sku,
                    "gtin": gtin,
                    "codigo_barras": codigo_variacao
                })

            offset += page_size
            pages += 1

            if total_api and offset >= total_api:
                break

            if max_pages and pages >= max_pages:
                break

        return jsonify({
            "ok": True,
            "total_api": total_api,
            "total_produtos": len(out),
            "produtos": out
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


# =========================================================
# =================== NOVA ROTA ADICIONADA ================
# =========================================================
@app.get("/wbuy/estoque")
def estoque_simples():
    try:
        cache_key = "estoque_simples"
        cached = cache_get(cache_key, 300)

        if cached:
            return jsonify({
                "ok": True,
                "total": len(cached),
                "data": cached
            })

        itens, total = paginate_stock(
            page_size=200,
            sleep_ms=50,
            only_active=True,
            only_sale=True
        )

        simples = [{
            "sku": i["sku"],
            "produto": i["produto"],
            "tamanho": i["tamanho"],
            "cor": i["cor"],
            "estoque": i["qty"]
        } for i in itens]

        cache_set(cache_key, simples)

        return jsonify({
            "ok": True,
            "total": total,
            "data": simples
        })

    except Exception as e:
        traceback.print_exc()
        return safe_error("Erro ao buscar estoque", extra={"detail": str(e)})



# =========================================================
# ==================== CLIENTES WBUY =======================
# =========================================================

def extract_customer_list(data):
    if isinstance(data, dict):
        for key in ("data", "customers", "clientes", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    if isinstance(data, list):
        return data

    return []


def paginate_customers(page_size=200, sleep_ms=0, max_pages=50):
    offset = 0
    total = None
    out = []
    pages = 0

    page_size = max(1, min(page_size, 200))
    max_pages = max(1, min(max_pages, 100))

    while True:
        data = wbuy_get(
            "/customer/",
            params={"limit": f"{offset},{page_size}"}
        )

        if total is None:
            total = to_int(data.get("total", 0), 0)

        items = extract_customer_list(data)

        if not items:
            break

        out.extend(items)

        offset += page_size
        pages += 1

        if total and offset >= total:
            break

        if pages >= max_pages:
            break

        if sleep_ms and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    return out, total or len(out)


def normalize_customer_item(item):
    enderecos = item.get("enderecos") or []
    endereco = enderecos[0] if enderecos and isinstance(enderecos[0], dict) else {}

    telefones = [
        item.get("telefone1") or "",
        item.get("telefone2") or "",
        item.get("telefone3") or "",
    ]

    telefone_principal = next(
        (t for t in telefones if str(t).strip()),
        ""
    )

    return {
        "id": str(item.get("id") or ""),
        "nome": str(item.get("nome") or ""),
        "email": str(item.get("email") or ""),
        "cpf_cnpj": str(item.get("doc1") or ""),
        "telefone": str(telefone_principal),
        "telefones": [str(t) for t in telefones if str(t).strip()],
        "cidade": str(item.get("cidade") or endereco.get("cidade") or ""),
        "uf": str(item.get("uf") or endereco.get("uf") or ""),
        "tabela_nome": str(item.get("tabela_nome") or ""),
        "credito_valor": item.get("credito_valor") or "",
        "pontos": item.get("pontos") or "",
        "ativo": str(item.get("ativo") or ""),
        "raw": item
    }


def buscar_cliente_wbuy_por_telefone(telefone, page_size=200, max_pages=50):
    telefone_normalizado = normalizar_telefone(telefone)

    cache_key = f"cliente_wbuy_tel_v4_{telefone_normalizado}"
    cached = cache_get(cache_key, ttl_sec=600)

    if cached is not None:
        return cached

    raw_customers, total_api = paginate_customers(
        page_size=page_size,
        sleep_ms=0,
        max_pages=max_pages
    )

    for item in raw_customers:
        cliente = normalize_customer_item(item)

        for telefone_cliente in cliente.get("telefones", []):
            if telefones_compativeis(
                telefone_cliente,
                telefone_normalizado
            ):
                resultado = {
                    "encontrado": True,
                    "total_api": total_api,
                    "cliente": cliente
                }
                cache_set(cache_key, resultado)
                return resultado

    resultado = {
        "encontrado": False,
        "total_api": total_api,
        "cliente": None
    }
    cache_set(cache_key, resultado)
    return resultado


def pedido_pertence_ao_cliente(pedido, cliente):
    if not cliente:
        return False

    if telefones_compativeis(
        pedido.get("telefone"),
        cliente.get("telefone")
    ):
        return True

    email_pedido = str(pedido.get("email") or "").strip().lower()
    email_cliente = str(cliente.get("email") or "").strip().lower()

    if email_pedido and email_cliente and email_pedido == email_cliente:
        return True

    doc_pedido = apenas_numeros(pedido.get("cpf_cnpj"))
    doc_cliente = apenas_numeros(cliente.get("cpf_cnpj"))

    if doc_pedido and doc_cliente and doc_pedido == doc_cliente:
        return True

    return False


# =========================================================
# ====================== CRM WHATSAPP ======================
# =========================================================

def apenas_numeros(valor):
    return "".join(c for c in str(valor or "") if c.isdigit())


def normalizar_telefone(valor):
    telefone = apenas_numeros(valor)

    if telefone.startswith("55") and len(telefone) > 11:
        telefone = telefone[2:]

    return telefone


def telefones_compativeis(telefone_a, telefone_b):
    a = normalizar_telefone(telefone_a)
    b = normalizar_telefone(telefone_b)

    if not a or not b:
        return False

    if a == b:
        return True

    if len(a) >= 10 and len(b) >= 10:
        if a[-10:] == b[-10:]:
            return True

        if len(a) >= 11 and len(b) >= 11 and a[-11:] == b[-11:]:
            return True

    return False


def valor_float(valor):
    if valor is None:
        return 0.0

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()

    if not texto:
        return 0.0

    texto = texto.replace("R$", "").replace(" ", "")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except Exception:
        return 0.0


def ordenar_pedidos_por_data(pedidos):
    def chave(pedido):
        return str(pedido.get("data") or "")

    return sorted(pedidos, key=chave, reverse=True)


@app.get("/crm/cliente")
def crm_cliente():
    try:
        telefone_busca = request.args.get("telefone", "").strip()

        if not telefone_busca:
            return safe_error(
                "Informe o telefone usando ?telefone=27999999999",
                400
            )

        telefone_normalizado = normalizar_telefone(telefone_busca)

        if len(telefone_normalizado) < 10:
            return safe_error("Telefone inválido.", 400)

        customer_page_size = to_int(
            request.args.get("customer_page_size", 200),
            200
        )
        customer_max_pages = to_int(
            request.args.get("customer_max_pages", 50),
            50
        )
        order_page_size = to_int(
            request.args.get("order_page_size", 100),
            100
        )
        order_max_pages = to_int(
            request.args.get("order_max_pages", 100),
            100
        )

        customer_page_size = max(1, min(customer_page_size, 200))
        customer_max_pages = max(1, min(customer_max_pages, 100))
        order_page_size = max(1, min(order_page_size, 200))
        order_max_pages = max(1, min(order_max_pages, 100))

        cache_key = (
            f"crm_cliente_v4_{telefone_normalizado}_"
            f"{customer_page_size}_{customer_max_pages}_"
            f"{order_page_size}_{order_max_pages}"
        )

        cached = cache_get(cache_key, ttl_sec=180)
        if cached:
            return jsonify(cached)

        busca_cliente = buscar_cliente_wbuy_por_telefone(
            telefone_normalizado,
            page_size=customer_page_size,
            max_pages=customer_max_pages
        )

        if not busca_cliente.get("encontrado"):
            payload = {
                "ok": True,
                "encontrado": False,
                "motivo": "Cliente não localizado no cadastro da WBuy.",
                "telefone_consultado": telefone_normalizado,
                "total_clientes_api": busca_cliente.get("total_api", 0),
                "cliente": None,
                "estatisticas": {
                    "quantidade_pedidos": 0,
                    "valor_total": 0.0,
                    "ticket_medio": 0.0
                },
                "ultimo_pedido": None,
                "pedidos": []
            }
            cache_set(cache_key, payload)
            return jsonify(payload)

        cliente_wbuy = busca_cliente.get("cliente")

        raw_items, total_orders_api = paginate_orders(
            page_size=order_page_size,
            sleep_ms=0,
            status_filter=None,
            max_pages=order_max_pages
        )

        pedidos_encontrados = []

        for item in raw_items:
            pedido = normalize_order_item(item)

            if pedido_pertence_ao_cliente(pedido, cliente_wbuy):
                pedidos_encontrados.append(pedido)

        pedidos_encontrados = ordenar_pedidos_por_data(
            pedidos_encontrados
        )

        valor_total = sum(
            valor_float(pedido.get("valor_total"))
            for pedido in pedidos_encontrados
        )

        quantidade_pedidos = len(pedidos_encontrados)
        ticket_medio = (
            valor_total / quantidade_pedidos
            if quantidade_pedidos
            else 0.0
        )

        pedidos_resumidos = []

        for pedido in pedidos_encontrados[:10]:
            pedidos_resumidos.append({
                "pedido_id": pedido.get("pedido_id", ""),
                "numero": pedido.get("numero", ""),
                "status": pedido.get("status", ""),
                "data": pedido.get("data", ""),
                "valor_total": pedido.get("valor_total", ""),
                "forma_envio": pedido.get("forma_envio", ""),
                "transportadora": pedido.get("transportadora", ""),
                "codigo_rastreio": pedido.get("codigo_rastreio", ""),
                "rastreio_url": pedido.get("rastreio_url", ""),
                "prazo": pedido.get("prazo", "")
            })

        cliente_publico = {
            "id": cliente_wbuy.get("id", ""),
            "nome": cliente_wbuy.get("nome", ""),
            "telefone": cliente_wbuy.get("telefone", ""),
            "email": cliente_wbuy.get("email", ""),
            "cpf_cnpj": cliente_wbuy.get("cpf_cnpj", ""),
            "cidade": cliente_wbuy.get("cidade", ""),
            "uf": cliente_wbuy.get("uf", ""),
            "tabela_nome": cliente_wbuy.get("tabela_nome", ""),
            "credito_valor": cliente_wbuy.get("credito_valor", ""),
            "pontos": cliente_wbuy.get("pontos", "")
        }

        payload = {
            "ok": True,
            "encontrado": True,
            "telefone_consultado": telefone_normalizado,
            "total_clientes_api": busca_cliente.get("total_api", 0),
            "total_pedidos_api": total_orders_api,
            "total_pedidos_api_consultados": len(raw_items),
            "cliente": cliente_publico,
            "estatisticas": {
                "quantidade_pedidos": quantidade_pedidos,
                "valor_total": round(valor_total, 2),
                "ticket_medio": round(ticket_medio, 2)
            },
            "ultimo_pedido": (
                pedidos_resumidos[0]
                if pedidos_resumidos
                else None
            ),
            "pedidos": pedidos_resumidos
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(
            "Erro ao consultar cliente no CRM.",
            500,
            {
                "detail": str(e),
                "trace": traceback.format_exc()
            }
        )

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
