"""
Valida un plan semanal antes de subirlo. Sale con código 1 y lista de problemas si algo falla.

  python3 .github/mkt/validar_plan.py .github/mkt/semanas/2026-W44.json [--estrenados 6]

Esquema (ver PLAN.md):
{ "semana": "2026-W44", "creado": ISO, "hipotesis": "...",
  "piezas": [ { "id": "w44-01", "tipo": "fragmento|escena|carrusel", "familia": "dramatico|costumbre|recuerdo|personaje|pregunta",
                "fecha": "AAAA-MM-DD", "hora": "HH:MM", "plataformas": ["fb","ig","yt","tt"],
                "imagen": "tienda|banner|cartel", "gancho": "≤ 60 caracteres",
                "fragmento": {"capitulo": N, "inicio": s, "fin": s},
                "escena": {"lineas": [{"personaje","texto","emocion?","pausa?"}], "fondo": "colchon_tierno|colchon_tension|tiple_de_cinco|sintonia_concurso|null", "capitulo_cta": 1},
                "carrusel": {"diapositivas": [{"titulo","texto","imagen?","papel?"}]},
                "texto": {"fb","ig","yt_titulo","yt_desc","tiktok","comentario"},
                "por_que": "razón de esta pieza (dato o hipótesis)" } ] }
"""
import argparse, json, re, sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import MKT, CONFIG, ErrorPieza, validar_texto

TIPOS = {"fragmento", "escena", "carrusel"}
FAMILIAS = {"dramatico", "costumbre", "recuerdo", "personaje", "pregunta", "dato"}
MARCA = "#radionovelasdeayer"
# Del guionista: nada de esto en boca de un personaje ni del locutor.
MODERNAS = ["celular", "internet", "computador", "correo electrónico", "chat", "en línea", "aplicación", "video", "selfie", "ok",
            "cool", "parce", "parcero", "bacano", "literal", "súper", "full", "brutal", "empoderar", "tóxico", "resiliencia",
            "salud mental", "terapia", "estrés", "fin de semana", "pareja", "mi ex", "crush", "date", "wow", "man", "bro", "genial",
            "básicamente", "definitivamente", "al final del día", "hacer sentido", "eventualmente", "dispositivo", "batería", "o sea"]
ESTILO = json.loads((MKT / "estilo" / "estilo.json").read_text())
PLATAFORMAS = {"fb", "ig", "yt", "tt"}
FONDOS = {None, "colchon_tierno", "colchon_tension", "tiple_de_cinco", "sintonia_concurso"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("--estrenados", type=int, default=10)
    a = ap.parse_args()
    plan = json.loads(Path(a.plan).read_text())
    voces = json.loads((MKT / "datos" / "voces.json").read_text())
    imagenes = {p.stem for p in (MKT / "imagenes").iterdir()}
    mal = []
    if not re.fullmatch(r"\d{4}-W\d{2}", plan.get("semana", "")):
        mal.append("semana debe ser AAAA-Www")
    if Path(a.plan).stem != plan.get("semana"):
        mal.append("el archivo debe llamarse <semana>.json")
    tend = plan.get("tendencias") or []
    if not tend:
        mal.append("falta 'tendencias' (investigación de la semana: [{tema, fuente, uso}])")
    for t in tend:
        if not str(t.get("fuente", "")).startswith("http") or not t.get("tema"):
            mal.append(f"tendencia sin tema o sin fuente URL: {t}")
    ids = set()
    por_dia = {}
    for p in plan.get("piezas", []):
        pid = p.get("id", "?"); e = lambda m: mal.append(f"{pid}: {m}")
        if pid in ids: e("id repetido")
        ids.add(pid)
        if not re.fullmatch(r"[a-z0-9-]{3,30}", pid): e("id inválido (minúsculas, números, guiones)")
        if p.get("tipo") not in TIPOS: e("tipo inválido")
        if p.get("familia") not in FAMILIAS: e(f"familia inválida {p.get('familia')}")
        try:
            f = date.fromisoformat(p["fecha"]); datetime.strptime(p["hora"], "%H:%M")
            if not re.fullmatch(r"\d{2}:\d{2}", p["hora"]): raise ValueError
            iso = f.isocalendar()
            if f"{iso[0]}-W{iso[1]:02d}" != plan.get("semana"): e("fecha fuera de la semana del plan")
        except Exception:
            e("fecha/hora inválidas")
        pls = set(p.get("plataformas", []))
        if not pls or pls - PLATAFORMAS: e("plataformas inválidas")
        if p.get("tipo") == "carrusel" and "yt" in pls: e("carrusel no va a YouTube")
        for pl in pls & {"fb", "ig", "yt"}:
            por_dia[(p.get("fecha"), pl)] = por_dia.get((p.get("fecha"), pl), 0) + 1
        if p.get("tipo") != "carrusel":
            if not p.get("ilustracion") and p.get("imagen") not in imagenes: e(f"falta 'ilustracion' (o 'imagen' del catálogo: {sorted(imagenes)})")
            if len(p.get("gancho", "")) > 60: e("gancho > 60 caracteres")
        t = p.get("texto") or {}
        req = {"fb": "fb" in pls, "ig": "ig" in pls, "yt_titulo": "yt" in pls, "yt_desc": "yt" in pls, "tiktok": "tt" in pls}
        for k, need in req.items():
            if need and not t.get(k): e(f"falta texto.{k}")
        if len(t.get("ig", "")) > 2200: e("texto.ig > 2200")
        if len(t.get("yt_titulo", "")) > 95: e("yt_titulo > 95")
        tags = lambda k: [h.lower() for h in re.findall(r"#\w+", t.get(k, ""))]
        for k, lo, hi in (("ig", 3, 5), ("tiktok", 3, 5)):
            if t.get(k):
                n = tags(k)
                if not lo <= len(n) <= hi: e(f"texto.{k}: {len(n)} hashtags (deben ser {lo} a {hi})")
                if MARCA not in n: e(f"texto.{k}: falta {MARCA}")
        if len(tags("fb")) > 2: e("texto.fb: más de 2 hashtags")
        if len(tags("yt_titulo") + tags("yt_desc")) > 4: e("YouTube: más de 4 hashtags entre título y descripción")
        if p.get("familia") == "dato":
            fu = p.get("fuentes") or []
            if not fu or not all(str(x).startswith("http") for x in fu): e("un dato curioso necesita 'fuentes' (URL verificables)")
            if not p.get("dato"): e("falta 'dato': la afirmación exacta que se verificó")
        il_list = [p.get("ilustracion")] + [d.get("ilustracion") for d in (p.get("carrusel") or {}).get("diapositivas", [])]
        for il in [x for x in il_list if x]:
            if not il.get("escena"): e("ilustración sin 'escena'")
            if len(il.get("personajes") or []) > 4: e("ilustración con más de 4 personajes")
            for per in il.get("personajes") or []:
                c = ESTILO["personajes"].get(per)
                if not c: e(f"ilustración: personaje desconocido {per}")
                elif not c.get("ref"): e(f"ilustración: {per} no tiene hoja de personaje aprobada")
        for k, v in t.items():
            try: validar_texto(v, f"texto.{k}")
            except ErrorPieza as x: e(str(x))
        try: validar_texto(p.get("gancho", ""), "gancho")
        except ErrorPieza as x: e(str(x))
        if p.get("tipo") == "fragmento":
            fr = p.get("fragmento") or {}
            cap = fr.get("capitulo")
            if not isinstance(cap, int) or not 1 <= cap <= a.estrenados: e(f"capítulo {cap} no estrenado o inválido")
            else:
                tp = MKT / "datos" / f"tiempos-cap{cap}.json"
                if not tp.exists(): e(f"falta {tp.name}")
                d = fr.get("fin", 0) - fr.get("inicio", 0)
                if not 8 <= d <= CONFIG["duracion_max_video"] - 3.5: e(f"fragmento de {d:.1f}s (8 a {CONFIG['duracion_max_video'] - 3.5}s)")
                if tp.exists():
                    lin = json.loads(tp.read_text())["lineas"]
                    cortes = [l for l in lin if l["inicio"] + 0.3 < fr["inicio"] < l["fin"] - 0.3 or l["inicio"] + 0.3 < fr["fin"] < l["fin"] - 0.3]
                    if cortes: e(f"el fragmento corta una línea a la mitad ({cortes[0]['personaje']}: {cortes[0]['texto'][:40]}…); usa inicio/fin de tiempos")
        if p.get("tipo") == "escena":
            es = p.get("escena") or {}
            if es.get("fondo") not in FONDOS: e("fondo inválido")
            pal = 0
            for l in es.get("lineas", []):
                if l.get("personaje") not in voces: e(f"personaje sin voz {l.get('personaje')}")
                txt = re.sub(r"\[[^\]]*\]", "", l.get("texto", "")).strip()
                if not l.get("emocion"): e(f"línea sin 'emocion': {txt[:40]}")
                if len(txt.split()) < 4: e(f"línea de menos de 4 palabras (falla en la voz): {txt}")
                if txt.endswith("…") or txt.endswith("..."): e(f"línea terminada en puntos suspensivos (sale cortada): {txt[:40]}")
                for w in MODERNAS:
                    if re.search(r"(?<![\wáéíóúñ])" + re.escape(w) + r"(?![\wáéíóúñ])", txt, re.I): e(f"palabra moderna prohibida '{w}': {txt[:50]}")
                if re.search(r"\b(pues|mire|vea|¿oyó\?)[.!?]?$", txt, re.I): e(f"muletilla de relleno al final: {txt[:50]}")
                pal += len(re.sub(r"\[[^\]]*\]", "", l.get("texto", "")).split())
                try: validar_texto(l.get("texto", ""), "escena")
                except ErrorPieza as x: e(str(x))
            if not 2 <= len(es.get("lineas", [])) <= 12: e("la escena necesita 2 a 12 líneas")
            if pal > 120: e(f"escena de {pal} palabras (máx. 120 ≈ 50 s)")
        if p.get("tipo") == "carrusel":
            ds = (p.get("carrusel") or {}).get("diapositivas", [])
            if not 2 <= len(ds) <= 10: e("carrusel de 2 a 10 diapositivas")
            for d in ds:
                if d.get("imagen") and d["imagen"] not in imagenes: e(f"imagen '{d['imagen']}' no existe")
                if len(d.get("texto", "")) > 260: e("texto de diapositiva > 260 caracteres")
    for (f, pl), n in por_dia.items():
        if n > CONFIG["max_por_dia"][pl]: mal.append(f"{f} {pl}: {n} piezas (máx. {CONFIG['max_por_dia'][pl]})")
    if mal:
        print("PLAN CON PROBLEMAS:\n- " + "\n- ".join(mal)); sys.exit(1)
    print(f"Plan válido: {len(ids)} piezas")


if __name__ == "__main__":
    main()
