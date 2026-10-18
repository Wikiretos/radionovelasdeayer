"""
Trae métricas de cada pieza publicada y de las cuentas → redes.json (el workflow lo sube a R2 datos/redes.txt).

  python3 .github/mkt/metricas.py --estado estado.json --salida redes.json

Cada métrica se pide por separado: si una no existe o falta un permiso, queda en `errores` y el resto sigue.
Permisos de Meta necesarios: read_insights (Página), instagram_manage_insights (IG).
"""
import argparse, json, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import CONFIG, ErrorPieza, http, leer_publico, log, semanas, aprobaciones

UMBRAL_AB = 1.3        # la variante gana si supera a la otra en 30 % o más
MIN_VISTAS_AB = 300    # y entre las dos suman al menos esto a las 48 h

TZ = ZoneInfo(CONFIG["zona"])
G = CONFIG["graph"]

FB_REEL = ["blue_reels_play_count", "fb_reels_total_plays", "post_impressions_unique", "post_video_avg_time_watched",
           "post_video_social_actions", "post_video_followers"]
FB_POST = ["post_media_view", "post_impressions_unique", "post_clicks", "post_reactions_by_type_total"]
IG_REEL = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "ig_reels_avg_watch_time", "follows"]
IG_CAR = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "follows"]


def valor(d):
    v = (d.get("values") or [{}])[0].get("value")
    return v if v is not None else d.get("total_value", {}).get("value")


def fb(reg, tipo, token, err):
    m, oid = {}, reg["id"]
    if tipo == "carrusel":
        try:
            r = http("GET", f"{G}/{oid}?fields=shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)&access_token={token}")
            m.update(compartidos=(r.get("shares") or {}).get("count", 0), comentarios=r["comments"]["summary"]["total_count"],
                     reacciones=r["reactions"]["summary"]["total_count"])
        except (ErrorPieza, KeyError) as e:
            err.append(f"fb campos: {e}")
        for k in FB_POST:
            try:
                d = http("GET", f"{G}/{oid}/insights?metric={k}&access_token={token}").get("data", [])
                if d: m[k] = valor(d[0])
            except ErrorPieza as e:
                err.append(f"fb {k}: {str(e)[:160]}")
    else:
        try:
            r = http("GET", f"{G}/{oid}?fields=comments.summary(true).limit(0),likes.summary(true).limit(0),length&access_token={token}")
            m.update(comentarios=r["comments"]["summary"]["total_count"], me_gusta=r["likes"]["summary"]["total_count"], duracion=r.get("length"))
        except (ErrorPieza, KeyError) as e:
            err.append(f"fb campos: {e}")
        try:
            for d in http("GET", f"{G}/{oid}/video_insights?access_token={token}").get("data", []):
                if d.get("name") in FB_REEL:
                    m[d["name"]] = valor(d)
        except ErrorPieza as e:
            err.append(f"fb video_insights: {str(e)[:160]}")
    m["vistas"] = m.get("blue_reels_play_count") or m.get("fb_reels_total_plays") or m.get("post_media_view") or m.get("post_impressions_unique")
    return m


def ig(reg, tipo, token, err):
    m, oid = {}, reg["id"]
    try:
        r = http("GET", f"{G}/{oid}?fields=like_count,comments_count&access_token={token}")
        m.update(me_gusta=r.get("like_count"), comentarios=r.get("comments_count"))
    except ErrorPieza as e:
        err.append(f"ig campos: {e}")
    for k in (IG_CAR if tipo == "carrusel" else IG_REEL):
        try:
            d = http("GET", f"{G}/{oid}/insights?metric={k}&access_token={token}").get("data", [])
            if d: m[k] = valor(d[0])
        except ErrorPieza as e:
            err.append(f"ig {k}: {str(e)[:160]}")
    m["vistas"] = m.get("views") or m.get("reach")
    m["compartidos"] = m.get("shares")
    return m


def yt_stats(ids, err):
    if not ids:
        return {}
    try:
        from publicar import yt_token
        tok = yt_token()
        out = {}
        for i in range(0, len(ids), 50):
            r = http("GET", "https://www.googleapis.com/youtube/v3/videos?part=statistics,status&id=" + ",".join(ids[i:i+50]),
                     cabeceras={"Authorization": f"Bearer {tok}"})
            for it in r.get("items", []):
                s = it.get("statistics", {})
                out[it["id"]] = {"vistas": int(s.get("viewCount", 0)), "me_gusta": int(s.get("likeCount", 0)),
                                 "comentarios": int(s.get("commentCount", 0)), "privacidad": it.get("status", {}).get("privacyStatus")}
        return out
    except ErrorPieza as e:
        err.append(f"yt: {e}")
        return {}


def cuentas(token, err):
    c = {}
    try:
        c["fb_seguidores"] = http("GET", f"{G}/{CONFIG['meta_page_id']}?fields=followers_count,fan_count&access_token={token}").get("followers_count")
    except ErrorPieza as e:
        err.append(f"fb cuenta: {e}")
    try:
        c["ig_seguidores"] = http("GET", f"{G}/{CONFIG['meta_ig_user_id']}?fields=followers_count&access_token={token}").get("followers_count")
    except ErrorPieza as e:
        err.append(f"ig cuenta: {e}")
    try:
        from publicar import yt_token
        r = http("GET", "https://www.googleapis.com/youtube/v3/channels?part=statistics&mine=true", cabeceras={"Authorization": f"Bearer {yt_token()}"})
        c["yt_suscriptores"] = int(r["items"][0]["statistics"].get("subscriberCount", 0))
    except (ErrorPieza, KeyError, IndexError) as e:
        err.append(f"yt cuenta: {e}")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--estado", required=True); ap.add_argument("--salida", required=True)
    ap.add_argument("--historial", help="historial.json (foto de métricas a las 48 h por pieza y plataforma); se actualiza en el sitio")
    a = ap.parse_args()
    estado = json.loads(Path(a.estado).read_text()) if Path(a.estado).exists() else {}
    token = os.environ.get("META_PAGE_TOKEN", "")
    ahora = datetime.now(TZ)
    planes = {}
    for rp in semanas():
        if not rp.name.endswith(".aprobaciones.json"):
            pl = json.loads(rp.read_text())
            for p in pl["piezas"]:
                planes[p["id"]] = (pl["semana"], p)
    errores, filas, yt_ids = [], [], []
    estado = {k: v for k, v in estado.items() if not k.startswith("_")}
    for pid, regs in estado.items():
        for pl, reg in regs.items():
            if pl == "yt" and reg.get("id"):
                yt_ids.append(reg["id"])
    ytm = yt_stats(yt_ids, errores)
    for pid, regs in estado.items():
        sem, p = planes.get(pid, (None, {}))
        for pl, reg in regs.items():
            txt = (p.get("texto") or {}).get({"yt": "yt_titulo"}.get(pl, pl), "") + " " + ((p.get("texto") or {}).get("yt_desc", "") if pl == "yt" else "")
            fila = {"id": pid, "semana": sem, "plataforma": pl, "tipo": p.get("tipo"), "familia": p.get("familia"),
                    "fecha": reg.get("fecha"), "url": reg.get("url"), "hashtags": sorted(set(h.lower() for h in __import__("re").findall(r"#\w+", txt))),
                    "ilustracion_nueva": bool(p.get("ilustracion"))}
            if reg.get("error") or reg.get("vencida") or reg.get("en_curso"):
                fila["estado"] = "error" if reg.get("error") else "vencida" if reg.get("vencida") else "error"
                fila["error"] = reg.get("error") or ("se cortó a mitad de la publicación: revisar a mano en la red si salió" if reg.get("en_curso") else None)
                filas.append(fila); continue
            err = []
            fila["estado"] = "publicada"
            if reg.get("aviso"): fila["aviso"] = reg["aviso"]
            fila["metricas"] = fb(reg, p.get("tipo"), token, err) if pl == "fb" else ig(reg, p.get("tipo"), token, err) if pl == "ig" else ytm.get(reg["id"], {})
            if err: fila["errores"] = err
            try:
                fila["horas_publicada"] = round((ahora - datetime.fromisoformat(reg["fecha"])).total_seconds() / 3600, 1)
            except Exception:
                pass
            filas.append(fila)
    # foto a las 48 h (comparación justa entre piezas y para A/B)
    hist = json.loads(Path(a.historial).read_text()) if a.historial and Path(a.historial).exists() else {}
    for f in filas:
        k = f"{f['id']}/{f['plataforma']}"
        if f.get("estado") == "publicada" and k not in hist and 40 <= (f.get("horas_publicada") or 0) <= 72:
            hist[k] = {"horas": f["horas_publicada"], **{m: f["metricas"].get(m) for m in ("vistas", "compartidos", "comentarios", "me_gusta", "saved", "reach")}}
        if k in hist:
            f["a_48h"] = hist[k]
    if a.historial:
        Path(a.historial).write_text(json.dumps(hist, ensure_ascii=False, indent=1))
    # costo de producción por pieza (render.json de cada semana)
    costo = {}
    for sem in sorted({s for s, _ in planes.values()}):
        try:
            for pid, r in ((leer_publico(f"mkt/{sem}/render.json", {}) or {}).get("piezas") or {}).items():
                if r.get("ok"):
                    costo[pid] = float(r.get("usd_aprox") or 0)
        except ErrorPieza as e:
            errores.append(f"render.json {sem}: {e}")
    por_pieza = defaultdict(lambda: {"vistas": 0, "compartidos": 0, "plataformas": []})
    for f in filas:
        if f.get("estado") != "publicada": continue
        g = por_pieza[f["id"]]; g["plataformas"].append(f["plataforma"])
        g["vistas"] += int(f.get("metricas", {}).get("vistas") or 0); g["compartidos"] += int(f.get("metricas", {}).get("compartidos") or 0)
    costos = []
    for pid, g in por_pieza.items():
        usd = costo.get(pid)
        costos.append({"id": pid, "semana": planes.get(pid, (None,))[0], "usd_produccion": usd, "vistas_todas_las_redes": g["vistas"],
                       "usd_por_mil_vistas": round(usd / g["vistas"] * 1000, 3) if usd is not None and g["vistas"] else None,
                       "plataformas": g["plataformas"]})
        for f in filas:
            if f["id"] == pid: f["usd_produccion_pieza"] = usd
    total_usd = round(sum(c["usd_produccion"] or 0 for c in costos), 3)
    total_vistas = sum(c["vistas_todas_las_redes"] for c in costos)
    # experimentos A/B
    experimentos = []
    grupos = defaultdict(dict)
    for pid, (sem, p) in planes.items():
        if p.get("ab"):
            grupos[(sem, p["ab"]["experimento"])][p["ab"]["variante"]] = p
    for (sem, nombre), vs in grupos.items():
        if set(vs) != {"A", "B"}: continue
        A, B = vs["A"], vs["B"]
        metrica = A["ab"].get("metrica", "vistas")
        res = {"semana": sem, "experimento": nombre, "variable": A["ab"]["variable"], "hipotesis": A["ab"].get("hipotesis"),
               "metrica": metrica, "A": A["id"], "B": B["id"], "por_plataforma": {}}
        for pl in A.get("plataformas", []):
            fa = next((f for f in filas if f["id"] == A["id"] and f["plataforma"] == pl), None)
            fb_ = next((f for f in filas if f["id"] == B["id"] and f["plataforma"] == pl), None)
            if not fa or not fb_: continue
            va, vb = (fa.get("a_48h") or {}).get(metrica), (fb_.get("a_48h") or {}).get(metrica)
            if va is None or vb is None:
                veredicto = "esperando 48 h"
            elif (fa.get("a_48h", {}).get("vistas") or 0) + (fb_.get("a_48h", {}).get("vistas") or 0) < MIN_VISTAS_AB:
                veredicto = "muestra insuficiente"
            elif va >= vb * UMBRAL_AB and va > 0:
                veredicto = "gana A"
            elif vb >= va * UMBRAL_AB and vb > 0:
                veredicto = "gana B"
            else:
                veredicto = "sin diferencia clara"
            res["por_plataforma"][pl] = {"A": va, "B": vb, "veredicto": veredicto}
        vers = [x["veredicto"] for x in res["por_plataforma"].values()]
        res["veredicto"] = ("gana A" if vers.count("gana A") > len(vers) / 2 else "gana B" if vers.count("gana B") > len(vers) / 2
                            else "esperando 48 h" if "esperando 48 h" in vers or not vers else "sin diferencia clara")
        experimentos.append(res)
    # resúmenes
    def resumen(clave):
        acc = defaultdict(lambda: {"piezas": 0, "vistas": 0, "compartidos": 0, "comentarios": 0})
        for f in filas:
            if f.get("estado") != "publicada": continue
            g = acc[f.get(clave) or "?"]; m = f.get("metricas", {})
            g["piezas"] += 1
            for k in ("vistas", "compartidos", "comentarios"):
                g[k] += int(m.get(k) or 0)
        for g in acc.values():
            g["vistas_promedio"] = round(g["vistas"] / g["piezas"], 1) if g["piezas"] else 0
        return dict(acc)
    por_hashtag = defaultdict(lambda: {"piezas": 0, "vistas": 0})
    for f in filas:
        if f.get("estado") != "publicada": continue
        for h in f.get("hashtags", []):
            por_hashtag[h]["piezas"] += 1; por_hashtag[h]["vistas"] += int(f.get("metricas", {}).get("vistas") or 0)
    for g in por_hashtag.values():
        g["vistas_promedio"] = round(g["vistas"] / g["piezas"], 1)
    top = sorted([f for f in filas if f.get("estado") == "publicada"], key=lambda f: -(f.get("metricas", {}).get("vistas") or 0))[:10]
    # pendientes de TikTok (a mano): aprobadas con tt, de hoy y mañana
    tiktok = []
    for pid, (sem, p) in planes.items():
        apr = aprobaciones(sem).get(pid, {})
        if apr.get("estado") == "aprobado" and "tt" in p.get("plataformas", []):
            f = apr.get("fecha", p.get("fecha"))
            if f in (ahora.strftime("%Y-%m-%d"), (ahora + timedelta(days=1)).strftime("%Y-%m-%d")):
                txt = dict(p.get("texto") or {}); txt.update(apr.get("texto") or {})
                tiktok.append({"id": pid, "fecha": f, "hora": apr.get("hora", p.get("hora")), "texto": txt.get("tiktok", ""),
                               "video": f"{CONFIG['r2_publico']}/mkt/{sem}/{pid}.mp4"})
    salida = {"generado": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"), "cuentas": cuentas(token, errores),
              "piezas": filas, "por_familia": resumen("familia"), "por_tipo": resumen("tipo"), "por_plataforma": resumen("plataforma"), "por_hashtag": dict(por_hashtag), "por_ilustracion": resumen("ilustracion_nueva"),
              "top10": [{"id": f["id"], "plataforma": f["plataforma"], "familia": f["familia"], "vistas": f["metricas"].get("vistas"), "url": f["url"]} for f in top],
              "errores_publicacion": [f for f in filas if f.get("estado") == "error"],
              "tiktok_pendientes": tiktok, "errores_metricas": errores,
              "costos": {"total_usd_produccion": total_usd, "vistas_totales": total_vistas,
                         "usd_por_mil_vistas": round(total_usd / total_vistas * 1000, 3) if total_vistas else None,
                         "pauta_usd": 0, "por_pieza": sorted(costos, key=lambda c: c["id"]),
                         "nota": "Producción = ilustraciones + voces (Gemini). Publicar y medir no cuesta. La pauta, cuando la haya, se suma aparte."},
              "experimentos": experimentos,
              "nota": "vistas: FB=reproducciones de Reel (o alcance en carrusel), IG=views (o alcance), YT=viewCount. Métricas acumuladas desde la publicación."}
    Path(a.salida).write_text(json.dumps(salida, ensure_ascii=False, indent=1))
    log(f"Métricas: {len(filas)} filas, {len(errores)} errores de métricas")


if __name__ == "__main__":
    main()
