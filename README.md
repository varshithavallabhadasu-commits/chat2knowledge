# ContextForge

A small prototype that turns conversations into reusable knowledge while using **Hindsight as its only persistent memory system**.

## Run

1. Start Hindsight (Cloud or local) and set `HINDSIGHT_API_URL`. For a local server, follow [Hindsight's quick start](https://github.com/vectorize-io/hindsight#quick-start).
2. Create and activate an environment, then install dependencies:

   ```bash
   uv venv --python 3.11
   source .venv/bin/activate
   uv pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`, supply the Hindsight URL (and optional API key), then run:

   ```bash
   uvicorn app.main:app --reload
   ```

Open http://127.0.0.1:8000. The UI includes demo buttons for the two-conversation flow.

`Hindsight` must be available before retaining or recalling; ContextForge deliberately does not substitute a local JSON/SQL/vector-store fallback.
