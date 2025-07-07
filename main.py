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
        produtos = data.get("data", [])
        return jsonify(produtos)
    else:
        return jsonify({"erro": "Erro ao buscar produtos", "status": response.status_code}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
