"""
PolicyStore - MongoDB-backed policy and claims storage for insurance agent.

All reads and updates are scoped by user_id: identifiers supplied by the model
or the customer can only ever address records that already belong to the
current user. Claim resolution enforces a deterministic state machine so a
claim can only close along the path its risk analysis and (when required)
human review authorized.
"""

import threading
from concurrent.futures import Future
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.collection import Collection

# Collection name constants
POLICY_COLLECTION = "insurance_policies"
CLAIMS_COLLECTION = "insurance_claims"


class Policy(BaseModel):
    """Pydantic model for insurance policy."""

    policy_number: str = Field(..., description="Unique policy identifier")
    user_id: str = Field(..., description="User ID of the policy holder")
    holder_name: str = Field(..., description="Name of the policy holder")
    holder_email: str = Field(..., description="Email of the policy holder")
    type: str = Field(..., description="Type of insurance (e.g., 'Auto Insurance')")
    coverage: str = Field(..., description="Coverage level (e.g., 'Comprehensive')")
    coverage_limit: int = Field(default=50000, description="Maximum coverage amount in dollars")
    premium: str = Field(..., description="Monthly/annual premium")
    deductible: str = Field(default="$500", description="Deductible amount")
    status: str = Field(default="Active", description="Policy status")
    risk_score: float = Field(default=0.3, description="Customer risk score (0.0-1.0)")
    created_at: Optional[str] = Field(default=None, description="Creation timestamp")
    # Vehicle information
    car_model: str = Field(..., description="Vehicle model (e.g., 'Camry', 'Model 3')")
    car_year: int = Field(..., description="Vehicle year (e.g., 2024)")
    car_edition: str = Field(..., description="Vehicle edition/trim (e.g., 'SE', 'Long Range')")


# Claim status types
ClaimStatus = Literal[
    "pending",
    "under_review",
    "approved",
    "denied",
    "suspended_for_review",
    "closed",
]

# Risk assessment types
RiskLevel = Literal["low", "medium", "high"]


class Claim(BaseModel):
    """Pydantic model for insurance claim."""

    claim_id: str = Field(..., description="Unique claim identifier")
    policy_number: str = Field(..., description="Associated policy number")
    user_id: str = Field(..., description="User ID of the claimant")
    claim_type: str = Field(
        ..., description="Type of claim (e.g., 'collision', 'theft', 'comprehensive')"
    )
    claim_amount: float = Field(..., description="Claimed amount in dollars")
    description: str = Field(..., description="Description of the incident")
    status: ClaimStatus = Field(default="pending", description="Current claim status")
    risk_assessment: Optional[RiskLevel] = Field(
        default=None, description="Risk level from analysis"
    )
    risk_confidence: Optional[float] = Field(
        default=None, description="Confidence score of risk assessment (0.0-1.0)"
    )
    recommendation: Optional[str] = Field(
        default=None, description="Recommendation from risk analysis"
    )
    reviewer_notes: Optional[str] = Field(default=None, description="Notes from human reviewer")
    resolution: Optional[str] = Field(
        default=None, description="Final resolution (approved/denied/adjusted)"
    )
    created_at: Optional[str] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[str] = Field(default=None, description="Last update timestamp")
    resolved_at: Optional[str] = Field(default=None, description="Resolution timestamp")


class _LazyCollection:
    """A MongoDB collection that connects, and creates its indexes, on first use.

    Building a ``MongoClient`` for a ``mongodb+srv`` URI resolves DNS, and creating
    indexes contacts the server. Doing either at import time fails when the agent
    starts before it can reach the database (for example before Atlas egress is
    ready), and the store would stay broken for the life of the process. Nothing
    is kept until the connection and its indexes succeed, so a call made while the
    database is unreachable raises, and the next call tries again.
    """

    def __init__(self, mongodb_uri: str, database: str, name: str, indexes: list[tuple[str, bool]]):
        """Keep the connection settings; ``indexes`` pairs each key with whether it is unique."""
        self._mongodb_uri = mongodb_uri
        self._database = database
        self._name = name
        self._indexes = indexes
        self._collection: Optional[Collection] = None
        # Guards the connection attempt that overlapping tool calls share.
        self._lock = threading.Lock()
        self._attempt: Optional[Future] = None

    def get(self) -> Collection:
        """Return the collection, connecting first if no earlier call succeeded.

        Raises a ``PyMongoError`` when the database cannot be reached. Calls that
        overlap a connection attempt share its outcome rather than starting their
        own, so no call spans more than one attempt.
        """
        if self._collection is not None:
            return self._collection
        with self._lock:
            # An attempt may have connected since the check above.
            if self._collection is not None:
                return self._collection
            attempt, owner = self._attempt, self._attempt is None
            if owner:
                attempt = self._attempt = Future()
        if not owner:
            return attempt.result()
        try:
            self._collection = self._connect()
            attempt.set_result(self._collection)
            return self._collection
        except BaseException as error:
            # Calls joined to this attempt wait on it, so settle it however it ends.
            attempt.set_exception(error)
            raise
        finally:
            with self._lock:
                self._attempt = None

    def _connect(self) -> Collection:
        """Connect and create the indexes, closing the client if either fails."""
        # 2 s fast-fail rather than the driver defaults (20 s for the
        # mongodb+srv DNS lookup, 30 s for server selection). The socket timeout
        # stops a server that connects but never answers from holding a call, and
        # every call joined to it, forever.
        client: MongoClient = MongoClient(
            self._mongodb_uri,
            connectTimeoutMS=2000,
            serverSelectionTimeoutMS=2000,
            socketTimeoutMS=10000,
        )
        try:
            collection = client[self._database][self._name]
            for keys, unique in self._indexes:
                collection.create_index(keys, unique=unique)
        except BaseException:
            client.close()
            raise
        return collection


class PolicyStore:
    """MongoDB-backed policy storage."""

    def __init__(self, mongodb_uri: str, database: str):
        self._lazy = _LazyCollection(
            mongodb_uri,
            database,
            POLICY_COLLECTION,
            [
                ("policy_number", True),
                ("holder_email", False),  # For listing by customer
                ("user_id", False),  # For listing by user_id
            ],
        )

    @property
    def _collection(self) -> Collection:
        return self._lazy.get()

    def get_policy(self, policy_number: str, user_id: str) -> Optional[dict]:
        """Look up a policy by number for the current user.

        A policy belonging to a different user is indistinguishable from a
        missing one.
        """
        return self._collection.find_one(
            {"policy_number": policy_number.upper(), "user_id": user_id}, {"_id": 0}
        )

    def list_by_user_id(self, user_id: str) -> List[dict]:
        """List all policies for a customer by user_id."""
        return list(self._collection.find({"user_id": user_id}, {"_id": 0}))

    def create_policy(self, policy: Policy) -> dict:
        """Create a new policy. Returns the created policy."""
        policy_dict = policy.model_dump()
        policy_dict["policy_number"] = policy_dict["policy_number"].upper()
        policy_dict["holder_email"] = policy_dict["holder_email"].lower()
        policy_dict["status"] = "Active"
        policy_dict["created_at"] = datetime.now().isoformat()
        self._collection.insert_one(policy_dict)
        return {k: v for k, v in policy_dict.items() if k != "_id"}


class ClaimStore:
    """MongoDB-backed claims storage."""

    def __init__(self, mongodb_uri: str, database: str):
        self._lazy = _LazyCollection(
            mongodb_uri,
            database,
            CLAIMS_COLLECTION,
            [
                ("claim_id", True),
                ("policy_number", False),
                ("user_id", False),
                ("status", False),
            ],
        )

    @property
    def _collection(self) -> Collection:
        return self._lazy.get()

    def create_claim(self, claim: Claim) -> dict:
        """Create a new claim. Returns the created claim."""
        claim_dict = claim.model_dump()
        claim_dict["claim_id"] = claim_dict["claim_id"].upper()
        claim_dict["policy_number"] = claim_dict["policy_number"].upper()
        claim_dict["status"] = "pending"
        claim_dict["created_at"] = datetime.now().isoformat()
        claim_dict["updated_at"] = claim_dict["created_at"]
        self._collection.insert_one(claim_dict)
        return {k: v for k, v in claim_dict.items() if k != "_id"}

    def get_claim(self, claim_id: str, user_id: str) -> Optional[dict]:
        """Look up a claim by ID for the current user.

        A claim belonging to a different user is indistinguishable from a
        missing one.
        """
        return self._collection.find_one(
            {"claim_id": claim_id.upper(), "user_id": user_id}, {"_id": 0}
        )

    def update_claim(self, claim_id: str, user_id: str, updates: dict) -> Optional[dict]:
        """Update a claim with the given fields. Returns the updated claim."""
        updates["updated_at"] = datetime.now().isoformat()
        result = self._collection.find_one_and_update(
            {"claim_id": claim_id.upper(), "user_id": user_id},
            {"$set": updates},
            return_document=True,
        )
        if result:
            return {k: v for k, v in result.items() if k != "_id"}
        return None

    def list_by_policy(self, policy_number: str, user_id: str) -> List[dict]:
        """List all claims for a policy owned by the current user."""
        return list(
            self._collection.find(
                {"policy_number": policy_number.upper(), "user_id": user_id},
                {"_id": 0},
            ).sort("created_at", -1)
        )

    def list_by_user_id(self, user_id: str) -> List[dict]:
        """List all claims for a user."""
        return list(self._collection.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1))

    def resolve_claim(
        self,
        claim_id: str,
        user_id: str,
        resolution: str,
        reviewer_notes: Optional[str] = None,
    ) -> Optional[dict]:
        """Resolve a claim along the path its analysis and review authorized.

        Enforces the claim state machine:
        - The claim must have completed risk analysis (status "under_review").
        - When analysis recommended auto-approval, the only valid resolution is
          "approved".
        - Otherwise a human review decision (approved/denied) is required.

        Raises:
            ValueError: When the claim is missing, already resolved, not yet
                analyzed, or the resolution does not match the authorized path.
        """
        claim = self.get_claim(claim_id, user_id)
        if not claim:
            raise ValueError(f"Claim not found: {claim_id}")
        if claim.get("status") == "closed" or claim.get("resolution"):
            raise ValueError(f"Claim {claim_id} is already resolved")
        if claim.get("status") != "under_review":
            raise ValueError(
                f"Claim {claim_id} has not completed risk analysis; run analyze_claim_risk first"
            )
        recommendation = claim.get("recommendation")
        if recommendation == "auto_approve":
            if resolution != "approved":
                raise ValueError(
                    f"Claim {claim_id} was recommended for auto-approval; "
                    "only 'approved' is valid without human review"
                )
        elif recommendation in {"review_recommended", "manual_review_required"}:
            if resolution not in {"approved", "denied"}:
                raise ValueError(
                    f"Claim {claim_id} requires human review; "
                    "resolution must be the reviewer's 'approved' or 'denied' decision"
                )
        else:
            raise ValueError(
                f"Claim {claim_id} has no valid authorization for resolution: {recommendation!r}"
            )
        updates = {
            "status": "closed",
            "resolution": resolution,
            "resolved_at": datetime.now().isoformat(),
        }
        if reviewer_notes:
            updates["reviewer_notes"] = reviewer_notes
        return self.update_claim(claim_id, user_id, updates)


def create_policy_store(mongodb_uri: str, database: str) -> Optional[PolicyStore]:
    """Create a PolicyStore when MongoDB is configured; it connects on first use."""
    if not mongodb_uri:
        return None
    return PolicyStore(mongodb_uri, database)


def create_claim_store(mongodb_uri: str, database: str) -> Optional[ClaimStore]:
    """Create a ClaimStore when MongoDB is configured; it connects on first use."""
    if not mongodb_uri:
        return None
    return ClaimStore(mongodb_uri, database)
