#!/usr/bin/env python3
"""Libera para Google los capítulos ya estrenados.

Capítulo N se estrena el día ESTRENO + (N-1) a las 6 a. m. de Colombia (11:00 UTC).
Para cada cap-N.html ya estrenado: quita <meta name="robots" content="noindex">
y lo agrega a sitemap.xml (lastmod = fecha de estreno). Nunca agrega noindex.
Uso: estrenos.py [AAAA-MM-DDTHH:MM] (fecha UTC opcional para pruebas).
"""
import datetime as dt, glob, json, re, sys

NOINDEX = re.compile(r'\s*<meta name="robots" content="noindex">')
BASE = "https://radionovelasdeayer.com"

ahora = (dt.datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
         else dt.datetime.utcnow())
cambios = []

def estreno_de(html):
    m = re.search(r'"estreno":\s*"(\d{4}-\d{2}-\d{2})"', html)
    if not m:
        raise SystemExit("No encontré la fecha de estreno en la página")
    return dt.date.fromisoformat(m.group(1))

sitemap = open("sitemap.xml", encoding="utf-8").read()

for ruta in sorted(glob.glob("cap-*.html"), key=lambda p: int(re.findall(r"\d+", p)[0])):
    n = int(re.findall(r"\d+", ruta)[0])
    html = open(ruta, encoding="utf-8").read()
    fecha = estreno_de(html) + dt.timedelta(days=n - 1)
    if ahora < dt.datetime.combine(fecha, dt.time(11, 0)):
        continue
    if NOINDEX.search(html):
        open(ruta, "w", encoding="utf-8").write(NOINDEX.sub("", html, count=1))
        cambios.append(f"cap-{n}: sin noindex")
    loc = f"<loc>{BASE}/cap-{n}</loc>"
    if loc not in sitemap:
        linea = f"  <url>{loc}<lastmod>{fecha.isoformat()}</lastmod></url>\n"
        # Después del último capítulo listado; si no hay, después de /capitulos.
        previos = list(re.finditer(r"  <url><loc>[^<]*/cap-\d+</loc>.*\n", sitemap)) or \
                  list(re.finditer(r"  <url><loc>[^<]*/capitulos</loc>.*\n", sitemap))
        pos = previos[-1].end() if previos else sitemap.index("</urlset>")
        sitemap = sitemap[:pos] + linea + sitemap[pos:]
        cambios.append(f"cap-{n}: agregado al sitemap")

if cambios:
    open("sitemap.xml", "w", encoding="utf-8").write(sitemap)
print("\n".join(cambios) or "Sin cambios")
