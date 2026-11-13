# Atlas Agent Engine - Chatbot Starter App

A minimal chatbot UI that connects to the [Atlas Agent Engine](https://agentengine.mongodb.com). Built with Next.js, the Vercel AI SDK, and Tailwind CSS.

**Features:** Human-in-the-loop client — streaming chat plus a workspace-scoped review queue.

## Prerequisites

- Node.js 18+
- [pnpm](https://pnpm.io/installation)
- An Atlas Agent Engine workspace with a project service account

## Quick Start

1. **Install dependencies**

   ```bash
   pnpm install
   ```

2. **Configure environment variables**

   ```bash
   cp .env.example .env.local
   ```

   Fill in your values in `.env.local`. The endpoint URL identifies your
   workspace, so it embeds your project and workspace IDs:

   ```
   MONGODB_AGENTIC_API_URL=https://agentengine.mongodb.com/api/v1/projects/<your project id>/workspaces/<your workspace id>/invokeStream
   MONGODB_AGENTIC_CLIENT_ID=<your service account client ID>
   MONGODB_AGENTIC_CLIENT_SECRET=<your service account client secret>
   ```

   The service account needs the `AGENT_DEVELOPER` role to invoke the workspace
   and resume suspended executions. You can create it with the Agent Engine CLI:

   ```bash
   agentengine projects list        # project ID
   agentengine workspace list       # workspace ID
   agentengine service-account create chatbot-client \
     --project-id <project id> --role AGENT_DEVELOPER
   ```

   Save the client secret when the command returns it; it cannot be retrieved
   later. The starter exchanges these credentials for short-lived access tokens
   on the server and refreshes them before they expire.

3. **Start the dev server**

   ```bash
   pnpm run dev
   ```

4. **Open [http://localhost:3000](http://localhost:3000)** to chat, or open
   [http://localhost:3000/reviews](http://localhost:3000/reviews) to review
   suspended executions.

## Connecting to a Local Agent

To point the chatbot at an agent running locally via `agentengine dev up`:

1. **Start your agent** in its own directory:

   ```bash
   agentengine dev up
   ```

   This starts the local Agent Engine proxy. Obtain the URL of the locally running agent from the command output.

2. **Update `MONGODB_AGENTIC_API_URL`** in `.env.local` to use the local streaming endpoint:

   ```
   MONGODB_AGENTIC_API_URL=http://localhost:<configured-ui-port>/invoke/stream
   ```

   Use the playground UI port. Only the playground serves the review queue and
   session history, so an orchestration engine URL gives you working chat and a
   Reviews page that cannot load. `agentengine dev up` prints the orchestration
   engine port instead whenever it runs headless, which it does for every
   non-chat agent.

   For `agentengine dev up --all`, preserve the workspace selector printed by the
   CLI, for example `.../invoke/stream?workspace=insurance-agent`.

   The local proxy does not enforce auth, so leave the service-account variables
   blank.

3. **Start the chatbot dev server**:

   ```bash
   pnpm run dev
   ```

   If your locally running agent also runs on port 3000, you can run the chatbot client on a different port to avoid port conflicts:

   ```bash
   pnpm run dev -- --port 3001
   ```

## Project Structure

```
app/
  api/chat/           — server-side message and session-history proxies
  api/reviews/        — server-side list and resume proxies
  page.tsx            — Home page (renders the chat)
  reviews/page.tsx    — Human review queue
  layout.tsx          — Root layout
  globals.css         — Theme and base styles
components/
  chat/               — Chat UI (messages, input, icons)
  ai-elements/        — Shimmer animation for loading state
  ui/                 — Shared UI primitives (button, input)
hooks/                — Auto-scroll hook
lib/utils.ts          — Helpers (cn, generateUUID)
```

## How It Works

The UI sends messages to `/api/chat`, which forwards them to the Atlas Agent Engine streaming endpoint. The response is an SSE stream of text chunks that get piped back to the browser via the Vercel AI SDK's `createUIMessageStream`. The proxy also preserves the authoritative `X-Session-ID` returned by the platform so later turns and native interrupt resumes continue the same session.

The browser remembers the active session ID and reloads its messages after a
page refresh. The header includes editable **User ID** and **Session ID** fields
so you can switch sessions without restarting; randomizing the session ID starts
a new conversation.

## Human review queue

The **Reviews** link opens a workspace-scoped queue of suspended executions.
The page refreshes automatically and supports both platform suspension contracts:

- Decision reviews submit a decision and optional reviewer notes to the
  execution resume endpoint.
- Native framework interrupts continue the original session through the
  workspace invoke endpoint. The JSON `resume_map` is seeded with each
  interrupt ID and any required fields from its response schema; edit the
  values before continuing.

After submitting a review, open the resumed conversation from the confirmation
message. The chat watches the session until the agent's final response appears.

The service-account secret and short-lived access tokens stay in server-side
route handlers. This starter intentionally has no end-user authentication, so
do not expose it publicly as-is: add application authentication and
authorization to both chat and review routes before a production deployment.
