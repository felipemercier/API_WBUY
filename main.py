from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

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

# ✅ LISTAR PEDIDOS COM STATUS 16 (disponível para retirada)
@app.route("/api/pedidos")
def listar_pedidos():
    url = f"{API_URL}/order?status=16&limit=100"
    response = requests.get(url, headers=HEADERS)
    data = response.json().get("data", [])
    return jsonify(data)

# ✅ CONCLUIR PEDIDO
@app.route("/api/concluir", methods=["POST"])
def concluir_pedido():
    pedido_id = request.json.get("id")
    url = f"{API_URL}/order/{pedido_id}"
    payload = {
        "status_id": "7",  # Concluído
        "info_status": "Pedido concluído via painel"
    }
    response = requests.put(url, json=payload, headers=HEADERS)
    return jsonify({"success": response.ok}), response.status_code

# ✅ IMPORTAR PRODUTOS ATIVOS COM VARIAÇÕES
@app.route('/importar-produtos', methods=['GET'])
def importar_produtos():
    url = f"{API_URL}/product/?ativo=1&limit=9999&complete=1"
    response = requests.get(url, headers=HEADERS)

    if response.status_code == 200:
        data = response.json()
        produtos_raw = data.get("data", [])

        produtos_filtrados = []
        for produto in produtos_raw:
            nome = produto.get("produto", "sem nome")
            estoque = produto.get("estoque", [])

            for variacao in estoque:
                erp_id = variacao.get("erp_id", "sem erp_id")
                tamanho = "sem tamanho"

                variacoes = variacao.get("variacao", {})
                if variacoes.get("nome") == "Tamanho":
                    tamanho = variacoes.get("valor", "sem tamanho")

                produtos_filtrados.append({
                    "produto": nome,
                    "erp_id": erp_id,
                    "tamanho": tamanho
                })

        return jsonify(produtos_filtrados)

    return jsonify({"erro": "Erro ao buscar produtos", "status": response.status_code}), 500

# ✅ OBSERVAÇÕES DO PEDIDO POR ID
@app.route('/observacoes/<pedido_id>', methods=['GET'])
def buscar_observacoes(pedido_id):
    url = f"{API_URL}/order/{pedido_id}"
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            observacoes = data["data"][0].get("observacoes", "")
            return jsonify({"observacoes": observacoes})
        else:
            return jsonify({"erro": "Erro ao buscar pedido", "status": response.status_code}), 500
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == "__main__":
    app.run()
