# MongoDB Agentic Platform - Chatbot Starter App

A minimal chatbot UI that connects to the [MongoDB Agentic Platform](https://agentic-platform.mongodb.com). Built with Next.js, the Vercel AI SDK, and Tailwind CSS.

## Prerequisites

- Node.js 18+
- [pnpm](https://pnpm.io/installation)
- A MongoDB Agentic Platform workspace with an API key

## Quick Start

1. **Install dependencies**

   ```bash
   pnpm install
   ```

2. **Configure environment variables**

   ```bash
   cp .env.example .env.local
   ```

   Fill in your values in `.env.local`:

   ```
   MONGODB_AGENTIC_API_URL=https://agentic-platform.mongodb.com/api/v1/invokeWorkspaceStream
   MONGODB_AGENTIC_PROJECT_ID=<your project id>
   MONGODB_AGENTIC_WORKSPACE_ID=<your workspace id>
   MONGODB_AGENTIC_API_KEY=<your api key>
   ```

   You can get these values using the Agentic CLI:

   ```bash
   agentic projects list        # project ID
   agentic workspace list       # workspace ID
   agentic api-keys create      # API key
   ```

3. **Start the dev server**

   ```bash
   pnpm run dev
   ```

4. **Open [http://localhost:3000](http://localhost:3000)** and start chatting.

## Connecting to a Local Agent

To point the chatbot at an agent running locally via `agentic dev up`:

1. **Start your agent** in its own directory:

   ```bash
   agentic dev up
   ```

   This starts the local Agentic Platform proxy. Obtain the URL of the locally running agent from the command output.

2. **Update `MONGODB_AGENTIC_API_URL`** in `.env.local` to use the local streaming endpoint:

   ```
   MONGODB_AGENTIC_API_URL=<your agent's running URL>/stream
   ```

   The local proxy does not enforce auth, so you can leave `MONGODB_AGENTIC_PROJECT_ID`, `MONGODB_AGENTIC_WORKSPACE_ID`, and `MONGODB_AGENTIC_API_KEY` blank or set them to any placeholder value.

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
  api/chat/route.ts   — API route that proxies messages to the MongoDB Agentic API
  page.tsx            — Home page (renders the chat)
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

The UI sends messages to `/api/chat`, which forwards them to the MongoDB Agentic Platform streaming endpoint. The response is an SSE stream of text chunks that get piped back to the browser via the Vercel AI SDK's `createUIMessageStream`.

The header includes editable **User ID** and **Session ID** fields so you can test multi-user and multi-session behavior without restarting.
