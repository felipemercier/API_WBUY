import os
import re
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

        offset += len(items)
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

    status_obj = item.get("status") or {}
    status_id = ""

    if isinstance(status_obj, dict):
        status_id = str(status_obj.get("id") or "").strip()
        status = (
            status_obj.get("nome")
            or status_obj.get("status_nome")
            or status_obj.get("descricao")
            or ""
        )
    else:
        status = (
            status_obj
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

    valor_obj = (
        item.get("valor_total")
        or item.get("total")
        or item.get("total_venda")
        or item.get("order_total")
        or ""
    )

    if isinstance(valor_obj, dict):
        valor_total = (
            valor_obj.get("total")
            or valor_obj.get("valor")
            or valor_obj.get("subtotal")
            or 0
        )
    else:
        valor_total = valor_obj

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
        or ""
    )

    cep = (
        endereco_obj.get("cep")
        or endereco_obj.get("zipcode")
        or endereco_obj.get("zip_code")
        or get_nested(cliente_obj, ["cep"], "")
        or ""
    )

    endereco = (
        endereco_obj.get("endereco")
        or endereco_obj.get("logradouro")
        or endereco_obj.get("street")
        or get_nested(cliente_obj, ["endereco"], "")
        or ""
    )

    numero_endereco = (
        endereco_obj.get("numero")
        or endereco_obj.get("endnum")
        or endereco_obj.get("street_number")
        or get_nested(cliente_obj, ["endnum"], "")
        or ""
    )

    bairro = (
        endereco_obj.get("bairro")
        or endereco_obj.get("district")
        or get_nested(cliente_obj, ["bairro"], "")
        or ""
    )

    complemento = (
        endereco_obj.get("complemento")
        or endereco_obj.get("complement")
        or get_nested(cliente_obj, ["complemento"], "")
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
        or get_nested(cliente_obj, ["uf"], "")
        or get_nested(cliente_obj, ["estado"], "")
        or ""
    )

    previsao_entrega = (
        frete_obj.get("estimativa")
        or item.get("previsao_entrega")
        or ""
    )

    return {
        "pedido_id": str(pedido_id),
        "numero": str(numero),
        "cliente": str(cliente_nome),
        "status_id": str(status_id),
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
        "cep": str(cep),
        "endereco": str(endereco),
        "numero_endereco": str(numero_endereco),
        "bairro": str(bairro),
        "complemento": str(complemento),
        "cidade": str(cidade),
        "uf": str(uf),
        "previsao_entrega": str(previsao_entrega),
        "cliente_id": str(
            get_nested(item, ["cliente", "id"], "")
            or get_nested(item, ["customer", "id"], "")
            or ""
        ),
        "produtos": item.get("produtos") if isinstance(item.get("produtos"), list) else [],
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



def is_correios_tracking_code(value):
    """Reconhece o padrão oficial de objetos dos Correios: AA123456789BR."""
    tracking = str(value or "").strip().upper()
    return re.fullmatch(r"[A-Z]{2}\d{9}BR", tracking) is not None


def contains_correios_shipping(item, normalized_row=None):
    """Identifica Correios pelo serviço, transportadora ou padrão do rastreio."""
    frete = item.get("frete") or {}
    row = normalized_row or normalize_order_item(item)

    nome = str(frete.get("nome") or row.get("forma_envio") or "").strip().lower()
    tipo = str(
        frete.get("tipo_envio_nome")
        or row.get("transportadora")
        or ""
    ).strip().lower()
    rastreio = str(
        frete.get("rastreio")
        or row.get("codigo_rastreio")
        or ""
    ).strip().upper()

    if "correios" in nome or "correios" in tipo:
        return True

    if any(servico in nome for servico in ("sedex", "pac", "mini envios")):
        return True

    return is_correios_tracking_code(rastreio)


def detect_shipping_carrier(item, normalized_row=None):
    """Retorna correios, jt ou outro sem impedir a importação do pedido."""
    row = normalized_row or normalize_order_item(item)

    if contains_correios_shipping(item, row):
        return "correios"

    if contains_jt_shipping(item, row):
        return "jt"

    tracking = str(row.get("codigo_rastreio") or "").strip()

    if tracking.isdigit():
        return "jt"

    return "outro"


def row_matches_wbuy_status(row, status_id="", status_name=""):
    """Compara o status ATUAL do pedido, nunca apenas o histórico."""
    row_status_id = str(row.get("status_id") or "").strip()
    row_status_name = str(row.get("status") or "").strip().lower()

    expected_id = str(status_id or "").strip()
    expected_name = str(status_name or "").strip().lower()

    if expected_id and row_status_id == expected_id:
        return True

    if expected_name and row_status_name == expected_name:
        return True

    return False


def row_is_in_transport(row):
    return row_matches_wbuy_status(row, status_id="5", status_name="em transporte")


def row_is_completed(row):
    return row_matches_wbuy_status(row, status_id="7", status_name="pedido concluído")


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



@app.get("/wbuy/pedidos/transporte")
def wbuy_pedidos_transporte():
    """
    Lista todos os pedidos cujo status ATUAL na WBuy é 5 - Em transporte
    e que possuem código de rastreio, sem limitar a uma transportadora.
    """
    try:
        page_size = max(
            1,
            min(to_int(request.args.get("page_size", 100), 100), 200)
        )
        max_pages = max(
            1,
            min(to_int(request.args.get("max_pages", 20), 20), 200)
        )

        status_id = "5"
        status_name = "em transporte"

        cache_key = (
            f"pedidos_transporte_v2_ps{page_size}_"
            f"mp{max_pages}_sid{status_id}"
        )
        cached = cache_get(cache_key, ttl_sec=180)

        if cached:
            return jsonify(cached)

        raw_items, total_api = paginate_orders(
            page_size=page_size,
            sleep_ms=0,
            status_filter=status_id,
            max_pages=max_pages
        )

        out = []
        totals_by_carrier = {
            "jt": 0,
            "correios": 0,
            "outro": 0,
        }

        for item in raw_items:
            row = normalize_order_item(item)

            if not row_is_in_transport(row):
                continue

            tracking = str(row.get("codigo_rastreio") or "").strip().upper()

            if not tracking:
                continue

            carrier = detect_shipping_carrier(item, row)
            row["codigo_rastreio"] = tracking
            row["transportadora_detectada"] = carrier
            row["carrier"] = carrier

            totals_by_carrier[carrier] = totals_by_carrier.get(carrier, 0) + 1
            out.append(row)

        payload = {
            "ok": True,
            "filtro": {
                "status_id": status_id,
                "status": "Em transporte",
                "transportadora": "Todas",
                "exige_codigo_rastreio": True,
            },
            "total_api": total_api,
            "total": len(out),
            "totais_transportadora": totals_by_carrier,
            "data": out,
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(
            str(e),
            500,
            {"trace": traceback.format_exc()}
        )


@app.get("/wbuy/pedidos/correios")
def wbuy_pedidos_correios():
    """
    Lista pedidos dos Correios cujo status ATUAL na WBuy é
    5 - Em transporte e que possuem código de rastreio.
    """
    try:
        page_size = max(
            1,
            min(to_int(request.args.get("page_size", 100), 100), 200)
        )
        max_pages = max(
            1,
            min(to_int(request.args.get("max_pages", 20), 20), 200)
        )

        status_id = "5"

        cache_key = (
            f"pedidos_correios_v2_ps{page_size}_"
            f"mp{max_pages}_sid{status_id}"
        )
        cached = cache_get(cache_key, ttl_sec=180)

        if cached:
            return jsonify(cached)

        raw_items, total_api = paginate_orders(
            page_size=page_size,
            sleep_ms=0,
            status_filter=status_id,
            max_pages=max_pages
        )

        out = []

        for item in raw_items:
            row = normalize_order_item(item)

            if not row_is_in_transport(row):
                continue

            if not contains_correios_shipping(item, row):
                continue

            tracking = str(row.get("codigo_rastreio") or "").strip().upper()

            if not tracking:
                continue

            row["codigo_rastreio"] = tracking
            row["transportadora_detectada"] = "correios"
            row["carrier"] = "correios"
            out.append(row)

        payload = {
            "ok": True,
            "filtro": {
                "status_id": status_id,
                "status": "Em transporte",
                "transportadora": "Correios",
                "exige_codigo_rastreio": True,
            },
            "total_api": total_api,
            "total": len(out),
            "data": out,
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(
            str(e),
            500,
            {"trace": traceback.format_exc()}
        )


@app.get("/wbuy/pedidos/jt")
def wbuy_pedidos_jt():
    """Lista pedidos J&T cujo status ATUAL na WBuy é 5 - Em transporte."""
    try:
        page_size = to_int(request.args.get("page_size", 100), 100)
        max_pages = to_int(request.args.get("max_pages", 20), 20)

        # A WBuy usa o ID 5 para "Em transporte". Mantemos o filtro
        # local pelo ID e pelo nome para evitar depender somente do texto.
        status_id = "5"
        status_name = "em transporte"

        cache_key = f"pedidos_jt_v2_ps{page_size}_mp{max_pages}_sid{status_id}"
        cached = cache_get(cache_key, ttl_sec=180)
        if cached:
            return jsonify(cached)

        raw_items, total_api = paginate_orders(
            page_size=page_size,
            sleep_ms=0,
            status_filter=status_id,
            max_pages=max_pages
        )

        out = []
        for it in raw_items:
            row = normalize_order_item(it)

            if not row_is_in_transport(row):
                continue

            if not contains_jt_shipping(it, row):
                continue

            if not str(row.get("codigo_rastreio") or "").strip():
                continue

            out.append(row)

        payload = {
            "ok": True,
            "filtro": {
                "status_id": status_id,
                "status": "Em transporte",
                "transportadora": "J&T",
                "exige_codigo_rastreio": True
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
    """Versão rápida: consulta apenas o primeiro lote de pedidos em transporte."""
    try:
        page_size = to_int(request.args.get("page_size", 100), 100)
        status_id = "5"

        data = wbuy_get("/order/", params={
            "limit": f"0,{page_size}",
            "status": status_id
        })
        items = extract_order_list(data)

        out = []
        for it in items:
            row = normalize_order_item(it)

            if not row_is_in_transport(row):
                continue

            if not contains_jt_shipping(it, row):
                continue

            if not str(row.get("codigo_rastreio") or "").strip():
                continue

            out.append(row)

        return jsonify({
            "ok": True,
            "filtro": {
                "status_id": status_id,
                "status": "Em transporte",
                "transportadora": "J&T",
                "exige_codigo_rastreio": True
            },
            "total": len(out),
            "data": out
        })

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


@app.get("/wbuy/pedidos/jt-entregues")
def wbuy_pedidos_jt_entregues():
    """Lista pedidos J&T cujo status ATUAL é 7 - Pedido concluído."""
    try:
        page_size = to_int(request.args.get("page_size", 100), 100)
        max_pages = to_int(request.args.get("max_pages", 10), 10)
        status_id = "7"

        raw_items, total_api = paginate_orders(
            page_size=page_size,
            sleep_ms=0,
            status_filter=status_id,
            max_pages=max_pages
        )

        out = []
        for it in raw_items:
            row = normalize_order_item(it)

            if not row_is_completed(row):
                continue

            if not contains_jt_shipping(it, row):
                continue

            if not str(row.get("codigo_rastreio") or "").strip():
                continue

            out.append(row)

        return jsonify({
            "ok": True,
            "filtro": {
                "status_id": status_id,
                "status": "Pedido concluído",
                "transportadora": "J&T",
                "exige_codigo_rastreio": True
            },
            "total_api": total_api,
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
# =========== BUSCA DIRETA DE PRODUTO POR CÓDIGO ==========
# =========================================================
def _norm_code(value):
    return "".join(ch.lower() for ch in str(value or "").strip() if ch.isalnum())


def _first_text(obj, keys):
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        value = obj.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _code_matches(obj, query):
    if not isinstance(obj, dict):
        return False

    target = _norm_code(query)
    if not target:
        return False

    fields = (
        "id", "erp_id", "cod_estoque", "codestoque", "codigo_estoque",
        "codigo", "cod", "codigo_barras", "barcode", "ean", "gtin",
        "sku", "sku_id", "produto_id", "id_produto"
    )

    for key in fields:
        if _norm_code(obj.get(key)) == target:
            return True

    # Alguns códigos completos também aparecem dentro da tabela de valores.
    valores = obj.get("valores") or []
    if isinstance(valores, list):
        for valor in valores:
            if isinstance(valor, dict) and _norm_code(valor.get("codigo")) == target:
                return True

    return False


def _normalize_product_from_catalog(parent, stock, code_read):
    parent = parent if isinstance(parent, dict) else {}
    stock = stock if isinstance(stock, dict) else {}

    variacao_obj = stock.get("variacao") if isinstance(stock.get("variacao"), dict) else {}
    cor_obj = stock.get("cor") if isinstance(stock.get("cor"), dict) else {}

    nome = _first_text(parent, ["produto", "nome", "titulo", "descricao", "name"])
    cor = _first_text(cor_obj, ["nome", "nome_simples", "valor", "cor", "name"])
    tamanho = _first_text(variacao_obj, ["valor", "nome", "tamanho", "size"])

    erp_id = _first_text(stock, ["erp_id", "cod_estoque", "codigo_estoque", "id"])
    cod_estoque = _first_text(stock, ["cod_estoque", "erp_id", "codigo_estoque", "id"])
    codigo_barras = _first_text(stock, ["gtin", "ean", "codigo_barras", "barcode", "cod_estoque", "erp_id"])

    return {
        "id": _first_text(stock, ["id", "sku_id"]) or _first_text(parent, ["id"]) or code_read,
        "produto_id": _first_text(parent, ["id"]),
        "nome": nome or "Produto sem nome",
        "cor": "" if cor == "." else cor,
        "tamanho": tamanho,
        "variacao": tamanho,
        "erp_id": erp_id or code_read,
        "cod_estoque": cod_estoque or erp_id or code_read,
        "codigo_barras": codigo_barras or cod_estoque or erp_id or code_read,
        "sku": _first_text(stock, ["sku"]),
        "gtin": _first_text(stock, ["gtin", "ean"]),
        "estoque": to_int(stock.get("quantidade_em_estoque"), 0),
        "ativo": str(stock.get("ativo", parent.get("ativo", ""))),
        "venda": str(parent.get("venda", "")),
    }


@app.get("/wbuy/produto")
@app.get("/wbuy/produto/buscar")
def wbuy_produto_buscar():
    """Localiza uma variação pelo código bipado percorrendo /product/ com paginação.

    O endpoint /product/ retorna produtos-pai e, dentro de cada um, a lista `estoque`
    com SKU, ERP ID, código externo, cor e tamanho. A busca encerra assim que encontra
    uma correspondência exata.
    """
    try:
        codigo = (request.args.get("q") or request.args.get("codigo") or "").strip()
        if not codigo:
            return safe_error("Informe o código usando ?q=8770774", 400)

        page_size = max(10, min(to_int(request.args.get("page_size", 50), 50), 100))
        max_pages = max(1, min(to_int(request.args.get("max_pages", 100), 100), 200))
        cache_key = f"produto_catalogo_v5_{_norm_code(codigo)}"
        cached = cache_get(cache_key, ttl_sec=900)
        if cached:
            return jsonify(cached)

        offset = 0
        pages = 0
        total_api = 0

        while True:
            data = wbuy_get("/product/", params={"limit": f"{offset},{page_size}"})
            if not total_api:
                total_api = to_int(data.get("total", 0), 0)

            products = data.get("data") or []
            if not isinstance(products, list) or not products:
                break

            for parent in products:
                if not isinstance(parent, dict):
                    continue

                # Também aceita o código do produto-pai. Nesse caso, usa a primeira
                # variação ativa para devolver dados completos.
                parent_match = _code_matches(parent, codigo)
                stocks = parent.get("estoque") or []
                if not isinstance(stocks, list):
                    stocks = []

                for stock in stocks:
                    if not isinstance(stock, dict):
                        continue
                    if _code_matches(stock, codigo) or parent_match:
                        produto = _normalize_product_from_catalog(parent, stock, codigo)
                        payload = {
                            "ok": True,
                            "codigo_consultado": codigo,
                            "origem": "wbuy_product_catalog",
                            "pagina": pages + 1,
                            "offset": offset,
                            "total_api": total_api,
                            "produto": produto,
                        }
                        cache_set(cache_key, payload)
                        return jsonify(payload)

            pages += 1
            offset += len(products)

            if total_api and offset >= total_api:
                break
            if len(products) < page_size:
                break
            if pages >= max_pages:
                break

        return safe_error(
            "Produto não encontrado no catálogo da WBuy.",
            404,
            {
                "codigo_consultado": codigo,
                "paginas_consultadas": pages,
                "itens_catalogo_consultados": offset,
                "total_api": total_api,
            },
        )

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})


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


@app.get("/crm/cliente-pedidos")
@app.get("/crm/cliente")
def crm_cliente_pedidos():
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

        offset = to_int(request.args.get("offset", 0), 0)
        page_size = to_int(request.args.get("limit", 100), 100)

        offset = max(0, offset)
        page_size = max(1, min(page_size, 200))

        cache_key = (
            f"crm_pedidos_lote_v2_"
            f"{telefone_normalizado}_"
            f"{offset}_"
            f"{page_size}"
        )

        cached = cache_get(cache_key, ttl_sec=180)
        if cached:
            return jsonify(cached)

        data = wbuy_get(
            "/order/",
            params={"limit": f"{offset},{page_size}"}
        )

        raw_items = extract_order_list(data)
        total_api = to_int(data.get("total", 0), 0)

        encontrados = []

        for item in raw_items:
            pedido = normalize_order_item(item)

            if not telefones_compativeis(
                pedido.get("telefone"),
                telefone_normalizado
            ):
                continue

            produtos_resumidos = []

            for produto in pedido.get("produtos", []):
                if not isinstance(produto, dict):
                    continue

                produtos_resumidos.append({
                    "produto": produto.get("produto", ""),
                    "quantidade": to_int(produto.get("qtd", 1), 1),
                    "cor": produto.get("cor", ""),
                    "tamanho": produto.get("variacaoValor", ""),
                    "sku": produto.get("sku", ""),
                    "valor": produto.get("valor", "")
                })

            encontrados.append({
                "cliente": {
                    "id": pedido.get("cliente_id", ""),
                    "nome": pedido.get("cliente", ""),
                    "telefone": pedido.get("telefone", ""),
                    "email": pedido.get("email", ""),
                    "cpf_cnpj": pedido.get("cpf_cnpj", ""),
                    "cidade": pedido.get("cidade", ""),
                    "uf": pedido.get("uf", "")
                },
                "pedido": {
                    "pedido_id": pedido.get("pedido_id", ""),
                    "numero": pedido.get("numero", ""),
                    "status": pedido.get("status", ""),
                    "data": pedido.get("data", ""),
                    "valor_total": round(
                        valor_float(pedido.get("valor_total")),
                        2
                    ),
                    "forma_envio": pedido.get("forma_envio", ""),
                    "transportadora": pedido.get("transportadora", ""),
                    "codigo_rastreio": pedido.get("codigo_rastreio", ""),
                    "rastreio_url": pedido.get("rastreio_url", ""),
                    "prazo": pedido.get("prazo", ""),
                    "produtos": produtos_resumidos
                }
            })

        proximo_offset = offset + len(raw_items)

        terminou = (
            len(raw_items) == 0
            or (
                total_api > 0
                and proximo_offset >= total_api
            )
        )

        payload = {
            "ok": True,
            "versao_crm": "pedidos-lote-v2",
            "telefone_consultado": telefone_normalizado,
            "offset": offset,
            "limit": page_size,
            "recebidos": len(raw_items),
            "total_api": total_api,
            "proximo_offset": proximo_offset,
            "terminou": terminou,
            "quantidade_encontrada_no_lote": len(encontrados),
            "encontrados": encontrados
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(
            "Erro ao consultar lote de pedidos do CRM.",
            500,
            {
                "detail": str(e),
                "trace": traceback.format_exc()
            }
        )


@app.get("/crm/versao")
def crm_versao():
    return jsonify({
        "ok": True,
        "versao_crm": "pedidos-lote-v2",
        "rota": "/crm/cliente-pedidos",
        "modo": "consulta incremental por offset"
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
