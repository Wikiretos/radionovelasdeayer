// Worker: consulta la GA4 Data API con una cuenta de servicio y deja datos/informe.json en R2
// (público en https://audio.radionovelasdeayer.com/datos/informe.json).
// Secretos: GA_SA_KEY (JSON completo de la cuenta de servicio), CLAVE_MANUAL (para /generar?clave=...).
// Vars: GA_PROPERTY, ESTRENO.

const COL = -5 * 3600e3;
const fecha = d => new Date(d.getTime() + COL).toISOString().slice(0, 10);
const hace = n => fecha(new Date(Date.now() - n * 864e5));

function b64url(buf) {
  const s = typeof buf === "string" ? btoa(buf) : btoa(String.fromCharCode(...new Uint8Array(buf)));
  return s.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function pemToDer(pem) {
  const b = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
  return Uint8Array.from(atob(b), c => c.charCodeAt(0)).buffer;
}
async function tokenGoogle(sa) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claim = b64url(JSON.stringify({
    iss: sa.client_email, scope: "https://www.googleapis.com/auth/analytics.readonly",
    aud: "https://oauth2.googleapis.com/token", iat: now, exp: now + 3600 }));
  const key = await crypto.subtle.importKey("pkcs8", pemToDer(sa.private_key),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${header}.${claim}`));
  const jwt = `${header}.${claim}.${b64url(sig)}`;
  const r = await fetch("https://oauth2.googleapis.com/token", { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: `grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=${jwt}` });
  if (!r.ok) throw new Error("token: " + await r.text());
  return (await r.json()).access_token;
}

// Varias consultas en una sola llamada (batchRunReports admite 5 por llamada): el plan gratuito limita subrequests.
function filas(d, dims, mets) {
  return (d.rows || []).map(row => {
    const o = {};
    dims.forEach((k, i) => o[k] = row.dimensionValues[i].value);
    mets.forEach((k, i) => o[k] = Number(row.metricValues[i].value));
    return o;
  });
}
async function lote(env, token, rango, specs) {
  const out = {};
  for (let i = 0; i < specs.length; i += 5) {
    const parte = specs.slice(i, i + 5);
    const r = await fetch(`https://analyticsdata.googleapis.com/v1beta/properties/${env.GA_PROPERTY}:batchRunReports`, {
      method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ requests: parte.map(s => ({ dateRanges: [rango], dimensions: s.dims.map(name => ({ name })),
        metrics: s.mets.map(name => ({ name })), limit: s.limit || 50, ...(s.extra || {}) })) }) });
    if (!r.ok) { const e = (await r.text()).slice(0, 300); parte.forEach(s => out[s.k] = { error: e }); continue; }
    const d = await r.json();
    parte.forEach((s, j) => { const rep = d.reports?.[j]; out[s.k] = rep ? filas(rep, s.dims, s.mets) : { error: "sin informe" }; });
  }
  return out;
}

async function generar(env) {
  const sa = JSON.parse(env.GA_SA_KEY);
  const token = await tokenGoogle(sa);
  const ayer = hace(1);
  const R = {
    ayer: { startDate: ayer, endDate: ayer },
    sem: { startDate: hace(7), endDate: ayer },
    sem_anterior: { startDate: hace(14), endDate: hace(8) },
    // Antes del estreno, el acumulado cubre los últimos 30 días (pruebas); después, desde el estreno.
    acumulado: { startDate: env.ESTRENO <= ayer ? env.ESTRENO : hace(30), endDate: ayer },
  };
  const U = ["totalUsers", "newUsers", "sessions"];
  const EV = ["eventCount", "totalUsers"];
  const EVENTOS = ["play", "progreso", "complete", "share_click", "canal_click", "toda_serie_click"];
  const fEv = { dimensionFilter: { filter: { fieldName: "eventName", inListFilter: { values: EVENTOS } } } };
  const out = { generado: new Date().toISOString(), fecha_informe: ayer, hoy: hace(0), estreno: env.ESTRENO,
    propiedad: env.GA_PROPERTY,
    nota: "Edad y sexo requieren Google Signals y umbrales de privacidad; lista vacía = sin datos suficientes. " +
          "Los datos de GA4 de ayer pueden tardar hasta 24-48 h en consolidarse. " +
          "fuente/capitulo/pct/donde son dimensiones personalizadas creadas el 2026-10-09; no hay datos anteriores." };
  for (const [k, rango] of Object.entries(R)) {
    const specs = [
      { k: "totales", dims: [], mets: U },
      { k: "nuevos_vs_recurrentes", dims: ["newVsReturning"], mets: U },
      { k: "canal", dims: ["sessionDefaultChannelGroup"], mets: U },
      { k: "fuente_sitio", dims: ["customEvent:fuente"], mets: U },
      { k: "pagina", dims: ["pagePath"], mets: U },
      { k: "eventos", dims: ["eventName"], mets: EV, extra: fEv },
      { k: "eventos_por_capitulo", dims: ["eventName", "customEvent:capitulo"], mets: EV, extra: fEv },
      { k: "eventos_por_fuente", dims: ["eventName", "customEvent:fuente"], mets: EV, extra: fEv },
      { k: "progreso_pct", dims: ["customEvent:capitulo", "customEvent:pct"], mets: EV,
        extra: { dimensionFilter: { filter: { fieldName: "eventName", stringFilter: { value: "progreso" } } } } },
      { k: "dispositivo", dims: ["deviceCategory", "operatingSystem"], mets: U },
      { k: "ciudad", dims: ["city"], mets: U, limit: 15 },
      { k: "edad", dims: ["userAgeBracket"], mets: U },
      { k: "sexo", dims: ["userGender"], mets: U },
    ];
    if (k !== "ayer") specs.push({ k: "por_dia", dims: ["date"], mets: U, limit: 120, extra: { orderBys: [{ dimension: { dimensionName: "date" } }] } });
    const res = await lote(env, token, rango, specs);
    res.totales = Array.isArray(res.totales) ? (res.totales[0] || { totalUsers: 0, newUsers: 0, sessions: 0 }) : res.totales;
    out[k] = { rango, ...res };
  }
  const cuerpo = JSON.stringify(out, null, 1);
  const meta = { httpMetadata: { contentType: "application/json; charset=utf-8", cacheControl: "public, max-age=300" } };
  await env.AUDIO.put("datos/informe.json", cuerpo, meta);
  await env.AUDIO.put(`datos/informe-${ayer}.json`, cuerpo, meta);
  return out;
}

export default {
  async scheduled(ev, env, ctx) { ctx.waitUntil(generar(env)); },
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.pathname === "/generar" && env.CLAVE_MANUAL && u.searchParams.get("clave") === env.CLAVE_MANUAL) {
      try { const o = await generar(env); return Response.json({ ok: true, fecha: o.fecha_informe }); }
      catch (e) { return Response.json({ ok: false, error: String(e) }, { status: 500 }); }
    }
    return new Response("ga4-informe", { status: 200 });
  }
};
