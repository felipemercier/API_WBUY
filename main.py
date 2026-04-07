@app.get("/wbuy/produtos")
def wbuy_produtos():
    try:
        q = (request.args.get("q") or "").strip().lower()
        page_size = to_int(request.args.get("page_size", 200), 200)
        max_pages = to_int(request.args.get("max_pages", 20), 20)

        cache_key = f"wbuy_produtos_q{q}_ps{page_size}_mp{max_pages}"
        cached = cache_get(cache_key, ttl_sec=300)
        if cached:
            return jsonify(cached)

        offset = 0
        total_api = 0
        pages = 0
        out = []

        while True:
            data = wbuy_get("/product/", params={"limit": f"{offset},{page_size}"})

            if not total_api:
                total_api = to_int(data.get("total", 0), 0)

            items = data.get("data") or []
            if not items:
                break

            for item in items:
                produto_id = str(item.get("id") or "").strip()

                nome = str(
                    item.get("produto")
                    or item.get("name")
                    or item.get("titulo")
                    or ""
                ).strip()

                sku = str(
                    item.get("sku")
                    or item.get("codigo")
                    or item.get("cod")
                    or ""
                ).strip()

                gtin = str(
                    item.get("gtin")
                    or item.get("barcode")
                    or item.get("ean")
                    or ""
                ).strip()

                codigo_barras = gtin if gtin else sku

                texto_busca = f"{produto_id} {nome} {sku} {gtin} {codigo_barras}".lower()

                if q and q not in texto_busca:
                    continue

                if not nome:
                    continue

                out.append({
                    "id": produto_id,
                    "nome": nome,
                    "sku": sku,
                    "gtin": gtin,
                    "codigo_barras": codigo_barras
                })

            offset += page_size
            pages += 1

            if total_api and offset >= total_api:
                break

            if max_pages and pages >= max_pages:
                break

        payload = {
            "ok": True,
            "total_api": total_api,
            "total_produtos": len(out),
            "produtos": out
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except Exception as e:
        return safe_error(str(e), 500, {"trace": traceback.format_exc()})
