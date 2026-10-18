#!/usr/bin/env bash
# Pasos del workflow Redes. Uso: ciclo.sh render|previas|bajar|publicar|metricas
# Ante error deja el final del registro como anotación (legible por la API de GitHub).
set -euo pipefail
R="${R:-radionovelas-audio}"; PUBLICO="${PUBLICO:-https://audio.radionovelasdeayer.com}"
r2put(){ npx --yes wrangler@3 r2 object put "$R/$1" --file "$2" --content-type "$3" --cache-control "$4" >/dev/null; }

render(){
  HOY=$(TZ=America/Bogota date +%Y-%m-%d)
  for f in .github/mkt/semanas/[0-9][0-9][0-9][0-9]-W[0-9][0-9].json; do
    case "$f" in *.aprobaciones.json) continue;; esac
    SEM=$(basename "$f" .json)
    FIN=$(python3 -c "import json;p=json.load(open('$f'));print(max(x['fecha'] for x in p['piezas']))")
    if [[ "$FIN" < "$HOY" ]]; then echo "$SEM ya pasó"; continue; fi
    rm -rf salida && mkdir salida
    curl -sf "$PUBLICO/mkt/$SEM/render.json?v=$(date +%s)" -o previo.json || echo '{}' > previo.json
    python3 .github/mkt/render.py "$f" --salida salida --previo previo.json
    mkdir -p "medios/$SEM"
    for a in salida/*; do
      n=$(basename "$a")
      case "$n" in *-prev.mp4|*.jpg|render.json) cp "$a" "medios/$SEM/";; esac
      case "$n" in
        *-prev.mp4) continue;;
        *.mp4) r2put "mkt/$SEM/$n" "$a" video/mp4 "public, max-age=31536000";;
        *.jpg) r2put "mkt/$SEM/$n" "$a" image/jpeg "public, max-age=31536000";;
        render.json) r2put "mkt/$SEM/$n" "$a" application/json no-store;;
        *) continue;;
      esac
      echo "subido mkt/$SEM/$n"
    done
  done
}

previas(){
  [ -d medios ] || { echo "nada nuevo"; return 0; }
  rm -rf /tmp/rama && mkdir /tmp/rama
  if git fetch -q origin mkt-medios 2>/dev/null; then git --work-tree=/tmp/rama checkout -q FETCH_HEAD -- . ; git reset -q; fi
  SEMHOY=$(TZ=America/Bogota date +%G-W%V)
  for d in /tmp/rama/*/; do [ -d "$d" ] || continue; s=$(basename "$d"); if [[ "$s" < "$SEMHOY" ]]; then rm -rf "$d"; fi; done
  cp -r medios/* /tmp/rama/
  cd /tmp/rama && git init -q -b mkt-medios && git add -A
  git -c user.name=redes-bot -c user.email=redes-bot@users.noreply.github.com commit -q -m "Vistas previas de redes"
  git push -q -f "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" mkt-medios
  echo "rama mkt-medios actualizada"
}

bajar(){ curl -sf "$PUBLICO/mkt/estado.json?v=$(date +%s)" -o estado.json || echo '{}' > estado.json; }

publicar(){
  S=""; [ "${SIMULAR:-false}" = true ] && S="--simular"
  python3 .github/mkt/publicar.py --estado estado.json $S \
    --subir "npx --yes wrangler@3 r2 object put $R/mkt/estado.json --file estado.json --content-type application/json --cache-control no-store >/dev/null"
}

metricas(){
  python3 .github/mkt/metricas.py --estado estado.json --salida redes.json
  r2put datos/redes.txt redes.json "text/plain; charset=utf-8" no-store
  echo "subido datos/redes.txt"
}

paso="$1"
set +e
( set -e; "$paso" ) 2>&1 | tee "/tmp/paso-$paso.log"
s=${PIPESTATUS[0]}
if [ "$s" != 0 ]; then
  msg=$(tail -n 30 "/tmp/paso-$paso.log" | sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')
  echo "::error title=Redes $paso::$msg"
fi
# errores por pieza (no detienen el paso) también como anotación
grep -E "ERROR" "/tmp/paso-$paso.log" | head -8 | while read -r l; do echo "::warning title=Redes $paso::$l"; done || true
exit "$s"
