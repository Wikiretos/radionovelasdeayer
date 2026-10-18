"""
Renderiza las piezas de un plan semanal.

  python3 .github/mkt/render.py .github/mkt/semanas/2026-W43.json --salida /tmp/salida [--solo ID] [--sin-tts]

Tipos de pieza:
  fragmento  audio recortado de un capítulo (audio/capNN_web.mp3) con subtítulos de tiempos-capN.json
  escena     audio nuevo: líneas con personajes → Gemini TTS (GEMINI_API_KEY) + colchón musical
  carrusel   imágenes 1080×1350 (JPEG) con textos

Salida por pieza: <id>.mp4 + <id>.jpg (portada) o <id>-1.jpg… (carrusel), y render.json con el resultado.
Un error detiene SOLO esa pieza y queda en render.json; nunca se corrige solo.
"""
import argparse, base64, json, math, os, re, shutil, struct, subprocess, sys, tempfile, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import MKT, REPO, CONFIG, ErrorPieza, validar_texto, http, log
import ilustrar

W, H, FPS = 1080, 1920, 30
BG = (14, 8, 3); INK = (245, 241, 234); MUTED = (179, 171, 161); ACCENT = (242, 162, 58)
PAPEL = (236, 231, 221); PAPEL_INK = (23, 18, 14); PAPEL_ACENTO = (140, 79, 0)
F_DISPLAY = str(MKT / "fuentes" / "Imbue.ttf")
F_TEXTO = str(MKT / "fuentes" / "AlbertSans.ttf")
IMG_Y, IMG_H = 280, 800          # franja de la ilustración
SUB_Y = 1120                     # subtítulos
WAVE_Y = 1460                    # onda
CIERRE_S = 3.5                   # tarjeta final


def fuente(ruta, tam, peso=None):
    f = ImageFont.truetype(ruta, tam)
    if peso:
        vals = []
        for ax in f.get_variation_axes():
            n = ax["name"].decode() if isinstance(ax["name"], bytes) else str(ax["name"])
            v = peso if n.lower().startswith("weight") else min(max(tam, ax["minimum"]), ax["maximum"])
            vals.append(min(max(v, ax["minimum"]), ax["maximum"]))
        f.set_variation_by_axes(vals)
    return f


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorPieza("ffmpeg: " + r.stderr[-600:])
    return r


def duracion(ruta):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(ruta)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def limpiar(texto):
    t = re.sub(r"\[[^\]]*\]\s*", "", texto)
    return re.sub(r"\s+", " ", t).strip()


def envolver(draw, texto, f, ancho):
    palabras, lineas, actual = texto.split(), [], ""
    for p in palabras:
        prueba = (actual + " " + p).strip()
        if draw.textlength(prueba, font=f) <= ancho:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas


# ---------- imágenes ----------

def imagen_fuente(nombre):
    for ext in ("jpg", "webp", "png"):
        p = MKT / "imagenes" / f"{nombre}.{ext}"
        if p.exists():
            return p
    raise ErrorPieza(f"imagen '{nombre}' no existe en .github/mkt/imagenes/")


def lamina_base(dst, serie_titulo, pie):
    """Fondo fijo del video: cabecera y pie."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    f1 = fuente(F_TEXTO, 34, 500); f2 = fuente(F_DISPLAY, 78, 600); f3 = fuente(F_TEXTO, 38, 600)
    d.text((W / 2, 140), "RADIONOVELAS DE AYER", font=f1, fill=MUTED, anchor="mm")
    d.text((W / 2, 215), serie_titulo, font=f2, fill=ACCENT, anchor="mm")
    d.text((W / 2, 1590), pie, font=f3, fill=INK, anchor="mm")
    im.save(dst)


def marco_vertical(dst, serie_titulo, pie):
    """Capa transparente para ilustración a pantalla completa: degradados, cabecera y pie."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = im.load()
    for y in range(H):
        if y < 330:
            a = int(225 * (1 - y / 330) ** 1.2)
        elif y > 980:
            a = int(min(240, 240 * ((y - 980) / 420) ** 0.9))
        else:
            a = 0
        for x in range(W):
            px[x, y] = (14, 8, 3, a)
    d = ImageDraw.Draw(im)
    d.text((W / 2, 140), "RADIONOVELAS DE AYER", font=fuente(F_TEXTO, 34, 500), fill=MUTED, anchor="mm")
    d.text((W / 2, 215), serie_titulo, font=fuente(F_DISPLAY, 78, 600), fill=ACCENT, anchor="mm")
    d.text((W / 2, 1590), pie, font=fuente(F_TEXTO, 38, 600), fill=INK, anchor="mm")
    im.save(dst)


def png_texto(dst, texto, quien=None, tam=62, banda=False, alto=300):
    """Subtítulo (o gancho) como PNG transparente de W×alto."""
    im = Image.new("RGBA", (W, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = fuente(F_TEXTO, tam, 700)
    lineas = envolver(d, texto, f, W - 140)[:4]
    alto_l = int(tam * 1.22)
    total = len(lineas) * alto_l + (54 if quien else 0)
    y = (alto - total) // 2
    if banda:
        d.rounded_rectangle((40, y - 30, W - 40, y + total + 30), 28, fill=(14, 8, 3, 215))
    if quien:
        d.text((W / 2, y + 18), quien.upper(), font=fuente(F_TEXTO, 36, 600), fill=ACCENT, anchor="mm")
        y += 54
    for l in lineas:
        d.text((W / 2, y + alto_l / 2), l, font=f, fill=INK, anchor="mm", stroke_width=3, stroke_fill=(14, 8, 3))
        y += alto_l
    im.save(dst)


def png_cierre(dst, capitulo, sitio):
    im = Image.new("RGBA", (W, H), (14, 8, 3, 245))
    d = ImageDraw.Draw(im)
    d.text((W / 2, 640), "¿Quiere saber qué pasó?", font=fuente(F_DISPLAY, 84, 600), fill=ACCENT, anchor="mm")
    txt = f"Escuche el capítulo {capitulo} completo, gratis" if capitulo else "Escuche la radionovela completa, gratis"
    for i, l in enumerate(envolver(d, txt, fuente(F_TEXTO, 60, 600), W - 160)):
        d.text((W / 2, 800 + i * 76), l, font=fuente(F_TEXTO, 60, 600), fill=INK, anchor="mm")
    d.text((W / 2, 1040), sitio.replace("https://", ""), font=fuente(F_TEXTO, 56, 700), fill=ACCENT, anchor="mm")
    d.text((W / 2, 1180), "Y mándeselo a alguien que se acuerde", font=fuente(F_TEXTO, 44, 500), fill=MUTED, anchor="mm")
    im.save(dst)


# ---------- subtítulos ----------

def trozos(texto, ini, fin, max_pal=9):
    """Parte una línea en trozos de ≤ max_pal palabras con tiempos proporcionales al número de letras."""
    limpio = limpiar(texto)
    if not limpio:
        return []
    grupos = []
    for frase in re.split(r"(?<=[.?!…])\s+", limpio):  # primero por frases, luego por largo
        pal = frase.split()
        if not pal:
            continue
        n = math.ceil(len(pal) / max_pal)
        tam = math.ceil(len(pal) / n)
        grupos += [" ".join(pal[i:i + tam]) for i in range(0, len(pal), tam)]
    total = sum(len(g) for g in grupos)
    t, out = ini, []
    for g in grupos:
        dur = (fin - ini) * len(g) / total
        out.append((t, t + dur, g))
        t += dur
    return out


NOMBRES = {"locutor": None, "carmenza": "Carmenza", "eutimio": "Eutimio", "obdulia": "Misiá Obdulia",
           "saturnino": "Saturnino", "padre_severo": "Padre Severo", "animador": "La radio",
           "alvaro": "Álvaro", "belisario": "Don Belisario", "regulo": "Don Régulo"}


# ---------- audio ----------

def audio_fragmento(p, tmp):
    fr = p["fragmento"]
    cap = int(fr["capitulo"])
    src = REPO / "audio" / f"cap{cap:02d}_web.mp3"
    if not src.exists():
        raise ErrorPieza(f"no existe {src.name}")
    ini, fin = float(fr["inicio"]), float(fr["fin"])
    if not 5 <= fin - ini <= CONFIG["duracion_max_video"] - CIERRE_S:
        raise ErrorPieza(f"duración del fragmento {fin-ini:.1f}s fuera de rango")
    dst = tmp / "voz.wav"
    d = fin - ini
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(ini), "-t", str(d), "-i", str(src),
         "-af", f"afade=t=in:d=0.3,afade=t=out:st={d-0.8:.2f}:d=0.8", "-ac", "1", "-ar", "44100", str(dst)])
    tiempos = json.loads((MKT / "datos" / f"tiempos-cap{cap}.json").read_text())
    subs = []
    for l in tiempos["lineas"]:
        a, b = max(l["inicio"], ini), min(l["fin"], fin)
        if b - a > 0.4:
            for s in trozos(l["texto"], l["inicio"] - ini, l["fin"] - ini):
                s0, s1 = max(s[0], 0), min(s[1], d)
                if s1 - s0 > 0.25:
                    subs.append((s0, s1, s[2], NOMBRES.get(l["personaje"], l["personaje"].title())))
    return dst, subs, cap


def tts(personaje, texto, emocion, voces):
    if personaje not in voces:
        raise ErrorPieza(f"personaje sin voz: {personaje}")
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        raise ErrorPieza("falta GEMINI_API_KEY")
    v = voces[personaje]
    modelo = CONFIG["tts_modelo_pro"] if personaje in CONFIG["tts_pro_para"] else CONFIG["tts_modelo"]
    marcas = [m.strip() for m in re.findall(r"\[([^\]]*)\]", texto) if m.strip()]
    if emocion:
        marcas.append("registro general: " + emocion)
    prompt = v["direccion"].rstrip(".") + "."
    if marcas:
        prompt += "\nEn esta línea: " + "; ".join(marcas) + "."
    prompt += "\nHabla en español colombiano. Di únicamente el siguiente texto, sin añadir ni comentar nada:\n" + limpiar(texto)
    cuerpo = {"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseModalities": ["AUDIO"],
                                   "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v["voz"]}}}}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    for intento in range(3):  # solo reintenta respuestas sin audio (finishReason OTHER, conocido de Flash) y 429
        try:
            r = http("POST", url, json_body=cuerpo, cabeceras={"x-goog-api-key": clave}, timeout=180)
        except ErrorPieza as e:
            if "HTTP 429" in str(e) and intento < 2:
                time.sleep(30); continue
            raise
        try:
            data = r["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
            return base64.b64decode(data), r.get("usageMetadata", {})
        except (KeyError, IndexError):
            time.sleep(7)
    raise ErrorPieza(f"Gemini no devolvió audio para {personaje} tras 3 intentos")


def audio_escena(p, tmp, sin_tts=False):
    esc = p["escena"]
    voces = json.loads((MKT / "datos" / "voces.json").read_text())
    piezas, subs, t, uso_total = [], [], 0.25, 0
    for i, l in enumerate(esc["lineas"]):
        validar_texto(l["texto"], f"línea {i+1}")
        wav = tmp / f"l{i:02d}.wav"
        if sin_tts:  # prueba local: silencio proporcional al texto
            dur = max(1.5, len(limpiar(l["texto"]).split()) / 2.6)
            run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f=220:d={dur}", "-af", "volume=0.05", "-ar", "44100", "-ac", "1", str(wav)])
        else:
            pcm, uso = tts(l["personaje"], l["texto"], l.get("emocion"), voces)
            uso_total += uso.get("candidatesTokenCount", 0)
            cab = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, 1, 24000, 48000, 2, 16, b"data", len(pcm))
            crudo = tmp / f"l{i:02d}_c.wav"; crudo.write_bytes(cab + pcm)
            # recorta silencios de los extremos
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(crudo), "-af",
                 "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse",
                 "-ar", "44100", "-ac", "1", str(wav)])
            time.sleep(6.5)  # Flash: 10 solicitudes/minuto
        d = duracion(wav)
        for s in trozos(l["texto"], t, t + d):
            subs.append((s[0], s[1], s[2], NOMBRES.get(l["personaje"], l["personaje"].title())))
        piezas.append((wav, t)); t += d + float(l.get("pausa", 0.45))
    total = t + 0.3
    if total > CONFIG["duracion_max_video"] - CIERRE_S:
        raise ErrorPieza(f"la escena dura {total:.1f}s; máximo {CONFIG['duracion_max_video'] - CIERRE_S}s")
    # mezcla: voces en su tiempo + colchón
    entradas, filtros = [], []
    for k, (wav, t0) in enumerate(piezas):
        entradas += ["-i", str(wav)]
        filtros.append(f"[{k}]adelay={int(t0*1000)}|{int(t0*1000)}[v{k}]")
    voces_mix = "".join(f"[v{k}]" for k in range(len(piezas))) + f"amix=inputs={len(piezas)}:normalize=0[vz]"
    fondo = esc.get("fondo")
    dst = tmp / "voz.wav"
    if fondo:
        m = MKT / "musica" / f"{fondo}.mp3"
        if not m.exists():
            raise ErrorPieza(f"colchón '{fondo}' no existe")
        entradas += ["-stream_loop", "-1", "-i", str(m)]
        k = len(piezas)
        vol = float(esc.get("fondo_vol", -24))
        filtros.append(f"[{k}]volume={vol}dB,atrim=0:{total},afade=t=in:d=1,afade=t=out:st={total-1.2}:d=1.2[mu]")
        cadena = ";".join(filtros) + ";" + voces_mix + ";[vz][mu]amix=inputs=2:normalize=0,atrim=0:" + str(total) + "[out]"
    else:
        cadena = ";".join(filtros) + ";" + voces_mix + ";[vz]apad,atrim=0:" + str(total) + "[out]"
    run(["ffmpeg", "-y", "-loglevel", "error", *entradas, "-filter_complex", cadena, "-map", "[out]", "-ac", "1", "-ar", "44100", str(dst)])
    return dst, subs, esc.get("capitulo_cta"), uso_total


# ---------- video ----------

def video(p, voz, subs, cap_cta, tmp, destino):
    serie = p.get("serie_titulo", "La Hora de la Fortuna")
    validar_texto(p.get("gancho", ""), "gancho")
    base = tmp / "base.png"
    lamina_base(base, serie, CONFIG["sitio"].replace("https://", "") + " · gratis")
    # audio + cola con cortinilla para la tarjeta final
    dur_voz = duracion(voz)
    total = dur_voz + CIERRE_S
    audio = tmp / "audio.m4a"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(voz), "-ss", "0", "-t", str(CIERRE_S + 0.5), "-i", str(MKT / "musica" / "tiple_de_cinco.mp3"),
         "-filter_complex", f"[1]volume=-10dB,afade=t=in:d=0.4,afade=t=out:st={CIERRE_S-0.8}:d=0.8,adelay={int(dur_voz*1000)}|{int(dur_voz*1000)}[c];"
                            f"[0][c]amix=inputs=2:normalize=0,atrim=0:{total},loudnorm=I=-15:TP=-1.5:LRA=11[a]",
         "-map", "[a]", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", str(audio)])
    # ilustración: nueva (Gemini, vertical a pantalla completa) o del catálogo (franja con paneo)
    usd = 0.0
    if p.get("ilustracion_de"):  # misma ilustración de otra pieza (A/B en que solo cambia otra cosa): sin costo nuevo
        src = destino / f"{p['ilustracion_de']}-ilu.jpg"
        if not src.exists():
            from comun import url_media
            datos = http("GET", url_media(p["_semana"], f"{p['ilustracion_de']}-ilu.jpg"), cabeceras={"User-Agent": "mkt"}, crudo=True)
            src = tmp / "ilu_de.jpg"; src.write_bytes(datos)
    elif p.get("ilustracion"):
        src, usd = ilustrar.generar(p["ilustracion"], "video", destino / f"{p['id']}-ilu.jpg")
    else:
        src = imagen_fuente(p.get("imagen", "tienda"))
    im = Image.open(src).convert("RGB")
    ilu = tmp / "ilu.png"
    ola = [f"[2]showwaves=s=900x110:mode=cline:scale=sqrt:rate={FPS}:colors=white:draw=full,format=gray,lut=y='if(gt(val,20),255,0)'[m]",
           f"color=c=0xf2a23a:s=900x110:r={FPS},format=rgba[col]", "[col][m]alphamerge[ola]"]
    if im.height > im.width:
        # pantalla completa con acercamiento lento (8 %) y marco con degradados
        ew, eh = int(W * 1.08), int(H * 1.08)
        esc = max(ew / im.width, eh / im.height)
        im = im.resize((int(im.width * esc) + 1, int(im.height * esc) + 1), Image.LANCZOS); im.save(ilu)
        marco = tmp / "marco.png"; marco_vertical(marco, serie, CONFIG["sitio"].replace("https://", "") + " · gratis")
        entradas = ["-loop", "1", "-framerate", str(FPS), "-i", str(marco),
                    "-loop", "1", "-framerate", str(FPS), "-i", str(ilu),
                    "-i", str(audio)]
        f = [f"[1]crop={W}:{H}:x='(iw-{W})*(0.5+0.5*t/{total:.2f})':y='(ih-{H})*(1-t/{total:.2f})'[ilu]",
             "[ilu][0]overlay=0:0[b0]", *ola, f"[b0][ola]overlay=90:{WAVE_Y}[b1]"]
    else:
        esc = IMG_H / im.height
        im = im.resize((max(W + 2, int(im.width * esc)), IMG_H), Image.LANCZOS)
        im.save(ilu)
        entradas = ["-loop", "1", "-framerate", str(FPS), "-i", str(base),
                    "-loop", "1", "-framerate", str(FPS), "-i", str(ilu),
                    "-i", str(audio)]
        f = [f"[1]crop={W}:{IMG_H}:x='(iw-{W})*t/{total:.2f}':y=0[ilu]",
             f"[0][ilu]overlay=0:{IMG_Y}[b0]", *ola, f"[b0][ola]overlay=90:{WAVE_Y}[b1]"]
    ult, k = "b1", 3
    if p.get("gancho"):
        g = tmp / "gancho.png"; png_texto(g, p["gancho"], tam=70, banda=True, alto=IMG_H)
        entradas += ["-loop", "1", "-t", "3", "-i", str(g)]
        f.append(f"[{k}]fade=t=out:st=2.3:d=0.3:alpha=1[gk]"); f.append(f"[{ult}][gk]overlay=0:{IMG_Y}:eof_action=pass:enable='lt(t,2.6)'[g]"); ult, k = "g", k + 1
    # subtítulos: una sola pista (concat de PNG con duraciones) en vez de una entrada por trozo
    vacio = tmp / "vacio.png"; Image.new("RGBA", (W, 300), (0, 0, 0, 0)).save(vacio)
    lista, t = ["ffconcat version 1.0"], 0.0
    for i, (a, b, texto, quien) in enumerate(sorted(subs)):
        a = max(a, t)
        if b - a < 0.2:
            continue
        if a - t > 0.01:
            lista += [f"file '{vacio}'", f"duration {a - t:.3f}"]
        s_png = tmp / f"s{i:03d}.png"; png_texto(s_png, texto, quien)
        lista += [f"file '{s_png}'", f"duration {b - a:.3f}"]; t = b
    lista += [f"file '{vacio}'", f"duration {max(total - t, 0.1):.3f}", f"file '{vacio}'"]
    (tmp / "subs.txt").write_text("\n".join(lista))
    entradas += ["-f", "concat", "-safe", "0", "-i", str(tmp / "subs.txt")]
    f.append(f"[{k}]fps={FPS},format=rgba[subs]")
    f.append(f"[{ult}][subs]overlay=0:{SUB_Y}:eof_action=pass[st]"); ult, k = "st", k + 1
    c = tmp / "cierre.png"; png_cierre(c, cap_cta, CONFIG["sitio"])
    entradas += ["-loop", "1", "-framerate", str(FPS), "-t", f"{CIERRE_S:.2f}", "-i", str(c)]
    f.append(f"[{k}]setpts=PTS+{dur_voz:.2f}/TB[ck]")
    f.append(f"[{ult}][ck]overlay=0:0:eof_action=pass,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-loglevel", "error", *entradas, "-filter_complex", ";".join(f),
         "-map", "[v]", "-map", "2:a", "-t", f"{total:.2f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-movflags", "+faststart", str(destino / f"{p['id']}.mp4")])
    # vista previa liviana para la página de aprobación (540×960)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(destino / f"{p['id']}.mp4"), "-vf", "scale=540:960", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(destino / f"{p['id']}-prev.mp4")])
    # portada: fotograma a los 1,2 s
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1.2", "-i", str(destino / f"{p['id']}.mp4"), "-frames:v", "1", "-q:v", "3",
         str(destino / f"{p['id']}.jpg")])
    return round(total, 1), usd


def carrusel(p, destino):
    car = p["carrusel"]
    archivos = []
    diapos = car["diapositivas"]
    if not 2 <= len(diapos) <= 10:
        raise ErrorPieza("el carrusel necesita de 2 a 10 diapositivas")
    usd = 0.0
    for i, dp in enumerate(diapos, 1):
        validar_texto(dp.get("titulo", "") + " " + dp.get("texto", ""), f"diapositiva {i}")
        if dp.get("ilustracion"):  # lámina a sangre con texto abajo sobre degradado
            ruta, u = ilustrar.generar(dp["ilustracion"], "carrusel", destino / f"{p['id']}-ilu{i}.jpg"); usd += u
            ilu = Image.open(ruta).convert("RGB")
            esc = max(1080 / ilu.width, 1350 / ilu.height)
            ilu = ilu.resize((int(ilu.width * esc) + 1, int(ilu.height * esc) + 1), Image.LANCZOS).crop((0, 0, 1080, 1350))
            capa = Image.new("RGBA", (1080, 1350), (0, 0, 0, 0)); cp = capa.load()
            for yy in range(700, 1350):
                for xx in range(1080):
                    cp[xx, yy] = (14, 8, 3, int(235 * ((yy - 700) / 650) ** 0.8))
            im = Image.alpha_composite(ilu.convert("RGBA"), capa).convert("RGB")
            d = ImageDraw.Draw(im)
            lt = envolver(d, dp.get("titulo", ""), fuente(F_DISPLAY, 88, 600), 940) if dp.get("titulo") else []
            lx = [l for parr in (dp.get("texto") or "").split("\n") for l in envolver(d, parr, fuente(F_TEXTO, 50, 500), 940)]
            y = 1230 - len(lt) * 104 - len(lx) * 66
            for l in lt:
                d.text((70, y), l, font=fuente(F_DISPLAY, 88, 600), fill=ACCENT); y += 104
            for l in lx:
                d.text((70, y), l, font=fuente(F_TEXTO, 50, 500), fill=INK); y += 66
            d.text((70, 1270), "Radionovelas de ayer", font=fuente(F_TEXTO, 32, 600), fill=ACCENT)
            d.text((1010, 1270), f"{i}/{len(diapos)}", font=fuente(F_TEXTO, 32, 500), fill=ACCENT, anchor="ra")
            nombre = f"{p['id']}-{i}.jpg"; im.save(destino / nombre, quality=90); archivos.append(nombre)
            continue
        im = Image.new("RGB", (1080, 1350), PAPEL if dp.get("papel", i > 1) else BG)
        d = ImageDraw.Draw(im)
        oscuro = not dp.get("papel", i > 1)
        tinta, acento = (INK, ACCENT) if oscuro else (PAPEL_INK, PAPEL_ACENTO)
        y = 120 if dp.get("imagen") else 260
        if dp.get("imagen"):
            ilu = Image.open(imagen_fuente(dp["imagen"])).convert("RGB")
            ilu = ilu.resize((1080, int(ilu.height * 1080 / ilu.width)), Image.LANCZOS)
            alto = min(ilu.height, 700)
            im.paste(ilu.crop((0, 0, 1080, alto)), (0, 0)); y = alto + 70
        if dp.get("titulo"):
            for l in envolver(d, dp["titulo"], fuente(F_DISPLAY, 96, 600), 940):
                d.text((70, y), l, font=fuente(F_DISPLAY, 96, 600), fill=acento); y += 112
            y += 24
        for parr in (dp.get("texto") or "").split("\n"):
            for l in envolver(d, parr, fuente(F_TEXTO, 56, 500), 940):
                d.text((70, y), l, font=fuente(F_TEXTO, 56, 500), fill=tinta); y += 74
            y += 22
        if y > 1250:
            raise ErrorPieza(f"el texto de la diapositiva {i} no cabe")
        d.text((70, 1270), "Radionovelas de ayer", font=fuente(F_TEXTO, 32, 600), fill=acento)
        d.text((1010, 1270), f"{i}/{len(diapos)}", font=fuente(F_TEXTO, 32, 500), fill=acento, anchor="ra")
        nombre = f"{p['id']}-{i}.jpg"
        im.save(destino / nombre, quality=90)
        archivos.append(nombre)
    return archivos, usd


CAMPOS_MEDIA = ("tipo", "fragmento", "escena", "carrusel", "imagen", "ilustracion", "ilustracion_de", "gancho", "serie_titulo", "reintento")


def huella(p):
    import hashlib
    return hashlib.sha1(json.dumps({k: p.get(k) for k in CAMPOS_MEDIA}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan"); ap.add_argument("--salida", required=True)
    ap.add_argument("--solo"); ap.add_argument("--sin-tts", action="store_true")
    ap.add_argument("--previo", help="render.json anterior: no repite piezas con la misma huella (ni las que fallaron)")
    a = ap.parse_args()
    plan = json.loads(Path(a.plan).read_text())
    destino = Path(a.salida); destino.mkdir(parents=True, exist_ok=True)
    previo = json.loads(Path(a.previo).read_text()).get("piezas", {}) if a.previo and Path(a.previo).exists() else {}
    resultado = {"semana": plan["semana"], "generado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "piezas": {}}
    nuevas = 0
    for p in plan["piezas"]:
        if a.solo and p["id"] != a.solo:
            continue
        h = huella(p)
        pv = previo.get(p["id"], {})
        if pv.get("huella") == h and not str(pv.get("error", "")).startswith("falta ") and (pv.get("vista_previa") or not pv.get("ok")):  # falta de secreto sí se reintenta
            resultado["piezas"][p["id"]] = previo[p["id"]]
            continue
        nuevas += 1
        p["_semana"] = plan["semana"]
        log("→", p["id"], p["tipo"])
        try:
            for k, v in (p.get("texto") or {}).items():
                validar_texto(v, f"texto.{k}")
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                if p["tipo"] == "fragmento":
                    voz, subs, cap = audio_fragmento(p, tmp)
                    dur, usd_img = video(p, voz, subs, cap, tmp, destino)
                    r = {"ok": True, "video": f"{p['id']}.mp4", "portada": f"{p['id']}.jpg", "vista_previa": f"{p['id']}-prev.mp4", "segundos": dur,
                         "usd_aprox": usd_img}
                elif p["tipo"] == "escena":
                    voz, subs, cap, tokens = audio_escena(p, tmp, a.sin_tts)
                    dur, usd_img = video(p, voz, subs, cap, tmp, destino)
                    r = {"ok": True, "video": f"{p['id']}.mp4", "portada": f"{p['id']}.jpg", "vista_previa": f"{p['id']}-prev.mp4", "segundos": dur,
                         "tts_tokens_salida": tokens, "usd_aprox": round(tokens * 20 / 1e6 + usd_img, 4)}
                elif p["tipo"] == "carrusel":
                    imgs, usd_img = carrusel(p, destino); r = {"ok": True, "imagenes": imgs, "vista_previa": imgs[0], "usd_aprox": usd_img}
                else:
                    raise ErrorPieza(f"tipo desconocido {p['tipo']}")
        except Exception as e:
            log("   ERROR:", e)
            r = {"ok": False, "error": f"{'' if isinstance(e, ErrorPieza) else type(e).__name__ + ': '}{e}"[:600]}
        r["huella"] = h
        resultado["piezas"][p["id"]] = r
    (destino / "render.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1))
    malas = [k for k, v in resultado["piezas"].items() if not v["ok"]]
    log(f"Listo: {nuevas} renderizadas ahora; {len(resultado['piezas']) - len(malas)} bien, {len(malas)} con error {malas}")


if __name__ == "__main__":
    main()
