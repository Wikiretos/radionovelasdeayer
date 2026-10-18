"""Utilidades compartidas del pipeline de redes (render, publicar, métricas)."""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

MKT = Path(__file__).resolve().parent
REPO = MKT.parent.parent
CONFIG = json.loads((MKT / "config.json").read_text())
R2_PUBLICO = CONFIG["r2_publico"]  # https://audio.radionovelasdeayer.com


class ErrorPieza(Exception):
    """Error que detiene UNA pieza (se reporta, no se corrige solo)."""


# Palabras que nunca pueden salir al público (regla: nunca se menciona IA ni a Andrés).
PROHIBIDAS = [r"\bIA\b", r"inteligencia artificial", r"\bAI\b", r"gemini", r"claude", r"chatgpt",
              r"generad[oa]s? (por|con)", r"sintétic", r"andr[eé]s", r"franco p[eé]rez", r"wikiretos"]


def validar_texto(texto, donde):
    for p in PROHIBIDAS:
        if re.search(p, texto or "", re.I):
            raise ErrorPieza(f"texto prohibido ({p}) en {donde}")


def http(metodo, url, datos=None, cabeceras=None, json_body=None, timeout=120, crudo=False):
    """Petición HTTP simple. Devuelve JSON (o bytes si crudo). Lanza ErrorPieza con el cuerpo del error."""
    cab = dict(cabeceras or {})
    cuerpo = None
    if json_body is not None:
        cuerpo = json.dumps(json_body).encode()
        cab["Content-Type"] = "application/json"
    elif isinstance(datos, dict):
        cuerpo = urllib.parse.urlencode(datos).encode()
        cab.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif datos is not None:
        cuerpo = datos
    req = urllib.request.Request(url, data=cuerpo, method=metodo, headers=cab)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return b if crudo else (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        detalle = e.read().decode("utf8", "replace")[:800]
        detalle = re.sub(r"access_token=[^&\s\"]+", "access_token=***", detalle)
        raise ErrorPieza(f"HTTP {e.code} {metodo} {url.split('?')[0]}: {detalle}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise ErrorPieza(f"red {metodo} {url.split('?')[0]}: {e}")


def leer_publico(ruta, defecto=None):
    """Lee un JSON publicado en R2 (sin caché)."""
    url = f"{R2_PUBLICO}/{ruta}?v={int(time.time())}"
    try:
        return http("GET", url, cabeceras={"User-Agent": "mkt"})
    except ErrorPieza as e:
        if "HTTP 404" in str(e):
            return defecto
        raise


def semanas():
    """Planes semanales en .github/mkt/semanas/AAAA-Www.json"""
    return sorted(p for p in (MKT / "semanas").glob("*.json") if re.fullmatch(r"\d{4}-W\d{2}\.json", p.name))


def aprobaciones(semana):
    p = MKT / "semanas" / f"{semana}.aprobaciones.json"
    return json.loads(p.read_text()) if p.exists() else {}


def url_media(semana, nombre):
    return f"{R2_PUBLICO}/mkt/{semana}/{nombre}"


def log(*a):
    print(*a, flush=True)
