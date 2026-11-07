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
MIN_GA4_AB = 10        # clics o plays: entre las dos al menos esto (desde GA4, por utm_content)
GA4_METRICA = {"clics": "usuarios", "plays": "play"}  # ab.metrica → campo de informe.txt → por_pieza[id][red]

TZ = ZoneInfo(CONFIG["zona"])
G = CONFIG["graph"]

FB_REEL = ["blue_reels_play_count", "fb_reels_total_plays", "post_impressions_unique", "post_video_avg_time_watched",
           "post_video_social_actions", "post_video_followers",
           "total_video_views", "total_video_avg_time_watched", "total_video_view_total_time"]  # los tres últimos: videos normales (capítulo completo, partes > 90 s)
FB_POST = ["post_media_view", "post_impressions_unique", "post_clicks", "post_reactions_by_type_total"]
IG_REEL = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "ig_reels_avg_watch_time", "follows"]
IG_CAR = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "follows"]


def valor(d):
    v = (d.get("values") or [{}])[0].get("value")
    return v if v is not None else d.get("total_value", {}).get("value")


def comentarios_ajenos(r):
    """Comentarios que no son de la página (el publicador deja uno con el enlace al capítulo en cada reel)."""
    propios = {str(CONFIG["meta_page_id"]), str(CONFIG.get("meta_ig_user_id", ""))}
    datos = (r.get("comments") or {}).get("data")
    if datos is None:
        return 0 if "comments" not in r else r["comments"].get("summary", {}).get("total_count", 0)
    return sum(1 for c in datos if str((c.get("from") or {}).get("id", "")) not in propios)


def fb(reg, tipo, token, err):
    m, oid = {}, reg["id"]
    if tipo == "carrusel":
        try:
            r = http("GET", f"{G}/{oid}?fields=shares,comments.limit(100){{from}},reactions.summary(true).limit(0)&access_token={token}")
            m.update(compartidos=(r.get("shares") or {}).get("count", 0), comentarios=comentarios_ajenos(r),
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
            r = http("GET", f"{G}/{oid}?fields=comments.limit(100){{from}},likes.summary(true).limit(0),length&access_token={token}")
            m.update(comentarios=comentarios_ajenos(r), me_gusta=r["likes"]["summary"]["total_count"], duracion=r.get("length"))
        except (ErrorPieza, KeyError) as e:
            err.append(f"fb campos: {e}")
        try:
            for d in http("GET", f"{G}/{oid}/video_insights?access_token={token}").get("data", []):
                if d.get("name") in FB_REEL:
                    m[d["name"]] = valor(d)
        except ErrorPieza as e:
            err.append(f"fb video_insights: {str(e)[:160]}")
    m["vistas"] = (m.get("blue_reels_play_count") or m.get("fb_reels_total_plays") or m.get("total_video_views")
                   or m.get("post_media_view") or m.get("post_impressions_unique"))
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
            if pl == "tt":  # borrador en la bandeja de TikTok: Andrés lo publica desde la app; sin métricas por API
                fila["estado"] = "borrador_tiktok"; fila["publish_id"] = reg.get("publish_id"); fila["estado_tiktok"] = reg.get("estado")
                if reg.get("aviso"): fila["aviso"] = reg["aviso"]
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
    # clics y plays por pieza: el Worker ga4-informe los publica en datos/informe.txt → por_pieza {id: {red: {usuarios, play}}}
    ga4_pp, ga4_err = {}, None
    if any(p.get("ab", {}).get("metrica") in GA4_METRICA for _, p in planes.values()):
        try:
            inf = leer_publico("datos/informe.txt", {}) or {}
            ga4_pp = inf.get("por_pieza") or {}
            if "por_pieza" not in inf:
                ga4_err = "informe.txt aún no trae por_pieza (falta el cambio del Worker ga4-informe)"
        except ErrorPieza as e:
            ga4_err = f"informe.txt: {e}"
        if ga4_err:
            errores.append(ga4_err)
    for (sem, nombre), vs in grupos.items():
        if set(vs) != {"A", "B"}: continue
        A, B = vs["A"], vs["B"]
        metrica = A["ab"].get("metrica", "vistas")
        if metrica in GA4_METRICA:
            campo = GA4_METRICA[metrica]
            res = {"semana": sem, "experimento": nombre, "variable": A["ab"]["variable"], "hipotesis": A["ab"].get("hipotesis"),
                   "metrica": metrica, "fuente": "GA4 (utm_content)", "A": A["id"], "B": B["id"], "por_plataforma": {}}
            edad = []
            for X in (A, B):
                fx = [f for f in filas if f["id"] == X["id"] and f.get("estado") == "publicada"]
                edad.append(min((f.get("horas_publicada") or 0) for f in fx) if fx else 0)
            for pl in A.get("plataformas", []):
                va = ((ga4_pp.get(A["id"]) or {}).get(pl) or {}).get(campo)
                vb = ((ga4_pp.get(B["id"]) or {}).get(pl) or {}).get(campo)
                if ga4_err or min(edad) < 48:
                    ver = "esperando 48 h" if not ga4_err else "sin datos de GA4 por pieza"
                else:
                    va, vb = va or 0, vb or 0
                    ver = ("muestra insuficiente" if va + vb < MIN_GA4_AB else "gana A" if va >= vb * UMBRAL_AB and va > 0
                           else "gana B" if vb >= va * UMBRAL_AB and vb > 0 else "sin diferencia clara")
                res["por_plataforma"][pl] = {"A": va, "B": vb, "veredicto": ver}
            ta = sum((v.get("A") or 0) for v in res["por_plataforma"].values()); tb = sum((v.get("B") or 0) for v in res["por_plataforma"].values())
            vers = [x["veredicto"] for x in res["por_plataforma"].values()]
            if not vers or any(v in ("esperando 48 h", "sin datos de GA4 por pieza") for v in vers):
                res["veredicto"] = next((v for v in vers if v in ("sin datos de GA4 por pieza", "esperando 48 h")), "esperando 48 h")
            else:  # el total de las redes decide: un solo enlace por pieza, poca muestra por red
                res["veredicto"] = ("muestra insuficiente" if ta + tb < MIN_GA4_AB else "gana A" if ta >= tb * UMBRAL_AB and ta > 0
                                    else "gana B" if tb >= ta * UMBRAL_AB and tb > 0 else "sin diferencia clara")
            res["total"] = {"A": ta, "B": tb}
            experimentos.append(res); continue
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
    # serialización: retención entre partes = vistas de la parte N+1 ÷ vistas de la parte N, por red
    serializacion = []
    series = defaultdict(dict)
    for pid, (sem, p) in planes.items():
        if p.get("tipo") == "parte" and p.get("parte"):
            series[(p["parte"]["capitulo"], p["parte"]["m"])][p["parte"]["n"]] = pid
    for (cap, m), partes in sorted(series.items()):
        fila_s = {"capitulo": cap, "partes": m, "por_plataforma": {}}
        for pl in ("fb", "ig", "yt"):
            vistas = []
            for n in range(1, m + 1):
                f = next((x for x in filas if x["id"] == partes.get(n) and x["plataforma"] == pl and x.get("estado") == "publicada"), None)
                vistas.append((f.get("metricas") or {}).get("vistas") if f else None)
            ret = [round(vistas[i + 1] / vistas[i], 2) if vistas[i] and vistas[i + 1] is not None else None for i in range(m - 1)]
            if any(v is not None for v in vistas):
                fila_s["por_plataforma"][pl] = {"vistas": vistas, "retencion": ret}
        serializacion.append(fila_s)
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
                               "video": f"{CONFIG['r2_publico']}/mkt/{sem}/{pid}.mp4",
                               "en_bandeja": bool((estado.get(pid) or {}).get("tt", {}).get("publish_id"))})
    salida = {"generado": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"), "cuentas": cuentas(token, errores),
              "piezas": filas, "por_familia": resumen("familia"), "por_tipo": resumen("tipo"), "por_plataforma": resumen("plataforma"), "por_hashtag": dict(por_hashtag), "por_ilustracion": resumen("ilustracion_nueva"),
              "top10": [{"id": f["id"], "plataforma": f["plataforma"], "familia": f["familia"], "vistas": f["metricas"].get("vistas"), "url": f["url"]} for f in top],
              "errores_publicacion": [f for f in filas if f.get("estado") == "error"],
              "tiktok_pendientes": tiktok, "errores_metricas": errores,
              "costos": {"total_usd_produccion": total_usd, "vistas_totales": total_vistas,
                         "usd_por_mil_vistas": round(total_usd / total_vistas * 1000, 3) if total_vistas else None,
                         "pauta_usd": 0, "por_pieza": sorted(costos, key=lambda c: c["id"]),
                         "nota": "Producción = ilustraciones + voces (Gemini). Publicar y medir no cuesta. La pauta, cuando la haya, se suma aparte."},
              "experimentos": experimentos, "serializacion": serializacion,
              "nota": "vistas: FB=reproducciones de Reel (o alcance en carrusel), IG=views (o alcance), YT=viewCount. Métricas acumuladas desde la publicación."}
    Path(a.salida).write_text(json.dumps(salida, ensure_ascii=False, indent=1))
    log(f"Métricas: {len(filas)} filas, {len(errores)} errores de métricas")


if __name__ == "__main__":
    main()
