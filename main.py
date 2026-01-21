from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv
import xml.etree.ElementTree as ET

load_dotenv()

app = Flask(__name__)
# CORS aberto (simplifica teste; em produção você pode limitar ao seu domínio)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ====== Config WBuy ======
API_URL = "https://sistema.sistemawbuy.com.br/api/v1"
TOKEN   = os.getenv("WBUY_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

TIMEOUT = 30

# ====== XML Avançado (catálogo completo) ======
XML_URL = os.getenv(
    "WBUY_XML_URL",
    "https://sistema.sistemawbuy.com.br/xmlavancado/0dd5a2fcdb3bc2fc27915cfea8d3624b/produtos.xml"
)

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
    """
    Normaliza a resposta da WBuy para um shape estável:
    { order_id, tracking, shipping_total }
    """
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
        data.get("rastreio_codigo"),
    )
    if not tracking:
        rastreios = data.get("rastreios") or data.get("trackings")
        if isinstance(rastreios, list) and rastreios:
            for r in rastreios:
                cand = r.get("codigo") if isinstance(r, dict) else r
                if cand:
                    tracking = str(cand)
                    break

    shipping_total = _first_nonempty(
        data.get("shipping_total"),
        data.get("valor_frete"),
        data.get("frete"),
        (data.get("totals") or {}).get("shipping") if isinstance(data.get("totals"), dict) else None,
        (data.get("shipping") or {}).get("total") if isinstance(data.get("shipping"), dict) else None,
        (data.get("order") or {}).get("shipping", {}).get("total") if isinstance(data.get("order"), dict) else None,
    )

    return {
        "order_id": str(order_id) if order_id is not None else None,
        "tracking": tracking,
        "shipping_total": _safe_float(shipping_total, 0.0),
    }

def _clean_text(s):
    if s is None:
        return ""
    # ElementTree pode devolver string com espaços/quebras
    return str(s).strip()

def _parse_xml_produtos(xml_bytes: bytes):
    """
    Lê o XML avançado e devolve lista no formato:
    [{produto, erp_id, tamanho}]
    Observação: o XML vem com namespace g:
    """
    root = ET.fromstring(xml_bytes)

    ns = {"g": "http://base.google.com/ns/1.0"}
    produtos = []

    for item in root.findall(".//item"):
        # Nome: pode vir em <title> (sem namespace)
        title_el = item.find("title")
        produto_nome = _clean_text(title_el.text if title_el is not None else "sem nome")

        # ERP/SKU: normalmente em <g:id>
        gid_el = item.find("g:id", ns)
        erp_id = _clean_text(gid_el.text if gid_el is not None else "sem erp_id")

        # Tamanho: normalmente em <g:size>
        size_el = item.find("g:size", ns)
        tamanho = _clean_text(size_el.text if size_el is not None else "sem tamanho")

        produtos.append({
            "produto": produto_nome,
            "erp_id": erp_id,
            "tamanho": tamanho
        })

    return produtos


# ====== SUAS ROTAS EXISTENTES ======
@app.route("/")
def home():
    return "API da Martier rodando com todas as rotas!"

@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    url = f"{API_URL}/order?status={status}&limit=100"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
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
        r = requests.put(url, json=payload, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if r.status_code in (200, 201, 202, 204):
        return jsonify({"success": True}), 200
    return make_response(r.text, r.status_code)

# ====== IMPORTAÇÃO DE PRODUTOS (agora com modo REST ou XML) ======
@app.route("/importar-produtos", methods=["GET"])
def importar_produtos():
    """
    Por padrão, importa via XML (catálogo completo).
    Se quiser forçar REST (o que pode vir incompleto), use:
      /importar-produtos?fonte=rest
    """
    fonte = (request.args.get("fonte") or "xml").strip().lower()

    # ---------- VIA XML (recomendado) ----------
    if fonte == "xml":
        try:
            r = requests.get(XML_URL, timeout=60)
            if not r.ok:
                return make_response(r.text, r.status_code)

            produtos = _parse_xml_produtos(r.content)

            # Mantém o retorno compatível com o seu front:
            # antes você retornava uma lista pura.
            # Aqui vamos retornar lista pura também.
            return jsonify(produtos)

        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    # ---------- VIA REST (legado / pode não trazer todos) ----------
    url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)

        data = r.json()
        produtos_raw = data.get("data", []) or []
        produtos_filtrados = []
        for produto in produtos_raw:
            nome = produto.get("produto", "sem nome")
            estoque = produto.get("estoque", []) or []
            for variacao in estoque:
                erp_id = variacao.get("erp_id", "sem erp_id")
                tamanho = "sem tamanho"
                variacoes = variacao.get("variacao", {}) or {}
                if variacoes.get("nome") == "Tamanho":
                    tamanho = variacoes.get("valor", "sem tamanho")
                produtos_filtrados.append({
                    "produto": nome,
                    "erp_id": erp_id,
                    "tamanho": tamanho
                })
        return jsonify(produtos_filtrados)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/observacoes/<pedido_id>", methods=["GET"])
def buscar_observacoes(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
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


# ====== ROTAS NOVAS (mínimas, para o front) ======
@app.get("/api/ping")
def ping():
    return jsonify({"ok": True})

@app.get("/api/wbuy/ping")
def ping_wbuy():
    return jsonify({"ok": True})

@app.get("/api/wbuy/order/<order_id>")
def wbuy_by_order(order_id):
    """Proxy: busca pedido por ID na WBuy e normaliza campos principais."""
    try:
        url = f"{API_URL}/order/{order_id}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if not r.ok:
            return make_response(r.text, r.status_code)
        return jsonify(_normalize_order_json(r.json()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# (Opcional) Buscar por código de rastreio — útil como fallback no front
@app.get("/api/wbuy/tracking/<tracking_code>")
def wbuy_by_tracking(tracking_code):
    # 1) tenta com possíveis parâmetros de filtro
    for p in ["tracking", "rastreio", "codigo_rastreio", "codigo_rastreamento"]:
        try:
            url = f"{API_URL}/order?{p}={tracking_code}&limit=100"
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.ok:
                data = r.json().get("data")
                if isinstance(data, list) and data:
                    return jsonify(_normalize_order_json({"data": data[0]}))
                if isinstance(data, dict) and data:
                    return jsonify(_normalize_order_json({"data": data}))
        except Exception:
            pass

    # 2) fallback: varre algumas páginas recentes
    try:
        for page in range(1, 4):
            url = f"{API_URL}/order?limit=100&page={page}"
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if not r.ok:
                break
            arr = r.json().get("data") or []
            for item in arr:
                cand = _first_nonempty(
                    item.get("tracking"),
                    item.get("codigo_rastreio"),
                    item.get("codigo_rastreamento"),
                    item.get("rastreio"),
                )
                if cand and str(cand).strip().upper() == tracking_code.strip().upper():
                    return jsonify(_normalize_order_json({"data": item}))
    except Exception:
        pass

    return jsonify({"error": "order_not_found"}), 404


# ====== DEBUG (pra você conferir rápido o XML) ======
@app.get("/debug/xml-info")
def debug_xml_info():
    try:
        r = requests.get(XML_URL, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)
        produtos = _parse_xml_produtos(r.content)
        exemplo = produtos[0] if produtos else None
        return jsonify({
            "ok": True,
            "xml_url": XML_URL,
            "total": len(produtos),
            "exemplo": exemplo
        })
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500


# ====== RUN ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
