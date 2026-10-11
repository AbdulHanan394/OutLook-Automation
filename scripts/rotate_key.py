"""Rotate encrypted refresh-token values without exposing plaintext in logs."""
import argparse
import os
from sqlalchemy import create_engine, text
from app.security.crypto import TokenCipher

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--old-key", required=True); parser.add_argument("--new-key", required=True); parser.add_argument("--new-key-id", default="v2")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    old, new = TokenCipher("v1", [args.old_key]), TokenCipher(args.new_key_id, [args.new_key])
    with engine.begin() as db:
        for row in db.execute(text("SELECT id, encrypted_refresh_token FROM mailbox_connections WHERE encrypted_refresh_token IS NOT NULL")):
            db.execute(text("UPDATE mailbox_connections SET encrypted_refresh_token=:token WHERE id=:id"), {"id": row.id, "token": new.encrypt(old.decrypt(bytes(row.encrypted_refresh_token)))})
if __name__ == "__main__": main()
