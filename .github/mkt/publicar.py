"""
Publica en Facebook, Instagram y YouTube las piezas APROBADAS cuya hora ya llegó.

  python3 .github/mkt/publicar.py --estado estado.json [--simular]

- Plan:          .github/mkt/semanas/AAAA-Www.json
- Aprobaciones:  .github/mkt/semanas/AAAA-Www.aprobaciones.json  {id: {estado: aprobado|rechazado, texto: {...}}}
- Medios:        R2 mkt/AAAA-Www/ (render.json, <id>.mp4, <id>.jpg, <id>-N.jpg)
- Estado:        archivo local (el workflow lo baja y lo sube a R2 mkt/estado.json después de CADA publicación)

Reglas: un intento por pieza y plataforma; si falla, queda el error y NO se reintenta
(se reporta en el informe diario y se decide a mano). Piezas con más de 20 h de atraso
se marcan "vencida" y no salen. TikTok: si config.json → tt_modo = "borrador", el video se manda como
borrador a la bandeja de TikTok (tiktok.py) y Andrés lo publica desde la app; si no, va a mano.
Secretos (entorno): META_PAGE_TOKEN, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, TT_CLIENT_KEY, TT_CLIENT_SECRET, TT_REFRESH_TOKEN.
"""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import CONFIG, ErrorPieza, MKT, http, leer_publico, log, semanas, aprobaciones, url_media, validar_texto, v3, enlace

TZ = ZoneInfo(CONFIG["zona"])
G = CONFIG["graph"]
VENCE_H = 20


RED_DE = {"fb": "fb", "comentario": "fb", "ig": "ig", "yt_titulo": "yt", "yt_desc": "yt", "tiktok": "tt"}
ESTADO = {}  # lo llena main(): para resolver enlaces a videos ya publicados (destino "video:<id>")


def url_destino(p, pl):
    """Enlace del único llamado a la acción de la pieza para la red pl (None si la pieza no lleva enlace)."""
    dest = (p.get("cta") or {}).get("destino", "cap-1")
    if dest.startswith("video:"):
        reg = (ESTADO.get(dest[6:]) or {})
        return (reg.get(pl) or {}).get("url") or (reg.get("yt") or {}).get("url") or (reg.get("fb") or {}).get("url")
    return enlace(p, pl, p.get("_semana"))


def texto_final(p, apr, clave):
    t = dict(p.get("texto") or {})
    t.update((apr.get("texto") or {}))
    v = t.get(clave, "")
    if "{enlace}" in v:
        u = url_destino(p, RED_DE.get(clave, "fb"))
        if not u:
            raise ErrorPieza(f"texto.{clave} pide {{enlace}} pero el destino {(p.get('cta') or {}).get('destino')} aún no tiene URL")
        v = v.replace("{enlace}", u)
    validar_texto(v, f"texto.{clave}")
    return v


VERIFICADOS = {}


def verificar_enlace(p, pl):
    """Reglas v3: el enlace se comprueba antes de publicar. 404, 5xx o falla de red detienen la pieza en esa red."""
    u = url_destino(p, pl)
    if not u or not u.startswith(CONFIG["sitio"]):
        return
    base = u.split("?")[0]
    if base not in VERIFICADOS:
        try:
            http("GET", base, cabeceras={"User-Agent": "Mozilla/5.0 (Linux; Android 13) radionovelas-redes"}, crudo=True, timeout=30)
            VERIFICADOS[base] = None
        except ErrorPieza as e:
            VERIFICADOS[base] = None if ("HTTP 403" in str(e) or "HTTP 429" in str(e)) else str(e)  # 403/429: protección del CDN, no un enlace roto
    if VERIFICADOS[base]:
        raise ErrorPieza(f"enlace roto, no se publica: {base} ({VERIFICADOS[base][:200]})")


# ---------- Facebook ----------

def fb_reel(p, apr, semana, token, hv=None):
    page = CONFIG["meta_page_id"]
    ini = http("POST", f"{G}/{page}/video_reels", {"upload_phase": "start", "access_token": token})
    vid = ini["video_id"]
    http("POST", ini["upload_url"], cabeceras={"Authorization": f"OAuth {token}", "file_url": url_media(semana, p["id"] + ".mp4", hv)})
    http("POST", f"{G}/{page}/video_reels", {"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                                              "description": texto_final(p, apr, "fb"), "access_token": token})
    # ya está publicado; desde aquí nada puede convertirlo en "error"
    r = {"id": vid}
    try:
        for _ in range(24):
            st = http("GET", f"{G}/{vid}?fields=status,permalink_url&access_token={token}")
            sts = st.get("status") or {}
            if sts.get("video_status") == "error" or (sts.get("publishing_phase") or {}).get("status") == "error":
                r["aviso"] = f"Facebook reporta error después de publicar: {sts}"; break
            if (sts.get("publishing_phase") or {}).get("status") == "complete":
                pl = st.get("permalink_url") or ""
                r["url"] = ("https://www.facebook.com" + pl) if pl.startswith("/") else (pl or None)
                break
            time.sleep(10)
        else:
            r["aviso"] = "publicación sin confirmar tras 4 min"
    except ErrorPieza as e:
        r["aviso"] = f"no se pudo confirmar la publicación: {e}"
    com = texto_final(p, apr, "comentario")
    if com:
        try:
            http("POST", f"{G}/{vid}/comments", {"message": com, "access_token": token})
        except ErrorPieza as e:
            r["aviso"] = f"comentario no publicado: {e}"
    return r


def fb_video(p, apr, semana, token, hv=None):
    """Video normal de Facebook (capítulo completo o parte de más de 90 s, que no cabe como Reel)."""
    page = CONFIG["meta_page_id"]
    datos = {"file_url": url_media(semana, p["id"] + ".mp4", hv), "description": texto_final(p, apr, "fb"), "access_token": token}
    if p.get("texto", {}).get("yt_titulo"):
        datos["title"] = texto_final(p, apr, "yt_titulo")[:250]
    v = http("POST", f"{G}/{page}/videos", datos, timeout=600)
    if "id" not in v:
        raise ErrorPieza(f"Facebook no devolvió id del video: {v}")
    r = {"id": v["id"], "url": f"https://www.facebook.com/{page}/videos/{v['id']}"}
    com = texto_final(p, apr, "comentario")
    if com:
        try:
            http("POST", f"{G}/{v['id']}/comments", {"message": com, "access_token": token})
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
    if "id" not in post:
        raise ErrorPieza(f"Facebook no devolvió id del post: {post}")
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
    if "id" not in m:
        raise ErrorPieza(f"Instagram no devolvió id: {m}")
    r = {"id": m["id"]}
    try:
        r["url"] = http("GET", f"{G}/{m['id']}?fields=permalink&access_token={token}").get("permalink")
    except ErrorPieza as e:
        r["aviso"] = f"publicado, sin enlace: {e}"
    return r


def ig_reel(p, apr, semana, token, hv=None):
    ig = CONFIG["meta_ig_user_id"]
    c = http("POST", f"{G}/{ig}/media", {"media_type": "REELS", "video_url": url_media(semana, p["id"] + ".mp4", hv),
                                         "caption": texto_final(p, apr, "ig"), "share_to_feed": "true",
                                         "cover_url": url_media(semana, p["id"] + ".jpg", hv), "access_token": token})
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


def yt_short(p, apr, semana, hv=None):
    tok = yt_token()
    video = http("GET", url_media(semana, p["id"] + ".mp4", hv), cabeceras={"User-Agent": "mkt"}, crudo=True, timeout=300)
    titulo = re.sub(r"[<>]", "", texto_final(p, apr, "yt_titulo"))[:95]
    meta = {"snippet": {"title": titulo, "description": texto_final(p, apr, "yt_desc"), "categoryId": "24",
                        "defaultLanguage": "es-419", "defaultAudioLanguage": "es-419",
                        "tags": ["radionovela", "radionovelas", "radionovelas colombianas", "nostalgia", p.get("serie_titulo", "La Hora de la Fortuna")]},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    if CONFIG.get("yt_contenido_sintetico") is not None:  # declaración de contenido alterado/sintético de YouTube (decisión de Andrés)
        meta["status"]["containsSyntheticMedia"] = bool(CONFIG["yt_contenido_sintetico"])
    limite = b"mktlimite7f3a"
    cuerpo = (b"--" + limite + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + json.dumps(meta).encode() +
              b"\r\n--" + limite + b"\r\nContent-Type: video/mp4\r\n\r\n" + video + b"\r\n--" + limite + b"--")
    r = http("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status", cuerpo,
             cabeceras={"Authorization": f"Bearer {tok}", "Content-Type": "multipart/related; boundary=" + limite.decode()}, timeout=600)
    if "id" not in r:
        raise ErrorPieza(f"YouTube no devolvió id: {str(r)[:300]}")
    largo = p.get("tipo") == "capitulo"
    res = {"id": r["id"], "url": f"https://www.youtube.com/{'watch?v=' if largo else 'shorts/'}{r['id']}"}
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
    ESTADO.clear(); ESTADO.update(estado)
    ahora = datetime.now(TZ)
    token = os.environ.get("META_PAGE_TOKEN", "")

    def guardar():
        ruta.write_text(json.dumps(estado, ensure_ascii=False, indent=1))
        if a.subir and not a.simular:
            for intento in range(3):
                if subprocess.run(a.subir, shell=True).returncode == 0:
                    return
                time.sleep(10)
            raise SystemExit("No se pudo subir estado.json a R2 tras 3 intentos. Detenido para no duplicar publicaciones.")

    # reintentos autorizados por Andrés: semanas/reintentos.json = [{"id": "w43-01", "plataforma": "fb", "clave": "r1"}]
    rp = MKT / "semanas" / "reintentos.json"
    usados = estado.setdefault("_reintentos", [])
    for r in (json.loads(rp.read_text()) if rp.exists() else []):
        k = f"{r['id']}/{r['plataforma']}/{r.get('clave', '')}"
        if k not in usados and r["plataforma"] in estado.get(r["id"], {}):
            log(f"reintento autorizado: {k}")
            estado[r["id"]].pop(r["plataforma"]); usados.append(k); guardar()

    hoy = ahora.strftime("%Y-%m-%d")
    cuenta = {k: sum(1 for pid, pe in estado.items() if not pid.startswith("_") for pl, v in pe.items() if pl == k and str(v.get("fecha", "")).startswith(hoy) and (v.get("id") or v.get("publish_id")))
              for k in CONFIG["max_por_dia"]}
    for ruta_plan in semanas():
        if ruta_plan.name.endswith(".aprobaciones.json"):
            continue
        plan = json.loads(ruta_plan.read_text())
        sem = plan["semana"]
        aprs = aprobaciones(sem)
        render = None
        for p in plan["piezas"]:
            p["_semana"] = sem
            apr = aprs.get(p["id"], {})
            if apr.get("estado") != "aprobado":
                continue
            try:
                cuando = datetime.strptime(f"{apr.get('fecha', p['fecha'])} {apr.get('hora', p['hora'])}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            except (ValueError, KeyError) as e:
                log(f"   {p.get('id')}: fecha/hora inválida ({e}); se salta"); continue
            if cuando > ahora:
                continue
            for pl in p.get("plataformas", []):
                if pl not in ("fb", "ig", "yt") and not (pl == "tt" and CONFIG.get("tt_modo") == "borrador"):
                    continue  # tt: a mano salvo modo borrador
                reg = estado.setdefault(p["id"], {})
                if pl in reg:
                    continue  # ya publicado, vencido o con error: un solo intento
                if ahora - cuando > timedelta(hours=VENCE_H):
                    reg[pl] = {"vencida": True, "fecha": ahora.isoformat()}; guardar(); continue
                if cuenta.get(pl, 0) >= CONFIG["max_por_dia"].get(pl, 3):
                    continue
                if render is None:
                    render = (leer_publico(f"mkt/{sem}/render.json", {}) or {}).get("piezas", {})
                rp = render.get(p["id"])
                if not rp:
                    continue  # aún sin render: se intenta en el siguiente ciclo
                if not rp.get("ok"):
                    reg[pl] = {"error": "sin medios renderizados (render.json)", "fecha": ahora.isoformat()}; guardar(); continue
                if p["tipo"] == "carrusel" and pl in ("yt", "tt"):
                    continue
                dest = (p.get("cta") or {}).get("destino", "")
                if dest.startswith("video:") and not url_destino(p, pl):
                    continue  # el capítulo completo al que apunta aún no está publicado: se intenta en el siguiente ciclo
                log(f"→ {p['id']} {pl}")
                if a.simular:
                    log("   (simulado)"); continue
                hv = rp.get("huella")
                reg[pl] = {"en_curso": True, "fecha": ahora.isoformat(), "semana": sem}; guardar()  # marca antes: nunca se publica dos veces
                try:
                    if v3(sem):
                        verificar_enlace(p, pl)
                    largo = p["tipo"] == "capitulo" or float(rp.get("segundos") or 0) > CONFIG.get("fb_reel_max_s", 90)
                    if pl == "fb":
                        r = fb_fotos(p, apr, sem, rp, token) if p["tipo"] == "carrusel" else fb_video(p, apr, sem, token, hv) if largo else fb_reel(p, apr, sem, token, hv)
                    elif pl == "ig":
                        r = ig_carrusel(p, apr, sem, rp, token) if p["tipo"] == "carrusel" else ig_reel(p, apr, sem, token, hv)
                    elif pl == "yt":
                        r = yt_short(p, apr, sem, hv)
                    else:
                        from tiktok import tt_borrador
                        r = tt_borrador(p, sem, hv)
                    r["fecha"] = datetime.now(TZ).isoformat(); r["semana"] = sem
                    ESTADO[p["id"]] = estado[p["id"]] | {pl: r}
                    cuenta[pl] = cuenta.get(pl, 0) + 1
                    log("   OK", r.get("url"))
                except Exception as e:  # cualquier fallo queda registrado; el ciclo sigue con las demás
                    r = {"error": f"{type(e).__name__}: {e}"[:900], "fecha": datetime.now(TZ).isoformat(), "semana": sem}
                    log("   ERROR", e)
                reg[pl] = r
                guardar()
    log("Ciclo de publicación terminado.")


if __name__ == "__main__":
    main()
