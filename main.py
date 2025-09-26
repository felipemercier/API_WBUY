from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ========= WBuy =========
API_URL   = os.getenv("WBUY_BASE_URL", "https://sistema.sistemawbuy.com.br/api/v1").rstrip("/")
TOKEN     = os.getenv("WBUY_TOKEN", "")
TIMEOUT_S = 30

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Martier-API/1.4"
})

# ========= Helpers =========
def _smart_float(x, default=0.0):
    """
    Converte strings com formatos BR/US para float:
      - '1.234,56' -> 1234.56
      - '21,74'    -> 21.74
      - '21.74'    -> 21.74
      - 21.74      -> 21.74
    """
    if x is None:
        return default
    if isinstance(x, (int, float)):
        try:
            return float(x)
        except Exception:
            return default

    s = str(x).strip()
    if not s or s.lower() == "none":
        return default

    has_comma = "," in s
    has_dot   = "." in s

    try:
        if has_comma and has_dot:
            # Decide pelo último separador como decimal
            if s.rfind(",") > s.rfind("."):
                # BR: '.' milhar, ',' decimal
                s = s.replace(".", "").replace(",", ".")
            else:
                # EN: ',' milhar, '.' decimal
                s = s.replace(",", "")
        elif has_comma and not has_dot:
            # Apenas vírgula -> decimal
            s = s.replace(",", ".")
        else:
            # Apenas ponto ou nenhum -> ponto decimal (remove vírgulas de milhar)
            s = s.replace(",", "")
        return float(s)
    except Exception:
        # fallback bruto
        try:
            return float(s)
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

def _scan_for_shipping(obj, path="$", matches=None):
    """
    Varredura recursiva para chaves relacionadas a frete.
    """
    if matches is None:
        matches = []

    key_hits = {
        "frete", "valor_frete", "frete_total", "total_frete",
        "shipping", "shipping_total", "valor_envio", "valorenvio",
        "entrega", "delivery", "frete_valor"
    }

    try:
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}"
                kn = str(k).lower().replace("-", "_")
                if any(tag in kn for tag in key_hits):
                    matches.append((p, v))
                _scan_for_shipping(v, p, matches)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _scan_for_shipping(v, f"{path}[{i}]", matches)
    except Exception:
        pass
    return matches

def _extract_shipping_from_payload(payload, debug=False):
    """
    Extrai valor de frete de várias estruturas possíveis.
    Retorna (valor, dbg|None).
    """
    dbg = {"tried": [], "matches": []}

    # normaliza "data"
    data = payload.get("data", payload)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        data = {}

    # palpites diretos
    candidates = [
        data.get("shipping_total"),
        data.get("valor_frete"),
        data.get("frete_total"),
        data.get("total_frete"),
        data.get("frete_valor"),
        (data.get("frete") or {}).get("valor"),
        (data.get("totals") or {}).get("shipping") if isinstance(data.get("totals"), dict) else None,
        (data.get("totais") or {}).get("frete") if isinstance(data.get("totais"), dict) else None,
        (data.get("resumo") or {}).get("frete") if isinstance(data.get("resumo"), dict) else None,
        (data.get("shipping") or {}).get("total") if isinstance(data.get("shipping"), dict) else None,
        (data.get("order") or {}).get("shipping", {}).get("total") if isinstance(data.get("order"), dict) else None,
        (data.get("pagamento") or {}).get("frete") if isinstance(data.get("pagamento"), dict) else None,
        (data.get("financeiro") or {}).get("frete") if isinstance(data.get("financeiro"), dict) else None,
    ]

    # produtos[] também podem ter frete
    produtos = data.get("produtos") or data.get("itens") or []
    if isinstance(produtos, list):
        for it in produtos:
            if isinstance(it, dict):
                candidates.append(it.get("frete_valor"))
                candidates.append((it.get("frete") or {}).get("valor"))

    dbg["tried"] = [str(c) for c in candidates]

    for c in candidates:
        val = _smart_float(c, None)
        if val is not None:
            return (val, dbg) if debug else (val, None)

    # varredura completa por nomes de chave
    matches = _scan_for_shipping(data)
    dbg["matches"] = [{"path": p, "value": v} for p, v in matches[:100]]

    for _, v in matches:
        if isinstance(v, dict):
            for k in ("valor", "total", "valor_total"):
                if k in v:
                    val = _smart_float(v[k], None)
                    if val is not None:
                        return (val, dbg) if debug else (val, None)
        val = _smart_float(v, None)
        if val is not None:
            return (val, dbg) if debug else (val, None)

    return (0.0, dbg) if debug else (0.0, None)

def _normalize_order(payload, debug=False):
    """Retorna {order_id, tracking, shipping_total, [debug]}."""
    data = payload.get("data", payload)
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        data = {}

    order_id = _first_nonempty(
        data.get("id"), data.get("order_id"), data.get("pedido_id"), data.get("numero_pedido")
    )
    tracking = _first_nonempty(
        data.get("tracking"), data.get("codigo_rastreio"),
        data.get("codigo_rastreamento"), data.get("rastreio"),
        data.get("rastreio_codigo")
    )
    if not tracking:
        arr = data.get("rastreios") or data.get("trackings")
        if isinstance(arr, list):
            for r in arr:
                cand = r.get("codigo") if isinstance(r, dict) else r
                if cand:
                    tracking = str(cand)
                    break

    shipping_total, dbg = _extract_shipping_from_payload(payload, debug=debug)

    out = {
        "order_id": str(order_id) if order_id is not None else None,
        "tracking": tracking,
        "shipping_total": shipping_total
    }
    if debug:
        out["debug"] = dbg
    return out

# ========= Rotas existentes =========
@app.route("/")
def home():
    return "API da Martier rodando com todas as rotas!"

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})

@app.get("/api/wbuy/ping")
def ping_wbuy():
    return jsonify({"ok": True})

@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    url = f"{API_URL}/order?status={status}&limit=100"
    try:
        r = session.get(url, timeout=TIMEOUT_S)
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
        r = session.put(url, json=payload, timeout=TIMEOUT_S)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if r.status_code in (200, 201, 202, 204):
        return jsonify({"success": True}), 200

    return make_response(r.text, r.status_code)

@app.route("/importar-produtos", methods=["GET"])
def importar_produtos():
    url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
    try:
        r = session.get(url, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)

        data = r.json()
        produtos_raw = data.get("data", []) or []
        resp = []

        for produto in produtos_raw:
            nome = produto.get("produto", "sem nome")
            estoque = produto.get("estoque", []) or []
            for variacao in estoque:
                erp_id = variacao.get("erp_id", "sem erp_id")
                tamanho = "sem tamanho"
                variacoes = variacao.get("variacao", {}) or {}
                if variacoes.get("nome") == "Tamanho":
                    tamanho = variacoes.get("valor", "sem tamanho")

                resp.append({
                    "produto": nome,
                    "erp_id": erp_id,
                    "tamanho": tamanho
                })

        return jsonify(resp)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/observacoes/<pedido_id>", methods=["GET"])
def buscar_observacoes(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    try:
        r = session.get(url, timeout=TIMEOUT_S)
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

# ========= Novas rotas (proxy WBuy) =========
@app.get("/api/wbuy/order/<order_id>")
def wbuy_by_order(order_id):
    """
    Busca /order/{id} e extrai o frete do JSON.
    Use ?debug=1 para ver caminhos analisados.
    """
    debug = request.args.get("debug") is not None
    try:
        url = f"{API_URL}/order/{order_id}"
        r = session.get(url, timeout=TIMEOUT_S)
        if not r.ok:
            return make_response(r.text, r.status_code)
        payload = r.json()
        out = _normalize_order(payload, debug=debug)
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/wbuy/tracking/<tracking_code>")
def wbuy_by_tracking(tracking_code):
    """
    Fallback por código de rastreio: varre algumas páginas recentes.
    (Alguns ambientes WBuy não filtram por tracking via query.)
    """
    try:
        for page in range(1, 4):  # até 300 pedidos recentes
            url = f"{API_URL}/order?limit=100&page={page}"
            r = session.get(url, timeout=TIMEOUT_S)
            if not r.ok:
                break
            arr = r.json().get("data") or []
            for item in arr:
                cand = _first_nonempty(
                    item.get("tracking"),
                    item.get("codigo_rastreio"),
                    item.get("codigo_rastreamento"),
                    item.get("rastreio"),
                    item.get("rastreio_codigo"),
                )
                if cand and str(cand).strip().upper() == tracking_code.strip().upper():
                    return jsonify(_normalize_order({"data": item}))
    except Exception:
        pass
    return jsonify({"error": "order_not_found"}), 404

# ========= Run =========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
