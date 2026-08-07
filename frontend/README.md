# Personal AI Assistant Frontend

Next.js frontend for the FastAPI backend in the parent directory.

## Local development

The repository-level Compose stack starts the database, API, and this frontend in
one command, with source mounts and HMR enabled:

```bash
cp .env.example .env
docker compose up -d --build
```

Open <http://localhost:13000> with the example configuration. The host ports are
configured by `FRONTEND_PORT` and `API_PORT` in the repository-level `.env`.
Browser API calls use the same-origin `/backend` path; the frontend container
proxies them to the Compose service address `http://api:8000`.

Use `docker compose logs -f frontend` to follow
compiler output. The Compose service enables polling so file changes are detected
reliably through Docker Desktop bind mounts.

To run the frontend directly on the host instead:

```bash
cp .env.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>. The backend should be running at
<http://localhost:8000> and its CORS configuration should include
`http://localhost:3000`.

`npm run dev` uses Next.js development mode and watches frontend source files.
After a `.tsx`, `.ts` or `.css` change, it recompiles automatically and refreshes
the browser through HMR. Keep this process running while developing. `npm run start`
is production mode and does not watch source files; rebuild with `npm run build`
before using it.

Development output is stored in `.next-dev`, while `npm run build` and
`npm run start` use `.next-build`. The separation means validation builds can run
without overwriting the cache of a running development server.

Available pages:

- `/` — conversations and streaming chat
- `/memories` — memory files, Markdown editor, versions and diff
- `/review` — daily conversations, memory changes and manual consolidation
- `/timeline` — extracted and manually entered time-based items in today, upcoming, and month views
- `/settings` — runtime model status, browser-side chat preferences, and light/dark/system appearance

Global search is available on every page with the search button or `Cmd/Ctrl + K`.
Conversation results open the matching conversation and jump to the matched message;
memory results open the matching file.

The settings page stores appearance and chat interaction preferences in the current
browser only. The theme can follow the macOS/system appearance or be fixed to
light/dark. Backend runtime settings (provider, model, thinking, consolidation) are
edited on the same page: the form is rendered from the `fields` returned by
`GET /api/settings` and saved with `PATCH /api/settings`, which takes effect
immediately without a restart. Fields listed in `env_only` are not shown yet.

Memory version restore uses `POST /api/memories/restore` with a `version_id`.
`PATCH /api/conversations/{id}` still supports the three-state per-conversation
thinking setting, but no page currently exposes it.

Validation commands:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```
