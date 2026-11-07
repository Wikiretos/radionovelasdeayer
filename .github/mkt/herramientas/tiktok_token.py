"""
Obtiene el TT_REFRESH_TOKEN de TikTok (una vez; se repite si TikTok lo rota o vence al año).
Se corre en el Mac, en Terminal:  python3 tiktok_token.py
No guarda nada en disco. Los valores se pegan en GitHub → Settings → Secrets → Actions.

Antes, en developers.tiktok.com (la app, o su sandbox):
  - Productos: Login Kit y Content Posting API (scope video.upload; user.info.basic viene con Login Kit).
  - Login Kit → Redirect URI (web): https://radionovelasdeayer.com/
  - Sandbox: agregar @radionovelasdeayer como usuario de prueba (target user).
"""
import getpass, json, secrets, urllib.error, urllib.parse, urllib.request

REDIRECT = "https://radionovelasdeayer.com/"
SCOPES = "user.info.basic,video.upload"

clave = input("Client key: ").strip()
secreto = getpass.getpass("Client secret (no se ve al escribir): ").strip()
estado = secrets.token_urlsafe(16)
url = "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(
    {"client_key": clave, "scope": SCOPES, "response_type": "code", "redirect_uri": REDIRECT, "state": estado})
print("\n1) Abra este enlace en el navegador, entre con la cuenta @radionovelasdeayer y autorice:\n\n" + url)
print("\n2) TikTok lo devuelve a radionovelasdeayer.com con ?code=... en la barra de direcciones. Copie esa dirección completa.")
vuelta = input("\nPegue aquí la dirección completa: ").strip()
q = urllib.parse.parse_qs(urllib.parse.urlparse(vuelta).query)
if q.get("error"):
    raise SystemExit(f"TikTok devolvió error: {q.get('error')} {q.get('error_description')}")
if q.get("state", [""])[0] != estado:
    raise SystemExit("El 'state' no coincide: vuelva a empezar (no use un enlace viejo).")
codigo = q["code"][0]
datos = urllib.parse.urlencode({"client_key": clave, "client_secret": secreto, "code": codigo,
                                "grant_type": "authorization_code", "redirect_uri": REDIRECT}).encode()
req = urllib.request.Request("https://open.tiktokapis.com/v2/oauth/token/", data=datos,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
except urllib.error.HTTPError as e:
    raise SystemExit(f"Error HTTP {e.code}: {e.read().decode()[:500]}")
if "refresh_token" not in r:
    raise SystemExit(f"Sin refresh token: {r}")
print("\nListo. Permisos concedidos:", r.get("scope"))
if "video.upload" not in r.get("scope", ""):
    print("OJO: falta video.upload. Revise que la app tenga Content Posting API.")
print(f"Vence en {int(r.get('refresh_expires_in', 0)) // 86400} días.\n")
print("Cree estos 3 secretos en GitHub (Settings → Secrets and variables → Actions):")
print("  TT_CLIENT_KEY     =", clave)
print("  TT_CLIENT_SECRET  = (el que pegó arriba)")
print("  TT_REFRESH_TOKEN  =", r["refresh_token"])
