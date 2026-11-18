export const REVIEW_POLL_INTERVAL_MS = 10_000;

export type PendingReview = {
  execution_id: string;
  status: string;
  session_id?: string;
  suspend_reason?: string;
  suspend_context?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

export type PendingInterrupt = {
  id: string;
  value: unknown;
  responseSchema?: unknown;
};

export type ResumeReviewRequest =
  | {
      type: "decision";
      decision: string;
      reviewer_notes?: string;
    }
  | {
      type: "interrupt";
      session_id: string;
      resume_map: Record<string, unknown>;
    };

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function getPendingInterrupts(
  review: PendingReview
): PendingInterrupt[] | null {
  const interrupts = review.suspend_context?.interrupts;
  if (!Array.isArray(interrupts) || interrupts.length === 0) return null;

  const pending = interrupts.flatMap((interrupt): PendingInterrupt[] => {
    if (!isRecord(interrupt) || typeof interrupt.id !== "string" || !interrupt.id) {
      return [];
    }
    const value = interrupt.value;
    return [
      {
        id: interrupt.id,
        value,
        ...(isRecord(value) && Object.hasOwn(value, "response_schema")
          ? { responseSchema: value.response_schema }
          : {}),
      },
    ];
  });
  return pending.length > 0 ? pending : null;
}

const RESPONSE_SCHEMA_KEYS = new Set([
  "type",
  "properties",
  "required",
  "additionalProperties",
  "title",
  "description",
]);
const RESPONSE_FIELD_SCHEMA_KEYS = new Set([
  "type",
  "enum",
  "title",
  "description",
]);

function isScalar(value: unknown): value is string | number | boolean {
  return (
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

function matchesType(value: string | number | boolean, type: unknown): boolean {
  if (type === undefined) return true;
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  return typeof value === type;
}

function isSupportedResponseSchema(schema: unknown): schema is Record<string, unknown> {
  if (
    !isRecord(schema) ||
    schema.type !== "object" ||
    !isRecord(schema.properties) ||
    Object.keys(schema).some((key) => !RESPONSE_SCHEMA_KEYS.has(key)) ||
    (schema.additionalProperties !== undefined && schema.additionalProperties !== false)
  ) {
    return false;
  }

  const required = schema.required;
  if (
    required !== undefined &&
    (!Array.isArray(required) || !required.every((name) => typeof name === "string"))
  ) {
    return false;
  }
  if (
    Array.isArray(required) &&
    required.some((name) => !Object.hasOwn(schema.properties as object, name))
  ) {
    return false;
  }

  return Object.values(schema.properties).every((fieldSchema) => {
    if (
      !isRecord(fieldSchema) ||
      Object.keys(fieldSchema).some((key) => !RESPONSE_FIELD_SCHEMA_KEYS.has(key))
    ) {
      return false;
    }
    if (fieldSchema.enum !== undefined) {
      return (
        Array.isArray(fieldSchema.enum) &&
        fieldSchema.enum.length > 0 &&
        fieldSchema.enum.every(isScalar) &&
        fieldSchema.enum.every((option) => matchesType(option, fieldSchema.type))
      );
    }
    return ["string", "boolean", "number", "integer"].includes(
      String(fieldSchema.type)
    );
  });
}

function initialSchemaValue(schema: unknown): unknown {
  if (!isSupportedResponseSchema(schema)) return null;
  const required = Array.isArray(schema.required) ? schema.required : [];
  return Object.fromEntries(required.map((name) => [name, null]));
}

export function initialResumeMap(interrupts: PendingInterrupt[]): string {
  return JSON.stringify(
    Object.fromEntries(
      interrupts.map((interrupt) => [
        interrupt.id,
        initialSchemaValue(interrupt.responseSchema),
      ])
    ),
    null,
    2
  );
}

function schemaValueError(
  value: unknown,
  schema: unknown,
  label: string
): string | null {
  if (!isRecord(schema)) return null;

  if (schema.type === "object") {
    if (!isSupportedResponseSchema(schema)) return null;
    if (!isRecord(value)) return `${label} must be a JSON object.`;
    const properties = schema.properties as Record<string, unknown>;
    const required = Array.isArray(schema.required)
      ? schema.required.filter((name): name is string => typeof name === "string")
      : [];
    const missing = required.find((name) => !Object.hasOwn(value, name));
    if (missing) return `${missing.replaceAll("_", " ")} is required.`;

    if (schema.additionalProperties === false) {
      const unexpected = Object.keys(value).find(
        (name) => !Object.hasOwn(properties, name)
      );
      if (unexpected) return `Unexpected answer field: ${unexpected}.`;
    }

    for (const [name, propertySchema] of Object.entries(properties)) {
      if (!Object.hasOwn(value, name)) continue;
      const propertyLabel =
        isRecord(propertySchema) && typeof propertySchema.title === "string"
          ? propertySchema.title
          : name.replaceAll("_", " ");
      const error = schemaValueError(value[name], propertySchema, propertyLabel);
      if (error) return error;
    }
    return null;
  }

  if (
    Array.isArray(schema.enum) &&
    !schema.enum.some((option) => Object.is(option, value))
  ) {
    return `${label} must be one of: ${schema.enum
      .map((option) => JSON.stringify(option))
      .join(", ")}.`;
  }

  if (schema.type === "string" && typeof value !== "string") {
    return `${label} must be text.`;
  }
  if (schema.type === "boolean" && typeof value !== "boolean") {
    return `${label} must be true or false.`;
  }
  if (
    schema.type === "number" &&
    (typeof value !== "number" || !Number.isFinite(value))
  ) {
    return `${label} must be a number.`;
  }
  if (
    schema.type === "integer" &&
    (typeof value !== "number" || !Number.isInteger(value))
  ) {
    return `${label} must be an integer.`;
  }
  if (schema.type === "array" && !Array.isArray(value)) {
    return `${label} must be a JSON array.`;
  }
  return null;
}

export function parseResumeMap(
  text: string,
  interrupts: PendingInterrupt[]
): { resumeMap: Record<string, unknown> } | { error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "Resume map must be valid JSON." };
  }
  if (!isRecord(parsed) || Object.keys(parsed).length === 0) {
    return { error: "Resume map must be a non-empty JSON object." };
  }

  const expectedIds = new Set(interrupts.map((interrupt) => interrupt.id));
  const missingIds = [...expectedIds].filter((id) => !Object.hasOwn(parsed, id));
  const extraIds = Object.keys(parsed).filter((id) => !expectedIds.has(id));
  if (missingIds.length > 0 || extraIds.length > 0) {
    const details = [
      missingIds.length > 0 ? `Missing: ${missingIds.join(", ")}.` : "",
      extraIds.length > 0 ? `Unexpected: ${extraIds.join(", ")}.` : "",
    ]
      .filter(Boolean)
      .join(" ");
    return {
      error: `Resume map keys must exactly match the pending interrupt IDs. ${details}`,
    };
  }

  for (const interrupt of interrupts) {
    const error = schemaValueError(
      parsed[interrupt.id],
      interrupt.responseSchema,
      `Answer for ${interrupt.id}`
    );
    if (error) return { error };
  }
  return { resumeMap: parsed };
}
