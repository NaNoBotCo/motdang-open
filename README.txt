motdang-open: two ways in to Mot Dang (https://motdang.net) for people who build things.

mcp/ is a Cloudflare Worker that speaks the Model Context Protocol over streamable HTTP, live at https://motdang.net/mcp. Claude, Cursor or any MCP client asks it about places. It calls the published Mot Dang API (https://motdang.net/api/v1/) and the semantic search (https://ask.motdang.net/api/search) and holds no data of its own. Six tools: search_places, semantic_search, get_place, near, open_now, list_categories. Connect Claude Code with: claude mcp add --transport http motdang https://motdang.net/mcp . Details in mcp/README.txt.

export/ reads the published places.json and api/v1/index.json and writes a monthly export: NDJSON, GeoJSON and Parquet, split into three files by licence, with a manifest, a LICENCES.txt and a changelog against the month before. The files for each month are attached to a release: https://github.com/NaNoBotCo/motdang-open/releases . The repository holds the code; the data sits in the releases. Details in export/README.txt.

LICENCES.txt says what each data file is and what each source needs. Site terms: https://motdang.net/terms.html
