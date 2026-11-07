"""Fast mode: a live voice conversation with Gemini (speech in, speech out, near-instant).

The live model is only the voice. For anything beyond small talk it calls ``ask_stark``, which hands the request
to the assistant through the normal chat path, so tools, approvals, modes and the emergency stop all apply, and
the exchange is saved in the conversation. Push-to-talk: audio is sent only while the button is held.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

from .gemini import API, VOICES

WS = ("wss://generativelanguage.googleapis.com/ws/"
      "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")

PERSONA = """You are {name}, an assistant inside STARK AI, speaking with the user by voice. {style}.
You are only the voice. For anything beyond greetings and small talk (facts, the user's posts, analytics,
accounts, drafts, reminders, their computer, memory, or any action) call ask_stark with the user's request in
their own words, then say what it returned, briefly and faithfully. Never say something was done unless ask_stark
said so. If it says an approval is needed, tell the user to approve it on screen; you cannot approve anything.
Reply in the language the user speaks (English or Urdu). Keep spoken answers to one to three sentences."""

TOOL = {"functionDeclarations": [{
    "name": "ask_stark",
    "description": "Hand the user's request to the STARK AI assistant (tools, data, actions) and get the answer.",
    "parameters": {"type": "OBJECT", "properties": {"request": {"type": "STRING"}}, "required": ["request"]}}]}


async def live_model(key: str, transport=None) -> str:
    async with httpx.AsyncClient(base_url=API, timeout=20, transport=transport, headers={"x-goog-api-key": key}) as c:
        r = await c.get("/models", params={"pageSize": 1000})
        r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "bidiGenerateContent" in (m.get("supportedGenerationMethods") or [])]
    names.sort(key=lambda n: ("audio" in n or "native" in n, "flash" in n, n), reverse=True)
    return names[0] if names else ""


class LiveSession:
    def __init__(self, key: str, audio, kernel, bus, agent_id: str, voice: str, style: str, connect=None):
        self.key, self.audio, self.kernel, self.bus = key, audio, kernel, bus
        self.agent_id, self.voice, self.style = agent_id, voice if voice in VOICES else "Charon", style
        self._connect = connect
        self.ws: Any = None
        self.task: asyncio.Task | None = None
        self.player = None
        self.talking = False
        self.model = ""

    @property
    def open(self) -> bool:
        return self.ws is not None and self.task is not None and not self.task.done()

    async def start(self) -> None:
        if self._connect is None:
            import websockets

            self._connect = lambda url, headers: websockets.connect(url, additional_headers=headers, max_size=None)
        self.model = self.model or await live_model(self.key)
        if not self.model:
            raise RuntimeError("This Gemini key has no live voice model.")
        agent = self.kernel.registry.get(self.agent_id)
        self.ws = await self._connect(WS, {"x-goog-api-key": self.key})
        await self.ws.send(json.dumps({"setup": {
            "model": f"models/{self.model}",
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}}},
            "systemInstruction": {"parts": [{"text": PERSONA.format(name=agent.name, style=self.style or "Be calm and clear")}]},
            "tools": [TOOL],
            "inputAudioTranscription": {}, "outputAudioTranscription": {},
            "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}}}}))
        first = json.loads(await asyncio.wait_for(self.ws.recv(), 15))
        if "setupComplete" not in first:
            raise RuntimeError("Gemini did not start the live session.")
        self.task = asyncio.create_task(self._receive())

    async def talk(self, down: bool) -> None:
        """Push-to-talk: stream the microphone while held."""
        if down and not self.talking:
            self.talking = True
            if self.player is not None:
                self.audio.stop_playing()  # talking over the assistant interrupts it
                self.player = None
            await self.ws.send(json.dumps({"realtimeInput": {"activityStart": {}}}))
            loop = asyncio.get_running_loop()

            def chunk(data: bytes) -> None:
                msg = json.dumps({"realtimeInput": {"audio": {"data": base64.b64encode(data).decode(),
                                                               "mimeType": "audio/pcm;rate=16000"}}})
                asyncio.run_coroutine_threadsafe(self.ws.send(msg), loop)

            await asyncio.to_thread(self.audio.stream, chunk)
        elif not down and self.talking:
            self.talking = False
            self.audio.cancel()
            await self.ws.send(json.dumps({"realtimeInput": {"activityEnd": {}}}))

    async def _ask(self, call: dict[str, Any]) -> None:
        request = str((call.get("args") or {}).get("request", ""))[:4000]
        try:
            task = self.kernel.submit_chat(self.agent_id, request)
            done = await self.kernel.wait(task["id"], timeout=90)
            status = done.get("status")
            if status == "completed":
                out = done.get("result") or "Done."
            elif status == "waiting_approval":
                out = "This needs the user's approval on screen before it can continue."
            else:
                out = f"It did not finish ({status})."
        except RuntimeError as exc:  # e.g. the emergency stop is engaged
            out = str(exc)
        await self.ws.send(json.dumps({"toolResponse": {"functionResponses": [
            {"id": call.get("id"), "name": "ask_stark", "response": {"output": out[:6000]}}]}}))

    async def _receive(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                for call in (msg.get("toolCall") or {}).get("functionCalls") or []:
                    asyncio.create_task(self._ask(call))
                sc = msg.get("serverContent") or {}
                for kind, role in (("inputTranscription", "user"), ("outputTranscription", "assistant")):
                    text = (sc.get(kind) or {}).get("text")
                    if text:
                        self.bus.publish("voice.live", {"agent_id": self.agent_id, "role": role, "text": text})
                for part in (sc.get("modelTurn") or {}).get("parts") or []:
                    inline = part.get("inlineData") or {}
                    if inline.get("data"):
                        if self.player is None:
                            self.player = self.audio.player(24000)
                        self.player.feed(base64.b64decode(inline["data"]))
                if sc.get("interrupted"):
                    self.audio.stop_playing()
                    self.player = None
                if sc.get("turnComplete") and self.player is not None:
                    self.player.close()
                    self.player = None
                    self.bus.publish("voice.live", {"agent_id": self.agent_id, "role": "assistant", "done": True})
        except Exception:  # noqa: BLE001 - a dropped connection just ends the session
            pass
        finally:
            self.ws = None

    async def stop(self) -> None:
        if self.talking:
            self.audio.cancel()
            self.talking = False
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self.task:
            self.task.cancel()
        self.ws = None
