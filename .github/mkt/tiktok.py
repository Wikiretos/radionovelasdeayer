"""
TikTok: envía videos como BORRADOR a la bandeja de entrada de la cuenta (API "Upload", scope video.upload).
No publica: Andrés abre la notificación en la app de TikTok, pega el texto y publica.
(La publicación directa exige una auditoría que TikTok no concede a herramientas para cuentas propias.)

Secretos (entorno): TT_CLIENT_KEY, TT_CLIENT_SECRET, TT_REFRESH_TOKEN (se obtiene con herramientas/tiktok_token.py).

Prueba manual (no toca estado.json):
  python3 .github/mkt/tiktok.py prueba 2026-W45/w45-05
"""
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import ErrorPieza, http, log, url_media

API = "https://open.tiktokapis.com/v2"


def _err(r, paso):
    e = (r or {}).get("error") or {}
    if e.get("code") not in (None, "ok"):
        raise ErrorPieza(f"TikTok {paso}: {e.get('code')} — {e.get('message', '')[:300]}")


def tt_token():
    for k in ("TT_CLIENT_KEY", "TT_CLIENT_SECRET", "TT_REFRESH_TOKEN"):
        if not os.environ.get(k):
            raise ErrorPieza(f"falta el secreto {k}")
    r = http("POST", f"{API}/oauth/token/", {"client_key": os.environ["TT_CLIENT_KEY"], "client_secret": os.environ["TT_CLIENT_SECRET"],
                                             "grant_type": "refresh_token", "refresh_token": os.environ["TT_REFRESH_TOKEN"]})
    if "access_token" not in r:
        raise ErrorPieza(f"TikTok no renovó el token: {r.get('error')} {r.get('error_description', '')[:200]}")
    # TikTok puede rotar el refresh token: nunca se imprime (los registros del repo son públicos), solo se avisa.
    rotado = r.get("refresh_token") and r["refresh_token"] != os.environ["TT_REFRESH_TOKEN"]
    return r["access_token"], {"scope": r.get("scope", ""), "rotado": bool(rotado), "refresh_vence_s": r.get("refresh_expires_in")}


def tt_borrador(p, semana, hv=None):
    """Sube <id>.mp4 de R2 como borrador. Devuelve {publish_id, estado, aviso?}."""
    tok, info = tt_token()
    if "video.upload" not in info["scope"]:
        raise ErrorPieza(f"el token de TikTok no tiene el permiso video.upload (tiene: {info['scope']})")
    video = http("GET", url_media(semana, p["id"] + ".mp4", hv), cabeceras={"User-Agent": "mkt"}, crudo=True, timeout=300)
    n = len(video)
    if n > 64 * 1024 * 1024:
        raise ErrorPieza(f"video de {n} bytes: más de 64 MB, habría que partirlo")
    cab = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}
    ini = http("POST", f"{API}/post/publish/inbox/video/init/", cabeceras=cab,
               json_body={"source_info": {"source": "FILE_UPLOAD", "video_size": n, "chunk_size": n, "total_chunk_count": 1}})
    _err(ini, "init")
    pid, up = ini["data"]["publish_id"], ini["data"]["upload_url"]
    req = urllib.request.Request(up, data=video, method="PUT", headers={
        "Content-Type": "video/mp4", "Content-Length": str(n), "Content-Range": f"bytes 0-{n - 1}/{n}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            if r.status not in (200, 201):
                raise ErrorPieza(f"TikTok subida: HTTP {r.status}")
    except urllib.error.HTTPError as e:
        raise ErrorPieza(f"TikTok subida: HTTP {e.code} {e.read().decode('utf8', 'replace')[:300]}")
    res = {"publish_id": pid, "borrador": True}
    if info["rotado"]:
        res["aviso"] = "TikTok entregó un refresh token nuevo: hay que regenerar TT_REFRESH_TOKEN con tiktok_token.py"
    # ya está subido; desde aquí nada lo convierte en error
    try:
        for _ in range(18):
            st = http("POST", f"{API}/post/publish/status/fetch/", cabeceras=cab, json_body={"publish_id": pid})
            d = st.get("data") or {}
            res["estado"] = d.get("status")
            if d.get("status") in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                break
            if d.get("status") == "FAILED":
                res["aviso"] = f"TikTok rechazó el video: {d.get('fail_reason')}"; break
            time.sleep(10)
        else:
            res["aviso"] = "subido, sin confirmar que llegó a la bandeja tras 3 min"
    except ErrorPieza as e:
        res["aviso"] = f"subido, no se pudo confirmar: {e}"
    return res


def tt_usuario():
    tok, info = tt_token()
    r = http("GET", f"{API}/user/info/?fields=open_id,display_name", cabeceras={"Authorization": f"Bearer {tok}"})
    _err(r, "user/info")
    u = (r.get("data") or {}).get("user") or {}
    return {"cuenta": u.get("display_name"), "permisos": info["scope"], "token_rotado": info["rotado"]}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "prueba" or "/" not in sys.argv[2]:
        sys.exit("uso: tiktok.py prueba AAAA-Www/<id>")
    sem, pid = sys.argv[2].split("/", 1)
    try:
        log("cuenta:", json.dumps(tt_usuario(), ensure_ascii=False))
        r = tt_borrador({"id": pid}, sem)
        log("RESULTADO", json.dumps(r, ensure_ascii=False))
        print(f"::notice title=TikTok prueba::{pid} → {r.get('estado')} {r.get('aviso', '')}")
    except ErrorPieza as e:
        log("ERROR", e)
        print(f"::error title=TikTok prueba::{e}")
        sys.exit(1)
