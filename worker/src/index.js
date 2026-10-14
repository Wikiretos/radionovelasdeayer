// Worker ga4-informe. Cada día (5:30 a. m. Colombia) escribe en R2 (público en https://audio.radionovelasdeayer.com/datos/):
//   - informe.txt / informe.json: GA4 del sitio (usuarios, embudo, fuentes, audiencia), sin ciudades de centros de datos.
//   - seo.txt: Search Console + sitemap + embudo orgánico (spec: claude/spec-seo-worker.md v2, 2026-10-14).
// Secretos: GA_SA_KEY (JSON de la cuenta de servicio ga4-informe@radionovelas.iam.gserviceaccount.com), CLAVE_MANUAL.
// Vars: GA_PROPERTY, ESTRENO.

const COL = -5 * 3600e3;
const fecha = d => new Date(d.getTime() + COL).toISOString().slice(0, 10);
const hace = n => fecha(new Date(Date.now() - n * 864e5));
const sumar = (ymd, n) => new Date(Date.parse(ymd + "T12:00:00Z") + n * 864e5).toISOString().slice(0, 10);
const mayor = (a, b) => (a > b ? a : b);

// ===== Constantes editables =====
// Ciudades de centros de datos (bots / rastreadores). Filtro activo desde 2026-10-14 en informe.txt y en el embudo de seo.txt.
const CIUDADES_CENTROS_DATOS = ["Boardman", "Council Bluffs", "Ashburn", "The Dalles", "Lenoir", "Moncks Corner",
  "Pryor", "Prineville", "Altoona", "Quincy"];
const KEYWORDS = {
  cabeza: ["radionovelas", "radio novelas", "radio novela", "escuchar radionovelas", "radionovela"],
  escuchar: ["radionovelas completas para escuchar", "escuchar radionovelas gratis", "radionovelas gratis",
    "dónde escuchar radionovelas", "radionovelas online"],
  colombianas: ["radionovelas colombianas", "radionovela colombiana", "radionovelas colombianas antiguas",
    "radionovelas famosas en colombia", "radionovelas todelar"],
  titulos: ["kaliman radionovela"],
  didactico: ["qué es una radionovela"],
  marca: ["la hora de la fortuna", "radionovelas de ayer"],
  expansion: ["leyendas colombianas", "mitos y leyendas de colombia", "cuentos colombianos", "historias para dormir"],
};
const SITIO = "https://radionovelasdeayer.com";
const SC_PROPIEDAD = "sc-domain:radionovelasdeayer.com";
const SC_URL = `https://searchconsole.googleapis.com/webmasters/v3/sites/${encodeURIComponent(SC_PROPIEDAD)}/searchAnalytics/query`;
const SC_DESDE_MIN = "2026-09-14";
const EMBUDO_DESDE = "2026-10-09";

// ===== Autenticación (un token para GA4 y Search Console) =====
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
    iss: sa.client_email,
    scope: "https://www.googleapis.com/auth/analytics.readonly https://www.googleapis.com/auth/webmasters.readonly",
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

// ===== GA4 =====
const fCiudadIn = { filter: { fieldName: "city", inListFilter: { values: CIUDADES_CENTROS_DATOS, caseSensitive: false } } };
const fCiudadOut = { notExpression: fCiudadIn };
const y = (...ex) => ({ andGroup: { expressions: ex } });
const fEvento = v => ({ filter: { fieldName: "eventName", stringFilter: { value: v } } });
const fEventos = vs => ({ filter: { fieldName: "eventName", inListFilter: { values: vs } } });
// Añade el filtro de centros de datos a la consulta (salvo spec.sinFiltroCiudad).
function conCiudad(s) {
  if (s.sinFiltroCiudad) return s.extra || {};
  const df = s.extra?.dimensionFilter;
  return { ...(s.extra || {}), dimensionFilter: df ? y(fCiudadOut, df) : fCiudadOut };
}
function filas(d, dims, mets) {
  return (d.rows || []).map(row => {
    const o = {};
    dims.forEach((k, i) => o[k] = row.dimensionValues[i].value);
    mets.forEach((k, i) => o[k] = Number(row.metricValues[i].value));
    return o;
  });
}
// batchRunReports admite 5 consultas por llamada; cada spec puede traer su propio rango.
async function lote(env, token, rangoBase, specs) {
  const out = {};
  for (let i = 0; i < specs.length; i += 5) {
    const parte = specs.slice(i, i + 5);
    const r = await fetch(`https://analyticsdata.googleapis.com/v1beta/properties/${env.GA_PROPERTY}:batchRunReports`, {
      method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({ requests: parte.map(s => ({ dateRanges: [s.rango || rangoBase],
        dimensions: s.dims.map(name => ({ name })), metrics: s.mets.map(name => ({ name })),
        limit: s.limit || 50, ...conCiudad(s) })) }) });
    if (!r.ok) { const e = `${r.status} ${(await r.text()).slice(0, 300)}`; parte.forEach(s => out[s.k] = { error: e }); continue; }
    const d = await r.json();
    parte.forEach((s, j) => { const rep = d.reports?.[j]; out[s.k] = rep ? filas(rep, s.dims, s.mets) : { error: "sin informe" }; });
  }
  return out;
}

async function generarInforme(env, token) {
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
  const fEv = { dimensionFilter: fEventos(EVENTOS) };
  const out = { generado: new Date().toISOString(), fecha_informe: ayer, hoy: hace(0), estreno: env.ESTRENO,
    propiedad: env.GA_PROPERTY, filtro_ciudades: CIUDADES_CENTROS_DATOS,
    nota: "Edad y sexo requieren Google Signals y umbrales de privacidad; lista vacía = sin datos suficientes. " +
          "Los datos de GA4 de ayer pueden tardar hasta 24-48 h en consolidarse. " +
          "fuente/capitulo/pct/donde son dimensiones personalizadas creadas el 2026-10-09; cluster/keyword el 2026-10-14. " +
          "Desde el 2026-10-14 todas las cifras excluyen las ciudades de filtro_ciudades (centros de datos); " +
          "los descartados de cada bloque van en 'excluidos'." };
  for (const [k, rango] of Object.entries(R)) {
    const specs = [
      { k: "totales", dims: [], mets: U },
      { k: "excluidos", dims: [], mets: U, sinFiltroCiudad: true, extra: { dimensionFilter: fCiudadIn } },
      { k: "nuevos_vs_recurrentes", dims: ["newVsReturning"], mets: U },
      { k: "canal", dims: ["sessionDefaultChannelGroup"], mets: U },
      { k: "fuente_sitio", dims: ["customEvent:fuente"], mets: U },
      { k: "pagina", dims: ["pagePath"], mets: U },
      { k: "eventos", dims: ["eventName"], mets: EV, extra: fEv },
      { k: "eventos_por_capitulo", dims: ["eventName", "customEvent:capitulo"], mets: EV, extra: fEv },
      { k: "eventos_por_fuente", dims: ["eventName", "customEvent:fuente"], mets: EV, extra: fEv },
      { k: "progreso_pct", dims: ["customEvent:capitulo", "customEvent:pct"], mets: EV,
        extra: { dimensionFilter: fEvento("progreso") } },
      { k: "dispositivo", dims: ["deviceCategory", "operatingSystem"], mets: U },
      { k: "ciudad", dims: ["city"], mets: U, limit: 15 },
      { k: "edad", dims: ["userAgeBracket"], mets: U },
      { k: "sexo", dims: ["userGender"], mets: U },
    ];
    if (k !== "ayer") {
      const orden = { orderBys: [{ dimension: { dimensionName: "date" } }] };
      specs.push({ k: "por_dia", dims: ["date"], mets: U, limit: 120, extra: orden });
      specs.push({ k: "por_dia_play", dims: ["date"], mets: ["totalUsers"], limit: 120,
        extra: { ...orden, dimensionFilter: fEvento("play") } });
    }
    const res = await lote(env, token, rango, specs);
    for (const t of ["totales", "excluidos"])
      res[t] = Array.isArray(res[t]) ? (res[t][0] || { totalUsers: 0, newUsers: 0, sessions: 0 }) : res[t];
    if (Array.isArray(res.por_dia)) {
      const play = Array.isArray(res.por_dia_play) ? Object.fromEntries(res.por_dia_play.map(f => [f.date, f.totalUsers])) : null;
      res.por_dia = res.por_dia.map(f => ({ ...f, play: play ? (play[f.date] || 0) : null }));
    }
    delete res.por_dia_play;
    out[k] = { rango, ...res };
  }
  const cuerpo = JSON.stringify(out, null, 1);
  const meta = { httpMetadata: { contentType: "application/json; charset=utf-8", cacheControl: "public, max-age=300" } };
  await env.AUDIO.put("datos/informe.json", cuerpo, meta);
  await env.AUDIO.put(`datos/informe-${ayer}.json`, cuerpo, meta);
  // Copia en texto plano: algunos lectores automáticos no aceptan application/json.
  await env.AUDIO.put("datos/informe.txt", cuerpo, { httpMetadata: { contentType: "text/plain; charset=utf-8", cacheControl: "public, max-age=300" } });
  return out;
}

// ===== Search Console =====
const norm = s => String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();
function rutaDe(u) {
  let p;
  try { p = new URL(u, SITIO).pathname; } catch { p = String(u); }
  if (p.length > 1) p = p.replace(/\/+$/, "");
  return p || "/";
}
function clusterDe(p) {
  if (/^\/cap-\d+/.test(p)) return "capitulos";
  if (/^\/$/.test(p) || /^\/capitulos\/?$/.test(p) || /^\/donde-escuchar/.test(p) || /^\/la-hora-de-la-fortuna/.test(p)) return "escuchar";
  if (/^\/radionovelas-colombianas/.test(p) || /^\/radionovelas\//.test(p) || /^\/radionovelas-antiguas/.test(p) || /^\/radionovelas$/.test(p)) return "colombianas";
  if (/^\/leyendas/.test(p) || /^\/cuentos/.test(p) || /^\/radionovelas-para-dormir/.test(p)) return "expansion";
  return "otros";
}
const CLUSTERS = ["capitulos", "escuchar", "colombianas", "expansion", "otros"];
async function scConsulta(token, startDate, endDate, dimensions) {
  const r = await fetch(SC_URL, { method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ startDate, endDate, dimensions, rowLimit: 25000, startRow: 0, type: "web", dataState: "final",
      dimensionFilterGroups: [{ filters: [{ dimension: "country", operator: "equals", expression: "col" }] }] }) });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).replace(/\s+/g, " ").slice(0, 200)}`);
  return (await r.json()).rows || [];
}
// Suma filas de Search Console: impresiones y clics; posición media ponderada por impresiones.
function agregar(rows) {
  let imp = 0, cli = 0, pp = 0;
  for (const r of rows) { imp += r.impressions; cli += r.clicks; pp += r.position * r.impressions; }
  return { impresiones: imp, clics: cli, ctr: imp ? Math.round(cli / imp * 1e4) / 1e4 : 0,
    posicion: imp ? Math.round(pp / imp * 10) / 10 : null };
}
const dentro = (d, p) => d >= p.desde && d <= p.hasta;

async function leerSitemap(url, profundidad = 0) {
  const r = await fetch(url, { headers: { "cache-control": "no-cache" } });
  if (!r.ok) throw new Error(`${r.status} sitemap ${url}`);
  const xml = await r.text();
  if (/<sitemapindex/i.test(xml) && profundidad < 2) {
    const hijos = [...xml.matchAll(/<sitemap>[\s\S]*?<loc>\s*([^<\s]+)\s*<\/loc>[\s\S]*?<\/sitemap>/gi)].map(m => m[1]);
    const todas = [];
    for (const h of hijos) todas.push(...await leerSitemap(h, profundidad + 1));
    return todas;
  }
  return [...xml.matchAll(/<url>([\s\S]*?)<\/url>/gi)].map(m => {
    const loc = (m[1].match(/<loc>\s*([^<\s]+)\s*<\/loc>/i) || [])[1];
    const lastmod = (m[1].match(/<lastmod>\s*([^<\s]+)\s*<\/lastmod>/i) || [])[1];
    return { ruta: rutaDe(loc), publicado: lastmod ? lastmod.slice(0, 10) : null };
  }).filter(u => u.ruta);
}

async function generarSeo(env, token) {
  const out = { generado: new Date().toISOString(), hoy: hace(0), datos_hasta: null, propiedad_sc: SC_PROPIEDAD, pais: "col",
    estado: { search_console: "ok", sitemap: "ok", ga4: "ok" }, periodos: {}, keywords: [], agregado_radionovela: {},
    por_dia: [], paginas: [], indexadas: { sitemap_total: null, con_impresiones_28: null, sin_impresiones: [] },
    embudo_organico: {}, posts: [], top_consultas: [],
    nota: "Search Console entrega datos con 2 a 3 días de retraso; datos_hasta es el último día con filas. " +
          "Keywords: coincidencia exacta sin mayúsculas ni tildes, país Colombia. " +
          "Embudo orgánico: fuente = seo, sin ciudades de centros de datos." };

  // --- Search Console ---
  let scOk = false, porPaginaD28 = null;
  try {
    const ultimos = await scConsulta(token, hace(10), hace(1), ["date"]);
    const H = ultimos.map(r => r.keys[0]).sort().pop() || null;
    out.datos_hasta = H;
    scOk = true;
    if (H) {
      const P = {
        d7: { desde: sumar(H, -6), hasta: H }, d7_prev: { desde: sumar(H, -13), hasta: sumar(H, -7) },
        d28: { desde: sumar(H, -27), hasta: H }, d28_prev: { desde: sumar(H, -55), hasta: sumar(H, -28) },
      };
      out.periodos = P;
      const desde = mayor(SC_DESDE_MIN, sumar(H, -89));
      const serie = await scConsulta(token, desde, H, ["date"]);
      const serPag = await scConsulta(token, desde, H, ["date", "page"]);
      const QP = {};
      for (const [k, p] of Object.entries(P)) QP[k] = await scConsulta(token, p.desde, p.hasta, ["query", "page"]);

      // Serie diaria total y por cluster.
      const clusPorDia = {};
      for (const r of serPag) {
        const d = r.keys[0], c = clusterDe(rutaDe(r.keys[1]));
        clusPorDia[d] = clusPorDia[d] || Object.fromEntries(CLUSTERS.map(x => [x, { impresiones: 0, clics: 0 }]));
        clusPorDia[d][c].impresiones += r.impressions; clusPorDia[d][c].clics += r.clicks;
      }
      out.por_dia = serie.map(r => ({ date: r.keys[0], impresiones: r.impressions, clics: r.clicks,
        posicion: r.impressions ? Math.round(r.position * 10) / 10 : null,
        clusters: clusPorDia[r.keys[0]] || Object.fromEntries(CLUSTERS.map(x => [x, { impresiones: 0, clics: 0 }])) }))
        .sort((a, b) => a.date.localeCompare(b.date));

      // Páginas (28 días).
      const pag = {};
      for (const r of serPag) {
        if (!dentro(r.keys[0], P.d28)) continue;
        const p = rutaDe(r.keys[1]);
        (pag[p] = pag[p] || []).push(r);
      }
      porPaginaD28 = Object.fromEntries(Object.entries(pag).map(([p, rows]) => [p, agregar(rows)]));
      out.paginas = Object.entries(porPaginaD28).map(([url, a]) => ({ url, impresiones: a.impresiones, clics: a.clics,
        posicion: a.posicion, cluster: clusterDe(url) })).sort((a, b) => b.impresiones - a.impresiones);

      // Keywords fijas.
      const paginaTop = rows => {
        const m = {};
        for (const r of rows) { const p = rutaDe(r.keys[1]); m[p] = (m[p] || 0) + r.impressions; }
        const e = Object.entries(m).sort((a, b) => b[1] - a[1])[0];
        return e && e[1] > 0 ? e[0] : null;
      };
      for (const [cluster, lista] of Object.entries(KEYWORDS)) for (const keyword of lista) {
        const nk = norm(keyword);
        const fila = { cluster, keyword, pagina: null };
        const sel = {};
        for (const k of Object.keys(P)) { sel[k] = QP[k].filter(r => norm(r.keys[0]) === nk); fila[k] = agregar(sel[k]); }
        fila.pagina = paginaTop(sel.d28) || paginaTop(sel.d7);
        out.keywords.push(fila);
      }

      // Agregado "radionovela".
      const esRadionovela = q => { const n = norm(q); return n.includes("radionovela") || n.includes("radio novela"); };
      const ag = { consultas: new Set(QP.d28.filter(r => esRadionovela(r.keys[0])).map(r => norm(r.keys[0]))).size };
      for (const k of Object.keys(P)) ag[k] = agregar(QP[k].filter(r => esRadionovela(r.keys[0])));
      out.agregado_radionovela = ag;

      // Top 20 consultas (28 días).
      const porQ = {};
      for (const r of QP.d28) (porQ[r.keys[0]] = porQ[r.keys[0]] || []).push(r);
      out.top_consultas = Object.entries(porQ).map(([query, rows]) => {
        const a = agregar(rows); return { query, impresiones: a.impresiones, clics: a.clics, posicion: a.posicion };
      }).sort((a, b) => b.impresiones - a.impresiones).slice(0, 20);
    }
  } catch (e) {
    out.estado.search_console = { error: String(e.message || e).slice(0, 220) };
    out.datos_hasta = null; out.periodos = {}; out.keywords = []; out.agregado_radionovela = {};
    out.por_dia = []; out.paginas = []; out.top_consultas = [];
  }

  // --- Sitemap ---
  let urls = [];
  try {
    urls = await leerSitemap(`${SITIO}/sitemap.xml`);
    const vistas = new Map(); for (const u of urls) if (!vistas.has(u.ruta)) vistas.set(u.ruta, u);
    urls = [...vistas.values()];
    out.indexadas.sitemap_total = urls.length;
    if (scOk && porPaginaD28) {
      const sin = urls.filter(u => !(porPaginaD28[u.ruta]?.impresiones > 0)).map(u => u.ruta);
      out.indexadas.con_impresiones_28 = urls.length - sin.length;
      out.indexadas.sin_impresiones = sin;
    } else if (scOk) {
      // Search Console sin datos aún: ninguna URL tiene impresiones.
      out.indexadas.con_impresiones_28 = 0;
      out.indexadas.sin_impresiones = urls.map(u => u.ruta);
    }
  } catch (e) {
    out.estado.sitemap = { error: String(e.message || e).slice(0, 220) };
  }

  // --- GA4: embudo orgánico y posts ---
  try {
    const ayer = hace(1);
    const PE = {
      d7: { desde: hace(7), hasta: ayer }, d7_prev: { desde: hace(14), hasta: hace(8) },
      acumulado: { desde: env.ESTRENO <= ayer ? env.ESTRENO : EMBUDO_DESDE, hasta: ayer },
    };
    const fSeo = { filter: { fieldName: "customEvent:fuente", stringFilter: { value: "seo" } } };
    const fCap1 = { filter: { fieldName: "pagePath", stringFilter: { matchType: "FULL_REGEXP", value: "/cap-1(\\.html)?/?" } } };
    const EVS = ["play", "complete", "share_click"];
    const rangoDe = p => ({ startDate: p.desde, endDate: p.hasta });
    const specs = [];
    const add = (k, rango, dims) => {
      specs.push({ k: `${k}_u`, rango, dims, mets: ["totalUsers"], limit: 200, extra: { dimensionFilter: fSeo } });
      specs.push({ k: `${k}_ev`, rango, dims: [...dims, "eventName"], mets: ["totalUsers"], limit: 600,
        extra: { dimensionFilter: y(fSeo, fEventos(EVS)) } });
      specs.push({ k: `${k}_c1`, rango, dims, mets: ["totalUsers"], limit: 200,
        extra: { dimensionFilter: y(fSeo, fEvento("play"), fCap1) } });
    };
    for (const [k, p] of Object.entries(PE)) add(k, rangoDe(p), []);
    add("dia", { startDate: EMBUDO_DESDE, endDate: ayer }, ["date"]);
    const posts = urls.filter(u => !/^\/cap-\d+/.test(u.ruta) && u.ruta !== "/" && u.ruta !== "/capitulos");
    if (posts.length) specs.push({ k: "posts", rango: { startDate: hace(28), endDate: ayer }, limit: 500,
      dims: ["pagePath", "pageTitle", "customEvent:cluster", "customEvent:keyword"], mets: ["totalUsers"],
      extra: { dimensionFilter: { filter: { fieldName: "pagePath", inListFilter: { values: posts.flatMap(p => [p.ruta, p.ruta + ".html", p.ruta + "/"]) } } } } });
    const res = await lote(env, token, null, specs);
    const err = Object.entries(res).find(([k, v]) => k !== "posts" && v && v.error);
    if (err) throw new Error(err[1].error);

    const usuarios = rows => rows.reduce((s, r) => s + r.totalUsers, 0);
    const resumen = (u, ev, c1) => {
      const e = Object.fromEntries(EVS.map(n => [n, ev.filter(r => r.eventName === n).reduce((s, r) => s + r.totalUsers, 0)]));
      return { usuarios: usuarios(u), play: e.play, play_cap1: usuarios(c1), complete: e.complete, share_click: e.share_click };
    };
    const emb = { periodos: PE };
    for (const k of Object.keys(PE)) emb[k] = resumen(res[`${k}_u`], res[`${k}_ev`], res[`${k}_c1`]);
    const dias = new Set([...res.dia_u, ...res.dia_ev, ...res.dia_c1].map(r => r.date));
    emb.por_dia = [...dias].sort().map(d => ({ date: `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`,
      ...resumen(res.dia_u.filter(r => r.date === d), res.dia_ev.filter(r => r.date === d), res.dia_c1.filter(r => r.date === d)) }));
    out.embudo_organico = emb;

    // Posts: título, cluster y keyword desde GA4 (null si no hay datos o la consulta falla).
    const ga = Array.isArray(res.posts) ? res.posts : [];
    const limpio = v => (v && v !== "(not set)" && v !== "(other)" ? v : null);
    out.posts = posts.map(p => {
      const filasP = ga.filter(r => rutaDe(r.pagePath.replace(/\.html$/, "")) === p.ruta).sort((a, b) => b.totalUsers - a.totalUsers);
      const primero = campo => limpio((filasP.find(r => limpio(r[campo])) || {})[campo]);
      const sc = scOk && porPaginaD28 ? (porPaginaD28[p.ruta] || { impresiones: 0, clics: 0, posicion: null }) : null;
      return { url: p.ruta, titulo: primero("pageTitle"), publicado: p.publicado,
        cluster: primero("customEvent:cluster"), keyword: primero("customEvent:keyword"),
        impresiones_28: sc ? sc.impresiones : (scOk ? 0 : null), clics_28: sc ? sc.clics : (scOk ? 0 : null),
        posicion_28: sc ? sc.posicion : null };
    });
  } catch (e) {
    out.estado.ga4 = { error: String(e.message || e).slice(0, 220) };
    out.embudo_organico = {};
    out.posts = [];
  }

  await env.AUDIO.put("datos/seo.txt", JSON.stringify(out, null, 1),
    { httpMetadata: { contentType: "text/plain; charset=utf-8", cacheControl: "no-store" } });
  return out;
}

// ===== Orquestación =====
async function generarTodo(env) {
  const token = await tokenGoogle(JSON.parse(env.GA_SA_KEY));
  const r = {};
  try { const o = await generarInforme(env, token); r.informe = { ok: true, fecha: o.fecha_informe }; }
  catch (e) { r.informe = { ok: false, error: String(e).slice(0, 300) }; }
  try { const s = await generarSeo(env, token); r.seo = { ok: true, estado: s.estado, datos_hasta: s.datos_hasta }; }
  catch (e) { r.seo = { ok: false, error: String(e).slice(0, 300) }; }
  return r;
}

export default {
  async scheduled(ev, env, ctx) { ctx.waitUntil(generarTodo(env)); },
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.pathname === "/generar" && env.CLAVE_MANUAL && u.searchParams.get("clave") === env.CLAVE_MANUAL) {
      try { return Response.json(await generarTodo(env)); }
      catch (e) { return Response.json({ ok: false, error: String(e) }, { status: 500 }); }
    }
    return new Response("ga4-informe", { status: 200 });
  }
};
