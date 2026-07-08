import argparse
import json
import tomllib
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore


def load_new_service_account(secrets_path: Path) -> dict:
    data = tomllib.loads(secrets_path.read_text(encoding="utf-8"))
    if "firebase" not in data:
        raise RuntimeError(f"{secrets_path} does not contain a [firebase] section")
    return dict(data["firebase"])


def copy_document(old_doc, new_doc, counters):
    snapshot = old_doc.get()
    if not snapshot.exists:
        return

    new_doc.set(snapshot.to_dict() or {})
    counters["docs"] += 1

    for subcollection in old_doc.collections():
        counters["collections"].add(subcollection.id)
        for child_doc in subcollection.stream():
            copy_document(child_doc.reference, new_doc.collection(subcollection.id).document(child_doc.id), counters)


def migrate_all(old_json: Path, new_secrets: Path):
    old_app = firebase_admin.initialize_app(credentials.Certificate(json.loads(old_json.read_text(encoding="utf-8"))), name="old")
    new_app = firebase_admin.initialize_app(credentials.Certificate(load_new_service_account(new_secrets)), name="new")
    old_db = firestore.client(app=old_app)
    new_db = firestore.client(app=new_app)

    counters = {"docs": 0, "collections": set()}
    for collection in old_db.collections():
        counters["collections"].add(collection.id)
        for doc in collection.stream():
            copy_document(doc.reference, new_db.collection(collection.id).document(doc.id), counters)

    return counters


def main():
    parser = argparse.ArgumentParser(description="Copy all Firestore collections/documents to the new Streamlit Firebase project.")
    parser.add_argument("--old-json", required=True, help="Path to the old Firebase Admin SDK service account JSON")
    parser.add_argument(
        "--new-secrets",
        default=str(Path(__file__).parent / ".streamlit" / "secrets.toml"),
        help="Path to the new Streamlit secrets.toml containing [firebase]",
    )
    args = parser.parse_args()
    counters = migrate_all(Path(args.old_json), Path(args.new_secrets))
    print(f"Copied {counters['docs']} documents from {len(counters['collections'])} collections.")


if __name__ == "__main__":
    main()
