"""Start the redditgm v2 server.

Usage:
    python run.py                  # default: port 8000, auto-reload on
    python run.py --port 3000
    python run.py --no-reload      # production-ish mode, no file watching
"""

import argparse

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="redditgm v2 server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-reload", action="store_true", dest="no_reload")
    args = parser.parse_args()

    print(f"\n  redditgm v2  →  http://localhost:{args.port}\n")
    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
    )
