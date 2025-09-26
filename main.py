from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import os, requests, math
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ====== CONFIG WBUY ======
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "").strip()

HEADERS = {
    "Authorization": f"Bearer {TOKEN}" if TOKEN else "",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def _ok(r): 
    return r.status_code in (200, 201, 202)

def _num_centavos(v):
    """Normaliza dinheiro: aceita int/float/str BR, devolve centavos (int)."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        # se veio em reais com decimais (ex: 21.74) -> centavos
        if isinstance(v, float) and v < 1000:
            return int(round(v * 100))
        return int(round(v))
    s = str(v).strip()
    if not s:
        return 0
    # "21,74" / "1.234,56"
    s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except Exception:
        return 0

def _extract_shipping_total(order_json):
    cands = [
        order_json.get("shipping_total"),
        order_json.get("valor_frete"),
        order_json.get("frete_total"),
        order_json.get("total_frete"),
        (order_json.get("frete") or {}).get("valor"),
        (order_json.get("totals") or {}).get("shipping"),
        (order_json.get("order") or {}).get("shipping_total"),
    ]
    for c in cands:
        n = _num_centavos(c)
        if n > 0:
            return n
    return 0

def _extract_tracking_from_detail(order_json):
    cands = [
        (order_json.get("frete") or {}).get("rastreio"),
        order_json.get("rastreamento"),
        (order_json.get("shipping") or {}).get("tracking"),
        (order_json.get("order") or {}).get("tracking"),
    ]
    for c in cands:
        if c:
            return str(c).strip().upper()
    return ""

@app.route("/")
def home():
    return "API da Martier OK"

@app.route("/api/wbuy/ping")
def ping():
    if not TOKEN:
        return jsonify({"ok": False, "reason": "no_token"}), 200
    try:
        url = f"{API_URL}/order?limit=1"
        r = requests.get(url, headers=HEADERS, timeout=20)
        return jsonify({"ok": _ok(r)}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

@app.route("/api/wbuy/order/<order_id>")
def get_order_detail(order_id):
    """Detalhe por ID (pega tracking e frete)."""
    try:
        url = f"{API_URL}/order/{order_id}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        if not _ok(r):
            return make_response(r.text, r.status_code)
        data = r.json()
        # alguns endpoints devolvem {data: {...}} e outros já o objeto
        obj = data.get("data", data)
        ship = _extract_shipping_total(obj)
        trk = _extract_tracking_from_detail(obj)
        return jsonify({"order_id": str(order_id), "shipping_total": ship, "tracking": trk, "debug": {"raw": data}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/wbuy/order")
def find_by_query():
    """
    /api/wbuy/order?tracking=AM9931....BR[&deep=1]
       -> tenta search por tracking; se deep=1, faz varredura com detalhe por ID.
    /api/wbuy/order?id=9778016
       -> atalho p/ detalhe por id
    """
    order_id = (request.args.get("id") or "").strip()
    if order_id:
        return get_order_detail(order_id)

    tracking = (request.args.get("tracking") or "").strip().upper()
    deep = (request.args.get("deep") or "0").strip() in ("1", "true", "yes")

    if not tracking:
        return jsonify({"error": "informe tracking ou id"}), 400

    tried = []

    # 1) tentativa direta de search (se a WBuy associar o tracking ao campo pesquisável)
    try:
        url = f"{API_URL}/order?limit=100&complete=1&search={tracking}"
        tried.append(url)
        r = requests.get(url, headers=HEADERS, timeout=30)
        if _ok(r):
            data = r.json()
            arr = data.get("data", [])
            if isinstance(arr, dict):
                arr = arr.get("data", [])
            if arr:
                oid = arr[0].get("id") or arr[0].get("order_id")
                if oid:
                    return get_order_detail(str(oid))
    except Exception:
        pass

    if not deep:
        return jsonify({"order_id": None, "shipping_total": 0, "tracking": None, "debug": {"matches": [], "tried": tried}})

    # 2) varredura com detalhe: status 1..18, páginas 1..5 (configurável)
    STATUSES = list(range(1, 19))          # 1..18
    MAX_PAGES_PER_STATUS = 5               # pode aumentar se precisar
    LIMIT = 100

    for status in STATUSES:
        for page in range(1, MAX_PAGES_PER_STATUS + 1):
            try:
                url = f"{API_URL}/order?limit={LIMIT}&complete=1&page={page}&status={status}"
                tried.append(url)
                r = requests.get(url, headers=HEADERS, timeout=30)
                if not _ok(r):
                    continue
                data = r.json()
                arr = data.get("data", [])
                if isinstance(arr, dict):  # alguns formatos
                    arr = arr.get("data", [])
                if not arr:
                    break  # próxima combinação

                # checagem por detalhe (único jeito de ver frete.rastreio)
                for item in arr:
                    oid = item.get("id") or item.get("order_id")
                    if not oid:
                        continue
                    durl = f"{API_URL}/order/{oid}"
                    tried.append(durl)
                    d = requests.get(durl, headers=HEADERS, timeout=30)
                    if not _ok(d):
                        continue
                    dobj = d.json().get("data", d.json())
                    trk = _extract_tracking_from_detail(dobj)
                    if trk == tracking:
                        ship = _extract_shipping_total(dobj)
                        return jsonify({
                            "order_id": str(oid),
                            "shipping_total": ship,
                            "tracking": trk,
                            "debug": {"matches": [durl], "tried": tried}
                        }), 200
            except Exception:
                continue

    # não achou
    return jsonify({"order_id": None, "shipping_total": 0, "tracking": None, "debug": {"matches": [], "tried": tried}}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
