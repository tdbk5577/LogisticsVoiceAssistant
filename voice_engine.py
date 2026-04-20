import subprocess
import speech_recognition as sr

import config

try:
    from elevenlabs.client import ElevenLabs as _ElevenLabs
    _el_available = True
except ImportError:
    _el_available = False


class VoiceEngine:
    def __init__(self):
        self._recognizer = sr.Recognizer()
        self._recognizer.dynamic_energy_threshold = True
        self._recognizer.pause_threshold = 0.8
        self._el = (
            _ElevenLabs(api_key=config.ELEVENLABS_API_KEY)
            if _el_available and config.ELEVENLABS_API_KEY
            else None
        )

    def speak(self, text: str):
        print(f"[TRUCK AI] {text}")
        if self._el:
            try:
                audio = self._el.text_to_speech.stream(
                    text=text,
                    voice_id=config.ELEVENLABS_VOICE_ID,
                    model_id="eleven_turbo_v2_5",
                    output_format="mp3_44100_128",
                )
                proc = subprocess.Popen(
                    ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for chunk in audio:
                    if chunk:
                        proc.stdin.write(chunk)
                proc.stdin.close()
                proc.wait()
                return
            except Exception as e:
                print(f"[TRUCK AI] ElevenLabs error: {e}")

        # Fallback to macOS say
        try:
            subprocess.run(["say", "-r", "175", text], check=True)
        except Exception:
            pass

    def listen(self, timeout: int = 8, phrase_limit: int = 15) -> str | None:
        try:
            with sr.Microphone() as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.3)
                audio = self._recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_limit,
                )
            text = self._recognizer.recognize_google(audio)
            print(f"[DRIVER] {text}")
            return text.lower().strip()
        except sr.WaitTimeoutError:
            return None
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            print(f"[TRUCK AI] STT request error: {e}")
            return None
        except Exception as e:
            print(f"[TRUCK AI] Microphone error: {e}")
            return None

    def listen_for_wake_word(self, wake_words: list[str], timeout: int = 3) -> bool:
        try:
            with sr.Microphone() as source:
                audio = self._recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=5,
                )
            text = self._recognizer.recognize_google(audio).lower().strip()
            return any(w in text for w in wake_words)
        except sr.WaitTimeoutError:
            return False
        except sr.UnknownValueError:
            return False
        except sr.RequestError as e:
            print(f"[TRUCK AI] STT request error: {e}")
            return False
        except Exception as e:
            print(f"[TRUCK AI] Microphone error: {e}")
            return False
