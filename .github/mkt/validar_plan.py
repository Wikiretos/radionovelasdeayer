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

Reglas v3 (auditoría del 2026-11-07; semanas >= config.reglas_v3_desde):
  tipos nuevos  "parte":    {"capitulo": 1, "n": 1..m, "m": M, "inicio": s, "fin": s, "siguiente": "Mañana a las 7"} (partes del cap. 1 en orden;
                            ver datos/partes-cap1.json)
                "capitulo": {"numero": 1, "titulo": "...", "ilustraciones": [{escena, personajes, lugar, momento, desde: s}]} (solo fb y yt)
  cada pieza    "cta": {"destino": "cap-1" | "cap-2" | "video:<id de la pieza capitulo>" | "siguiente-parte"}  (UN solo llamado a la acción)
                "hallazgo": en qué hallazgo de claude/marketing/prompt-campanas-auditoria-2026-11-07.md se apoya
  textos        fb y yt_desc llevan {enlace} (el publicador lo cambia por la URL con ?f=<red> y utm_content=<id>); fb también en "comentario";
                la pregunta para comentar va SOLO en "comentario"; ig y tiktok sin enlace; nada de reenviar ni WhatsApp; cero spoilers.
  ab.metrica    "clics" o "plays" (GA4 por utm_content).
  volumen       ≤ config.max_sueltas_por_dia piezas sueltas por red y día (sin contar partes ni capítulo); ig solo como copia de fb.
"""
import argparse, json, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import MKT, CONFIG, ErrorPieza, validar_texto, v3, SPOILERS

TIPOS = {"fragmento", "escena", "carrusel"}
TIPOS_V3 = TIPOS | {"parte", "capitulo"}
DESTINOS = {"cap-1", "cap-2", "siguiente-parte"}
REENVIAR = [r"whats ?app", r"\breenv[ií]", r"m[aá]ndeselo", r"comp[aá]rta", r"compartir con"]
METRICAS_AB_V3 = {"clics", "plays"}
FAMILIAS = {"dramatico", "costumbre", "recuerdo", "personaje", "pregunta", "dato"}
MARCA = "#radionovelasdeayer"
# Del guionista: nada de esto en boca de un personaje ni del locutor.
MODERNAS = ["celular", "internet", "computador", "correo electrónico", "chat", "en línea", "aplicación", "video", "selfie", "ok",
            "cool", "parce", "parcero", "bacano", "literal", "súper", "full", "brutal", "empoderar", "tóxico", "resiliencia",
            "salud mental", "terapia", "estrés", "fin de semana", "pareja", "mi ex", "crush", "date", "wow", "man", "bro", "genial",
            "básicamente", "definitivamente", "al final del día", "hacer sentido", "eventualmente", "dispositivo", "batería", "o sea"]
ESTILO = json.loads((MKT / "estilo" / "estilo.json").read_text())
CANALES = {"fb", "ig", "yt", "tt"}
VARIABLES_AB = {"gancho", "ilustracion", "hora", "texto", "hashtags", "duracion", "tipo", "familia", "fondo", "inicio"}
USD_IMAGEN = ESTILO["usd_por_imagen"]
USD_SEG_VOZ = 25 * 20 / 1e6  # 25 tokens de audio por segundo, US$20 por millón
PALABRAS_SEG = 117 / 60


def costo_estimado(p):
    """US$ estimado de producir la pieza (ilustraciones + voces). Publicar y medir no cuesta."""
    imgs = ((1 if p.get("ilustracion") and not p.get("ilustracion_de") else 0) + sum(1 for d in (p.get("carrusel") or {}).get("diapositivas", []) if d.get("ilustracion"))
            + len((p.get("capitulo") or {}).get("ilustraciones") or []))
    pal = sum(len(re.sub(r"\[[^\]]*\]", "", l.get("texto", "")).split()) for l in (p.get("escena") or {}).get("lineas", []))
    return round(imgs * USD_IMAGEN + (pal / PALABRAS_SEG) * USD_SEG_VOZ * 1.15, 4)  # +15 % por la dirección enviada
PLATAFORMAS = {"fb", "ig", "yt", "tt"}
FONDOS = {None, "colchon_tierno", "colchon_tension", "tiple_de_cinco", "sintonia_concurso"}


def v3_pieza(p, pls, e):
    """Reglas de la auditoría del 2026-11-07."""
    t = p.get("texto") or {}
    if not p.get("hallazgo"): e("falta 'hallazgo': en qué hallazgo de la auditoría se apoya")
    cta = p.get("cta") or {}
    dest = cta.get("destino", "")
    if p.get("tipo") == "parte":
        pa = p.get("parte") or {}
        if not all(isinstance(pa.get(k), int) for k in ("capitulo", "n", "m")) or not 1 <= pa.get("n", 0) <= pa.get("m", 0):
            e("parte: necesita capitulo, n y m enteros (1 ≤ n ≤ m)")
        elif pa["capitulo"] not in CONFIG["capitulos_en_redes"]: e(f"parte: el capítulo {pa['capitulo']} no se cuenta en redes")
        elif pa["n"] < pa["m"] and not pa.get("siguiente"): e("parte: falta 'siguiente' (cuándo sale la próxima, p. ej. 'Mañana a las 7')")
        esperado = "siguiente-parte" if pa.get("n", 0) < pa.get("m", 0) else "cap-2"
        if dest != esperado: e(f"parte {pa.get('n')}/{pa.get('m')}: cta.destino debe ser '{esperado}'")
    elif p.get("tipo") == "capitulo":
        c = p.get("capitulo") or {}
        if c.get("numero") not in CONFIG["capitulos_en_redes"]: e(f"capitulo: solo {CONFIG['capitulos_en_redes']} va completo en redes")
        if not pls <= {"fb", "yt"}: e("capitulo completo: solo fb y yt")
        ils = c.get("ilustraciones") or []
        if not 1 <= len(ils) <= 6: e("capitulo: 1 a 6 ilustraciones")
        for il in ils:
            if not il.get("escena"): e("capitulo: ilustración sin 'escena'")
            for per in il.get("personajes") or []:
                if not (ESTILO["personajes"].get(per) or {}).get("ref"): e(f"capitulo: {per} sin hoja de personaje aprobada")
        if dest != "cap-2": e("capitulo completo: cta.destino debe ser 'cap-2'")
    elif not (dest in ("cap-1", "cap-2") or dest.startswith("video:")):
        e("cta.destino debe ser 'cap-1', 'cap-2' o 'video:<id del capítulo completo>'")
    if "ig" in pls and "fb" not in pls: e("Instagram solo como copia de Facebook: ig sin fb")
    lleva_enlace = dest in ("cap-1", "cap-2") or dest.startswith("video:")
    for k in ("fb", "yt_desc"):
        if t.get(k) and lleva_enlace and "{enlace}" not in t[k]: e(f"texto.{k}: falta {{enlace}} (el publicador pone la URL rastreable)")
    if "fb" in pls and lleva_enlace and "{enlace}" not in t.get("comentario", ""): e("texto.comentario: falta {enlace} (va fijado como primer comentario)")
    if "fb" in pls and "?" not in t.get("comentario", ""): e("texto.comentario: la pregunta para comentar va aquí")
    for k in ("ig", "tiktok"):
        if "{enlace}" in t.get(k, "") or "http" in t.get(k, ""): e(f"texto.{k}: sin enlaces (no se pueden tocar)")
    for k in ("fb", "ig", "tiktok", "yt_desc"):
        if "?" in re.sub(r"\{enlace\}", "", t.get(k, "")): e(f"texto.{k}: sin preguntas; la pregunta va en 'comentario' (un solo llamado a la acción)")
    for k, v in list(t.items()) + [("gancho", p.get("gancho", ""))]:
        for rx in REENVIAR:
            if re.search(rx, v or "", re.I): e(f"texto.{k}: nada de reenviar ni WhatsApp ({rx})")
        for rx in SPOILERS:
            if re.search(rx, v or "", re.I): e(f"texto.{k}: posible spoiler ({rx})")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("--estrenados", type=int, default=10)
    ap.add_argument("--escribir-costos", action="store_true", help="guarda costo_estimado en cada pieza del plan")
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    voces = json.loads((MKT / "datos" / "voces.json").read_text())
    imagenes = {p.stem for p in (MKT / "imagenes").iterdir()}
    mal = []
    if not re.fullmatch(r"\d{4}-W\d{2}", plan.get("semana", "")):
        mal.append("semana debe ser AAAA-Www")
    if Path(args.plan).stem != plan.get("semana"):
        mal.append("el archivo debe llamarse <semana>.json")
    tend = plan.get("tendencias") or []
    if not tend:
        mal.append("falta 'tendencias' (investigación de la semana: [{tema, fuente, uso}])")
    for t in tend:
        if not str(t.get("fuente", "")).startswith("http") or not t.get("tema"):
            mal.append(f"tendencia sin tema o sin fuente URL: {t}")
    refs = plan.get("referentes") or []
    for c in CANALES:
        rc = [r for r in refs if r.get("canal") == c]
        if not rc:
            mal.append(f"falta 'referentes' del canal {c} (competencia o virales 50+; si no hubo datos verificables, una entrada con sin_dato: true y la razón)")
        for r in rc:
            if not r.get("sin_dato") and (not str(r.get("fuente", "")).startswith("http") or not r.get("que_aprendemos")):
                mal.append(f"referente {c} sin fuente URL o sin 'que_aprendemos': {str(r)[:80]}")
    ids = set()
    por_dia = {}
    sueltas = {}
    exps = {}
    nuevas = v3(plan.get("semana"))
    tipos_ok = TIPOS_V3 if nuevas else TIPOS
    for p in plan.get("piezas", []):
        pid = p.get("id", "?"); e = lambda m: mal.append(f"{pid}: {m}")
        if pid in ids: e("id repetido")
        ids.add(pid)
        if not re.fullmatch(r"[a-z0-9-]{3,30}", pid): e("id inválido (minúsculas, números, guiones)")
        if p.get("tipo") not in tipos_ok: e("tipo inválido")
        if p.get("ab"):
            ab = p["ab"]
            if ab.get("variante") not in ("A", "B") or not ab.get("experimento") or ab.get("variable") not in VARIABLES_AB or not ab.get("hipotesis"):
                e(f"ab incompleto: necesita experimento, variante A|B, variable ({', '.join(sorted(VARIABLES_AB))}) e hipotesis")
            else:
                exps.setdefault(ab["experimento"], []).append(p)
        if p.get("familia") not in FAMILIAS: e(f"familia inválida {p.get('familia')}")
        try:
            f = date.fromisoformat(p["fecha"]); datetime.strptime(p["hora"], "%H:%M")
            if not re.fullmatch(r"\d{2}:\d{2}", p["hora"]): raise ValueError
            iso = f.isocalendar()
            lunes = date.fromisocalendar(int(plan["semana"][:4]), int(plan["semana"][-2:]), 1)
            adelanto = bool(plan.get("desde")) and date.fromisoformat(plan["desde"]) <= f < lunes  # arranque antes del lunes, con OK de Andrés
            if f"{iso[0]}-W{iso[1]:02d}" != plan.get("semana") and not adelanto: e("fecha fuera de la semana del plan")
        except Exception:
            e("fecha/hora inválidas")
        pls = set(p.get("plataformas", []))
        if not pls or pls - PLATAFORMAS: e("plataformas inválidas")
        if p.get("tipo") == "carrusel" and "yt" in pls: e("carrusel no va a YouTube")
        for pl in pls & {"fb", "ig", "yt"}:
            por_dia[(p.get("fecha"), pl)] = por_dia.get((p.get("fecha"), pl), 0) + 1
            if p.get("tipo") not in ("parte", "capitulo"):
                sueltas[(p.get("fecha"), pl)] = sueltas.get((p.get("fecha"), pl), 0) + 1
        if nuevas:
            v3_pieza(p, pls, e)
        if p.get("tipo") == "capitulo":
            pass
        elif p.get("tipo") != "carrusel":
            if p.get("ilustracion_de"):
                otra = next((x for x in plan["piezas"] if x.get("id") == p["ilustracion_de"]), None)
                if not otra or not otra.get("ilustracion") or plan["piezas"].index(otra) > plan["piezas"].index(p):
                    e("ilustracion_de debe apuntar a una pieza ANTERIOR del mismo plan que tenga 'ilustracion'")
            elif not p.get("ilustracion") and p.get("imagen") not in imagenes: e(f"falta 'ilustracion' (o 'imagen' del catálogo: {sorted(imagenes)})")
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
        if p.get("tipo") in ("fragmento", "parte"):
            fr = (p.get("parte") if p.get("tipo") == "parte" else p.get("fragmento")) or {}
            cap = fr.get("capitulo")
            if not isinstance(cap, int) or not 1 <= cap <= args.estrenados: e(f"capítulo {cap} no estrenado o inválido")
            else:
                tp = MKT / "datos" / f"tiempos-cap{cap}.json"
                if not tp.exists(): e(f"falta {tp.name}")
                d = fr.get("fin", 0) - fr.get("inicio", 0)
                tope = (CONFIG["duracion_max_parte"] if p.get("tipo") == "parte" else CONFIG["duracion_max_video"]) - 3.5
                if not 8 <= d <= tope: e(f"fragmento de {d:.1f}s (8 a {tope}s)")
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
    for nombre, ps in exps.items():
        vs = sorted(x["ab"]["variante"] for x in ps)
        if vs != ["A", "B"]:
            mal.append(f"experimento {nombre}: debe tener exactamente una A y una B (tiene {vs})"); continue
        a, b = sorted(ps, key=lambda x: x["ab"]["variante"])
        var = a["ab"]["variable"]
        if b["ab"]["variable"] != var: mal.append(f"experimento {nombre}: A y B prueban variables distintas")
        if set(a.get("plataformas", [])) != set(b.get("plataformas", [])): mal.append(f"experimento {nombre}: A y B deben ir a las mismas plataformas")
        if var == "ilustracion" and (a.get("ilustracion"), a.get("imagen")) == (b.get("ilustracion"), b.get("imagen")): mal.append(f"experimento {nombre}: A y B tienen la misma imagen")
        clave = {"gancho": "gancho", "hora": "hora", "texto": "texto", "tipo": "tipo", "familia": "familia"}.get(var)
        if clave and a.get(clave) == b.get(clave): mal.append(f"experimento {nombre}: A y B tienen el mismo '{clave}', no prueban nada")
        if var != "hora" and a.get("hora") != b.get("hora"): mal.append(f"experimento {nombre}: A y B deben salir a la misma hora (en días distintos) para que solo cambie '{var}'")
    if len(exps) < 2:
        mal.append(f"la semana necesita al menos 2 experimentos A/B (tiene {len(exps)})")
    if nuevas:
        for (f, pl), n in sueltas.items():
            if n > CONFIG["max_sueltas_por_dia"]: mal.append(f"{f} {pl}: {n} piezas sueltas (máx. {CONFIG['max_sueltas_por_dia']} más partes y capítulo)")
        partes = sorted((p for p in plan.get("piezas", []) if p.get("tipo") == "parte" and p.get("parte")), key=lambda p: (p["parte"].get("capitulo"), p["parte"].get("n")))
        for a, b in zip(partes, partes[1:]):
            pa, pb = a["parte"], b["parte"]
            if pa.get("capitulo") != pb.get("capitulo"): continue
            if pb.get("n") != pa.get("n", 0) + 1: mal.append(f"partes del cap. {pa.get('capitulo')}: falta la parte {pa.get('n', 0) + 1} entre {a['id']} y {b['id']}")
            elif not 0 <= pb.get("inicio", 0) - pa.get("fin", 0) <= 4: mal.append(f"{b['id']}: la parte {pb.get('n')} debe empezar donde termina la {pa.get('n')} ({pa.get('fin')} s)")
            if (b.get("fecha"), b.get("hora")) <= (a.get("fecha"), a.get("hora")): mal.append(f"{b['id']}: la parte {pb.get('n')} sale antes o a la vez que la {pa.get('n')}")
        for pid, ps in exps.items():
            for x in ps:
                if x["ab"].get("metrica") not in METRICAS_AB_V3:
                    mal.append(f"{x['id']}: ab.metrica debe ser 'clics' o 'plays' (se decide en GA4, no por vistas)")
    for (f, pl), n in por_dia.items():
        if n > CONFIG["max_por_dia"][pl]: mal.append(f"{f} {pl}: {n} piezas (máx. {CONFIG['max_por_dia'][pl]})")
    if mal:
        print("PLAN CON PROBLEMAS:\n- " + "\n- ".join(mal)); sys.exit(1)
    total = 0
    print("Costo estimado por pieza (US$):")
    for p in plan.get("piezas", []):
        c = costo_estimado(p); total += c
        print(f"  {p.get('id')}: {c:.3f}" + (f"  (declarado {p['costo_estimado']})" if "costo_estimado" in p else ""))
    print(f"Total estimado de la semana: US$ {total:.2f}")
    if args.escribir_costos:
        for p in plan["piezas"]:
            p["costo_estimado"] = costo_estimado(p)
        plan["costo_estimado_semana"] = round(total, 3)
        Path(args.plan).write_text(json.dumps(plan, ensure_ascii=False, indent=1))
        print("costo_estimado escrito en el plan")
    print(f"Plan válido: {len(ids)} piezas")


if __name__ == "__main__":
    main()
