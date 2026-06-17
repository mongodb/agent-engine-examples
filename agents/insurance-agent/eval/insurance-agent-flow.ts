/**
 * Local OE-based eval script for the Insurance Agent demo flow.
 *
 * Drives 10 customer personas through the full insurance agent conversation
 * using the local Orchestration Engine exposed by `agentic dev`.
 *
 * Unlike the API Gateway version, this script:
 * - Talks directly to the OE (`/invoke`, `/invoke/stream`, `/execution/{id}`, `/resume/{id}`)
 * - Does not require authentication
 * - Auto-discovers the local app URL from the live local stack, with `.agentic/dev-state.json` as a fallback
 * - Can exercise async `/invoke` polling, live `/invoke/stream`, or both
 *
 * Usage:
 *   npx tsx insurance-agent-flow.ts
 *   npx tsx insurance-agent-flow.ts --transport stream
 *   npx tsx insurance-agent-flow.ts --transport invoke
 *   npx tsx insurance-agent-flow.ts --personas 1,3,5
 *   npx tsx insurance-agent-flow.ts --concurrency 2
 *   npx tsx insurance-agent-flow.ts --base-url http://localhost:32825
 */

import * as crypto from "crypto";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

const DEFAULT_CONCURRENCY = 1;
const DEFAULT_POLL_INTERVAL_MS = 2_000;
const DEFAULT_POLL_TIMEOUT_MS = 180_000;
const REQUEST_TIMEOUT_MS = 30_000;
const STREAM_TIMEOUT_BUFFER_MS = 5_000;
const SCRIPT_DIR = path.dirname(path.resolve(process.argv[1] || "."));
const AGENT_ROOT = path.resolve(SCRIPT_DIR, "..");
const DEFAULT_DEV_STATE_PATH = path.resolve(
  SCRIPT_DIR,
  "..",
  ".agentic",
  "dev-state.json"
);
const DEFAULT_DEV_COMPOSE_PATH = path.resolve(
  SCRIPT_DIR,
  "..",
  ".agentic",
  "docker-compose.dev.yml"
);
const DOCKER_DISCOVERY_TIMEOUT_MS = 5_000;
const TRANSPORT_MODES = ["invoke", "stream", "both"] as const;

type TransportMode = (typeof TRANSPORT_MODES)[number];
type SingleTransportMode = Exclude<TransportMode, "both">;

interface Persona {
  name: string;
  info: string;
  vehicle: string;
  claim: string;
}

const PERSONAS: Persona[] = [
  {
    name: "Max Marcon",
    info: "Sure. Max Marcon, max.marcon@mongodb.com, 850522765",
    vehicle:
      "It's a 2024 Ford Mustang Mach-e GT. I'll go for Comprehensive. I am 43.",
    claim:
      "The accident occurred yesterday, Feb 21. It was a single car accident. I swerved to avoid a raccoon and crashed into a tree. The mechanic estimated $15k in damages as I need to replace the front and the headlights, and there is some minor structural damage.",
  },
  {
    name: "Sarah Chen",
    info: "Sure. Sarah Chen, sarah.chen@email.com, 555-234-5678",
    vehicle:
      "It's a 2023 Tesla Model 3 Long Range. I'd like Comprehensive coverage. I am 31.",
    claim:
      "I was rear-ended at a stoplight yesterday. The bumper is destroyed, the trunk won't close, and the rear sensors are all smashed. The repair shop quoted me $12,000.",
  },
  {
    name: "James Wilson",
    info: "Sure. James Wilson, james.w@outlook.com, 555-345-6789",
    vehicle:
      "It's a 2022 Toyota RAV4 XLE. I'll go with Standard coverage. I'm 28 years old.",
    claim:
      "We had a bad hailstorm two days ago. The roof and hood are covered in dents, and all the windows are cracked. The body shop estimate is about $9,000.",
  },
  {
    name: "Maria Garcia",
    info: "Sure. Maria Garcia, maria.garcia@gmail.com, 555-456-7890",
    vehicle:
      "It's a 2024 Honda CR-V Touring. Comprehensive please. I am 52.",
    claim:
      "I hit a deer on the highway last night. The entire front end is destroyed and the radiator is punctured. The estimate is $11,000 for repairs.",
  },
  {
    name: "David Kim",
    info: "Sure. David Kim, d.kim@company.org, 555-567-8901",
    vehicle:
      "It's a 2023 BMW X3 M40i. I want Comprehensive coverage. I'm 37.",
    claim:
      "I misjudged a turn in a parking garage and hit a concrete pillar. The side panels are crushed, the mirror is gone, and the door is crumpled. Repair estimate is $8,500.",
  },
  {
    name: "Emma Thompson",
    info: "Sure. Emma Thompson, emma.t@proton.me, 555-678-9012",
    vehicle:
      "It's a 2022 Subaru Outback Limited. Standard coverage works for me. I'm 45.",
    claim:
      "My car was caught in a flash flood last week. The engine is hydrolocked and the entire interior is waterlogged. The mechanic says it's $18,000 to fix.",
  },
  {
    name: "Robert Patel",
    info: "Sure. Robert Patel, r.patel@yahoo.com, 555-789-0123",
    vehicle:
      "It's a 2024 Hyundai Ioniq 5 SEL. Comprehensive please. I am 26.",
    claim:
      "I was T-boned at an intersection yesterday. The passenger door is caved in, the B-pillar is bent, and all the airbags deployed. The estimate is around $14,000.",
  },
  {
    name: "Lisa Nakamura",
    info: "Sure. Lisa Nakamura, lisa.n@fastmail.com, 555-890-1234",
    vehicle:
      "It's a 2023 Mazda CX-5 Turbo. I'll go with Standard. I'm 39.",
    claim:
      "I slid off an icy road into a ditch two days ago. The undercarriage is damaged, an axle is bent, and the front bumper is destroyed. The shop quoted $10,500.",
  },
  {
    name: "Carlos Rivera",
    info: "Sure. Carlos Rivera, carlos.r@icloud.com, 555-901-2345",
    vehicle:
      "It's a 2024 Chevrolet Equinox RS. Comprehensive coverage. I'm 55.",
    claim:
      "A large tree branch fell on my car during a storm while it was parked. The roof is crushed and the windshield is completely shattered. Estimate is $7,500.",
  },
  {
    name: "Aisha Johnson",
    info: "Sure. Aisha Johnson, aisha.j@gmail.com, 555-012-3456",
    vehicle:
      "It's a 2023 Volkswagen ID.4 Pro S. Comprehensive please. I am 33.",
    claim:
      "I was side-swiped by a truck on the freeway yesterday. Both driver-side doors are dented in and the fender is crumpled. The body shop says $13,000.",
  },
];

const STEP_DESCRIPTIONS: Record<number, string> = {
  1: "Greeting",
  2: "Provide Personal Info",
  3: "Ask About Coverage Types",
  4: "Provide Vehicle and Coverage Choice",
  5: "Create the Policy",
  6: "New Session (Memory Boundary)",
  7: "Report an Accident",
  8: "Provide Claim Details (Triggers Human Review)",
  9: "Approve the Human Review",
};

interface CliArgs {
  personaIndices: number[] | null;
  baseUrl: string | null;
  transport: TransportMode;
  concurrency: number;
  pollIntervalMs: number;
  pollTimeoutMs: number;
  help: boolean;
}

interface LocalDevState {
  app_url?: string;
}

interface HttpResult {
  ok: boolean;
  status: number;
  text: string;
  data: unknown;
}

interface ExecutionState {
  execution_id?: string;
  status?: string;
  result?: unknown;
  error?: unknown;
  suspend_reason?: string;
  suspend_context?: Record<string, unknown>;
}

type SendStatus = "completed" | "suspended" | "error";

interface SendResult {
  content: string;
  status: SendStatus;
  executionId?: string;
  suspendReason?: string;
  suspendContext?: Record<string, unknown>;
  raw?: ExecutionState;
}

interface StreamChunkPayload {
  chunk_type?: string;
  content?: string;
  metadata?: Record<string, string | undefined>;
  error?: string;
  execution_id?: string;
}

interface ResumeResult {
  success: boolean;
  result?: string;
  error?: string;
}

interface PersonaResult {
  transport: SingleTransportMode;
  personaIndex: number;
  persona: Persona;
  transcript: PersonaTranscript;
  elapsedMs: number;
  error?: string;
}

interface BaseUrlCandidate {
  baseUrl: string;
  source: string;
}

function printUsage(): void {
  console.log(`
Insurance Agent Local Eval (OE mode)
====================================

Usage:
  npx tsx insurance-agent-flow.ts
  npx tsx insurance-agent-flow.ts --transport both
  npx tsx insurance-agent-flow.ts --transport stream
  npx tsx insurance-agent-flow.ts --personas 1,3,5
  npx tsx insurance-agent-flow.ts --concurrency 2
  npx tsx insurance-agent-flow.ts --base-url http://localhost:32825
  npx tsx insurance-agent-flow.ts --poll-timeout-ms 300000

Options:
  --personas <list>          Comma-separated 1-based persona list
  --transport <mode>         invoke | stream | both (default: both)
  --concurrency <n>          Number of personas to run concurrently (default: 1)
  --base-url <url>           Explicit app URL; skips local dev state discovery
  --poll-interval-ms <n>     Poll interval for execution status (default: 2000)
  --poll-timeout-ms <n>      Max wait per execution before failing (default: 180000)
  --help                     Show this message
`);
}

function parseArgs(): CliArgs {
  const args = process.argv.slice(2);
  let personaIndices: number[] | null = null;
  let baseUrl: string | null = null;
  let transport: TransportMode = "both";
  let concurrency = DEFAULT_CONCURRENCY;
  let pollIntervalMs = DEFAULT_POLL_INTERVAL_MS;
  let pollTimeoutMs = DEFAULT_POLL_TIMEOUT_MS;
  let help = false;

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];

    switch (arg) {
      case "--personas": {
        const raw = args[i + 1];
        i += 1;
        if (!raw) {
          throw new Error("--personas requires a comma-separated value");
        }
        personaIndices = raw
          .split(",")
          .map((part) => parseInt(part.trim(), 10) - 1)
          .filter((index) => index >= 0 && index < PERSONAS.length);
        break;
      }
      case "--base-url": {
        const raw = args[i + 1];
        i += 1;
        if (!raw) {
          throw new Error("--base-url requires a value");
        }
        baseUrl = raw.replace(/\/$/, "");
        break;
      }
      case "--transport": {
        const raw = args[i + 1];
        i += 1;
        if (!raw) {
          throw new Error("--transport requires a value");
        }
        if (!TRANSPORT_MODES.includes(raw as TransportMode)) {
          throw new Error(
            `--transport must be one of: ${TRANSPORT_MODES.join(", ")}`
          );
        }
        transport = raw as TransportMode;
        break;
      }
      case "--concurrency": {
        const raw = args[i + 1];
        i += 1;
        const parsed = parseInt(raw || "", 10);
        if (!Number.isFinite(parsed) || parsed <= 0) {
          throw new Error("--concurrency must be a positive integer");
        }
        concurrency = parsed;
        break;
      }
      case "--poll-interval-ms": {
        const raw = args[i + 1];
        i += 1;
        const parsed = parseInt(raw || "", 10);
        if (!Number.isFinite(parsed) || parsed <= 0) {
          throw new Error("--poll-interval-ms must be a positive integer");
        }
        pollIntervalMs = parsed;
        break;
      }
      case "--poll-timeout-ms": {
        const raw = args[i + 1];
        i += 1;
        const parsed = parseInt(raw || "", 10);
        if (!Number.isFinite(parsed) || parsed <= 0) {
          throw new Error("--poll-timeout-ms must be a positive integer");
        }
        pollTimeoutMs = parsed;
        break;
      }
      case "--help":
      case "-h":
        help = true;
        break;
      default:
        throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return {
    personaIndices,
    baseUrl,
    transport,
    concurrency,
    pollIntervalMs,
    pollTimeoutMs,
    help,
  };
}

function readBaseUrlFromDevState(): BaseUrlCandidate | null {
  try {
    const devState = JSON.parse(
      fs.readFileSync(DEFAULT_DEV_STATE_PATH, "utf-8")
    ) as LocalDevState;
    const appUrl =
      typeof devState.app_url === "string"
        ? devState.app_url.replace(/\/$/, "")
        : "";
    if (!appUrl) {
      throw new Error(`Missing "app_url" in ${DEFAULT_DEV_STATE_PATH}`);
    }
    return {
      baseUrl: appUrl,
      source: DEFAULT_DEV_STATE_PATH,
    };
  } catch (error) {
    return null;
  }
}

function parseDockerPortUrl(output: string): string | null {
  const mapping = output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
  const match = mapping?.match(/:(\d+)\s*$/);
  return match ? `http://localhost:${match[1]}` : null;
}

function readBaseUrlFromDockerCompose(service: string): BaseUrlCandidate | null {
  if (!fs.existsSync(DEFAULT_DEV_COMPOSE_PATH)) {
    return null;
  }

  try {
    const output = execFileSync(
      "docker",
      ["compose", "-f", DEFAULT_DEV_COMPOSE_PATH, "port", service, "8000"],
      {
        cwd: AGENT_ROOT,
        encoding: "utf-8",
        stdio: ["ignore", "pipe", "pipe"],
        timeout: DOCKER_DISCOVERY_TIMEOUT_MS,
      }
    );
    const baseUrl = parseDockerPortUrl(output);
    if (!baseUrl) {
      return null;
    }
    return {
      baseUrl,
      source: `${DEFAULT_DEV_COMPOSE_PATH}#${service}`,
    };
  } catch {
    return null;
  }
}

function readBaseUrlFromDockerContainer(containerName: string): BaseUrlCandidate | null {
  try {
    const output = execFileSync("docker", ["port", containerName, "8000"], {
      cwd: AGENT_ROOT,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout: DOCKER_DISCOVERY_TIMEOUT_MS,
    });
    const baseUrl = parseDockerPortUrl(output);
    if (!baseUrl) {
      return null;
    }
    return {
      baseUrl,
      source: `docker:${containerName}`,
    };
  } catch {
    return null;
  }
}

function discoverBaseUrlCandidates(): BaseUrlCandidate[] {
  const projectName = path.basename(AGENT_ROOT);
  const candidates = [
    readBaseUrlFromDockerCompose("app"),
    readBaseUrlFromDockerCompose("oe"),
    readBaseUrlFromDockerContainer(`${projectName}-app-1`),
    readBaseUrlFromDockerContainer(`${projectName}-oe-1`),
    readBaseUrlFromDevState(),
  ].filter((candidate): candidate is BaseUrlCandidate => candidate !== null);

  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    if (seen.has(candidate.baseUrl)) {
      return false;
    }
    seen.add(candidate.baseUrl);
    return true;
  });
}

async function discoverBaseUrl(): Promise<BaseUrlCandidate> {
  const candidates = discoverBaseUrlCandidates();
  const failures: string[] = [];

  for (const candidate of candidates) {
    try {
      await healthCheck(candidate.baseUrl);
      return candidate;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      failures.push(`${candidate.source}: ${message}`);
    }
  }

  const failureText =
    failures.length > 0
      ? ` Tried: ${failures.join(" | ")}`
      : ` No live local app URL candidates were found.`;
  throw new Error(
    `Could not auto-discover a healthy local app URL. ` +
      `Start the local stack with \`agentic dev up --local-sdk\` or pass --base-url.${failureText}`
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatJson(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function extractText(value: unknown): string {
  if (value == null) {
    return "";
  }

  if (typeof value === "string") {
    return value;
  }

  if (typeof value !== "object") {
    return String(value);
  }

  const record = value as Record<string, unknown>;
  for (const key of ["result", "response", "message", "output", "text", "content"]) {
    const candidate = record[key];
    if (typeof candidate === "string") {
      return candidate;
    }
  }

  if (record.result && typeof record.result === "object") {
    const nested = extractText(record.result);
    if (nested) {
      return nested;
    }
  }

  return formatJson(value);
}

function parseJsonObject(
  value: string | undefined
): Record<string, unknown> | undefined {
  if (!value) {
    return undefined;
  }

  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : undefined;
  } catch {
    return undefined;
  }
}

function normalizeExecution(data: unknown): ExecutionState {
  if (!data || typeof data !== "object") {
    return {};
  }

  const record = data as Record<string, unknown>;
  const nested =
    record.execution && typeof record.execution === "object"
      ? (record.execution as Record<string, unknown>)
      : null;

  const executionId =
    typeof record.execution_id === "string"
      ? record.execution_id
      : typeof nested?.execution_id === "string"
        ? nested.execution_id
        : undefined;

  const status =
    typeof record.status === "string"
      ? record.status
      : typeof nested?.status === "string"
        ? nested.status
        : undefined;

  const result =
    record.result !== undefined
      ? record.result
      : nested?.result !== undefined
        ? nested.result
        : undefined;

  const error =
    record.error !== undefined
      ? record.error
      : nested?.error !== undefined
        ? nested.error
        : undefined;

  const suspendReason =
    typeof record.suspend_reason === "string"
      ? record.suspend_reason
      : typeof nested?.suspend_reason === "string"
        ? nested.suspend_reason
        : undefined;

  const suspendContext =
    record.suspend_context && typeof record.suspend_context === "object"
      ? (record.suspend_context as Record<string, unknown>)
      : nested?.suspend_context && typeof nested.suspend_context === "object"
        ? (nested.suspend_context as Record<string, unknown>)
        : undefined;

  return {
    execution_id: executionId,
    status,
    result,
    error,
    suspend_reason: suspendReason,
    suspend_context: suspendContext,
  };
}

function toSendStatus(execution: ExecutionState): SendStatus {
  if (execution.status === "suspended") {
    return "suspended";
  }

  if (execution.status === "completed" || !execution.status) {
    return execution.error ? "error" : "completed";
  }

  return "error";
}

async function fetchJson(
  url: string,
  init: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS
): Promise<HttpResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
    });
    const text = await response.text();

    let data: unknown = text;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    }

    return {
      ok: response.ok,
      status: response.status,
      text,
      data,
    };
  } catch (error) {
    const message =
      error instanceof Error && error.name === "AbortError"
        ? `Request timed out after ${timeoutMs}ms`
        : error instanceof Error
          ? error.message
          : String(error);

    return {
      ok: false,
      status: 0,
      text: message,
      data: { error: message },
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function healthCheck(baseUrl: string): Promise<void> {
  const response = await fetchJson(`${baseUrl}/health`, { method: "GET" });

  if (!response.ok) {
    throw new Error(
      `OE health check failed for ${baseUrl}: ` +
        `${response.status || "request_error"} ${response.text}`
    );
  }
}

async function waitForExecution(
  baseUrl: string,
  executionId: string,
  pollTimeoutMs: number,
  pollIntervalMs: number
): Promise<ExecutionState> {
  const start = Date.now();
  let lastSeen: ExecutionState = { execution_id: executionId };

  while (Date.now() - start < pollTimeoutMs) {
    const response = await fetchJson(
      `${baseUrl}/execution/${executionId}`,
      { method: "GET" }
    );

    if (response.ok) {
      lastSeen = normalizeExecution(response.data);
      if (
        lastSeen.status === "completed" ||
        lastSeen.status === "suspended" ||
        lastSeen.status === "failed" ||
        lastSeen.status === "error"
      ) {
        return lastSeen;
      }
    } else {
      lastSeen = {
        execution_id: executionId,
        status: "error",
        error:
          response.text ||
          `Polling failed with HTTP ${response.status || "request_error"}`,
      };
    }

    await sleep(pollIntervalMs);
  }

  return {
    ...lastSeen,
    execution_id: executionId,
    status: "error",
    error: `Timed out waiting for execution ${executionId} after ${pollTimeoutMs}ms`,
  };
}

async function sendMessageViaInvoke(
  baseUrl: string,
  threadId: string,
  userId: string,
  message: string,
  pollTimeoutMs: number,
  pollIntervalMs: number
): Promise<SendResult> {
  const invokeResponse = await fetchJson(`${baseUrl}/invoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      thread_id: threadId,
      user_id: userId,
      wait: false,
    }),
  });

  if (!invokeResponse.ok) {
    return {
      content:
        invokeResponse.text ||
        `HTTP ${invokeResponse.status || "request_error"} while invoking`,
      status: "error",
    };
  }

  const initialExecution = normalizeExecution(invokeResponse.data);
  const executionId = initialExecution.execution_id;

  if (!executionId) {
    return {
      content: extractText(initialExecution.result ?? invokeResponse.data),
      status: toSendStatus(initialExecution),
      suspendReason: initialExecution.suspend_reason,
      suspendContext: initialExecution.suspend_context,
      raw: initialExecution,
    };
  }

  const finalExecution = await waitForExecution(
    baseUrl,
    executionId,
    pollTimeoutMs,
    pollIntervalMs
  );

  const status = toSendStatus(finalExecution);
  const content =
    status === "error"
      ? extractText(finalExecution.error ?? finalExecution)
      : extractText(finalExecution.result);

  return {
    content,
    status,
    executionId,
    suspendReason: finalExecution.suspend_reason,
    suspendContext: finalExecution.suspend_context,
    raw: finalExecution,
  };
}

async function consumeSseStream(
  response: Response,
  onData: (payload: string) => void
): Promise<void> {
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("Streaming response body is unavailable");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let eventLines: string[] = [];

  const flushEvent = () => {
    if (eventLines.length === 0) {
      return;
    }

    const payload = eventLines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n")
      .trim();

    eventLines = [];

    if (payload) {
      onData(payload);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });

    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line === "") {
        flushEvent();
      } else {
        eventLines.push(line);
      }
    }

    if (done) {
      if (buffer) {
        eventLines.push(buffer);
      }
      flushEvent();
      return;
    }
  }
}

async function sendMessageViaStream(
  baseUrl: string,
  threadId: string,
  userId: string,
  message: string,
  streamTimeoutMs: number
): Promise<SendResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), streamTimeoutMs);

  try {
    const response = await fetch(`${baseUrl}/invoke/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        message,
        thread_id: threadId,
        user_id: userId,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const text = await response.text();
      return {
        content:
          text ||
          `HTTP ${response.status || "request_error"} while opening invoke stream`,
        status: "error",
      };
    }

    let content = "";
    let status: SendStatus = "completed";
    let executionId: string | undefined;
    let suspendReason: string | undefined;
    let suspendContext: Record<string, unknown> | undefined;
    let terminalSeen = false;

    await consumeSseStream(response, (payload) => {
      let chunk: StreamChunkPayload;
      try {
        chunk = JSON.parse(payload) as StreamChunkPayload;
      } catch {
        return;
      }

      const metadata = chunk.metadata ?? {};
      if (!executionId) {
        executionId = chunk.execution_id || metadata.execution_id;
      }

      if (chunk.chunk_type === "text") {
        if (chunk.content) {
          content += chunk.content;
        }
        return;
      }

      if (chunk.chunk_type === "done") {
        terminalSeen = true;
        if (metadata.status === "suspended") {
          status = "suspended";
          suspendReason = metadata.suspend_reason;
          suspendContext = parseJsonObject(metadata.suspend_context);
          return;
        }

        status = "completed";
        if (!content && chunk.content) {
          content = chunk.content;
        }
        return;
      }

      if (chunk.chunk_type === "error") {
        terminalSeen = true;
        status = "error";
        content = chunk.error || chunk.content || "Unknown stream error";
      }
    });

    if (!terminalSeen) {
      return {
        content: content || "Stream ended before a terminal chunk was received",
        status: "error",
        executionId,
      };
    }

    return {
      content,
      status,
      executionId,
      suspendReason,
      suspendContext,
      raw: {
        execution_id: executionId,
        status,
        result: status === "completed" ? content : undefined,
        error: status === "error" ? content : undefined,
        suspend_reason: suspendReason,
        suspend_context: suspendContext,
      },
    };
  } catch (error) {
    const message =
      error instanceof Error && error.name === "AbortError"
        ? `Stream timed out after ${streamTimeoutMs}ms`
        : error instanceof Error
          ? error.message
          : String(error);

    return {
      content: message,
      status: "error",
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function sendMessage(
  baseUrl: string,
  threadId: string,
  userId: string,
  message: string,
  transport: SingleTransportMode,
  pollTimeoutMs: number,
  pollIntervalMs: number
): Promise<SendResult> {
  if (transport === "stream") {
    return sendMessageViaStream(
      baseUrl,
      threadId,
      userId,
      message,
      Math.max(pollTimeoutMs, REQUEST_TIMEOUT_MS) + STREAM_TIMEOUT_BUFFER_MS
    );
  }

  return sendMessageViaInvoke(
    baseUrl,
    threadId,
    userId,
    message,
    pollTimeoutMs,
    pollIntervalMs
  );
}

async function approveReview(
  baseUrl: string,
  executionId: string
): Promise<ResumeResult> {
  const response = await fetchJson(`${baseUrl}/resume/${executionId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      human_review: {
        decision: "approved",
        reviewer_notes: "Eval auto-approve",
      },
    }),
  });

  if (!response.ok) {
    return {
      success: false,
      error:
        response.text ||
        `HTTP ${response.status || "request_error"} while resuming`,
    };
  }

  return {
    success: true,
    result: formatJson(response.data),
  };
}

class PersonaTranscript {
  lines: string[] = [];

  personaHeader(index: number, persona: Persona): void {
    this.lines.push(`\n---\n\n## Persona ${index + 1}: ${persona.name}\n`);
  }

  stepHeader(step: number): void {
    this.lines.push(
      `\n### Step ${step}: ${STEP_DESCRIPTIONS[step] || "Unknown"}\n`
    );
  }

  userMessage(message: string): void {
    this.lines.push(`**User:** ${message}\n`);
  }

  assistantMessage(message: string): void {
    const content = message.trim() || "(no assistant message returned)";
    this.lines.push(`**Assistant:** ${content}\n`);
  }

  action(description: string): void {
    this.lines.push(`**Action:** ${description}\n`);
  }

  result(description: string): void {
    this.lines.push(`**Result:** ${description}\n`);
  }

  metadata(key: string, value: string): void {
    this.lines.push(`**${key}:** ${value}\n`);
  }

  error(message: string): void {
    this.lines.push(`**ERROR:** ${message}\n`);
  }
}

class Semaphore {
  private readonly queue: Array<() => void> = [];
  private running = 0;

  constructor(private readonly max: number) {}

  async acquire(): Promise<void> {
    if (this.running < this.max) {
      this.running += 1;
      return;
    }

    await new Promise<void>((resolve) => {
      this.queue.push(() => {
        this.running += 1;
        resolve();
      });
    });
  }

  release(): void {
    this.running -= 1;
    const next = this.queue.shift();
    if (next) {
      next();
    }
  }
}

async function runPersona(
  baseUrl: string,
  persona: Persona,
  personaIndex: number,
  transport: SingleTransportMode,
  pollTimeoutMs: number,
  pollIntervalMs: number
): Promise<PersonaResult> {
  const transcript = new PersonaTranscript();
  const startedAt = Date.now();
  const tag = `[${transport.toUpperCase()} P${personaIndex + 1}]`;

  transcript.personaHeader(personaIndex, persona);
  transcript.metadata("Transport", transport);
  console.log(`${tag} Starting: ${persona.name}`);

  const userId = `eval-user-${personaIndex + 1}-${crypto.randomBytes(4).toString("hex")}`;
  const session1 = crypto.randomUUID();
  const session2 = crypto.randomUUID();

  transcript.metadata("User ID", userId);
  transcript.metadata("Session 1 (Policy)", session1);
  transcript.metadata("Session 2 (Claim)", session2);

  async function chat(
    step: number,
    session: string,
    message: string
  ): Promise<SendResult | null> {
    transcript.stepHeader(step);
    transcript.userMessage(message);

    try {
      const response = await sendMessage(
        baseUrl,
        session,
        userId,
        message,
        transport,
        pollTimeoutMs,
        pollIntervalMs
      );

      transcript.assistantMessage(response.content);
      transcript.metadata("Status", response.status);

      if (response.executionId) {
        transcript.metadata("Execution ID", response.executionId);
      }

      if (response.suspendReason) {
        transcript.metadata("Suspend Reason", response.suspendReason);
      }

      if (
        response.suspendContext &&
        Object.keys(response.suspendContext).length > 0
      ) {
        transcript.metadata(
          "Suspend Context",
          formatJson(response.suspendContext)
        );
      }

      console.log(
        `${tag} Step ${step}: ${response.status} (${response.content.length} chars)`
      );

      return response;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : String(error);
      transcript.error(message);
      console.log(`${tag} Step ${step}: ERROR - ${message}`);
      return null;
    }
  }

  try {
    await chat(1, session1, "Hello. I'd like to buy insurance for my new car.");
    await chat(2, session1, persona.info);
    await chat(
      3,
      session1,
      "Yes can you explain the differences between the coverage types?"
    );
    await chat(4, session1, persona.vehicle);
    await chat(5, session1, "Go ahead and create the policy.");

    transcript.stepHeader(6);
    transcript.action(
      `Switched to new session (session2: ${session2}). Same user_id (${userId}) preserves memory lookup across sessions.`
    );
    transcript.result("New session created (client-side UUID, no API call).");
    console.log(`${tag} Step 6: session switched`);

    let executionId: string | undefined;

    const response7 = await chat(
      7,
      session2,
      "I was in an accident and the car is pretty damaged."
    );

    if (response7?.status === "suspended") {
      executionId = response7.executionId;
      transcript.metadata(
        "Note",
        "Human review was triggered early at Step 7."
      );
    }

    if (executionId) {
      transcript.stepHeader(8);
      transcript.userMessage(persona.claim);
      transcript.action(
        "Skipped sending Step 8 because the execution was already suspended at Step 7."
      );
      transcript.metadata("Execution ID", executionId);
      transcript.metadata("Human Review", "TRIGGERED (from Step 7)");
      console.log(`${tag} Step 8: skipped (already suspended)`);
    } else {
      const response8 = await chat(8, session2, persona.claim);

      if (response8?.status === "suspended") {
        executionId = response8.executionId;
        transcript.metadata("Human Review", "TRIGGERED");
      } else {
        transcript.metadata("Human Review", "NOT TRIGGERED");
      }
    }

    transcript.stepHeader(9);

    if (!executionId) {
      transcript.error(
        "No execution_id available. Human review was not triggered."
      );
      console.log(`${tag} Step 9: SKIP (no execution_id)`);
    } else {
      transcript.action(
        `Approving execution ${executionId} via POST /resume/${executionId}`
      );

      const approval = await approveReview(baseUrl, executionId);
      if (!approval.success) {
        transcript.error(`Approval failed: ${approval.error}`);
        console.log(`${tag} Step 9: FAILED - ${approval.error}`);
      } else {
        transcript.result(`Approval successful: ${approval.result || "ok"}`);
        console.log(`${tag} Step 9: approved, waiting for completion...`);

        const completion = await waitForExecution(
          baseUrl,
          executionId,
          pollTimeoutMs,
          pollIntervalMs
        );
        transcript.metadata("Resume Status", completion.status || "unknown");

        const completionText =
          completion.status === "completed"
            ? extractText(completion.result)
            : extractText(completion.error || completion.result);

        if (completionText) {
          transcript.assistantMessage(completionText);
        }

        if (
          completion.status !== "completed" &&
          completion.status !== "suspended"
        ) {
          transcript.error(
            `Resume completed with non-success status: ${completion.status || "unknown"}`
          );
        }

        console.log(
          `${tag} Step 9: completion ${completion.status || "unknown"}`
        );
      }
    }
  } catch (error) {
    const message =
      error instanceof Error ? error.message : String(error);
    transcript.error(`Fatal persona error: ${message}`);
    console.log(`${tag} FATAL: ${message}`);
    return {
      transport,
      personaIndex,
      persona,
      transcript,
      elapsedMs: Date.now() - startedAt,
      error: message,
    };
  }

  const elapsedMs = Date.now() - startedAt;
  console.log(`${tag} Done in ${(elapsedMs / 1000).toFixed(1)}s`);

  return {
    transport,
    personaIndex,
    persona,
    transcript,
    elapsedMs,
  };
}

async function main(): Promise<void> {
  const args = parseArgs();

  if (args.help) {
    printUsage();
    return;
  }

  const discovered = args.baseUrl
    ? { baseUrl: args.baseUrl, source: "--base-url" }
    : await discoverBaseUrl();
  const { baseUrl, source: discoverySource } = discovered;
  if (args.baseUrl) {
    await healthCheck(baseUrl);
  }

  const indicesToRun = args.personaIndices ?? PERSONAS.map((_, index) => index);
  const transportsToRun: SingleTransportMode[] =
    args.transport === "both" ? ["invoke", "stream"] : [args.transport];

  console.log(`\nInsurance Agent Local Eval (OE mode)`);
  console.log(`====================================`);
  console.log(
    `Personas: ${indicesToRun.map((index) => index + 1).join(", ")} (${indicesToRun.length} total)`
  );
  console.log(`Transports: ${transportsToRun.join(", ")}`);
  console.log(`Concurrency: ${args.concurrency}`);
  console.log(`Target: ${baseUrl}`);
  console.log(`Discovery: ${discoverySource}`);
  console.log(`Poll interval: ${args.pollIntervalMs}ms`);
  console.log(`Poll timeout: ${args.pollTimeoutMs}ms\n`);

  const wallStartedAt = Date.now();
  const results: PersonaResult[] = [];

  for (const transport of transportsToRun) {
    console.log(`\n--- Transport: ${transport} ---`);
    const semaphore = new Semaphore(args.concurrency);
    const transportResults = await Promise.all(
      indicesToRun.map(async (index) => {
        await semaphore.acquire();
        try {
          return await runPersona(
            baseUrl,
            PERSONAS[index],
            index,
            transport,
            args.pollTimeoutMs,
            args.pollIntervalMs
          );
        } finally {
          semaphore.release();
        }
      })
    );
    results.push(...transportResults);
  }

  const transportOrder = new Map<SingleTransportMode, number>(
    transportsToRun.map((transport, index) => [transport, index])
  );

  results.sort((left, right) => {
    const transportDelta =
      (transportOrder.get(left.transport) ?? 0) -
      (transportOrder.get(right.transport) ?? 0);
    return transportDelta !== 0
      ? transportDelta
      : left.personaIndex - right.personaIndex;
  });

  const now = new Date();
  const timestamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const transcriptDir = path.join(SCRIPT_DIR, "transcripts");
  fs.mkdirSync(transcriptDir, { recursive: true });
  const transcriptPath = path.join(transcriptDir, `${timestamp}-${args.transport}.md`);

  const header = [
    `# Insurance Agent Local Eval Transcript - ${now.toISOString()}`,
    "",
    `- Base URL: ${baseUrl}`,
    `- Discovery: ${discoverySource}`,
    `- Personas: ${indicesToRun.map((index) => index + 1).join(", ")}`,
    `- Transports: ${transportsToRun.join(", ")}`,
    `- Concurrency: ${args.concurrency}`,
    `- Poll interval: ${args.pollIntervalMs}ms`,
    `- Poll timeout: ${args.pollTimeoutMs}ms`,
    "",
  ].join("\n");

  const transcriptBody = transportsToRun
    .map((transport) => {
      const transportResults = results.filter(
        (result) => result.transport === transport
      );
      const body = transportResults
        .flatMap((result) => result.transcript.lines)
        .join("\n");
      return [`## Transport: ${transport}`, "", body].join("\n");
    })
    .join("\n\n");
  fs.writeFileSync(transcriptPath, `${header}${transcriptBody}`, "utf-8");

  const wallElapsedSeconds = ((Date.now() - wallStartedAt) / 1000).toFixed(1);
  const totalPersonaMs = results.reduce(
    (sum, result) => sum + result.elapsedMs,
    0
  );
  const averagePersonaSeconds = (
    totalPersonaMs / Math.max(results.length, 1) / 1000
  ).toFixed(1);
  const speedup = (
    totalPersonaMs /
    1000 /
    Math.max(parseFloat(wallElapsedSeconds), 1)
  ).toFixed(1);

  console.log(`\n====================================`);
  console.log(`Eval complete!`);
  console.log(`Transcript: ${transcriptPath}`);
  console.log(`Wall time: ${wallElapsedSeconds}s`);
  console.log(`Avg/persona: ${averagePersonaSeconds}s (${results.length} personas)`);
  console.log(`Concurrency: ${args.concurrency} (speedup: ${speedup}x)`);

  const errors = results.filter((result) => result.error);
  if (errors.length > 0) {
    console.log(`\nErrors (${errors.length}):`);
    for (const result of errors) {
      console.log(
        `  [${result.transport}] P${result.personaIndex + 1} (${result.persona.name}): ${result.error}`
      );
    }
  }
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Fatal error: ${message}`);
  process.exitCode = 1;
});
