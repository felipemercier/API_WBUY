from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# =================== CONFIG ===================
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "").strip()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
SANDBOX = os.getenv("SANDBOX", "0") == "1" or not TOKEN

# =================== HELPERS ==================
def _to_cents(v):
    if v is None:
        return 0
    s = str(v).strip()
    try:
        # aceita "21,74", "21.74", 21.74
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return int(round(float(s) * 100))
    except Exception:
        try:
            n = int(s)
            return n if n >= 0 else 0
        except Exception:
            return 0

def _extract_shipping_and_tracking(order_dict):
    """
    Retorna (shipping_total_em_centavos, tracking_str) aceitando:
    - 'shipping_total' (centavos)
    - 'totals.shipping' (centavos)
    - 'frete.valor' (decimal)
    E tracking em: frete.rastreo/rastreio, rastreo/rastreio, tracking
    """
    if not isinstance(order_dict, dict):
        return 0, None

    candidates = [
        order_dict.get("shipping_total"),
        (order_dict.get("totals") or {}).get("shipping"),
    ]

    frete = order_dict.get("frete") or {}
    candidates.append(_to_cents(frete.get("valor")))

    tracking = (
        frete.get("rastreo")
        or frete.get("rastreio")
        or order_dict.get("rastreo")
        or order_dict.get("rastreio")
        or order_dict.get("tracking")
    )

    for c in candidates:
        cents = _to_cents(c)
        if cents > 0:
            return cents, tracking

    return 0, tracking

def _ok_json(d):
    return jsonify({k: v for k, v in d.items() if v is not None})

# =================== PING =====================
@app.route("/api/wbuy/ping")
def ping():
    if SANDBOX:
        return jsonify({"ok": True, "sandbox": True})
    try:
        url = f"{API_URL}/order?limit=1"
        r = requests.get(url, headers=HEADERS, timeout=20)
        return jsonify({"ok": r.ok})
    except Exception:
        return jsonify({"ok": False})

# ======= ORDER BY ID ==========================
@app.route("/api/wbuy/order/<order_id>")
def order_by_id(order_id):
    debug = request.args.get("debug") == "1"
    if SANDBOX:
        return _ok_json({
            "debug": {"mode": "mock-id"} if debug else None,
            "order_id": str(order_id),
            "shipping_total": 2174,
            "tracking": "AM000000000BR"
        })

    url = f"{API_URL}/order/{order_id}?complete=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not r.ok:
        return make_response(r.text, r.status_code)

    j = r.json()
    data = j.get("data")
    if isinstance(data, list) and data:
        data = data[0]

    if not isinstance(data, dict):
        return _ok_json({"order_id": str(order_id), "shipping_total": 0, "tracking": None})

    shipping, tracking = _extract_shipping_and_tracking(data)
    return _ok_json({
        "debug": {"hit": url} if debug else None,
        "order_id": str(data.get("id") or order_id),
        "shipping_total": shipping,
        "tracking": tracking,
    })

# ======= ORDER BY TRACKING ====================
@app.route("/api/wbuy/order")
def order_by_tracking():
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
    # Tenta listas com status comuns e sem filtro
    statuses = ["1", "3", "7", "16", "18", None]
    for st in statuses:
        url = f"{API_URL}/order?limit=100&complete=1"
        if st:
            url += f"&status={st}"
        tried.append(url)

        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except Exception:
            continue

        if not r.ok:
            continue

        data = r.json().get("data", []) or []
        for o in data:
            ship, trk = _extract_shipping_and_tracking(o)
            if (trk or "").upper() == tracking:
                return _ok_json({
                    "debug": {"tried": tried} if debug else None,
                    "order_id": str(o.get("id")),
                    "shipping_total": ship,
                    "tracking": trk,
                })

    return _ok_json({
        "debug": {"tried": tried} if debug else None,
        "order_id": None,
        "shipping_total": 0,
        "tracking": None
    }), 404

# Alias: /api/wbuy/tracking/<code>
@app.route("/api/wbuy/tracking/<code>")
def order_by_tracking_alias(code):
    with app.test_request_context(f"/api/wbuy/order?tracking={code}"):
        return order_by_tracking()

# ========== OUTRAS ROTAS QUE VOCÊ JÁ USAVA ==========
@app.route("/")
def home():
    return "API da Martier rodando com rotas WBuy + auxiliares"

@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    url = f"{API_URL}/order?status={status}&limit=100"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
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
    body = request.get_json(silent=True) or {}
    pedido_id = body.get("id")
    if not pedido_id:
        return jsonify({"success": False, "error": "id é obrigatório"}), 400
    url = f"{API_URL}/order/status/{pedido_id}"
    payload = {"status": "7", "info_status": "Pedido concluído via painel"}
    try:
        r = requests.put(url, json=payload, headers=HEADERS, timeout=30)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if r.status_code in (200, 201, 202, 204):
        return jsonify({"success": True})
    return make_response(r.text, r.status_code)

@app.route("/importar-produtos")
def importar_produtos():
    url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = r.json()
        produtos_raw = data.get("data", []) or []
        itens = []
        for produto in produtos_raw:
            nome = produto.get("produto", "sem nome")
            estoque = produto.get("estoque", []) or []
            for variacao in estoque:
                erp_id = variacao.get("erp_id", "sem erp_id")
                tamanho = "sem tamanho"
                variacoes = variacao.get("variacao", {}) or {}
                if variacoes.get("nome") == "Tamanho":
                    tamanho = variacoes.get("valor", "sem tamanho")
                itens.append({"produto": nome, "erp_id": erp_id, "tamanho": tamanho})
        return jsonify(itens)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/observacoes/<pedido_id>")
def buscar_observacoes(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
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

# =====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
