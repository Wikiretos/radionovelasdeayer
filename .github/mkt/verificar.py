"""
Verifica los secretos del pipeline sin publicar nada → datos/verificacion.txt (sin valores secretos).

  python3 .github/mkt/verificar.py --salida verificacion.json
"""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import CONFIG, ErrorPieza, http

G = CONFIG["graph"]
NECESARIOS_META = {"pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_manage_engagement", "read_insights",
                   "instagram_basic", "instagram_content_publish", "instagram_manage_insights", "instagram_manage_comments"}


def prueba(nombre, f, res):
    try:
        res[nombre] = {"ok": True, **(f() or {})}
    except ErrorPieza as e:
        res[nombre] = {"ok": False, "error": str(e)[:400]}
    except KeyError as e:
        res[nombre] = {"ok": False, "error": f"falta el secreto {e}"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--salida", required=True)
    a = ap.parse_args()
    res = {}
    tok = os.environ.get("META_PAGE_TOKEN", "")

    def meta_pagina():
        if not tok: raise KeyError("META_PAGE_TOKEN")
        r = http("GET", f"{G}/{CONFIG['meta_page_id']}?fields=name,followers_count&access_token={tok}")
        return {"pagina": r.get("name"), "seguidores": r.get("followers_count")}

    def meta_permisos():
        if not tok: raise KeyError("META_PAGE_TOKEN")
        d = http("GET", f"{G}/debug_token?input_token={tok}&access_token={tok}").get("data", {})
        tiene = set(d.get("scopes", []))
        return {"tipo": d.get("type"), "valido": d.get("is_valid"), "caduca": d.get("expires_at"),
                "faltan": sorted(NECESARIOS_META - tiene), "app_id": d.get("app_id")}

    def meta_ig():
        if not tok: raise KeyError("META_PAGE_TOKEN")
        r = http("GET", f"{G}/{CONFIG['meta_ig_user_id']}?fields=username,followers_count,media_count&access_token={tok}")
        lim = http("GET", f"{G}/{CONFIG['meta_ig_user_id']}/content_publishing_limit?fields=quota_usage,config&access_token={tok}")
        return {"usuario": r.get("username"), "seguidores": r.get("followers_count"), "limite_publicacion": (lim.get("data") or [{}])[0]}

    def youtube():
        from publicar import yt_token
        t = yt_token()
        r = http("GET", "https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics,status&mine=true", cabeceras={"Authorization": f"Bearer {t}"})
        it = (r.get("items") or [{}])[0]
        return {"canal": it.get("snippet", {}).get("title"), "suscriptores": it.get("statistics", {}).get("subscriberCount"),
                "estado_canal": it.get("status", {})}

    def gemini():
        k = os.environ["GEMINI_API_KEY"]
        if not k: raise KeyError("GEMINI_API_KEY")
        r = http("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", cabeceras={"x-goog-api-key": k})
        nombres = {m["name"].split("/")[-1] for m in r.get("models", [])}
        quiero = [CONFIG["tts_modelo"], CONFIG["tts_modelo_pro"], json.loads((Path(__file__).parent / "estilo" / "estilo.json").read_text())["modelo"]]
        return {"modelos_disponibles": {q: q in nombres for q in quiero}}

    prueba("meta_pagina", meta_pagina, res)
    prueba("meta_permisos", meta_permisos, res)
    prueba("instagram", meta_ig, res)
    prueba("youtube", youtube, res)
    prueba("gemini", gemini, res)
    salida = {"generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "pruebas": res,
              "todo_ok": all(v.get("ok") for v in res.values()) and not res.get("meta_permisos", {}).get("faltan")}
    Path(a.salida).write_text(json.dumps(salida, ensure_ascii=False, indent=1))
    for k, v in res.items():
        print(("OK  " if v.get("ok") else "ERROR ") + k, "" if v.get("ok") else v.get("error"))


if __name__ == "__main__":
    main()
