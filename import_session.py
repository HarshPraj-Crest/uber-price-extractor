# import_session.py
import base64
import sys
from pathlib import Path
from save_as_account import save_session_as_account

def main():
    if len(sys.argv) < 3:
        print("Usage: python import_session.py <session_name> <account_name> [b64_file] [proxy_url]")
        sys.exit(1)
    
    session_name = sys.argv[1]
    account_name = sys.argv[2]
    b64_path = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else Path("saved_sessions") / f"{session_name}.b64"
    proxy_url = sys.argv[4] if len(sys.argv) > 4 else ""
    
    if not b64_path.exists():
        print(f"❌ Base64 file not found: {b64_path}")
        sys.exit(1)
        
    b64_text = b64_path.read_text(encoding="utf-8").strip()
    data = base64.b64decode(b64_text)
    
    session_file = Path("saved_sessions") / f"{session_name}.json"
    session_file.parent.mkdir(exist_ok=True)
    session_file.write_bytes(data)
    print(f"✅ Decoded {len(data)} bytes -> {session_file}")
    
    save_session_as_account(session_name, account_name, proxy=proxy_url)

if __name__ == "__main__":
    main()
