from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# CORS explícito (inclui preflight/OPTIONS automaticamente)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# 🔐 Token da WBuy (vem do .env)
TOKEN = os.getenv("WBUY_TOKEN")

# 🔧 Headers padrão para requisições na API WBuy
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

API_URL = "https://sistema.sistemawbuy.com.br/api/v1"


@app.route("/")
def home():
    return "API da Martier rodando com todas as rotas!"


# ✅ LISTAR PEDIDOS (por padrão, status 16 = Disponível para retirada)
@app.route("/api/pedidos")
def listar_pedidos():
    status = request.args.get("status", "16")
    url = f"{API_URL}/order?status={status}&limit=100"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if not r.ok:
            # Repassa o erro bruto da WBuy p/ facilitar diagnóstico
            return make_response(r.text, r.status_code)

        data = r.json().get("data", [])
        if not isinstance(data, list):
            data = []
        return jsonify(data)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


# ✅ CONCLUIR PEDIDO (altera status para 7 = Concluído)
@app.route("/api/concluir", methods=["POST"])
def concluir_pedido():
    body = request.get_json(silent=True) or {}
    pedido_id = body.get("id")
    if not pedido_id:
        return jsonify({"success": False, "error": "id é obrigatório"}), 400

    # Endpoint específico para status
    url = f"{API_URL}/order/status/{pedido_id}"
    payload = {
        "status": "7",  # Concluído
        "info_status": "Pedido concluído via painel"
    }

    try:
        r = requests.put(url, json=payload, headers=HEADERS, timeout=30)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    # 204 No Content também representa sucesso na WBuy
    if r.status_code in (200, 201, 202, 204):
        return jsonify({"success": True}), 200

    # Se falhar, devolve o corpo do erro
    return make_response(r.text, r.status_code)


# ✅ IMPORTAR PRODUTOS ATIVOS COM VARIAÇÕES
@app.route("/importar-produtos", methods=["GET"])
def importar_produtos():
    url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        if not r.ok:
            return make_response(r.text, r.status_code)

        data = r.json()
        produtos_raw = data.get("data", [])
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


# ✅ OBSERVAÇÕES DO PEDIDO POR ID
@app.route("/observacoes/<pedido_id>", methods=["GET"])
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


if __name__ == "__main__":
    # Compatível com Render/Heroku etc.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
