"""
Publica en Facebook, Instagram y YouTube las piezas APROBADAS cuya hora ya llegó.

  python3 .github/mkt/publicar.py --estado estado.json [--simular]

- Plan:          .github/mkt/semanas/AAAA-Www.json
- Aprobaciones:  .github/mkt/semanas/AAAA-Www.aprobaciones.json  {id: {estado: aprobado|rechazado, texto: {...}}}
- Medios:        R2 mkt/AAAA-Www/ (render.json, <id>.mp4, <id>.jpg, <id>-N.jpg)
- Estado:        archivo local (el workflow lo baja y lo sube a R2 mkt/estado.json después de CADA publicación)

Reglas: un intento por pieza y plataforma; si falla, queda el error y NO se reintenta
(se reporta en el informe diario y se decide a mano). Piezas con más de 20 h de atraso
se marcan "vencida" y no salen. TikTok no se publica aquí (va a mano).
Secretos (entorno): META_PAGE_TOKEN, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN.
"""
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import CONFIG, ErrorPieza, MKT, http, leer_publico, log, semanas, aprobaciones, url_media, validar_texto

TZ = ZoneInfo(CONFIG["zona"])
G = CONFIG["graph"]
VENCE_H = 20


def texto_final(p, apr, clave):
    t = dict(p.get("texto") or {})
    t.update((apr.get("texto") or {}))
    v = t.get(clave, "")
    validar_texto(v, f"texto.{clave}")
    return v


# ---------- Facebook ----------

def fb_reel(p, apr, semana, token):
    page = CONFIG["meta_page_id"]
    ini = http("POST", f"{G}/{page}/video_reels", {"upload_phase": "start", "access_token": token})
    vid = ini["video_id"]
    http("POST", ini["upload_url"], cabeceras={"Authorization": f"OAuth {token}", "file_url": url_media(semana, p["id"] + ".mp4")})
    http("POST", f"{G}/{page}/video_reels", {"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                              "description": texto_final(p, apr, "fb"), "access_token": token})
    # espera a que quede publicado (máx. ~4 min)
    for _ in range(24):
        st = http("GET", f"{G}/{vid}?fields=status,permalink_url&access_token={token}")
        fase = (st.get("status") or {}).get("video_status")
        if fase in ("ready", "published") or (st.get("status") or {}).get("publishing_phase", {}).get("status") == "complete":
            break
        if fase == "error":
            raise ErrorPieza(f"Facebook rechazó el video: {st.get('status')}")
        time.sleep(10)
    r = {"id": vid, "url": st.get("permalink_url") and ("https://www.facebook.com" + st["permalink_url"] if st["permalink_url"].startswith("/") else st["permalink_url"])}
    com = texto_final(p, apr, "comentario")
    if com:
        try:
            http("POST", f"{G}/{vid}/comments", {"message": com, "access_token": token})
        except ErrorPieza as e:
            r["aviso"] = f"comentario no publicado: {e}"
    return r


def fb_fotos(p, apr, semana, render, token):
    page = CONFIG["meta_page_id"]
    ids = []
    for nombre in render["imagenes"]:
        f = http("POST", f"{G}/{page}/photos", {"url": url_media(semana, nombre), "published": "false", "access_token": token})
        ids.append({"media_fbid": f["id"]})
    datos = {"message": texto_final(p, apr, "fb"), "access_token": token}
    for i, m in enumerate(ids):
        datos[f"attached_media[{i}]"] = json.dumps(m)
    post = http("POST", f"{G}/{page}/feed", datos)
    return {"id": post["id"], "url": f"https://www.facebook.com/{post['id']}"}


# ---------- Instagram ----------

def ig_esperar(cid, token):
    for _ in range(36):
        st = http("GET", f"{G}/{cid}?fields=status_code,status&access_token={token}")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise ErrorPieza(f"Instagram no procesó el medio: {st}")
        time.sleep(10)
    raise ErrorPieza("Instagram tardó más de 6 min procesando el medio")


def ig_publicar(cid, token):
    ig = CONFIG["meta_ig_user_id"]
    m = http("POST", f"{G}/{ig}/media_publish", {"creation_id": cid, "access_token": token})
    info = http("GET", f"{G}/{m['id']}?fields=permalink&access_token={token}")
    return {"id": m["id"], "url": info.get("permalink")}


def ig_reel(p, apr, semana, token):
    ig = CONFIG["meta_ig_user_id"]
    c = http("POST", f"{G}/{ig}/media", {"media_type": "REELS", "video_url": url_media(semana, p["id"] + ".mp4"),
                                         "caption": texto_final(p, apr, "ig"), "share_to_feed": "true",
                                         "cover_url": url_media(semana, p["id"] + ".jpg"), "access_token": token})
    ig_esperar(c["id"], token)
    return ig_publicar(c["id"], token)


def ig_carrusel(p, apr, semana, render, token):
    ig = CONFIG["meta_ig_user_id"]
    hijos = []
    for nombre in render["imagenes"]:
        h = http("POST", f"{G}/{ig}/media", {"image_url": url_media(semana, nombre), "is_carousel_item": "true", "access_token": token})
        hijos.append(h["id"])
    for h in hijos:
        ig_esperar(h, token)
    c = http("POST", f"{G}/{ig}/media", {"media_type": "CAROUSEL", "children": ",".join(hijos),
                                         "caption": texto_final(p, apr, "ig"), "access_token": token})
    ig_esperar(c["id"], token)
    return ig_publicar(c["id"], token)


# ---------- YouTube ----------

def yt_token():
    for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        if not os.environ.get(k):
            raise ErrorPieza(f"falta el secreto {k}")
    r = http("POST", "https://oauth2.googleapis.com/token", {"client_id": os.environ["YT_CLIENT_ID"],
             "client_secret": os.environ["YT_CLIENT_SECRET"], "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    return r["access_token"]


def yt_short(p, apr, semana):
    tok = yt_token()
    video = http("GET", url_media(semana, p["id"] + ".mp4"), cabeceras={"User-Agent": "mkt"}, crudo=True, timeout=300)
    titulo = texto_final(p, apr, "yt_titulo")[:95]
    meta = {"snippet": {"title": titulo, "description": texto_final(p, apr, "yt_desc"), "categoryId": "24",
                        "defaultLanguage": "es-419", "defaultAudioLanguage": "es-419",
                        "tags": ["radionovela", "radionovelas", "radionovelas colombianas", "nostalgia", "La Hora de la Fortuna"]},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    if CONFIG.get("yt_contenido_sintetico") is not None:  # declaración de contenido alterado/sintético de YouTube (decisión de Andrés)
        meta["status"]["containsSyntheticMedia"] = bool(CONFIG["yt_contenido_sintetico"])
    limite = b"mktlimite7f3a"
    cuerpo = (b"--" + limite + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + json.dumps(meta).encode() +
              b"\r\n--" + limite + b"\r\nContent-Type: video/mp4\r\n\r\n" + video + b"\r\n--" + limite + b"--")
    r = http("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status", cuerpo,
             cabeceras={"Authorization": f"Bearer {tok}", "Content-Type": "multipart/related; boundary=" + limite.decode()}, timeout=600)
    res = {"id": r["id"], "url": f"https://www.youtube.com/shorts/{r['id']}"}
    priv = (r.get("status") or {}).get("privacyStatus")
    if priv != "public":
        res["aviso"] = f"YouTube lo dejó como '{priv}' (posible bloqueo de proyecto API sin auditar)"
    return res


# ---------- ciclo ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--estado", required=True); ap.add_argument("--simular", action="store_true")
    ap.add_argument("--subir", help="comando para subir el estado a R2 tras cada cambio")
    a = ap.parse_args()
    ruta = Path(a.estado)
    estado = json.loads(ruta.read_text()) if ruta.exists() else {}
    ahora = datetime.now(TZ)
    token = os.environ.get("META_PAGE_TOKEN", "")

    def guardar():
        ruta.write_text(json.dumps(estado, ensure_ascii=False, indent=1))
        if a.subir and not a.simular:
            subprocess.run(a.subir, shell=True, check=True)

    # reintentos autorizados por Andrés: semanas/reintentos.json = [{"id": "w43-01", "plataforma": "fb", "clave": "r1"}]
    rp = MKT / "semanas" / "reintentos.json"
    usados = estado.setdefault("_reintentos", [])
    for r in (json.loads(rp.read_text()) if rp.exists() else []):
        k = f"{r['id']}/{r['plataforma']}/{r.get('clave', '')}"
        if k not in usados and r["plataforma"] in estado.get(r["id"], {}):
            log(f"reintento autorizado: {k}")
            estado[r["id"]].pop(r["plataforma"]); usados.append(k); guardar()

    hoy = ahora.strftime("%Y-%m-%d")
    cuenta = {k: sum(1 for pid, pe in estado.items() if not pid.startswith("_") for pl, v in pe.items() if pl == k and str(v.get("fecha", "")).startswith(hoy) and v.get("id"))
              for k in CONFIG["max_por_dia"]}
    for ruta_plan in semanas():
        if ruta_plan.name.endswith(".aprobaciones.json"):
            continue
        plan = json.loads(ruta_plan.read_text())
        sem = plan["semana"]
        aprs = aprobaciones(sem)
        render = None
        for p in plan["piezas"]:
            apr = aprs.get(p["id"], {})
            if apr.get("estado") != "aprobado":
                continue
            cuando = datetime.fromisoformat(f"{apr.get('fecha', p['fecha'])}T{apr.get('hora', p['hora'])}").replace(tzinfo=TZ)
            if cuando > ahora:
                continue
            for pl in p.get("plataformas", []):
                if pl not in ("fb", "ig", "yt"):
                    continue  # tt: a mano
                reg = estado.setdefault(p["id"], {})
                if pl in reg:
                    continue  # ya publicado, vencido o con error: un solo intento
                if ahora - cuando > timedelta(hours=VENCE_H):
                    reg[pl] = {"vencida": True, "fecha": ahora.isoformat()}; guardar(); continue
                if cuenta.get(pl, 0) >= CONFIG["max_por_dia"][pl]:
                    continue
                if render is None:
                    render = (leer_publico(f"mkt/{sem}/render.json", {}) or {}).get("piezas", {})
                rp = render.get(p["id"])
                if not rp or not rp.get("ok"):
                    reg[pl] = {"error": "sin medios renderizados (render.json)", "fecha": ahora.isoformat()}; guardar(); continue
                if p["tipo"] == "carrusel" and pl == "yt":
                    continue
                log(f"→ {p['id']} {pl}")
                if a.simular:
                    log("   (simulado)"); continue
                try:
                    if pl == "fb":
                        r = fb_fotos(p, apr, sem, rp, token) if p["tipo"] == "carrusel" else fb_reel(p, apr, sem, token)
                    elif pl == "ig":
                        r = ig_carrusel(p, apr, sem, rp, token) if p["tipo"] == "carrusel" else ig_reel(p, apr, sem, token)
                    else:
                        r = yt_short(p, apr, sem)
                    r["fecha"] = datetime.now(TZ).isoformat(); r["semana"] = sem
                    cuenta[pl] = cuenta.get(pl, 0) + 1
                    log("   OK", r.get("url"))
                except ErrorPieza as e:
                    r = {"error": str(e), "fecha": datetime.now(TZ).isoformat(), "semana": sem}
                    log("   ERROR", e)
                reg[pl] = r
                guardar()
    log("Ciclo de publicación terminado.")


if __name__ == "__main__":
    main()
