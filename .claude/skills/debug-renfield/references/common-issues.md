# Common Issues & Fixes

## Intent Recognition Problems

1. **Check HA keywords loaded:**
   ```bash
   curl http://localhost:8000/admin/refresh-keywords
   ```

2. **Test intent extraction directly:**
   ```bash
   curl -X POST "http://localhost:8000/debug/intent?message=YOUR_MESSAGE"
   ```

3. **Verify Ollama model loaded:**
   ```bash
   docker exec -it renfield-ollama ollama list
   ```

## WebSocket Connection Failures

- Check CORS settings in `src/backend/main.py`
- Verify frontend `VITE_WS_URL` matches backend WebSocket endpoint
- Check backend logs: `docker compose logs -f backend`

## Voice Input Not Working

- Whisper model loads lazily on first use (check logs for loading messages)
- Ensure audio file format is supported (WAV, MP3, OGG)
- Check backend logs for transcription errors

## Home Assistant Integration

- Verify token is valid (test in HA Developer Tools → Services)
- Check network connectivity between containers
- Ensure HA URL is accessible from Docker network
- Use `http://homeassistant.local:8123` or IP address, not `localhost`

## Satellite Issues

- **Satellite not finding backend**: Check Zeroconf advertisement with `docker compose logs backend | grep zeroconf`
- **ReSpeaker not detected**: Check for GPIO4 conflict with `w1-gpio` overlay (disable in `/boot/firmware/config.txt`)
- **Wrong microphone**: Ensure `.asoundrc` is configured for ReSpeaker — copy from `src/satellite/config/asoundrc`
- **Garbled transcription**: PyAudio must be installed (not soundcard) for ALSA support
- **GPIO errors**: Add user to gpio group: `sudo usermod -aG gpio $USER`
- **lgpio build fails**: Install `swig` and `liblgpio-dev` system packages
- **openwakeword on Python 3.13+**: Install with `--no-deps` (tflite-runtime has no Python 3.13 wheels)

## MCP Server Issues

- Check if server is enabled in `.env` (e.g. `WEATHER_ENABLED=true`)
- Verify MCP server is running: check `docker compose logs backend` for connection errors
- For stdio servers: ensure `npx` is on PATH and env vars are set
- Test server health directly if it has an HTTP endpoint

## Database Issues

- **Migration errors**: `docker exec -it renfield-backend alembic upgrade head`
- **Rollback**: `docker exec -it renfield-backend alembic downgrade -1`
- **pgvector index issues**: Both HNSW and IVFFlat have 2000-dim limit on regular `vector` type — use `halfvec` cast for larger dimensions
