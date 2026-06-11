"""Launch the website locally:  python serve.py   (then open http://localhost:8000)
Live free data by default. For an offline demo:  TURB_MODE=synthetic python serve.py"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  Turbulence Monitor -> http://localhost:{port}\n  (Ctrl+C to stop)\n")
    uvicorn.run("web.server:app", host="0.0.0.0", port=port, reload=False)
