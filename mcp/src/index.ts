import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";

interface Env {
  API_BASE: string;
  ASK_URL: string;
  MCP_HOSTS?: string;
  // Service binding to the site Worker. A Worker's own fetch to a URL in its zone skips
  // the zone's routes and goes to the origin, which answers 522; the binding reaches the
  // site Worker directly.
  SITE?: Fetcher;
}

const UA = "motdang-mcp/0.1 (+https://motdang.net/api/)";
const ATTRIBUTION = "มดแดง Mot Dang · motdang.net";
const TERMS = "https://motdang.net/terms.html";
const MAX_LIMIT = 25;

// Fields kept from a list row. The API row carries more; these are the ones a
// client needs to answer a question and link to the page.
const ROW_KEYS = [
  "id", "name", "nameEn", "categories", "subcategories", "lat", "lng",
  "geoPrecision", "hours", "openNow", "distanceM", "antRank", "confidence",
  "osmDerived", "updatedAt", "url",
] as const;

// Fields dropped from a full record: build-time bookkeeping, not facts.
const RECORD_DROP = new Set([
  "tagVia", "provenance", "enrichedFields", "antBits", "bearings", "sched",
  "held", "asked", "featured", "landmark", "names",
]);

class UpstreamError extends Error {}

async function getJson(env: Env, url: string): Promise<any> {
  const init = {
    headers: { "User-Agent": UA, Accept: "application/json" },
    signal: AbortSignal.timeout(15_000),
  };
  const viaSite = !!env.SITE && new URL(url).hostname === "motdang.net";
  const cache = (globalThis as any).caches?.default as Cache | undefined;
  let res: Response | undefined;
  try {
    if (viaSite) {
      res = await cache?.match(url);
      if (!res) {
        res = await env.SITE!.fetch(url, init);
        if (res.ok && cache) {
          const keep = new Response(res.clone().body, res);
          keep.headers.set("cache-control", "public, max-age=300");
          await cache.put(url, keep);
        }
      }
    } else {
      res = await fetch(url, { ...init, cf: { cacheTtl: 300, cacheEverything: true } } as RequestInit);
    }
  } catch (e) {
    throw new UpstreamError(`motdang.net did not answer (${(e as Error).message})`);
  }
  if (res.status === 404) throw new UpstreamError("not found");
  if (!res.ok) throw new UpstreamError(`motdang.net answered ${res.status}`);
  return res.json();
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "" || v === false) continue;
    u.set(k, v === true ? "1" : String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

function pick(row: Record<string, unknown>, keys: readonly string[]) {
  const out: Record<string, unknown> = {};
  for (const k of keys) if (row[k] !== undefined && row[k] !== null) out[k] = row[k];
  return out;
}

function ok(payload: Record<string, unknown>) {
  const body = { ...payload, attribution: ATTRIBUTION, terms: TERMS };
  return {
    content: [{ type: "text" as const, text: JSON.stringify(body, null, 1) }],
    structuredContent: body,
  };
}

function fail(message: string) {
  return { isError: true, content: [{ type: "text" as const, text: message }] };
}

async function run(fn: () => Promise<Record<string, unknown>>) {
  try {
    return ok(await fn());
  } catch (e) {
    return fail(e instanceof UpstreamError ? e.message : `error: ${(e as Error).message}`);
  }
}

function listPayload(res: any, note?: string) {
  return {
    total: res.total,
    count: res.count,
    generated: res.generated,
    ...(note ? { note } : {}),
    results: (res.results ?? []).map((r: any) => pick(r, ROW_KEYS)),
  };
}

const limitSchema = z.number().int().min(1).max(MAX_LIMIT).optional()
  .describe(`Rows to return, 1 to ${MAX_LIMIT}. Default 10.`);
const categorySchema = z.string().max(80).optional()
  .describe("Category key, for example food, wat, hotel, massage, medical. list_categories returns the keys. Comma-separate several to match any.");
const provinceSchema = z.enum(["cm", "cr"]).optional()
  .describe("cm for Chiang Mai, cr for Chiang Rai.");
const latSchema = z.number().min(-90).max(90).describe("Latitude in degrees.");
const lngSchema = z.number().min(-180).max(180).describe("Longitude in degrees.");
const radiusSchema = z.number().int().min(50).max(50_000).optional()
  .describe("Metres from the point. Default 2000, maximum 50000.");

const READ_ONLY = { readOnlyHint: true, idempotentHint: true, openWorldHint: true } as const;

function createServer(env: Env) {
  const server = new McpServer({ name: "motdang", version: "0.1.0" });

  server.registerTool(
    "search_places",
    {
      title: "Search places by words",
      description:
        "Search the Mot Dang directory of Chiang Mai and Chiang Rai places by letters in a name, address, category or tag, in Thai or English. Terms are ANDed. Optional category, province and open-now filters. For a question described by meaning rather than by words, use semantic_search.",
      inputSchema: z.object({
        query: z.string().min(1).max(200).describe("Words to match, Thai or English."),
        category: categorySchema,
        province: provinceSchema,
        open_now: z.boolean().optional().describe("Only places open at this moment in Asia/Bangkok. A place with no known hours is left out, not called closed."),
        limit: limitSchema,
      }),
      annotations: READ_ONLY,
    },
    ({ query, category, province, open_now, limit }) =>
      run(async () => {
        const url = `${env.API_BASE}/places${qs({ q: query, cat: category, province, open_now, limit: limit ?? 10 })}`;
        return listPayload(await getJson(env, url));
      }),
  );

  server.registerTool(
    "semantic_search",
    {
      title: "Search places by meaning",
      description:
        "Search by meaning, in Thai or English, for example 'quiet cafe to work from' or 'where to get a tok sen massage'. Returns places ranked by similarity score with a text excerpt. Each row carries a slug; get_place takes it for the full record.",
      inputSchema: z.object({
        query: z.string().min(1).max(300).describe("A question or description."),
        limit: z.number().int().min(1).max(20).optional().describe("Rows to return, 1 to 20. Default 8."),
      }),
      annotations: READ_ONLY,
    },
    ({ query, limit }) =>
      run(async () => {
        const res = await getJson(env, `${env.ASK_URL}${qs({ q: query, n: limit ?? 8 })}`);
        return {
          count: res.count,
          results: (res.results ?? []).map((r: any) => ({
            slug: typeof r.url === "string" ? r.url.replace(/^.*\//, "").replace(/\.html$/, "") : undefined,
            title: r.title,
            url: r.url,
            score: r.score,
            excerpt: typeof r.text === "string" ? r.text.slice(0, 400) : undefined,
          })),
          note: "get_place takes the slug of a row.",
        };
      }),
  );

  server.registerTool(
    "get_place",
    {
      title: "Get one place",
      description:
        "Return every field Mot Dang holds for one place: names, address, phone, website, hours, tags, channels, sources with their licences, confidence. Takes a place id (for example cm-osm-way-307890044) or the slug at the end of the page address.",
      inputSchema: z.object({
        id: z.string().min(1).max(200).describe("Place id or page slug."),
      }),
      annotations: READ_ONLY,
    },
    ({ id }) =>
      run(async () => {
        const rec = await getJson(env, `${env.API_BASE}/places/${encodeURIComponent(id)}`);
        const out: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(rec)) if (!RECORD_DROP.has(k) && v !== null) out[k] = v;
        const osm = (rec.sources ?? []).some((s: any) => s?.type === "osm");
        out.osmDerived = osm;
        out.licenceNote = osm
          ? "Contains OpenStreetMap-derived fields (ODbL 1.0, © OpenStreetMap contributors). Each source entry names its own licence where one is stated."
          : "Each source entry names its own licence where one is stated. Compilation terms at the terms address.";
        return { place: out };
      }),
  );

  server.registerTool(
    "near",
    {
      title: "Places near a point",
      description:
        "Places within a radius of a latitude and longitude, nearest first, each with distanceM. A place with no pin is left out of the answer, not placed.",
      inputSchema: z.object({
        lat: latSchema,
        lng: lngSchema,
        radius_m: radiusSchema,
        category: categorySchema,
        query: z.string().max(200).optional().describe("Optional words to narrow by."),
        open_now: z.boolean().optional().describe("Only places open now."),
        limit: limitSchema,
      }),
      annotations: READ_ONLY,
    },
    ({ lat, lng, radius_m, category, query, open_now, limit }) =>
      run(async () => {
        const url = `${env.API_BASE}/places${qs({
          near: `${lat},${lng}`, radius: radius_m ?? 2000, cat: category, q: query,
          open_now, sort: "distance", limit: limit ?? 10,
        })}`;
        return listPayload(await getJson(env, url));
      }),
  );

  server.registerTool(
    "open_now",
    {
      title: "Places open now",
      description:
        "Places open at this moment in Asia/Bangkok, or at a stated time such as 'Sa 18:30'. Give lat and lng to sort by distance. Roughly 4,400 places carry hours; a place without hours is absent here, which does not mean it is shut.",
      inputSchema: z.object({
        lat: latSchema.optional(),
        lng: lngSchema.optional(),
        radius_m: radiusSchema,
        category: categorySchema,
        province: provinceSchema,
        query: z.string().max(200).optional().describe("Optional words to narrow by."),
        at: z.string().max(20).optional().describe("A time instead of now, for example 'Sa 18:30' (Mo Tu We Th Fr Sa Su, 24-hour clock)."),
        limit: limitSchema,
      }),
      annotations: READ_ONLY,
    },
    ({ lat, lng, radius_m, category, province, query, at, limit }) =>
      run(async () => {
        if ((lat === undefined) !== (lng === undefined)) throw new UpstreamError("give both lat and lng, or neither");
        const near = lat !== undefined && lng !== undefined;
        const url = `${env.API_BASE}/places${qs({
          open_now: at ? undefined : true, open_at: at,
          near: near ? `${lat},${lng}` : undefined, radius: near ? (radius_m ?? 2000) : undefined,
          cat: category, province, q: query, limit: limit ?? 10,
        })}`;
        return listPayload(await getJson(env, url));
      }),
  );

  server.registerTool(
    "list_categories",
    {
      title: "List categories",
      description: "The category tree: keys, Thai and English names, subcategories and place counts. The keys feed the category argument of the other tools.",
      inputSchema: z.object({}),
      annotations: READ_ONLY,
    },
    () =>
      run(async () => {
        const res = await getJson(env, `${env.API_BASE}/categories`);
        return {
          generated: res.generated,
          provinces: res.provinces,
          categories: (res.categories ?? []).map((c: any) => pick(c, ["key", "th", "en", "subcategories", "count"])),
        };
      }),
  );

  return server;
}

const INFO = `Mot Dang MCP server (มดแดง), Chiang Mai and Chiang Rai places.

Endpoint: POST /mcp (streamable HTTP)
Tools: search_places, semantic_search, get_place, near, open_now, list_categories
Reads: ${"https://motdang.net/api/v1/"} and https://ask.motdang.net/api/search
Terms: ${TERMS}
`;

export default {
  fetch(request: Request, env: Env, ctx: ExecutionContext) {
    const url = new URL(request.url);
    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response(url.pathname === "/health" ? "ok\n" : INFO, {
        headers: { "content-type": "text/plain; charset=utf-8", "access-control-allow-origin": "*" },
      });
    }
    // MCP_HOSTS: comma-separated hostnames served on a custom domain.
    const hosts = (env.MCP_HOSTS ?? "").split(",").map((h) => h.trim()).filter(Boolean);
    return createMcpHandler(() => createServer(env), {
      route: "/mcp",
      ...(hosts.length ? { allowedHostnames: hosts, allowedOriginHostnames: "*" as const } : {}),
    })(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
