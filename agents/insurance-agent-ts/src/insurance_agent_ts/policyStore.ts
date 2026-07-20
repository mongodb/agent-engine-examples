/**
 * MongoDB-backed policy and claim storage for the insurance agent.
 *
 * TypeScript port of `insurance_agent/policy_store.py`. The policy store is the
 * source of truth for policies and claims; long-term memory (see `tools.ts`) is
 * supplementary. Factory functions return `null` when no MongoDB URI is set so
 * the agent still starts (memory-only) during early local experimentation.
 */

import { MongoClient, type Collection } from "mongodb";

const POLICY_COLLECTION = "insurance_policies";
const CLAIMS_COLLECTION = "insurance_claims";

/**
 * Fire-and-forget index creation that logs instead of crashing. A bare
 * `void createIndex(...)` leaves the promise's rejection unhandled, which under
 * Node's default policy terminates the process — the opposite of the "index
 * creation must not crash a tool call" intent.
 */
function ensureIndex(create: Promise<string>): void {
  create.catch((err: unknown) => {
    console.error(
      `[insurance-agent-ts] index creation failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  });
}

export interface Policy {
  policy_number: string;
  user_id: string;
  holder_name: string;
  holder_email: string;
  type: string;
  coverage: string;
  coverage_limit?: number;
  premium: string;
  deductible?: string;
  status?: string;
  risk_score?: number;
  created_at?: string;
  car_model: string;
  car_year: number;
  car_edition: string;
}

export type ClaimStatus =
  | "pending"
  | "under_review"
  | "approved"
  | "denied"
  | "suspended_for_review"
  | "closed";

export type RiskLevel = "low" | "medium" | "high";

export interface Claim {
  claim_id: string;
  policy_number: string;
  user_id: string;
  claim_type: string;
  claim_amount: number;
  description: string;
  status?: ClaimStatus;
  risk_assessment?: RiskLevel | null;
  risk_confidence?: number | null;
  recommendation?: string | null;
  reviewer_notes?: string | null;
  resolution?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  resolved_at?: string | null;
}

/** Strip the Mongo `_id` field from a document before returning it to callers. */
function withoutId<T extends Record<string, unknown>>(
  doc: T | null,
): Omit<T, "_id"> | null {
  if (doc === null) return null;
  const { _id, ...rest } = doc as T & { _id?: unknown };
  void _id;
  return rest;
}

export class PolicyStore {
  private readonly collection: Collection<Policy>;

  constructor(private readonly client: MongoClient, database: string) {
    this.collection = client.db(database).collection<Policy>(POLICY_COLLECTION);
    // Indexes are created best-effort; a failure here must not crash a tool call.
    ensureIndex(this.collection.createIndex({ policy_number: 1 }, { unique: true }));
    ensureIndex(this.collection.createIndex({ holder_email: 1 }));
    ensureIndex(this.collection.createIndex({ user_id: 1 }));
  }

  async getPolicy(policyNumber: string): Promise<Policy | null> {
    return withoutId(
      await this.collection.findOne(
        { policy_number: policyNumber.toUpperCase() },
        { projection: { _id: 0 } },
      ),
    ) as Policy | null;
  }

  async listByUserId(userId: string): Promise<Policy[]> {
    return this.collection
      .find({ user_id: userId }, { projection: { _id: 0 } })
      .toArray() as Promise<Policy[]>;
  }

  async createPolicy(policy: Policy): Promise<Policy> {
    const doc: Policy = {
      coverage_limit: 50000,
      deductible: "$500",
      risk_score: 0.3,
      ...policy,
      policy_number: policy.policy_number.toUpperCase(),
      holder_email: policy.holder_email.toLowerCase(),
      status: "Active",
      created_at: new Date().toISOString(),
    };
    await this.collection.insertOne({ ...doc });
    return doc;
  }
}

export class ClaimStore {
  private readonly collection: Collection<Claim>;

  constructor(private readonly client: MongoClient, database: string) {
    this.collection = client.db(database).collection<Claim>(CLAIMS_COLLECTION);
    ensureIndex(this.collection.createIndex({ claim_id: 1 }, { unique: true }));
    ensureIndex(this.collection.createIndex({ policy_number: 1 }));
    ensureIndex(this.collection.createIndex({ user_id: 1 }));
    ensureIndex(this.collection.createIndex({ status: 1 }));
  }

  async createClaim(claim: Claim): Promise<Claim> {
    const now = new Date().toISOString();
    const doc: Claim = {
      ...claim,
      claim_id: claim.claim_id.toUpperCase(),
      policy_number: claim.policy_number.toUpperCase(),
      status: "pending",
      created_at: now,
      updated_at: now,
    };
    await this.collection.insertOne({ ...doc });
    return doc;
  }

  async getClaim(claimId: string): Promise<Claim | null> {
    return withoutId(
      await this.collection.findOne(
        { claim_id: claimId.toUpperCase() },
        { projection: { _id: 0 } },
      ),
    ) as Claim | null;
  }

  async updateClaim(
    claimId: string,
    updates: Partial<Claim>,
  ): Promise<Claim | null> {
    const result = await this.collection.findOneAndUpdate(
      { claim_id: claimId.toUpperCase() },
      { $set: { ...updates, updated_at: new Date().toISOString() } },
      { returnDocument: "after", projection: { _id: 0 } },
    );
    return (result ?? null) as Claim | null;
  }

  async listByPolicy(policyNumber: string): Promise<Claim[]> {
    return this.collection
      .find({ policy_number: policyNumber.toUpperCase() }, { projection: { _id: 0 } })
      .sort({ created_at: -1 })
      .toArray() as Promise<Claim[]>;
  }

  async listByUserId(userId: string): Promise<Claim[]> {
    return this.collection
      .find({ user_id: userId }, { projection: { _id: 0 } })
      .sort({ created_at: -1 })
      .toArray() as Promise<Claim[]>;
  }

  async resolveClaim(
    claimId: string,
    resolution: string,
    reviewerNotes?: string,
  ): Promise<Claim | null> {
    const updates: Partial<Claim> = {
      status: "closed",
      resolution,
      resolved_at: new Date().toISOString(),
    };
    if (reviewerNotes) updates.reviewer_notes = reviewerNotes;
    return this.updateClaim(claimId, updates);
  }
}

/**
 * Build both stores over one shared `MongoClient`, or return nulls when no URI
 * is configured. The Node driver connects lazily, so no `await` is needed here;
 * the 2 s server-selection timeout keeps a misconfigured URI from hanging a tool
 * call for the default 30 s.
 */
export function createStores(
  mongodbUri: string,
  database: string,
): { policyStore: PolicyStore | null; claimStore: ClaimStore | null } {
  if (!mongodbUri) return { policyStore: null, claimStore: null };
  const client = new MongoClient(mongodbUri, {
    serverSelectionTimeoutMS: 2000,
  });
  return {
    policyStore: new PolicyStore(client, database),
    claimStore: new ClaimStore(client, database),
  };
}
