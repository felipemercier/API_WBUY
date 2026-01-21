from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import os
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ====== Config WBuy ======
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

TIMEOUT = 30

# ====== XML Avançado ======
XML_URL = "https://sistema.sistemawbuy.com.br/xmlavancado/0dd5a2fcdb3bc2fc27915cfea8d3624b/produtos.xml"
NS = {"g": "http://base.google.com/ns/1.0"}

# ====== Helpers ======
def _safe_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return default

def _first_nonempty(*vals):
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None

def _normalize_order_json(payload):
    data = payload.get("data", payload)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        data = {}

    order_id = _first_nonempty(
        data.get("id"),
        data.get("order_id"),
        data.get("pedido_id"),
        data.get("numero_pedido"),
    )

    tracking = _first_nonempty(
        data.get("tracking"),
        data.get("codigo_rastreio"),
        data.get("codigo_rastreamento"),
        data.get("rastreio"),
    )

    shipping_total = _first_nonempty(
        data.get("shipping_total"),
        data.get("valor_frete"),
        data.get("frete"),
    )

    return {
        "order_id": str(order_id) if order_id else None,
        "tracking": tracking,
        "shipping_total": _safe_float(shipping_total, 0.0),
    }

# ====== ROTAS ======
@app.route("/")
def home():
    return "API da Martier rodando com todas as rotas!"

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})

# ====== PEDIDOS ======
@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    url = f"{API_URL}/order?status={status}&limit=100"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if not r.ok:
            return make_response(r.text, r.status_code)
        return jsonify(r.json().get("data", []))
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

    r = requests.put(url, json=payload, headers=HEADERS, timeout=TIMEOUT)
    if r.ok:
        return jsonify({"success": True})
    return make_response(r.text, r.status_code)

@app.route("/observacoes/<pedido_id>")
def buscar_observacoes(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if not r.ok:
        return make_response(r.text, r.status_code)

    data = r.json().get("data")
    if isinstance(data, list) and data:
        obs = data[0].get("observacoes", "")
    elif isinstance(data, dict):
        obs = data.get("observacoes", "")
    else:
        obs = ""

    return jsonify({"observacoes": obs})

# ====== IMPORTADOR XML (DEFINITIVO) ======
@app.route("/importar-produtos", methods=["GET"])
def importar_produtos():
    try:
        r = requests.get(XML_URL, timeout=60)
        if not r.ok:
            return jsonify({"erro": "Erro ao baixar XML"}), 500

        root = ET.fromstring(r.content)
        produtos = []

        for item in root.findall(".//item"):
            erp_id = item.findtext("g:id", default="", namespaces=NS).strip()
            nome = item.findtext("g:title", default="", namespaces=NS).strip()
            tamanho = item.findtext("g:size", default="", namespaces=NS).strip()

            if not erp_id:
                continue

            produtos.append({
                "erp_id": erp_id,
                "produto": nome or "sem nome",
                "tamanho": tamanho or "sem tamanho"
            })

        return jsonify(produtos)

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ====== RUN ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
