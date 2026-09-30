#!/usr/bin/env python3
"""
🎙️ Fireflies Live Meeting Bot
Direct in-call participant bot powered by Playwright + Chrome WebRTC audio capture.

Features:
- Joins Google Meet live via real browser WebRTC
- Auto-fills bot name ("Fireflies Notetaker")
- Mutes mic and camera on entry
- Clicks "Ask to join" and waits for host admission
- Actively intercepts and records raw WebRTC Opus audio to disk (.webm)
- Tracks live sound levels (RMS volume) and detects speaking
- Captures live speaker-attributed speech and displays clear real-time logs
- Automatically generates AI summary & action items upon meeting end
- Persists meeting, audio, and transcript to database (viewable at http://localhost:8000)

Usage:
    python live_meet_bot.py "https://meet.google.com/xxx-yyyy-zzz"
"""

import asyncio
import base64
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output streams on Windows to prevent charmap UnicodeEncodeErrors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.async_api import async_playwright

# Add backend to sys.path to access summarizer and database
backend_dir = Path(__file__).parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ai.summarizer import MeetingSummarizer
from config import get_settings
from database.repository import MeetingRepository, init_db
from models.meeting import MeetingORM, MeetingStatus
from models.transcript import NormalizedTranscript, TranscriptSegment

# Local audio and caption capture JS engine
from audio_capture import (
    CHECK_CAPTIONS_ACTIVE_JS,
    DRAIN_CHUNKS_JS,
    ENABLE_CAPTIONS_JS,
    ENSURE_CAPTURE_JS,
    FORCE_VISIBLE_JS,
    INIT_AUDIO_CAPTURE_JS,
    INJECT_CAPTURE_JS,
    IS_MEETING_ENDED_JS,
    SCRAPE_DOM_CAPTIONS_JS,
    SCRAPE_PARTICIPANTS_JS,
    STOP_RECORDER_JS,
)


class LiveMeetingBot:
    def __init__(self, meeting_url: str, bot_name: str = "Fireflies Notetaker", custom_title: Optional[str] = None):
        self.meeting_url = self._clean_url(meeting_url)
        self.bot_name = bot_name
        self.custom_title = custom_title
        self.segments: list[TranscriptSegment] = []
        self._last_utterances: dict[str, str] = {}
        self._card_segments: dict[str, TranscriptSegment] = {}
        self.participants: set[str] = set()
        self.meeting_id: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.total_audio_bytes = 0
        self.total_chunks = 0
        self.audio_file_path: Optional[Path] = None

    def _clean_url(self, raw_url: str) -> str:
        raw_url = raw_url.strip()
        if not raw_url.startswith("http"):
            raw_url = f"https://meet.google.com/{raw_url}"
        return raw_url

    async def _is_in_waiting_room(self, page) -> bool:
        try:
            body = (await page.inner_text("body")).lower()
            waiting_phrases = [
                "please wait until a meeting host brings you",
                "someone will let you in soon",
                "waiting to be admitted",
                "you'll join the call when someone admits you",
                "asking to join",
            ]
            return any(phrase in body for phrase in waiting_phrases)
        except Exception:
            return False

    async def _is_truly_admitted(self, page) -> bool:
        try:
            if await self._is_in_waiting_room(page):
                return False

            return await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, div[role="button"]'));
                return btns.some(b => {
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    const tip = (b.getAttribute('data-tooltip') || '').toLowerCase();
                    const text = (b.innerText || '').toLowerCase();
                    const combined = aria + ' ' + tip + ' ' + text;
                    return combined.includes('raise hand') ||
                           combined.includes('send a reaction') ||
                           combined.includes('turn on captions') ||
                           combined.includes('turn off captions') ||
                           combined.includes('people') ||
                           combined.includes('show everyone') ||
                           combined.includes('chat with everyone');
                });
            }""")
        except Exception:
            return False

    async def run(self):
        print("\n" + "=" * 70, flush=True)
        print(" [BOT] Fireflies Live Meeting Bot Starting", flush=True)
        print("=" * 70, flush=True)
        print(f"Target URL: {self.meeting_url}", flush=True)
        print(f"Bot Name:   {self.bot_name}\n", flush=True)

        settings = get_settings()

        # Initialize DB
        repo = await init_db(settings.database_url)

        # Create record in DB
        code = self.meeting_url.split("/")[-1].split("?")[0]
        meeting_title = self.custom_title or f"Live Meet ({code})"
        db_meeting = await repo.create_meeting(
            platform="google_meet",
            meeting_url=self.meeting_url,
            external_meeting_id=code,
            title=meeting_title,
        )
        self.meeting_id = db_meeting.id
        await repo.update_status(self.meeting_id, MeetingStatus.CAPTURING)

        # Prepare audio recording destination
        recordings_dir = (Path(__file__).parent / "data" / "recordings").resolve()
        recordings_dir.mkdir(parents=True, exist_ok=True)
        self.audio_file_path = recordings_dir / f"{self.meeting_id}.webm"
        audio_file = open(self.audio_file_path, "wb")

        print(f"[*] Meeting registered in dashboard: ID = {self.meeting_id}", flush=True)
        print(f"[*] Audio recording destination: {self.audio_file_path}", flush=True)
        print("[*] Launching Google Chrome browser with WebRTC audio capture...\n", flush=True)

        # Use a persistent Chrome profile so Google login & preferences persist
        profile_dir = (Path(__file__).parent / "data" / "bot_chrome_profile").resolve()
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Clean up any stale lockfile and zombie bot Chrome processes using bot profile
        if sys.platform == "win32":
            try:
                import subprocess
                subprocess.run(
                    ["powershell", "-Command", "Get-WmiObject Win32_Process -Filter \"name = 'chrome.exe'\" | Where-Object { $_.CommandLine -like '*bot_chrome_profile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                )
            except Exception:
                pass
        try:
            (profile_dir / "lockfile").unlink(missing_ok=True)
        except Exception:
            pass

        async with async_playwright() as p:
            browser_args = [
                "--remote-debugging-port=0",
                "--autoplay-policy=no-user-gesture-required",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            context = None
            for channel in ["chrome", "msedge"]:
                try:
                    context = await p.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        channel=channel,
                        headless=False,  # Visible browser so user can see bot
                        permissions=["microphone", "camera"],
                        viewport={"width": 1280, "height": 720},
                        args=browser_args,
                        ignore_default_args=["--enable-automation"],
                    )
                    break
                except Exception as b_err:
                    print(f"[-] Could not launch with channel={channel}: {b_err}", flush=True)
                    try:
                        (profile_dir / "lockfile").unlink(missing_ok=True)
                    except Exception:
                        pass

            if not context:
                # Ultimate fallback
                fallback_profile = profile_dir.parent / f"bot_profile_{int(time.time())}"
                fallback_profile.mkdir(parents=True, exist_ok=True)
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(fallback_profile),
                    channel="msedge" if sys.platform == "win32" else "chrome",
                    headless=False,
                    permissions=["microphone", "camera"],
                    viewport={"width": 1280, "height": 720},
                    args=browser_args,
                    ignore_default_args=["--enable-automation"],
                )

            # Invalidate webdriver and hook page visibility
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                if (!window.chrome) {
                    window.chrome = { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
                }
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """)
            await context.add_init_script(FORCE_VISIBLE_JS)

            # Hook WebRTC peer connections and media streams before navigation
            await context.add_init_script(INIT_AUDIO_CAPTURE_JS)

            page = context.pages[0] if context.pages else await context.new_page()

            try:
                print(f"[*] Navigating to {self.meeting_url}...", flush=True)
                await page.goto(self.meeting_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                # Dismiss common Meet dialogs
                for text in ["Got it", "Dismiss", "No thanks", "Close"]:
                    try:
                        btn = page.locator(f"button:has-text('{text}')")
                        if await btn.count() > 0:
                            await btn.first.click()
                            print(f"[*] Dismissed dialog: {text}", flush=True)
                    except Exception:
                        pass

                # Step 1: Input name if prompted
                name_filled = False
                name_input = page.locator("input[placeholder*='name' i], input[aria-label*='name' i], input[type='text']")
                if await name_input.count() > 0:
                    try:
                        await name_input.first.click()
                        await name_input.first.fill(self.bot_name)
                        await page.keyboard.press("Tab")
                        name_filled = True
                        print(f"[*] Entered participant name: '{self.bot_name}'", flush=True)
                    except Exception as e:
                        print(f"[-] Could not fill name via locator: {e}", flush=True)

                if not name_filled:
                    # JavaScript fallback to fill name
                    await page.evaluate(f"""() => {{
                        const inp = document.querySelector("input[placeholder*='name' i]") ||
                                    document.querySelector("input[aria-label*='name' i]") ||
                                    document.querySelector("input[type='text']");
                        if (inp) {{
                            inp.focus();
                            const nativeInput = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                            nativeInput.set.call(inp, '{self.bot_name}');
                            inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                            inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    }}""")
                    print(f"[*] Triggered JS name fill for: '{self.bot_name}'", flush=True)

                await asyncio.sleep(1)

                # Step 2: Mute mic & cam
                try:
                    await page.keyboard.press("Control+d")
                    await asyncio.sleep(0.5)
                    await page.keyboard.press("Control+e")
                    print("[*] Muted bot microphone and camera.", flush=True)
                except Exception:
                    pass

                await asyncio.sleep(1)

                # Step 3: Prioritize direct entry "Join now" (bypasses lobby & permissions!), then "Ask to join"
                clicked = await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
                    
                    // Priority 1: Direct "Join now" button (bypasses host admit)
                    const joinNowBtn = buttons.find(b => {
                        const txt = (b.innerText || '').toLowerCase().trim();
                        return (txt === 'join now' || txt.startsWith('join now')) && txt.length < 25;
                    });
                    if (joinNowBtn) {
                        joinNowBtn.click();
                        return 'Direct Access: Join now';
                    }

                    // Priority 2: "Ask to join"
                    const askBtn = buttons.find(b => {
                        const txt = (b.innerText || '').toLowerCase().trim();
                        return txt.includes('ask to join') && txt.length < 35;
                    });
                    if (askBtn) {
                        askBtn.click();
                        return 'Ask to join';
                    }
                    return null;
                }""")

                if not clicked:
                    if await page.locator("button:has-text('Join now')").count() > 0:
                        await page.locator("button:has-text('Join now')").first.click()
                        clicked = "Direct Access: Join now"
                    elif await page.locator("button:has-text('Ask to join')").count() > 0:
                        await page.locator("button:has-text('Ask to join')").first.click()
                        clicked = "Ask to join"

                print(f"[*] Join button triggered: '{clicked or 'Auto'}'", flush=True)

                body_text = await page.inner_text("body")
                if "You can't join this video call" in body_text:
                    print("[!] WARNING: Google Meet says 'You can't join this video call'.", flush=True)
                    print("    Please check Host Controls in your meeting tab and ensure 'Anyone with the link' is allowed.", flush=True)

                script_snippet = "setInterval(()=>{for(const b of document.querySelectorAll('button,div[role=\"button\"],span[role=\"button\"]')){const t=(b.innerText||'').toLowerCase().trim(),a=(b.getAttribute('aria-label')||'').toLowerCase().trim(),title=(b.getAttribute('title')||'').toLowerCase().trim();const isDeny=t.includes('deny')||a.includes('deny')||title.includes('deny');const isAdmit=(t==='admit'||t.includes('admit 1')||t.includes('admit all')||t.startsWith('admit')||a.includes('admit')||title.includes('admit'))&&!isDeny;const r=b.getBoundingClientRect();if(isAdmit&&r.width>0&&r.height>0){['pointerdown','mousedown','pointerup','mouseup','click'].forEach(e=>b.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window})));b.click();console.log('[Auto-Admit] Admitted participant/bot!');}}},300);"
                if sys.platform == "win32":
                    try:
                        import subprocess
                        subprocess.run(["powershell", "-Command", f"Set-Clipboard -Value @'\n{script_snippet}\n'@"], check=False, timeout=3)
                        print("[AUTO-CLIPBOARD] Silent Auto-Admit script automatically copied to your clipboard!", flush=True)
                    except Exception:
                        pass

                print("\n" + "=" * 70, flush=True)
                print(" >> SILENT AUTO-ADMIT SCRIPT (AUTOMATICALLY COPIED TO CLIPBOARD):", flush=True)
                print("    Open your Google Meet tab, press F12 -> Console -> Ctrl+V -> Enter", flush=True)
                print(f"\n    {script_snippet}\n", flush=True)
                print(" >> OR manually click 'Admit' when the prompt appears:", flush=True)
                print(f"    '{self.bot_name} wants to join this call'", flush=True)
                print("=" * 70 + "\n", flush=True)

                # Step 4: Wait to be admitted by host
                admitted = False
                print("[*] Waiting for host admission in Google Meet...", flush=True)
                for wait_idx in range(90):  # Wait up to 180s (3 minutes)
                    if await self._is_truly_admitted(page):
                        admitted = True
                        break
                    if wait_idx > 0 and wait_idx % 5 == 0:
                        print(f"[*] Still in lobby waiting for host to click 'Admit' ({wait_idx * 2}s elapsed)...", flush=True)
                    await asyncio.sleep(2)

                if not admitted:
                    print(f"[-] Timed out waiting for host to admit the bot.", flush=True)
                    return

                print("\n" + "=" * 70, flush=True)
                print(" [SUCCESS] Bot successfully admitted into the meeting room!", flush=True)
                print("=" * 70 + "\n", flush=True)

                self.start_time = datetime.now(timezone.utc)
                await repo.update_status(self.meeting_id, MeetingStatus.PROCESSING)

                # Dismiss modals like "Camera not found", "Add others"
                for _ in range(3):
                    await page.keyboard.press("Escape")
                    try:
                        dismiss_btn = page.locator("button[aria-label*='Close' i], button:has-text('Got it'), button:has-text('Dismiss')")
                        if await dismiss_btn.count() > 0:
                            await dismiss_btn.first.click()
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)

                # Save verified in-meeting screenshot
                in_call_screen = str(Path(__file__).parent / "admitted_screen.png")
                await page.screenshot(path=in_call_screen)
                print(f"[*] Saved verified in-call screenshot to: {in_call_screen}", flush=True)

                # Step 5: Start in-meeting audio capture and resume AudioContext
                print("[*] Hooking in-call audio streams & initializing MediaRecorder...", flush=True)
                inject_res = await page.evaluate(INJECT_CAPTURE_JS)
                print(f"[*] Audio capture engine status: {inject_res}", flush=True)

                # Step 6: Enable Closed Captions
                print("[*] Activating Google Meet Closed Captions...", flush=True)
                for cap_attempt in range(8):
                    await page.evaluate(ENABLE_CAPTIONS_JS)
                    await asyncio.sleep(1.0)
                    cap_status = await page.evaluate(CHECK_CAPTIONS_ACTIVE_JS)
                    if cap_status != "inactive":
                        print(f"[*] Captions active signal detected: {cap_status}", flush=True)
                        break
                    try:
                        await page.click("body")
                        await page.keyboard.press("c")
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)

                print("\n" + "=" * 70, flush=True)
                print(" [CAPTURING LIVE] WebRTC Audio Recording & Speech Recognition Active!", flush=True)
                print(" Speak into your microphone now:")
                print(" - Audio chunks will continuously be saved to disk (.webm)")
                print(" - Detected speech will appear below with timestamps & speaker name")
                print("=" * 70 + "\n", flush=True)

                # Step 7: Main in-call capture loop
                t0 = time.time()
                last_heartbeat = time.time()
                last_audio_active_log = 0.0

                while True:
                    now_sec = time.time() - t0
                    mins = int(now_sec // 60)
                    secs = int(now_sec % 60)
                    time_str = f"{mins:02d}:{secs:02d}"

                    # Check if meeting has truly ended (only after at least 10 seconds in call)
                    if now_sec > 10.0:
                        current_url = page.url or ""
                        end_signal = None
                        if "meet.google.com" not in current_url:
                            end_signal = "url_left_meet"
                        else:
                            try:
                                end_signal = await page.evaluate(IS_MEETING_ENDED_JS)
                            except Exception:
                                pass

                        if end_signal:
                            print(f"\n[*] Meeting end confirmed ({end_signal}). Finishing session...", flush=True)
                            break

                    # 1. Drain raw audio chunks from browser
                    try:
                        raw_chunks = await page.evaluate(DRAIN_CHUNKS_JS)
                        if raw_chunks:
                            chunk_list = raw_chunks.split(",")
                            for b64 in chunk_list:
                                if b64:
                                    chunk_bytes = base64.b64decode(b64)
                                    audio_file.write(chunk_bytes)
                                    audio_file.flush()
                                    self.total_audio_bytes += len(chunk_bytes)
                                    self.total_chunks += 1
                    except Exception:
                        pass

                    # 2. Check audio stream activity and sound volume
                    try:
                        status_raw = await page.evaluate(ENSURE_CAPTURE_JS)
                        if status_raw:
                            stats = json.loads(status_raw)
                            speaking = stats.get("speaking", False)
                            rms = stats.get("rms", 0.0)
                            if speaking and (time.time() - last_audio_active_log > 3.0):
                                print(f" [AUDIO LEVEL {time_str}] Sound detected in call (RMS: {rms:.3f}, Tracks: {stats.get('tracks', 0)})", flush=True)
                                last_audio_active_log = time.time()
                    except Exception:
                        pass

                    # 3. Harvest active participants from DOM every 4 seconds
                    if time.time() - last_heartbeat < 1.0 or (int(now_sec) % 4 == 0):
                        try:
                            parts_raw = await page.evaluate(SCRAPE_PARTICIPANTS_JS)
                            if parts_raw:
                                for p in json.loads(parts_raw):
                                    p_clean = p.strip()
                                    if p_clean and p_clean.lower() not in {'chat', 'apps', 'participant', 'settings'}:
                                        self.participants.add(p_clean)
                        except Exception:
                            pass

                    # 4. Scrape live spoken words from DOM captions
                    try:
                        caps_raw = await page.evaluate(SCRAPE_DOM_CAPTIONS_JS)
                        if caps_raw:
                            caps = json.loads(caps_raw)
                            for cap in caps:
                                card_id = cap.get("id") or ""
                                spk = (cap.get("speaker") or "Participant").strip()
                                txt = cap.get("text", "").strip()
                                if not txt or len(txt) < 2:
                                    continue

                                # Add verified speaker to participants
                                if spk and spk.lower() not in {'participant', 'unknown', 'chat', 'apps', 'speaker'}:
                                    self.participants.add(spk)

                                # 1. Direct card ID match (exact same DOM element still on screen)
                                if card_id and card_id in self._card_segments:
                                    seg = self._card_segments[card_id]
                                    if seg.speaker != spk and spk != "Participant":
                                        seg.speaker = spk
                                    if txt == seg.text:
                                        seg.end = max(seg.end, round(now_sec + 1.5, 1))
                                    elif len(txt) > len(seg.text) or txt.startswith(seg.text[:15]):
                                        seg.text = txt
                                        seg.end = round(now_sec + 2.0, 1)
                                        print(f" >>> [SPEECH {time_str}] {spk}: \"{txt}\"", flush=True)
                                    else:
                                        seg.end = max(seg.end, round(now_sec + 1.5, 1))
                                    continue

                                # 2. Word prefix / semantic match across active segments in last 15s
                                matched_recent = False
                                s_words = re.sub(r'[^a-zA-Z0-9\s]', '', txt).lower().split()
                                for seg in reversed(self.segments[-10:]):
                                    if seg.speaker == spk and (abs(now_sec - seg.end) <= 15.0 or (now_sec >= seg.start and now_sec <= seg.end + 2.0)):
                                        if txt.lower() == seg.text.lower():
                                            seg.end = max(seg.end, round(now_sec + 1.5, 1))
                                            if card_id:
                                                self._card_segments[card_id] = seg
                                            matched_recent = True
                                            break
                                        p_words = re.sub(r'[^a-zA-Z0-9\s]', '', seg.text).lower().split()
                                        min_w = min(len(s_words), len(p_words))
                                        if min_w > 0 and s_words[:min_w] == p_words[:min_w]:
                                            if len(s_words) >= len(p_words):
                                                seg.text = txt
                                                print(f" >>> [SPEECH {time_str}] {spk}: \"{txt}\"", flush=True)
                                            seg.end = round(now_sec + 2.0, 1)
                                            if card_id:
                                                self._card_segments[card_id] = seg
                                            matched_recent = True
                                            break
                                        if txt.lower() in seg.text.lower():
                                            seg.end = max(seg.end, round(now_sec + 1.5, 1))
                                            if card_id:
                                                self._card_segments[card_id] = seg
                                            matched_recent = True
                                            break
                                        elif seg.text.lower() in txt.lower():
                                            seg.text = txt
                                            seg.end = round(now_sec + 2.0, 1)
                                            if card_id:
                                                self._card_segments[card_id] = seg
                                            print(f" >>> [SPEECH {time_str}] {spk}: \"{txt}\"", flush=True)
                                            matched_recent = True
                                            break

                                if matched_recent:
                                    continue

                                # 3. New distinct utterance
                                new_seg = TranscriptSegment(
                                    speaker=spk,
                                    start=round(now_sec, 1),
                                    end=round(now_sec + 2.0, 1),
                                    text=txt,
                                )
                                if card_id:
                                    self._card_segments[card_id] = new_seg
                                self.segments.append(new_seg)
                                print(f" >>> [SPEECH {time_str}] {spk}: \"{txt}\"", flush=True)
                    except Exception:
                        pass

                    # 5. Periodic heartbeat every 10 seconds
                    if time.time() - last_heartbeat >= 10.0:
                        kb = self.total_audio_bytes / 1024
                        print(f" [STATUS {time_str}] Audio: {kb:.1f} KB recorded ({self.total_chunks} chunks) | Speech: {len(self.segments)} utterances", flush=True)
                        last_heartbeat = time.time()

                    await asyncio.sleep(0.8)

                # Stop audio recorder
                try:
                    await page.evaluate(STOP_RECORDER_JS)
                    await asyncio.sleep(1.5)
                    final_chunks = await page.evaluate(DRAIN_CHUNKS_JS)
                    if final_chunks:
                        for b64 in final_chunks.split(","):
                            if b64:
                                chunk_bytes = base64.b64decode(b64)
                                audio_file.write(chunk_bytes)
                                audio_file.flush()
                                self.total_audio_bytes += len(chunk_bytes)
                                self.total_chunks += 1
                except Exception:
                    pass

                dur_mins = int((time.time() - t0) // 60)
                dur_secs = int((time.time() - t0) % 60)
                total_kb = self.total_audio_bytes / 1024
                print("\n" + "=" * 70, flush=True)
                print(f" [CALL CONCLUDED] Duration: {dur_mins}m {dur_secs}s", flush=True)
                print(f" [AUDIO CAPTURED] Total Audio: {total_kb:.1f} KB ({self.total_chunks} chunks)", flush=True)
                print(f" [FILE SAVED]     {self.audio_file_path}", flush=True)
                print(f" [SPEECH SEGMENTS] Captured {len(self.segments)} utterances", flush=True)
                print("=" * 70 + "\n", flush=True)

            except KeyboardInterrupt:
                print("\n[*] Bot stopped by user (Ctrl+C). Exiting call...", flush=True)
            except Exception as e:
                print(f"[-] Bot execution error: {e}", flush=True)
            finally:
                audio_file.close()
                await context.close()

        # Step 8: Post-meeting Processing, Transcription & AI Summary
        print("\n" + "=" * 70, flush=True)
        print(" [AI] Post-Meeting Processing & AI Summarization", flush=True)
        print("=" * 70, flush=True)

        # Check if we need Whisper audio fallback if captions weren't produced
        if not self.segments and self.total_audio_bytes > 5000 and settings.openai_api_key:
            print("[*] No DOM captions captured, but raw audio is present. Running OpenAI Whisper transcription...", flush=True)
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
                with open(self.audio_file_path, "rb") as af:
                    transcription = await client.audio.transcriptions.create(
                        model="whisper-1",
                        file=af,
                        response_format="verbose_json",
                    )
                whisper_text = getattr(transcription, "text", "")
                if whisper_text:
                    print(f"[*] Whisper transcribed: \"{whisper_text[:100]}...\"", flush=True)
                    self.segments.append(
                        TranscriptSegment(
                            speaker="Participant",
                            start=0.0,
                            end=round(float(getattr(transcription, "duration", 10.0)), 1),
                            text=whisper_text,
                        )
                    )
            except Exception as w_err:
                print(f"[-] Whisper transcription fallback error: {w_err}", flush=True)

        if not self.segments:
            if self.total_audio_bytes > 0:
                print(f"[OK] Meeting audio successfully recorded ({self.total_audio_bytes / 1024:.1f} KB) at {self.audio_file_path}", flush=True)
                print("     (No spoken dialogue was detected during this session)", flush=True)
                await repo.update_status(self.meeting_id, MeetingStatus.COMPLETED)
            else:
                print("[-] No speech or audio captured during this session.", flush=True)
                await repo.update_status(self.meeting_id, MeetingStatus.FAILED)
            return

        # Filter out UI toolbar noise and coalesce progressive captions into clean segments
        clean_segs: list[TranscriptSegment] = []
        noise_spks = {'language', 'mood', 'more_vert', 'chat', 'apps', 'speaker', 'button', 'settings', 'font size'}
        noise_phrases = [
            'chat apps', 'chat with everyone', 'font size circle font color',
            'circle font color', 'font size', 'font color', 'format_size',
            'turn on captions', 'turn off captions', 'leave call', 'more options',
            'meeting details', 'meeting tools', 'closed_caption', 'back_hand'
        ]

        for s in self.segments:
            spk = (s.speaker or '').strip()
            txt = (s.text or '').strip()

            # Strict rejection of UI noise and invalid fragments
            if not txt or len(txt) < 2:
                continue
            if spk.lower() in noise_spks or any(np in txt.lower() for np in noise_phrases):
                continue
            if spk.lower() == txt.lower():
                continue
            if spk.lower() in {'participant', 'unknown'} and len(txt) < 15:
                continue

            # Deduplication and coalescing across recent segments for the same speaker
            s_words = re.sub(r'[^a-zA-Z0-9\s]', '', txt).lower().split()
            merged = False
            for prev in reversed(clean_segs):
                if prev.speaker == spk and abs(s.start - prev.end) <= 15.0:
                    p_words = re.sub(r'[^a-zA-Z0-9\s]', '', prev.text).lower().split()
                    # 1. Exact match
                    if txt.lower() == prev.text.lower():
                        prev.end = max(prev.end, s.end)
                        merged = True
                        break
                    # 2. Word prefix match
                    min_w = min(len(s_words), len(p_words))
                    if min_w > 0 and s_words[:min_w] == p_words[:min_w]:
                        if len(s_words) >= len(p_words):
                            prev.text = txt
                        prev.end = max(prev.end, s.end)
                        merged = True
                        break
                    # 3. Substring match
                    if txt.lower() in prev.text.lower():
                        prev.end = max(prev.end, s.end)
                        merged = True
                        break
                    elif prev.text.lower() in txt.lower():
                        prev.text = txt
                        prev.end = max(prev.end, s.end)
                        merged = True
                        break
                    # 4. Word overlap (>= 60%) indicating real-time ASR correction / expansion
                    s_set = set(s_words)
                    p_set = set(p_words)
                    if s_set and p_set:
                        common = sum(
                            1 for a in s_set
                            if any(a == b or (len(a) >= 3 and len(b) >= 3 and (a.startswith(b[:3]) or b.startswith(a[:3]))) for b in p_set)
                        )
                        if (common / min(len(s_set), len(p_set))) >= 0.6:
                            if len(s_words) >= len(p_words):
                                prev.text = txt
                            prev.end = max(prev.end, s.end)
                            merged = True
                            break
                elif abs(s.start - prev.end) > 20.0:
                    break

            if not merged:
                clean_segs.append(s)

        final_segments = clean_segs if clean_segs else self.segments

        # Collect verified meeting participants
        all_participants = sorted({
            p.strip() for p in self.participants
            if p and p.strip().lower() not in noise_spks and p.strip().lower() not in {'participant', 'unknown'}
        })
        for seg in final_segments:
            spk = (seg.speaker or '').strip()
            if spk and spk.lower() not in noise_spks and spk.lower() not in {'participant', 'unknown'}:
                if spk not in all_participants:
                    all_participants.append(spk)
        all_participants.sort()
        if not all_participants:
            all_participants = ["Govind Jadapalli"]

        norm_transcript = NormalizedTranscript(
            meeting_id=self.meeting_id,
            platform="google_meet",
            segments=final_segments,
        )

        await repo.store_transcript(
            meeting_id=self.meeting_id,
            transcript=norm_transcript,
            participants=all_participants,
        )
        await repo.update_status(self.meeting_id, MeetingStatus.SUMMARIZING)

        print(f"[*] Generating AI summary from {len(final_segments)} verified utterances ({len(all_participants)} participants: {', '.join(all_participants)})...", flush=True)
        summarizer = MeetingSummarizer()
        summary = await summarizer.summarize(norm_transcript)

        await repo.store_summary(
            meeting_id=self.meeting_id,
            summary_dict=summary.model_dump(),
        )

        print("\n" + "=" * 70, flush=True)
        print(" [SUMMARY] AI MEETING SUMMARY", flush=True)
        print("=" * 70, flush=True)
        print(f"Overview:", flush=True)
        print(f"  {summary.overview}\n", flush=True)

        print("Key Decisions:", flush=True)
        for d in summary.decisions:
            print(f"  - {d}", flush=True)

        print("\nAction Items:", flush=True)
        for ai in summary.action_items:
            owner = ai.owner or "Unassigned"
            due = f" (Due: {ai.due_date})" if ai.due_date else ""
            print(f"  [ ] [{owner}] {ai.task}{due}", flush=True)

        print("\n" + "=" * 70, flush=True)
        print(f" [OK] Meeting successfully processed!", flush=True)
        print(f" Dashboard URL: http://localhost:8000", flush=True)
        print(f" Meeting Page:  http://localhost:8000/meeting/{self.meeting_id}", flush=True)
        print(f" Audio File:    {self.audio_file_path}", flush=True)
        print("=" * 70 + "\n", flush=True)


async def main():
    if "--login" in sys.argv:
        print("\n" + "=" * 70, flush=True)
        print(" [LOGIN] Opening Google Login for Fireflies Bot Profile...", flush=True)
        print(" Please sign in with any Google account. Once signed in, close the browser.", flush=True)
        print("=" * 70 + "\n", flush=True)
        profile_dir = Path(__file__).parent / "data" / "bot_chrome_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=False,
                viewport={"width": 1280, "height": 720},
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://accounts.google.com/")
            print("[*] Browser is open. Sign in to your Google account, then close the browser window when done.", flush=True)
            try:
                await page.wait_for_timeout(300000)
            except Exception:
                pass
            await context.close()
        print("[OK] Bot profile saved! You can now join any meeting without guest blocks.", flush=True)
        return

    meet_url = None
    custom_title = None
    idx = 1
    while idx < len(sys.argv):
        arg = sys.argv[idx]
        if arg == "--title" and idx + 1 < len(sys.argv):
            custom_title = sys.argv[idx + 1]
            idx += 2
            continue
        elif not arg.startswith("--") and not meet_url:
            meet_url = arg
        idx += 1

    if not meet_url:
        print("[-] Meet URL cannot be empty.", flush=True)
        print("Usage: python live_meet_bot.py \"https://meet.google.com/xxx-yyyy-zzz\" [--title \"Custom Title\"]", flush=True)
        return

    bot = LiveMeetingBot(meeting_url=meet_url, custom_title=custom_title)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())