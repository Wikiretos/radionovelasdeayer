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
FAMILIAS = {"dramatico", "costumbre", "recuerdo", "personaje", "pregunta"}
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
            if p.get("imagen") not in imagenes: e(f"imagen '{p.get('imagen')}' no existe")
            if len(p.get("gancho", "")) > 60: e("gancho > 60 caracteres")
        t = p.get("texto") or {}
        req = {"fb": "fb" in pls, "ig": "ig" in pls, "yt_titulo": "yt" in pls, "yt_desc": "yt" in pls, "tiktok": "tt" in pls}
        for k, need in req.items():
            if need and not t.get(k): e(f"falta texto.{k}")
        if len(t.get("ig", "")) > 2200: e("texto.ig > 2200")
        if len(t.get("yt_titulo", "")) > 95: e("yt_titulo > 95")
        if t.get("ig") and len(re.findall(r"#\w+", t["ig"])) > 5: e("más de 5 hashtags en ig")
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
