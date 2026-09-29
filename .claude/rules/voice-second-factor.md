---
paths:
  - "src/backend/services/voice_factor_preconditions.py"
  - "src/backend/services/voice_second_factor_store.py"
  - "src/backend/services/speaker_resolver.py"
  - "src/backend/services/speaker_enrollment_service.py"
  - "src/backend/services/speaker_service.py"
  - "src/backend/api/routes/speakers.py"
  - "src/frontend/src/components/auth/VoiceSecondFactorStep.tsx"
  - "src/frontend/src/hooks/useVoiceFactorRecording.ts"
  - "bin/voice_2fa_emergency.py"
---
# Stimme als zweiter Anmeldefaktor + Stimmabdruck-Datenschutz

Aus `auth.md` herausgelöst: jene Rule deckte fünf Subsysteme ab und lief auf 104
Zeilen: die Vorgabe (`CLAUDE.md`) ist eine Rule je Subsystem, ≤ 60 Zeilen.
Der Anmeldeweg selbst steht weiter dort; hier steht der Faktor.

Vollständige Begründung + Runbook: `docs/ACCESS_CONTROL.md`.
Hier nur die Invarianten und die Fallen.

- **`POST /auth/voice` ist der ZWEITE Faktor, nicht der erste** (`VOICE_AUTH_ENABLED`, aus auf beiden Instanzen).
  `/auth/login` gibt für ein Konto mit `users.voice_second_factor_enabled` **keine Token, keine Cookies**, nur ein
  Einmalticket (`services/voice_second_factor_store`, Redis-`GETDEL`); `/auth/voice` löst es ein und prüft **1:1**
  gegen das verknüpfte Profil. Die Einwilligung ist eine Spalte je Person (Art. 9 DSGVO), kein ConfigMap-Flag.
  🛑 **KEIN Rückfall** auf Passwort allein — einen Rückfall löst der Angreifer selbst aus.
- 🛑 **Vorbedingungen NUR in `services/voice_factor_preconditions`.** Login (`second_factor_applies`), die
  409-Prüfung der Verwaltungsroute und `/auth/voice` (`voice_path_blocker`) fragen dort. Schreibt eine Seite wieder
  eine eigene Bedingung daneben, entsteht der Fehler neu: die Hürde stand auf EINER Bedingung und fiel auf FÜNF,
  und jede Lücke dazwischen war eine Aussperrung. Fehlt eine Vorbedingung, **RUHT** die Hürde (WARNING) und die
  Einwilligung bleibt stehen — sie ist der Nachweis einer Erklärung, kein Schalter für das System.
- 🛑 **Einbettung vom VOICE-SERVER** (`voice_server_client.stt` + `_service_token`), NIE aus `SpeakerService`:
  `SPEAKER_INPROCESS_EMBEDDINGS_ENABLED` ist überall aus (der Riegel nennt „voice-login" selbst), und offen wäre es
  der falsche Vektorraum (gespeichert: ONNX). `verify_speaker` bleibt unberührt. Mindestdauer = die vom
  voice-server gemessene `audio_duration_s`; die 1,5 s der Maske sind Bedienführung, keine Prüfung.
- 🛑 **`POST /users/{id}/voice-second-factor`, nicht `PATCH /users/{id}`** — die Richtungen haben verschiedene
  Rechte: einschalten nur für sich selbst (403), ausschalten für sich mit `users.manage`, für ein FREMDES Konto nur
  mit `admin` (der Rückweg ist auch ein Angriffsweg). Einschalten ohne einlösbares Profil → 409.
- 🛑 **Ein-Admin-Instanz:** `users.manage` sitzt per Standard nur auf Admin → die einzige Administratorin mit
  scharfem Faktor und defektem Mikrofon befreit niemand. Notausgang `bin/voice_2fa_emergency.py` (nur abschalten).
- 🛑 **Einschalten ≠ Wirken, und die Maske bildet das nach.** `GET /api/config/features` führt
  `voice_auth_enabled` + `speaker_recognition_enabled`, sonst meldet die Benutzerliste „scharf", während der
  Faktor ruht (Abnahme-Befund 2026-09-28). Der **Einschalt**-Riegel lässt `voice_auth_enabled` AUS (sonst ist die
  Cutover-Reihenfolge unmöglich), das **Abzeichen** zählt beide. Flags noch nicht geladen → NICHT auf „ruht" schließen.
- 🛑 **Sperrzähler:** `voice_factor_lock_id(user_id)` = `voice2fa:<id>`, NICHT der Benutzername (ein Stimm-Fehlversuch
  darf den Passwortpfad nicht mitsperren). Liste und `/unlock` prüfen und räumen BEIDE.
- 🛑 **Jeder Fehlschlag antwortet identisch** (`{"success": false, "message": "Voice authentication failed"}`); das
  Antwortmodell trägt **keine** `speaker_id`/`speaker_name`/`confidence`/`user_id`/`username`. Bis 2026-09-27 tat es
  das und war ein unangemeldetes Namensorakel plus Gradient für eine Wiedereinspielung.
- Browser: `LoginPage` fängt `SecondFactorRequired` ab → `components/auth/VoiceSecondFactorStep`
  (`hooks/useVoiceFactorRecording`, kein VAD, keine Transkription). Die Maske reicht die Server-Meldung NICHT durch
  und bietet keinen Weg vorbei; unterscheidbar nur Gerätefehler. Mikrofon wird beim Unmount freigegeben.
- **With `SPEAKER_RECOGNITION_ENABLED=false` NO voiceprint is persisted on ANY path (Art. 9 GDPR):**
  `chat_handler._resolve_wire_speaker` + `speaker_resolver.resolve_speaker_from_embedding` (refuse before DB access),
  enrollment routes/service (409), `Meeting.segments` (`meeting_pipeline.strip_biometric_fields`, always), fingerprints.
