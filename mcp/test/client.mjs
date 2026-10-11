// Calls every tool of a running server through a real MCP client.
// Usage: node test/client.mjs [http://localhost:8787/mcp]
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";

const url = process.argv[2] ?? "http://localhost:8787/mcp";
const client = new Client({ name: "motdang-mcp-test", version: "0.0.0" });
await client.connect(new StreamableHTTPClientTransport(new URL(url)));

let failed = 0;
function check(name, cond, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"} ${name}${detail ? "  " + detail : ""}`);
  if (!cond) failed++;
}
const data = (r) => r.structuredContent ?? JSON.parse(r.content[0].text);

const tools = (await client.listTools()).tools.map((t) => t.name).sort();
check("tools/list", tools.join() === "get_place,list_categories,near,open_now,search_places,semantic_search", tools.join());

let r = await client.callTool({ name: "search_places", arguments: { query: "ก๋วยเตี๋ยว", limit: 3 } });
let d = data(r);
check("search_places Thai", !r.isError && d.results.length === 3 && d.total > 3, `total=${d.total} first=${d.results[0]?.name}`);

r = await client.callTool({ name: "search_places", arguments: { query: "massage", category: "massage", province: "cm", limit: 2 } });
d = data(r);
check("search_places filters", !r.isError && d.results.length === 2, `total=${d.total}`);

r = await client.callTool({ name: "semantic_search", arguments: { query: "toksen", limit: 2 } });
d = data(r);
check("semantic_search", !r.isError && d.results.length > 0 && !!d.results[0].slug, d.results[0]?.title);
r = await client.callTool({ name: "get_place", arguments: { id: d.results[0].slug } });
check("get_place by semantic slug", !r.isError && data(r).place.lat !== undefined, d.results[0].slug);

const id = "cm-osm-way-307890044";
r = await client.callTool({ name: "get_place", arguments: { id } });
d = data(r);
check("get_place", !r.isError && d.place.id === id && d.place.osmDerived === true && !("tagVia" in d.place), d.place.name);

r = await client.callTool({ name: "get_place", arguments: { id: "no-such-place-zzz" } });
check("get_place missing -> isError", r.isError === true, r.content[0].text);

r = await client.callTool({ name: "near", arguments: { lat: 18.7883, lng: 98.9853, radius_m: 500, limit: 3 } });
d = data(r);
check("near sorted by distance", !r.isError && d.results.length === 3 &&
  d.results.every((x, i, a) => i === 0 || a[i - 1].distanceM <= x.distanceM), d.results.map((x) => x.distanceM).join(","));

r = await client.callTool({ name: "open_now", arguments: { lat: 18.7883, lng: 98.9853, radius_m: 1000, limit: 3 } });
d = data(r);
check("open_now near", !r.isError && d.results.every((x) => x.openNow === true), `total=${d.total}`);

r = await client.callTool({ name: "open_now", arguments: { category: "food", at: "Sa 18:30", limit: 2 } });
d = data(r);
check("open_now at time", !r.isError && d.results.length > 0, `total=${d.total}`);

r = await client.callTool({ name: "open_now", arguments: { lat: 18.78 } });
check("open_now lat without lng -> isError", r.isError === true, r.content[0].text);

r = await client.callTool({ name: "near", arguments: { lat: 123, lng: 98 } }).catch((e) => ({ isError: true, content: [{ text: String(e.message ?? e) }] }));
check("near bad lat rejected", r.isError === true, r.content[0].text.slice(0, 90));

r = await client.callTool({ name: "list_categories", arguments: {} });
d = data(r);
check("list_categories", !r.isError && d.categories.length > 20, `${d.categories.length} categories`);

check("attribution on every answer", !!d.attribution && d.terms.includes("terms.html"));

await client.close();
console.log(failed ? `${failed} failed` : "all passed");
process.exit(failed ? 1 : 0);
