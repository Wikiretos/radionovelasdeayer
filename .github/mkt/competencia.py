"""
Competencia y referentes en YouTube (lo único que se puede medir de otros por API sin permisos especiales).
Corre en Actions cada miércoles en la noche → datos/competencia.txt (lo lee el creador del jueves).

  python3 .github/mkt/competencia.py --salida competencia.json

- Búsquedas de `competencia.json → busquedas` en Colombia (regionCode CO, español), últimos 30 días, por vistas.
- Canales seguidos (`competencia.json → canales`): suscriptores y sus videos de los últimos 30 días.
- Patrones: duración (Short ≤ 60 s vs. largo), palabras de los títulos de los 20 más vistos.
Cuota YouTube: ~100 unidades por búsqueda (tope diario 10.000). Usa el mismo OAuth del canal (youtube.readonly).
Facebook, Instagram y TikTok no dan datos de cuentas ajenas por API: esos referentes los investiga el creador
(búsqueda web) y los que Andrés anota en la página de aprobación.
"""
import argparse, json, re, sys, urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import MKT, ErrorPieza, http, log
from publicar import yt_token

YT = "https://www.googleapis.com/youtube/v3"
CONF = json.loads((MKT / "competencia.json").read_text())
VACIAS = set("de la el los las y en a un una del por con para que se su al lo es como mi más o le".split())


def dur_s(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--salida", required=True)
    a = ap.parse_args()
    errores, tok = [], None
    try:
        tok = yt_token()
    except ErrorPieza as e:
        errores.append(f"yt token: {e}")
    cab = {"Authorization": f"Bearer {tok}"} if tok else {}
    desde = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    busquedas, ids = {}, set()
    if tok:
        for q in CONF["busquedas"]:
            try:
                r = http("GET", f"{YT}/search?part=snippet&type=video&maxResults=25&order=viewCount&regionCode=CO&relevanceLanguage=es"
                                f"&publishedAfter={desde}&q=" + urllib.parse.quote(q), cabeceras=cab)
                busquedas[q] = [it["id"]["videoId"] for it in r.get("items", [])]
                ids.update(busquedas[q])
            except ErrorPieza as e:
                errores.append(f"búsqueda '{q}': {str(e)[:200]}")
    videos = {}
    lista = list(ids)
    for i in range(0, len(lista), 50):
        try:
            r = http("GET", f"{YT}/videos?part=snippet,statistics,contentDetails&id=" + ",".join(lista[i:i + 50]), cabeceras=cab)
            for it in r.get("items", []):
                s, sn = it.get("statistics", {}), it["snippet"]
                videos[it["id"]] = {"id": it["id"], "url": f"https://www.youtube.com/watch?v={it['id']}", "titulo": sn["title"],
                                    "canal": sn["channelTitle"], "canal_id": sn["channelId"], "publicado": sn["publishedAt"][:10],
                                    "segundos": dur_s(it["contentDetails"].get("duration")), "vistas": int(s.get("viewCount", 0)),
                                    "me_gusta": int(s.get("likeCount", 0)), "comentarios": int(s.get("commentCount", 0))}
        except ErrorPieza as e:
            errores.append(f"videos: {str(e)[:200]}")
    # canales: los seguidos + los que más aparecen
    aparic = Counter(v["canal_id"] for v in videos.values())
    canal_ids = list(dict.fromkeys(CONF.get("canales", []) + [c for c, _ in aparic.most_common(15)]))
    canales = {}
    for i in range(0, len(canal_ids), 50):
        if not tok:
            break
        try:
            r = http("GET", f"{YT}/channels?part=snippet,statistics&id=" + ",".join(canal_ids[i:i + 50]), cabeceras=cab)
            for it in r.get("items", []):
                st = it.get("statistics", {})
                canales[it["id"]] = {"id": it["id"], "nombre": it["snippet"]["title"], "url": f"https://www.youtube.com/channel/{it['id']}",
                                     "suscriptores": int(st.get("subscriberCount", 0)), "videos": int(st.get("videoCount", 0)),
                                     "vistas_totales": int(st.get("viewCount", 0)), "seguido": it["id"] in CONF.get("canales", []),
                                     "apariciones_en_busquedas": aparic.get(it["id"], 0)}
        except ErrorPieza as e:
            errores.append(f"canales: {str(e)[:200]}")
    top = sorted(videos.values(), key=lambda v: -v["vistas"])[:20]
    palabras = Counter(w for v in top for w in re.findall(r"[a-záéíóúñü]{3,}", v["titulo"].lower()) if w not in VACIAS)
    shorts = [v for v in videos.values() if (v["segundos"] or 999) <= 60]
    salida = {
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ventana": f"últimos 30 días desde {desde[:10]}",
        "busquedas": {q: [videos[i] for i in vs if i in videos][:10] for q, vs in busquedas.items()},
        "top20": top, "canales": sorted(canales.values(), key=lambda c: -c["suscriptores"]),
        "patrones": {"videos_analizados": len(videos), "shorts": len(shorts),
                     "vistas_mediana_shorts": sorted(v["vistas"] for v in shorts)[len(shorts) // 2] if shorts else None,
                     "vistas_mediana_largos": (lambda l: l[len(l) // 2] if l else None)(sorted(v["vistas"] for v in videos.values() if (v["segundos"] or 0) > 60)),
                     "palabras_frecuentes_top20": palabras.most_common(20)},
        "errores": errores,
        "nota": "Solo YouTube. FB, IG y TikTok no exponen datos de cuentas ajenas por API; ver plan.referentes y la sección 'Referentes que vi' de la página de aprobación."}
    Path(a.salida).write_text(json.dumps(salida, ensure_ascii=False, indent=1))
    log(f"Competencia: {len(videos)} videos, {len(canales)} canales, {len(errores)} errores")


if __name__ == "__main__":
    main()
