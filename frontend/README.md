# Personal AI Assistant Frontend

Next.js frontend for the FastAPI backend in the parent directory.

## Local development

The repository-level Compose stack starts the database, API, and this frontend in
one command, with source mounts and HMR enabled:

```bash
cp .env.example .env
docker compose up -d --build
```

默认 Compose 栈会一并启动 API、前端、PostgreSQL 和 Phoenix；API/前端源码都支持
热更新，不需要另加 `--profile` 或单独启动 Phoenix。生产环境再在 `.env` 中设置
`RELOAD=0` 关闭 API 热重载。

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
- `/settings` — profile/avatar, appearance, models, tools, skills, reminders, voice, runtime status, and advanced diagnostics

Global search is available on every page with the search button or `Cmd/Ctrl + K`.
Conversation results open the matching conversation and jump to the matched message;
memory results open the matching file.

## Internationalization

The UI uses the app-level `I18nProvider` and typed translation keys. Chinese is
the default locale and the language selector in the workspace top bar persists
the selected locale with the other browser preferences.

- Add keys to `lib/locales/zh-CN.ts` first; this file defines the `TranslationKey` type.
- Add the matching English strings to `lib/locales/en-US.ts`; TypeScript rejects
  missing translations.
- Use `const { t } = useI18n()` in client components and render `t("area.key")`.
- Use named placeholders such as `{count}` and pass values as the second argument.

Keep API field names, tool names, and user-authored content locale-neutral. Only
user-facing interface copy belongs in the message dictionaries.

The settings page stores the profile name/avatar, appearance, locale, and chat
interaction preferences in the current browser. The theme can follow the system
appearance or be fixed to light/dark. Backend runtime settings (model routing,
assistant instructions, tools, skills, consolidation, reminders, and TTS/ASR) are
edited on the same page: the form is rendered from the `fields` returned by
`GET /api/settings` and saved with `PATCH /api/settings`, which takes effect
immediately without a restart. Environment-only fields are shown as read-only
deployment status. TTS/ASR service probes are deferred until the voice section is
opened so unrelated settings do not wait on an optional local service.

Memory version restore uses `POST /api/memories/restore` with a `version_id`.
`PATCH /api/conversations/{id}` still supports the three-state per-conversation
thinking setting. Both home and conversation composers expose a capability-aware
thinking control; adjustable effort values come from the selected model profile
rather than model-name checks and are remembered per profile in browser preferences.

Validation commands:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```
