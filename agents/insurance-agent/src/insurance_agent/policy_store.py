"""
PolicyStore - MongoDB-backed policy and claims storage for insurance agent.
"""

import logging
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field
from pymongo import MongoClient

logger = logging.getLogger(__name__)

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


class PolicyStore:
    """MongoDB-backed policy storage."""

    def __init__(self, mongodb_uri: str, database: str):
        # 2 s fast-fail rather than the default 30 s hang when the server is unreachable.
        self._client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=2000)
        self._collection = self._client[database][POLICY_COLLECTION]
        self._collection.create_index("policy_number", unique=True)
        self._collection.create_index("holder_email")  # For listing by customer
        self._collection.create_index("user_id")  # For listing by user_id

    def get_policy(self, policy_number: str) -> Optional[dict]:
        """Look up a policy by number."""
        return self._collection.find_one({"policy_number": policy_number.upper()}, {"_id": 0})

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
        # 2 s fast-fail rather than the default 30 s hang when the server is unreachable.
        self._client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=2000)
        self._collection = self._client[database][CLAIMS_COLLECTION]
        self._collection.create_index("claim_id", unique=True)
        self._collection.create_index("policy_number")
        self._collection.create_index("user_id")
        self._collection.create_index("status")

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

    def get_claim(self, claim_id: str) -> Optional[dict]:
        """Look up a claim by ID."""
        return self._collection.find_one({"claim_id": claim_id.upper()}, {"_id": 0})

    def update_claim(self, claim_id: str, updates: dict) -> Optional[dict]:
        """Update a claim with the given fields. Returns the updated claim."""
        updates["updated_at"] = datetime.now().isoformat()
        result = self._collection.find_one_and_update(
            {"claim_id": claim_id.upper()},
            {"$set": updates},
            return_document=True,
        )
        if result:
            return {k: v for k, v in result.items() if k != "_id"}
        return None

    def list_by_policy(self, policy_number: str) -> List[dict]:
        """List all claims for a policy."""
        return list(
            self._collection.find(
                {"policy_number": policy_number.upper()},
                {"_id": 0},
            ).sort("created_at", -1)
        )

    def list_by_user_id(self, user_id: str) -> List[dict]:
        """List all claims for a user."""
        return list(self._collection.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1))

    def resolve_claim(
        self,
        claim_id: str,
        resolution: str,
        reviewer_notes: Optional[str] = None,
    ) -> Optional[dict]:
        """Resolve a claim with final decision."""
        updates = {
            "status": "closed",
            "resolution": resolution,
            "resolved_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        if reviewer_notes:
            updates["reviewer_notes"] = reviewer_notes
        return self.update_claim(claim_id, updates)


def create_policy_store(mongodb_uri: str, database: str) -> Optional[PolicyStore]:
    """Factory function to create PolicyStore if MongoDB is configured."""
    if not mongodb_uri:
        return None
    try:
        return PolicyStore(mongodb_uri, database)
    except Exception as e:
        logger.warning(f"Failed to create PolicyStore: {e}")
        return None


def create_claim_store(mongodb_uri: str, database: str) -> Optional[ClaimStore]:
    """Factory function to create ClaimStore if MongoDB is configured."""
    if not mongodb_uri:
        return None
    try:
        return ClaimStore(mongodb_uri, database)
    except Exception as e:
        logger.warning(f"Failed to create ClaimStore: {e}")
        return None
