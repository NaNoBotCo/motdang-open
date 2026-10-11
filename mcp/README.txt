Mot Dang MCP server

A Cloudflare Worker, stateless, built on the Agents SDK handler (agents/mcp/server) and the MCP SDK v2 (@modelcontextprotocol/server). It answers POST /mcp. GET / and GET /health answer in plain text.

Tools
  search_places     words in Thai or English; optional category, province, open_now, limit
  semantic_search   a question described by meaning (calls ask.motdang.net)
  get_place         one place by id or page slug; every field, sources with their licences
  near              lat, lng, radius_m (50 to 50000), optional category, query, open_now
  open_now          open now or at a time such as "Sa 18:30"; lat and lng optional
  list_categories   the category tree with keys and counts

Every answer carries an attribution line and the terms address. Rows carry osmDerived and confidence. A place with unknown hours is left out of open_now answers; openNow null means unknown.

Reads
  API_BASE  https://motdang.net/api/v1
  ASK_URL   https://ask.motdang.net/api/search (ai.motdang.net answers the same path)
Both are variables in wrangler.jsonc. Upstream answers are cached at the edge for 300 seconds. Requests carry a User-Agent naming motdang-mcp and the API address, as the terms ask.

Run locally
  npm install
  npx wrangler dev --port 8787
  node test/client.mjs [url]      14 checks through a real MCP client, expects all to pass
  npx tsc -p .                    type check

Connect a client to the local server
  claude mcp add --transport http motdang http://localhost:8787/mcp
  Cursor, .cursor/mcp.json: {"mcpServers": {"motdang": {"url": "http://localhost:8787/mcp"}}}

Deploy
  Live at https://motdang.net/mcp since 2026-10-11 (Worker motdang-mcp, route motdang.net/mcp*, zone motdang.net). Also on https://motdang-mcp.nanobotco.workers.dev, which answers / and /health only: MCP_HOSTS limits /mcp to motdang.net.
  npx wrangler deploy
The route needs no DNS change. The site Worker (mot-dang-site) owns motdang.net/*; a route with a longer pattern wins, as motdang.net/sala* and motdang.net/anthill* do. A Worker's own fetch to a URL in its zone skips the zone's routes and reaches the origin, which answers 522, so wrangler.jsonc binds SITE to mot-dang-site and the tools read the API through the binding. The deploy token in keys.json lacks Workers Routes; the wrangler OAuth login (workers_routes write) deploys the route: env -u CLOUDFLARE_API_TOKEN npx wrangler deploy.
Roll back: npx wrangler rollback (previous version), or npx wrangler rollback <version-id>. Remove the route: delete the routes entry in wrangler.jsonc and deploy.

Files
  src/index.ts      the Worker
  test/client.mjs   the test client
  llms-section.txt  paragraph for docs/llms.txt
