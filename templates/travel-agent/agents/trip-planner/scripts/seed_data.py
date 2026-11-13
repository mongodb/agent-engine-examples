# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo>=4.9", "python-dotenv>=1.0"]
# ///
"""Load the flights and listings in data/ into MongoDB.

Reads MONGODB_URI and MONGODB_DATABASE from the environment or from .env in the
current directory. Re-running replaces the flights and listings collections.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

load_dotenv(Path.cwd() / ".env")
database = MongoClient(os.environ["MONGODB_URI"])[
    os.environ.get("MONGODB_DATABASE", "travel_agent")
]
for name in ("flights", "listings"):
    docs = json.loads((DATA_DIR / f"{name}.json").read_text())
    database[name].drop()
    database[name].insert_many(docs)
    print(f"Loaded {len(docs)} documents into {database.name}.{name}")
