# Open Notebook + local TTS for the learning gateway

The default backend of the learning gateway ([spec 012](../../specs/012-learning-gateway/spec.md)): Open Notebook
(notebooks, sources, podcast generation) and Speaches (OpenAI-compatible TTS with the Kokoro voice model).
LM Studio stays the only model server: Open Notebook reaches it at `http://host.docker.internal:1234/v1`.

```bash
cd deploy/open-notebook
cp .env.example .env            # fill the three values; .env is git-ignored
docker compose up -d
curl http://127.0.0.1:5055/health   # {"status":"healthy"}
uv run python -m pkpipe.learning.open_notebook_setup
```

The setup script is idempotent. It downloads the Kokoro voice model into Speaches (once), registers LM Studio and
Speaches as providers (no API key is needed, the placeholder is not a credential), registers the models, sets the
defaults and creates the `pkpipe_duo` speaker profile (voices `af_heart` and `am_adam`) and the `pkpipe_overview`
episode profile that the gateway generates with. Pass `--chat-model`, `--podcast-model`, `--tts-model` to use other
models; `--help` lists everything.

| Service | URL (loopback only) |
| --- | --- |
| Open Notebook UI | http://127.0.0.1:8502 |
| Open Notebook API (`/health`, `/docs`) | http://127.0.0.1:5055 |
| Speaches TTS (`/health`) | http://127.0.0.1:8969 |
| SurrealDB | http://127.0.0.1:8000 |

Notes

- The outline and the transcript are written by `--podcast-model` (default `google/gemma-4-12b`). Qwen3.5 writes its
  reasoning in front of the JSON, which Open Notebook's podcast pipeline cannot parse. Generation on CPU takes
  minutes (a 3 segment overview: about 10 minutes with a 12B model); the gateway timeout defaults to 90 minutes.
- Ports are bound to `127.0.0.1`. Do not publish them: Open Notebook's API has authentication disabled by default.
- Images are pinned by digest. To update, change tag and digest together and re-run the setup script.
- Data lives in `./data` (git-ignored); `docker compose down -v` also deletes the Kokoro model cache.
