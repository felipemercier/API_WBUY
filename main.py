from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ===== Config WBuy =====
TOKEN = os.getenv("WBUY_TOKEN", "").strip()
API_URL = os.getenv("WBUY_BASE", "https://sistema.sistemawbuy.com.br/api/v1").rstrip("/")
SANDBOX = not bool(TOKEN)  # sem token => respostas mock para não quebrar

HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

# ===== HTTP session robusta (retry/backoff) =====
def make_session():
    s = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"GET", "POST", "PUT", "DELETE"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=30, pool_maxsize=60)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "Martier-API/1.0 (+render) requests"
    })
    return s

http = make_session()


def _ok_json(payload, status=200):
    """Filtra None do debug para não poluir resposta."""
    clean = {k: v for k, v in payload.items() if v is not None}
    return jsonify(clean), status


@app.route("/")
def home():
    return "API Martier OK"


@app.route("/api/wbuy/ping")
def ping():
    if SANDBOX:
        return jsonify({"ok": False, "sandbox": True})
    try:
        r = http.get(f"{API_URL}/order?limit=1", headers=HEADERS, timeout=20)
        return jsonify({"ok": r.ok, "status": r.status_code})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ========= Helpers de parsing =========
def _num(v):
    if v is None:
        return 0
    try:
        return int(round(float(str(v).replace(".", "").replace(",", "."))))
    except Exception:
        return 0


def _extract_shipping_and_tracking(order_obj):
    """Tenta extrair total de frete e código de rastreio do objeto 'order' da WBuy."""
    ship = 0
    trk = None

    # Alguns pedidos vêm como dicionário, outros podem vir parcialmente
    o = order_obj or {}

    # candidatos para total de frete
    candidates = [
        o.get("shipping_total"),
        o.get("frete_valor"),
        o.get("frete"),
        o.get("valor_frete"),
        o.get("totals", {}).get("shipping"),
        (o.get("order") or {}).get("shipping", {}).get("total"),
    ]
    for c in candidates:
        n = _num(c)
        if n > 0:
            ship = n
            break

    # candidatos para tracking
    for key in ("rastreo", "rastreio", "tracking", "rastreador", "codigo_rastreio"):
        v = o.get(key)
        if v:
            trk = str(v).upper()
            break

    # caminhos alternativos
    if not trk:
        trk = (o.get("frete") or {}).get("rastreo") or (o.get("frete") or {}).get("rastreio")

    if trk:
        trk = str(trk).upper()

    return ship, trk


# ========= Endpoints já existentes do seu app =========
@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    if SANDBOX:
        return jsonify([])
    try:
        url = f"{API_URL}/order?status={status}&limit=100"
        r = http.get(url, headers=HEADERS, timeout=30)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = r.json().get("data", [])
        if not isinstance(data, list):
            data = []
        return jsonify(data)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/concluir", methods=["POST"])
def concluir_pedido():
    if SANDBOX:
        return jsonify({"success": True})
    body = request.get_json(silent=True) or {}
    pedido_id = body.get("id")
    if not pedido_id:
        return jsonify({"success": False, "error": "id é obrigatório"}), 400
    url = f"{API_URL}/order/status/{pedido_id}"
    payload = {"status": "7", "info_status": "Pedido concluído via painel"}
    try:
        r = http.put(url, json=payload, headers=HEADERS, timeout=30)
        if r.status_code in (200, 201, 202, 204):
            return jsonify({"success": True})
        return make_response(r.text, r.status_code)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


# ========= Novos endpoints WBuy: por ID e por tracking =========
@app.route("/api/wbuy/order/<order_id>")
def order_by_id(order_id):
    debug = request.args.get("debug") == "1"
    if SANDBOX:
        return _ok_json({
            "debug": {"mode": "mock-id"} if debug else None,
            "order_id": str(order_id),
            "shipping_total": 2174,
            "tracking": "AM993103075BR",
        })

    tried = []
    try:
        url = f"{API_URL}/order/{order_id}"
        tried.append(url)
        r = http.get(url, headers=HEADERS, timeout=30)
        if not r.ok:
            return _ok_json({"debug": {"tried": tried} if debug else None,
                             "order_id": str(order_id), "shipping_total": 0, "tracking": None}, 404)
        data = r.json().get("data")
        if isinstance(data, list) and data:
            o = data[0]
        elif isinstance(data, dict):
            o = data
        else:
            o = {}
        ship, trk = _extract_shipping_and_tracking(o)
        return _ok_json({
            "debug": {"tried": tried} if debug else None,
            "order_id": str(order_id),
            "shipping_total": ship,
            "tracking": trk
        })
    except Exception as e:
        return _ok_json({"error": "unexpected", "message": str(e),
                         "tried": tried if debug else None}, 502)


@app.route("/api/wbuy/order")
def order_by_tracking():
    """GET /api/wbuy/order?tracking=AM...  -> {order_id, shipping_total, tracking}"""
    tracking = (request.args.get("tracking") or "").upper().strip()
    debug = request.args.get("debug") == "1"
    if not tracking:
        return jsonify({"error": "missing tracking"}), 400

    if SANDBOX:
        return _ok_json({
            "debug": {"mode": "mock-tracking"} if debug else None,
            "order_id": "999999",
            "shipping_total": 2199,
            "tracking": tracking
        })

    tried = []

    def _scan_list(url):
        """Nunca levanta exceção; devolve None quando não acha/erro de rede."""
        tried.append(url)
        try:
            r = http.get(url, headers=HEADERS, timeout=30)
            if not r.ok:
                return None
            data = (r.json().get("data") or [])
        except Exception:
            return None

        for o in data:
            ship, trk = _extract_shipping_and_tracking(o)
            t = (trk or "").upper()
            # bateu exatamente, ou apareceu em campos equivalentes
            if tracking in [t, str(o.get("rastreo") or "").upper(), str(o.get("rastreio") or "").upper()]:
                return {"order_id": str(o.get("id")) if o.get("id") is not None else None,
                        "shipping_total": ship, "tracking": trk or tracking}
        return None

    try:
        # 1) buscas diretas (mais baratas)
        for q in [
            f"{API_URL}/order?limit=100&complete=1&tracking={tracking}",
            f"{API_URL}/order?limit=100&complete=1&search={tracking}",
        ]:
            tried.append(q)
            try:
                r = http.get(q, headers=HEADERS, timeout=30)
                if r.ok:
                    data = (r.json().get("data") or [])
                    for o in data:
                        ship, trk = _extract_shipping_and_tracking(o)
                        t = (trk or "").upper()
                        if tracking in [t, str(o.get('rastreo') or '').upper(), str(o.get('rastreio') or '').upper()]:
                            return _ok_json({
                                "debug": {"tried": tried} if debug else None,
                                "order_id": str(o.get("id")) if o.get("id") is not None else None,
                                "shipping_total": ship,
                                "tracking": trk or tracking
                            })
            except Exception:
                pass

        # 2) varredura limitada (status e páginas) – SEM nunca quebrar
        statuses = [None] + [str(i) for i in range(1, 19)]
        pages = range(1, 6)
        for st in statuses:
            for p in pages:
                url = f"{API_URL}/order?limit=100&complete=1&page={p}"
                if st:
                    url += f"&status={st}"
                res = _scan_list(url)
                if res:
                    return _ok_json({"debug": {"tried": tried} if debug else None, **res})

        # não achou
        return _ok_json({"debug": {"tried": tried} if debug else None,
                         "order_id": None, "shipping_total": 0, "tracking": None}, 404)

    except Exception as e:
        return _ok_json({"error": "unexpected", "message": str(e),
                         "tried": tried if debug else None}, 502)


# ========= Outros endpoints que você já tinha =========
@app.route("/importar-produtos")
def importar_produtos():
    if SANDBOX:
        return jsonify([])
    try:
        url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
        r = http.get(url, headers=HEADERS, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = r.json().get("data", []) or []
        out = []
        for produto in data:
            nome = produto.get("produto", "sem nome")
            estoque = produto.get("estoque", []) or []
            for variacao in estoque:
                erp_id = variacao.get("erp_id", "sem erp_id")
                tamanho = "sem tamanho"
                v = variacao.get("variacao", {}) or {}
                if v.get("nome") == "Tamanho":
                    tamanho = v.get("valor", "sem tamanho")
                out.append({"produto": nome, "erp_id": erp_id, "tamanho": tamanho})
        return jsonify(out)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/observacoes/<pedido_id>")
def buscar_observacoes(pedido_id):
    if SANDBOX:
        return jsonify({"observacoes": ""})
    try:
        r = http.get(f"{API_URL}/order/{pedido_id}", headers=HEADERS, timeout=30)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = r.json().get("data")
        if isinstance(data, list) and data:
            observacoes = data[0].get("observacoes", "")
        elif isinstance(data, dict):
            observacoes = data.get("observacoes", "")
        else:
            observacoes = ""
        return jsonify({"observacoes": observacoes})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
