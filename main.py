# main.py
from flask import Flask, jsonify, request, make_response, Blueprint
from flask_cors import CORS
import os, requests, csv, io, json
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ====== CONFIG ======
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN = os.getenv("WBUY_TOKEN", "").strip()
PRODUTOS_URL = os.getenv("PRODUTOS_URL", "").strip()  # opcional (JSON/CSV)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}" if TOKEN else "",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ====== Helpers comuns ======
def _ok(r):
    return r.status_code in (200, 201, 202)

def _json_safe(resp):
    try:
        return resp.json()
    except Exception:
        try:
            return resp.text
        except Exception:
            return {}

def _num_centavos(v):
    """Normaliza dinheiro (aceita int/float/str BR) -> centavos (int)."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v < 1000:
            return int(round(v * 100))
        return int(round(v))
    s = str(v).strip()
    if not s:
        return 0
    s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100))
    except Exception:
        return 0

def _extract_shipping_total(order_json: dict) -> int:
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

def _extract_tracking_from_detail(order_json: dict) -> str:
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

def _unwrap_first(obj):
    """
    Retorna o primeiro objeto "pedido" de qualquer formato comum:
    - {'data': {...}} / {'data': [{...}, ...]}
    - [{...}, ...]
    - {...}
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        if "data" in obj:
            d = obj.get("data")
            if isinstance(d, list):
                return d[0] if d else {}
            if isinstance(d, dict):
                return d
        return obj
    if isinstance(obj, list):
        return obj[0] if obj else {}
    return {}

def _detail_by_id_any(order_id, tried_list):
    """
    Busca detalhe garantindo formato de objeto:
      1) /order/{id}
      2) /order?id={id}&limit=1&complete=1
    """
    try:
        url = f"{API_URL}/order/{order_id}"
        tried_list.append(url)
        r = requests.get(url, headers=HEADERS, timeout=30)
        if _ok(r):
            raw = _json_safe(r)
            obj = _unwrap_first(raw)
            if obj:
                return obj, raw
    except Exception:
        pass
    try:
        url = f"{API_URL}/order?id={order_id}&limit=1&complete=1"
        tried_list.append(url)
        r = requests.get(url, headers=HEADERS, timeout=30)
        if _ok(r):
            raw = _json_safe(r)
            obj = _unwrap_first(raw)
            if obj:
                return obj, raw
    except Exception:
        pass
    return {}, {}

# ===================== ROTAS ORIGINAIS (v1) – mantidas =====================
@app.route("/")
def home():
    return "API da Martier OK"

@app.route("/api/wbuy/ping")
def ping_v1():
    if not TOKEN:
        return jsonify({"ok": False, "reason": "no_token"}), 200
    try:
        url = f"{API_URL}/order?limit=1"
        r = requests.get(url, headers=HEADERS, timeout=20)
        return jsonify({"ok": _ok(r)}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

@app.route("/api/wbuy/order/<order_id>")
def get_order_detail_v1(order_id):
    """
    (v1) Mantida a lógica original (pode falhar se a WBuy devolver lista).
    Foi preservada para não quebrar o outro sistema.
    """
    try:
        url = f"{API_URL}/order/{order_id}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        if not _ok(r):
            return make_response(r.text, r.status_code)
        data = r.json()
        obj = data.get("data", data)  # comportamento antigo
        if not isinstance(obj, dict):
            # mantém compat: devolve algo em vez de 500
            return jsonify({"order_id": str(order_id), "shipping_total": 0, "tracking": "", "debug": {"raw": data}}), 200
        ship = _extract_shipping_total(obj)
        trk = _extract_tracking_from_detail(obj)
        return jsonify({"order_id": str(order_id), "shipping_total": ship, "tracking": trk, "debug": {"raw": data}}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/wbuy/order")
def find_by_query_v1():
    """
    (v1) Mantido:
      /api/wbuy/order?id=...
      /api/wbuy/order?tracking=... [&deep=1]
    """
    order_id = (request.args.get("id") or "").strip()
    if order_id:
        return get_order_detail_v1(order_id)

    tracking = (request.args.get("tracking") or "").strip().upper()
    deep = (request.args.get("deep") or "0").strip() in ("1", "true", "yes")

    if not tracking:
        return jsonify({"error": "informe tracking ou id"}), 400

    tried = []
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
                    return get_order_detail_v1(str(oid))
    except Exception:
        pass

    if not deep:
        return jsonify({"order_id": None, "shipping_total": 0, "tracking": None, "debug": {"matches": [], "tried": tried}})

    STATUSES = list(range(1, 19))
    MAX_PAGES_PER_STATUS = 5
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
                if isinstance(arr, dict):
                    arr = arr.get("data", [])
                if not arr:
                    break
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
                    if not isinstance(dobj, dict):
                        continue
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

    return jsonify({"order_id": None, "shipping_total": 0, "tracking": None, "debug": {"matches": [], "tried": tried}}), 200

# ===================== NOVO (v2) – robusto p/ Correios =====================
api_v2 = Blueprint("api_v2", __name__, url_prefix="/api/v2/wbuy")

@api_v2.get("/ping")
def ping_v2():
    if not TOKEN:
        return jsonify({"ok": False, "reason": "no_token"}), 200
    try:
        url = f"{API_URL}/order?limit=1"
        r = requests.get(url, headers=HEADERS, timeout=25)
        return jsonify({"ok": _ok(r)}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 200

@api_v2.get("/order/<order_id>")
def get_order_detail_v2(order_id):
    tried = []
    try:
        obj, raw = _detail_by_id_any(order_id, tried)
        if not obj:
            return jsonify({"error": "not_found", "debug": {"tried": tried}}), 404
        ship = _extract_shipping_total(obj)
        trk  = _extract_tracking_from_detail(obj)
        return jsonify({
            "order_id": str(order_id),
            "shipping_total": ship,               # centavos
            "shipping_total_reais": ship/100.0,   # reais
            "tracking": trk,
            "debug": {"tried": tried}
        }), 200
    except Exception as e:
        return jsonify({"error": str(e), "debug": {"tried": tried}}), 500

@api_v2.get("/order")
def find_by_query_v2():
    """
    /api/v2/wbuy/order?id=...
    /api/v2/wbuy/order?tracking=AA123456789BR[&deep=1]  (deep=1 por padrão)
    """
    order_id = (request.args.get("id") or "").strip()
    if order_id:
        return get_order_detail_v2(order_id)

    tracking = (request.args.get("tracking") or "").strip().upper()
    deep = (request.args.get("deep") or "1").strip().lower() in ("1", "true", "yes", "y")

    if not tracking:
        return jsonify({"error": "informe tracking ou id"}), 400

    tried = []

    # 1) tentativa rápida via search
    try:
        url = f"{API_URL}/order?limit=100&complete=1&search={tracking}"
        tried.append(url)
        r = requests.get(url, headers=HEADERS, timeout=35)
        if _ok(r):
            raw = _json_safe(r)
            first = _unwrap_first(raw)
            oid = str(first.get("id") or first.get("order_id") or "").strip()
            if oid:
                obj, _ = _detail_by_id_any(oid, tried)
                ship = _extract_shipping_total(obj)
                trk  = _extract_tracking_from_detail(obj)
                return jsonify({
                    "order_id": oid,
                    "shipping_total": ship,
                    "shipping_total_reais": ship/100.0,
                    "tracking": trk,
                    "debug": {"matches": ["search"], "tried": tried}
                }), 200
    except Exception:
        pass

    if not deep:
        return jsonify({"order_id": None, "shipping_total": 0, "tracking": None, "debug": {"matches": [], "tried": tried}}), 200

    # 2) varredura robusta
    STATUSES = list(range(1, 19))
    MAX_PAGES_PER_STATUS = 5
    LIMIT = 100

    for status in STATUSES:
        for page in range(1, MAX_PAGES_PER_STATUS + 1):
            try:
                url = f"{API_URL}/order?limit={LIMIT}&complete=1&page={page}&status={status}"
                tried.append(url)
                r = requests.get(url, headers=HEADERS, timeout=40)
                if not _ok(r):
                    continue
                raw = _json_safe(r)
                arr = []
                if isinstance(raw, dict):
                    arr = raw.get("data", [])
                elif isinstance(raw, list):
                    arr = raw
                if isinstance(arr, dict):
                    arr = arr.get("data", [])
                if not isinstance(arr, list) or not arr:
                    break
                for item in arr:
                    oid = item.get("id") or item.get("order_id")
                    if not oid:
                        continue
                    obj, _ = _detail_by_id_any(oid, tried)
                    if not obj:
                        continue
                    trk = _extract_tracking_from_detail(obj)
                    if trk == tracking:
                        ship = _extract_shipping_total(obj)
                        return jsonify({
                            "order_id": str(oid),
                            "shipping_total": ship,
                            "shipping_total_reais": ship/100.0,
                            "tracking": trk,
                            "debug": {"matches": ["deep"], "tried": tried}
                        }), 200
            except Exception:
                continue

    return jsonify({"order_id": None, "shipping_total": 0, "tracking": None, "debug": {"matches": [], "tried": tried}}), 200

app.register_blueprint(api_v2)

# ===================== Compat: /importar-produtos (outro sistema) =====================
def _map_produtos_lista(lst):
    """Normaliza lista de dicts -> {produto, tamanho, erp_id}."""
    out = []
    for it in lst or []:
        nome   = it.get("produto") or it.get("nome") or it.get("name") or ""
        tam    = it.get("tamanho") or it.get("size") or it.get("variant") or ""
        erp_id = it.get("erp_id")  or it.get("sku")  or it.get("codigo") or it.get("code") or ""
        if not (nome and (tam or erp_id)):
            continue
        out.append({"produto": str(nome), "tamanho": str(tam), "erp_id": str(erp_id)})
    return out

@app.get("/importar-produtos")
def importar_produtos():
    # Se tiver PRODUTOS_URL, tenta baixar e normalizar (JSON ou CSV)
    if PRODUTOS_URL:
        try:
            r = requests.get(PRODUTOS_URL, timeout=40)
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            body  = r.content

            # JSON
            if "application/json" in ctype or body.strip().startswith(b"[") or body.strip().startswith(b"{"):
                data = json.loads(body.decode("utf-8", errors="ignore"))
                if isinstance(data, dict):
                    data = data.get("data") or data.get("items") or []
                return jsonify(_map_produtos_lista(data))

            # CSV (colunas: produto,tamanho,erp_id) – aceita ; ou ,
            text = body.decode("utf-8", errors="ignore")
            try:
                dialect = csv.Sniffer().sniff(text.splitlines()[0])
            except Exception:
                dialect = csv.excel
            reader = csv.DictReader(io.StringIO(text), dialect=dialect)
            rows = []
            for row in reader:
                rows.append({
                    "produto": row.get("produto") or row.get("nome") or "",
                    "tamanho": row.get("tamanho") or row.get("size") or "",
                    "erp_id":  row.get("erp_id")  or row.get("sku")  or row.get("codigo") or ""
                })
            rows = [r for r in rows if r["produto"] and (r["tamanho"] or r["erp_id"])]
            return jsonify(rows)
        except Exception as e:
            # Não derruba a UI: retorna estrutura vazia com info de erro
            return jsonify({"erro":"falha_fonte", "detalhe":str(e), "dados":[]}), 200

    # Mock básico (garante que o front não quebre se não houver fonte configurada)
    demo = [
        {"produto":"CAMISETA PRETA", "tamanho":"P",  "erp_id":"CMP-001"},
        {"produto":"CAMISETA PRETA", "tamanho":"M",  "erp_id":"CMP-002"},
        {"produto":"CAMISETA PRETA", "tamanho":"G",  "erp_id":"CMP-003"},
        {"produto":"MOLETOM AZUL",   "tamanho":"UN", "erp_id":"MOA-010"},
    ]
    return jsonify(demo)

# ====== run ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
