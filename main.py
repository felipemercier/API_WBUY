from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# === CONFIG WBUY ===
API_URL = os.getenv("WBUY_API_URL", "https://sistema.sistemawbuy.com.br/api/v1")
TOKEN   = os.getenv("WBUY_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def http_get(url, *, timeout=30):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    return r

def norm_money(v):
    if v is None: return 0
    if isinstance(v, (int, float)): return int(round(float(v)))
    s = str(v).replace(".", "").replace(",", ".")
    try:
        return int(round(float(s)))
    except Exception:
        return 0

def pick_shipping_total(data):
    """
    Tenta achar o total de frete em vários formatos que a WBuy costuma enviar.
    Retorna em centavos (int).
    """
    cands = []
    # formatos diretos
    for k in ("shipping_total", "valor_frete", "frete_total", "total_frete"):
        if isinstance(data, dict) and k in data:
            cands.append(data[k])

    # estruturas aninhadas comuns
    nest_paths = [
        ("totals", "shipping"),
        ("shipping", "total"),
        ("order", "shipping", "total"),
        ("frete", "valor"),
        ("frete", "valor_total"),
    ]
    for path in nest_paths:
        ref = data
        for p in path:
            if isinstance(ref, dict) and p in ref:
                ref = ref[p]
            else:
                ref = None
                break
        if ref is not None:
            cands.append(ref)

    for c in cands:
        n = norm_money(c)
        if n >= 0:
            return n
    return 0

def extract_tracking(data):
    """
    Tenta extrair o código de rastreio do JSON do pedido.
    """
    cands = []
    if isinstance(data, dict):
        for k in ("rastreamento", "rastreio", "tracking", "codigo_rastreio", "track"):
            if k in data:
                cands.append(data[k])
        # estruturas aninhadas
        for path in [("frete","rastreio"), ("frete","rastreamento"), ("shipping","tracking")]:
            ref = data
            for p in path:
                if isinstance(ref, dict) and p in ref:
                    ref = ref[p]
                else:
                    ref = None
                    break
            if ref:
                cands.append(ref)

    for c in cands:
        s = str(c).strip().upper()
        if len(s) >= 10:
            return s
    return ""

def unwrap_data(resp_json):
    """A WBuy às vezes retorna {'data': {...}} ou {'data': [...]}."""
    if resp_json is None:
        return None
    if isinstance(resp_json, dict) and "data" in resp_json:
        return resp_json["data"]
    return resp_json

@app.route("/")
def home():
    return "API da Martier – online"

@app.route("/api/wbuy/ping")
def ping():
    try:
        r = http_get(f"{API_URL}/me", timeout=10)
        ok = r.ok
    except Exception:
        ok = False
    return jsonify({"ok": ok})

# ======= POR ID (mantido) =======
@app.route("/api/wbuy/order/<pedido_id>")
def order_by_id_path(pedido_id):
    return _order_by_id(pedido_id)

@app.route("/api/wbuy/order")
def order_by_id_query_or_tracking():
    pedido_id = request.args.get("id")
    tracking  = request.args.get("tracking")

    if pedido_id:
        return _order_by_id(pedido_id)

    if tracking:
        return _order_by_tracking(tracking)

    return jsonify({"error":"Use ?id=<pedido_id> ou ?tracking=<codigo>"}), 400

def _order_by_id(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    try:
        r = http_get(url)
        if not r.ok:
            return make_response(r.text, r.status_code)

        j = r.json()
        data = unwrap_data(j)
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            data = {}

        total = pick_shipping_total(data)
        track = extract_tracking(data)

        out = {
            "order_id": str(pedido_id),
            "shipping_total": total,
            "tracking": track or None,
        }
        if request.args.get("debug"):
            out["debug"] = {"tried":[url], "raw": j}
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======= POR RASTREIO (NOVO) =======
def _order_by_tracking(tracking_code):
    tried = []
    # 1) tentativa com parâmetro 'tracking='
    urls = [
        f"{API_URL}/order?limit=100&complete=1&tracking={tracking_code}",
        f"{API_URL}/order?limit=100&complete=1&search={tracking_code}",
    ]
    # 3) varrer alguns status comuns
    for st in (16, 7, 3, 1):
        urls.append(f"{API_URL}/order?status={st}&limit=100&complete=1")

    found_order = None
    raw_hit = None
    try:
        for url in urls:
            tried.append(url)
            r = http_get(url, timeout=30)
            if not r.ok:
                continue
            j = r.json()
            data = unwrap_data(j)
            # pode ser um único dict ou uma lista
            lst = data if isinstance(data, list) else [data]
            for item in lst:
                if not isinstance(item, dict):
                    continue
                # tentamos achar o tracking dentro do item
                track = extract_tracking(item).upper()
                if track and tracking_code.upper() in track:
                    found_order = item
                    raw_hit = j
                    break
            if found_order:
                break

        if not found_order:
            out = {"order_id": None, "shipping_total": 0, "tracking": None}
            if request.args.get("debug"):
                out["debug"] = {"tried": tried, "matches": []}
            return jsonify(out), 404

        # extrair ID, frete e tracking
        order_id = str(found_order.get("id") or found_order.get("order_id") or "").strip()
        total    = pick_shipping_total(found_order)
        track    = extract_tracking(found_order) or tracking_code

        out = {"order_id": order_id, "shipping_total": total, "tracking": track}
        if request.args.get("debug"):
            out["debug"] = {"tried": tried, "raw": raw_hit}
        return jsonify(out)
    except Exception as e:
        out = {"order_id": None, "shipping_total": 0, "tracking": None, "error": str(e)}
        if request.args.get("debug"):
            out["debug"] = {"tried": tried}
        return jsonify(out), 500

# ======= OUTROS (mantidos do seu arquivo) =======
@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    url = f"{API_URL}/order?status={status}&limit=100"
    try:
        r = http_get(url)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = r.json().get("data", [])
        return jsonify(data if isinstance(data, list) else [])
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
        return jsonify({"success": True}), 200
    return make_response(r.text, r.status_code)

# produtos / observações (mantidos)
@app.route("/importar-produtos", methods=["GET"])
def importar_produtos():
    url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
    try:
        r = http_get(url, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = r.json().get("data", [])
        out = []
        for p in data or []:
            nome = p.get("produto", "sem nome")
            for v in (p.get("estoque") or []):
                erp_id = v.get("erp_id", "sem erp_id")
                tamanho = "sem tamanho"
                variacoes = v.get("variacao") or {}
                if variacoes.get("nome") == "Tamanho":
                    tamanho = variacoes.get("valor", "sem tamanho")
                out.append({"produto": nome, "erp_id": erp_id, "tamanho": tamanho})
        return jsonify(out)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/observacoes/<pedido_id>", methods=["GET"])
def buscar_observacoes(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    try:
        r = http_get(url)
        if not r.ok:
            return make_response(r.text, r.status_code)
        data = unwrap_data(r.json())
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
