# Personal AI Assistant Frontend

Next.js frontend for the FastAPI backend in the parent directory.

## Local development

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

Available pages:

- `/` — conversations and streaming chat
- `/memories` — memory files, Markdown editor, versions and diff
- `/review` — daily conversations, memory changes and manual consolidation
- `/settings` — runtime model status and browser-side chat preferences

Global search is available on every page with the search button or `Cmd/Ctrl + K`.
Conversation results open the matching conversation and jump to the matched message;
memory results open the matching file.

The chat page reads `/api/settings` for the runtime thinking capability and uses
`PATCH /api/conversations/{id}` for the three-state per-conversation thinking
setting. Memory version restore uses `POST /api/memories/restore` with a
`version_id`.

Validation commands:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```
