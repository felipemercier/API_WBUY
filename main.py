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

@app.route('/importar-produtos', methods=['GET'])
def importar_produtos():
    headers = {
        "Authorization": os.getenv("WBUY_TOKEN"),
        "Content-Type": "application/json"
    }
    url = "https://sistema.sistemawbuy.com.br/api/v1/product/?ativo=1&limit=9999"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
        produtos_raw = data.get("data", [])

        # Pega só nome e erp_id
        produtos_processados = []
        for item in produtos_raw:
            nome = item.get("produto", "sem nome")
            erp_id = "sem erp_id"

            estoque = item.get("estoque", [])
            if estoque and isinstance(estoque, list) and len(estoque) > 0:
                erp_id = estoque[0].get("erp_id", "sem erp_id")

            produtos_processados.append({
                "nome": nome,
                "erp_id": erp_id
            })

        return jsonify(produtos_processados)

    else:
        return jsonify({"erro": "Erro ao buscar produtos", "status": response.status_code}), 500
