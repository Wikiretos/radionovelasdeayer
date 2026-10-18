"""
Ilustraciones con Gemini (Nano Banana) en el estilo fijo de la casa (estilo/estilo.json).

Uso desde render.py:  generar(ilustracion, encuadre, destino) -> (ruta, usd)
Uso suelto (hojas de personaje):
  python3 .github/mkt/ilustrar.py personaje alvaro --salida /tmp/x.jpg

`ilustracion` en el plan: {"escena": "qué se ve, en una o dos frases",
                           "personajes": ["carmenza", ...]  (máx. 4, con hoja aprobada),
                           "lugar": "tienda|plaza|iglesia|rancho|camino|casa cural|...",
                           "momento": "mañana|tarde|noche|madrugada"}
Secreto: GEMINI_API_KEY.  Si el modelo no devuelve imagen, es ErrorPieza (no se reintenta a ciegas).
"""
import base64, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import MKT, ErrorPieza, http

ESTILO = json.loads((MKT / "estilo" / "estilo.json").read_text())
API = "https://generativelanguage.googleapis.com/v1beta"


def _b64(ruta):
    return base64.b64encode(Path(ruta).read_bytes()).decode()


def _buscar_imagen(o):
    """Encuentra el primer bloque de imagen en la respuesta (Interactions o generateContent)."""
    if isinstance(o, dict):
        if o.get("type") == "image" and o.get("data"):
            return o["data"]
        if "inlineData" in o and str(o["inlineData"].get("mimeType", "")).startswith("image"):
            return o["inlineData"]["data"]
        for v in o.values():
            r = _buscar_imagen(v)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _buscar_imagen(v)
            if r:
                return r
    return None


def llamar(prompt, refs, aspecto):
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        raise ErrorPieza("falta GEMINI_API_KEY")
    modelo = ESTILO["modelo"]
    entrada = [{"type": "text", "text": prompt}]
    for r in refs:
        entrada.append({"type": "image", "mime_type": "image/jpeg", "data": _b64(r)})
    cuerpo = {"model": modelo, "input": entrada,
              "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": aspecto, "image_size": ESTILO["tamano"]}}
    ultimo = None
    for intento in range(2):  # un solo reintento, y solo si la respuesta vino sin imagen o con 429/503
        try:
            r = http("POST", f"{API}/interactions", json_body=cuerpo, cabeceras={"x-goog-api-key": clave}, timeout=240)
        except ErrorPieza as e:
            ultimo = str(e)
            if ("HTTP 429" in ultimo or "HTTP 503" in ultimo) and intento == 0:
                time.sleep(30); continue
            raise
        datos = _buscar_imagen(r)
        if datos:
            return base64.b64decode(datos)
        ultimo = "respuesta sin imagen: " + json.dumps(r, ensure_ascii=False)[:400]
        time.sleep(5)
    raise ErrorPieza(f"Gemini no devolvió ilustración ({modelo}): {ultimo}")


def armar_prompt(il, encuadre):
    pers = il.get("personajes") or []
    if len(pers) > 4:
        raise ErrorPieza("máximo 4 personajes por ilustración")
    partes = [ESTILO["estilo"], ESTILO["reglas"], ESTILO["encuadres"][encuadre]["nota"]]
    refs = [MKT / "estilo" / r for r in ESTILO["referencias_estilo"]]
    if pers:
        desc = []
        for p in pers:
            c = ESTILO["personajes"].get(p)
            if not c:
                raise ErrorPieza(f"personaje '{p}' no está en estilo.json")
            if not c.get("ref"):
                raise ErrorPieza(f"'{p}' no tiene hoja de personaje aprobada")
            refs.append(MKT / "estilo" / c["ref"])
            desc.append(f"{p.replace('_', ' ').title()}: {c['aspecto']} (igual a su imagen de referencia)")
        partes.append("Personajes, idénticos a sus imágenes de referencia: " + "; ".join(desc) + ".")
    else:
        partes.append("Sin personajes reconocibles de la serie.")
    partes.append(f"Lugar: {il.get('lugar', 'el pueblo')}. Momento: {il.get('momento', 'tarde')}.")
    partes.append("Escena: " + il["escena"])
    partes.append("La primera imagen de referencia fija el ESTILO (no copies su composición). Las demás fijan la apariencia de cada personaje.")
    return "\n".join(partes), refs


def generar(il, encuadre, destino):
    prompt, refs = armar_prompt(il, encuadre)
    datos = llamar(prompt, refs, ESTILO["encuadres"][encuadre]["aspect_ratio"])
    Path(destino).write_bytes(datos)
    return Path(destino), ESTILO["usd_por_imagen"]


def hoja_personaje(nombre, destino):
    c = ESTILO["personajes"][nombre]
    prompt = "\n".join([ESTILO["estilo"], ESTILO["reglas"],
                        f"Hoja de personaje: retrato de medio cuerpo, de frente y tres cuartos, fondo liso sepia oscuro. {c['aspecto']}.",
                        "La imagen de referencia fija el ESTILO de dibujo; no copies sus personajes."])
    datos = llamar(prompt, [MKT / "estilo" / r for r in ESTILO["referencias_estilo"]], "4:5")
    Path(destino).write_bytes(datos)
    return Path(destino)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("modo", choices=["personaje"]); ap.add_argument("nombre")
    ap.add_argument("--salida", required=True)
    a = ap.parse_args()
    try:
        print(hoja_personaje(a.nombre, a.salida))
    except ErrorPieza as e:
        sys.exit(f"ERROR: {e}")
