from flask import Flask, jsonify
import requests
import os
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return "API da Martier rodando."

# 🔄 ROTA DE PRODUTOS EXISTENTE
@app.route('/importar-produtos', methods=['GET'])
def importar_produtos():
    headers = {
        "Authorization": os.getenv("WBUY_TOKEN"),
        "Content-Type": "application/json"
    }
    url = "https://sistema.sistemawbuy.com.br/api/v1/product/?ativo=1&limit=9999&complete=1"
    response = requests.get(url, headers=headers)

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

# ✅ NOVA ROTA PARA OBSERVAÇÕES POR PEDIDO
@app.route('/observacoes/<pedido_id>', methods=['GET'])
def buscar_observacoes(pedido_id):
    headers = {
        "Authorization": os.getenv("WBUY_TOKEN"),
        "Content-Type": "application/json"
    }
    url = f"https://sistema.sistemawbuy.com.br/api/v1/order/{pedido_id}"

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            observacoes = data["data"][0].get("observacoes", "")
            return jsonify({"observacoes": observacoes})
        else:
            return jsonify({"erro": "Erro ao buscar pedido", "status": response.status_code}), 500
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

if __name__ == '__main__':
    app.run()

