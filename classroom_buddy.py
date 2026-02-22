#!/usr/bin/env python3
"""
Classroom Buddy - AI Assistant for Elementary Students
Uses: OLED faces, TFT text, Groq Whisper/LLM, Claude Vision, CSI Camera, Piezo beeps
WITH CONVERSATION MEMORY!
"""

import os
import time
import base64
import RPi.GPIO as GPIO
from groq import Groq
from anthropic import Anthropic
import sounddevice as sd
import scipy.io.wavfile as wav
from st7789 import ST7789
from PIL import Image, ImageDraw, ImageFont
from picamera2 import Picamera2
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306

# Initialize displays
tft = ST7789(
    width=320,
    height=240,
    rotation=0,
    port=0,
    cs=8,       # CE0 - GPIO 8
    dc=25,      # Data/Command - GPIO 25
    backlight=None
)
font = ImageFont.load_default()
serial = i2c(port=1, address=0x3c)
oled = ssd1306(serial)

# Initialize CSI camera
print("Initializing camera...")
picam = Picamera2()
picam.configure(picam.create_still_configuration())
picam.start()
time.sleep(2)

# Initialize APIs
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
claude_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Setup GPIO
GPIO.setmode(GPIO.BCM)

# Setup button
BUTTON_PIN = 17
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Setup piezo
BUZZER_PIN = 13
GPIO.setup(BUZZER_PIN, GPIO.OUT)
buzzer = GPIO.PWM(BUZZER_PIN, 440)

# Idle timer and conversation memory
last_interaction_time = time.time()
SLEEPY_TIMEOUT = 60  # Increased to 60 seconds
conversation_history = []

# Personality definitions
PERSONALITIES = [
    {
        "name": "Friendly Teacher",
        "kid_prompt": """You are a friendly AI classroom assistant for 3rd-5th grade students (ages 8-11).

Guidelines:
- Use age-appropriate vocabulary
- Keep responses SHORT (2-3 sentences max)
- Be encouraging and positive
- Help students learn, don't just give answers
- For sensitive topics, suggest "ask your teacher"
- Be enthusiastic about learning!
- Remember previous questions in the conversation and refer back to them naturally""",
        "adult_prompt": """You are a knowledgeable AI assistant with no age restrictions.

Guidelines:
- Keep responses concise (2-3 sentences max)
- Be helpful, accurate, and direct
- No topic restrictions - answer anything honestly
- Remember previous questions in the conversation and refer back to them naturally"""
    },
    {
        "name": "Sarcastic Comedian",
        "kid_prompt": """You are a funny, witty AI classroom assistant for 3rd-5th grade students (ages 8-11).

Guidelines:
- Use kid-friendly humor and gentle sarcasm
- Keep responses SHORT (2-3 sentences max)
- Be playful and joke around, but still be helpful
- Crack puns and silly jokes while teaching
- For sensitive topics, suggest "ask your teacher"
- Make learning fun with humor!
- Remember previous questions and callback to earlier jokes""",
        "adult_prompt": """You are a sarcastic, witty AI assistant with no age restrictions.

Guidelines:
- Keep responses concise (2-3 sentences max)
- Use sharp wit, sarcasm, and adult humor
- Be genuinely helpful underneath the snark
- No topic restrictions - answer anything with comedic flair
- Remember previous questions and callback to earlier jokes"""
    },
    {
        "name": "Chill Tutor",
        "kid_prompt": """You are a super chill, laid-back AI tutor for 3rd-5th grade students (ages 8-11).

Guidelines:
- Use casual, relaxed language kids can relate to
- Keep responses SHORT (2-3 sentences max)
- Be calm and easygoing, like a cool older sibling
- Explain things simply without pressure
- For sensitive topics, suggest "ask your teacher"
- Use words like "cool", "no worries", "you got this"
- Remember previous questions in the conversation""",
        "adult_prompt": """You are a super chill, laid-back AI assistant with no age restrictions.

Guidelines:
- Keep responses concise (2-3 sentences max)
- Be casual and relaxed in tone
- No topic restrictions - answer anything in a chill way
- Keep it real and straightforward
- Remember previous questions in the conversation"""
    }
]

# Mode and personality state
current_personality = 0  # Index into PERSONALITIES
adult_mode = False


def get_system_prompt():
    """Return the active system prompt based on personality and mode."""
    p = PERSONALITIES[current_personality]
    return p["adult_prompt"] if adult_mode else p["kid_prompt"]


def check_voice_command(text):
    """Check if transcribed text is a voice command. Returns True if command detected."""
    global current_personality, adult_mode
    t = text.lower().strip()

    if "adult mode" in t:
        adult_mode = True
        p_name = PERSONALITIES[current_personality]["name"]
        print(f"COMMAND: Adult mode ON ({p_name})")
        beep_happy()
        tft_write_lines([
            "  Mode Changed!",
            "  ADULT MODE: ON",
            f"  {p_name}",
            ""
        ])
        time.sleep(2)
        return True

    if "kid mode" in t:
        adult_mode = False
        p_name = PERSONALITIES[current_personality]["name"]
        print(f"COMMAND: Kid mode ON ({p_name})")
        beep_happy()
        tft_write_lines([
            "  Mode Changed!",
            "  KID MODE: ON",
            f"  {p_name}",
            ""
        ])
        time.sleep(2)
        return True

    if "personality teacher" in t:
        current_personality = 0
        mode = "ADULT" if adult_mode else "KID"
        print(f"COMMAND: Personality -> Friendly Teacher ({mode})")
        beep_happy()
        tft_write_lines([
            "  Personality:",
            "  Friendly Teacher",
            f"  Mode: {mode}",
            ""
        ])
        time.sleep(2)
        return True

    if "personality comedian" in t:
        current_personality = 1
        mode = "ADULT" if adult_mode else "KID"
        print(f"COMMAND: Personality -> Sarcastic Comedian ({mode})")
        beep_happy()
        tft_write_lines([
            "  Personality:",
            "  Sarcastic Comedian",
            f"  Mode: {mode}",
            ""
        ])
        time.sleep(2)
        return True

    if "personality chill" in t:
        current_personality = 2
        mode = "ADULT" if adult_mode else "KID"
        print(f"COMMAND: Personality -> Chill Tutor ({mode})")
        beep_happy()
        tft_write_lines([
            "  Personality:",
            "  Chill Tutor",
            f"  Mode: {mode}",
            ""
        ])
        time.sleep(2)
        return True

    return False


# Piezo Sound Functions
def beep_startup():
    buzzer.start(50)
    for freq in [262, 330, 392, 523]:
        buzzer.ChangeFrequency(freq)
        time.sleep(0.15)
    buzzer.stop()


def beep_button():
    buzzer.start(50)
    buzzer.ChangeFrequency(800)
    time.sleep(0.05)
    buzzer.stop()


def beep_listening():
    buzzer.start(50)
    buzzer.ChangeFrequency(1000)
    time.sleep(0.1)
    buzzer.ChangeFrequency(1200)
    time.sleep(0.1)
    buzzer.stop()


def beep_thinking():
    buzzer.start(30)
    for _ in range(3):
        buzzer.ChangeFrequency(400)
        time.sleep(0.2)
        buzzer.ChangeFrequency(500)
        time.sleep(0.2)
    buzzer.stop()


def beep_happy():
    buzzer.start(50)
    for freq in [523, 659, 784, 1047]:
        buzzer.ChangeFrequency(freq)
        time.sleep(0.1)
    buzzer.stop()


def beep_photo():
    buzzer.start(50)
    buzzer.ChangeFrequency(1500)
    time.sleep(0.05)
    buzzer.ChangeFrequency(1200)
    time.sleep(0.05)
    buzzer.stop()


def beep_confused():
    buzzer.start(40)
    frequencies = [500, 600, 500, 700, 500, 650]
    for freq in frequencies:
        buzzer.ChangeFrequency(freq)
        time.sleep(0.12)
    buzzer.stop()


def beep_sleepy():
    buzzer.start(30)
    for freq in range(600, 300, -25):
        buzzer.ChangeFrequency(freq)
        time.sleep(0.08)
    buzzer.stop()


def beep_sad():
    buzzer.start(45)
    frequencies = [600, 550, 500, 450, 400, 350]
    for freq in frequencies:
        buzzer.ChangeFrequency(freq)
        time.sleep(0.15)
    buzzer.stop()


def reset_conversation():
    global conversation_history, current_personality, adult_mode
    conversation_history = []
    current_personality = 0
    adult_mode = False
    print("Conversation reset (kid mode, Friendly Teacher)")


def wait_for_button_press():
    global last_interaction_time, conversation_history
    print("Ready! Short press=voice, Long press=photo+voice")

    sleepy_shown = False
    while GPIO.input(BUTTON_PIN) == GPIO.HIGH:
        # Check for idle timeout while waiting
        idle_time = time.time() - last_interaction_time
        if idle_time > SLEEPY_TIMEOUT and not sleepy_shown:
            draw_sleepy_face()
            beep_sleepy()
            tft_write_lines([
                "  Classroom Buddy",
                "      -_-",
                "       Zzz...",
                "  (press button)"
            ])
            reset_conversation()
            sleepy_shown = True
            time.sleep(3)
            draw_idle_face()
            tft_write_lines([
                "  Classroom Buddy",
                "      ^_^",
                "",
                "Press button!"
            ])
        time.sleep(0.1)

    press_start = time.time()
    print("Button pressed! Hold for photo...")

    while GPIO.input(BUTTON_PIN) == GPIO.LOW:
        time.sleep(0.1)

    press_duration = time.time() - press_start
    time.sleep(0.2)

    return press_duration


def take_photo(filename="photo.jpg"):
    print("Taking photo...")
    picam.capture_file(filename)
    print(f"Photo saved: {filename}")
    return filename


def transcribe_audio(audio_file):
    with open(audio_file, "rb") as file:
        transcription = groq_client.audio.transcriptions.create(
            file=(audio_file, file.read()),
            model="whisper-large-v3",
            language="en"
        )
    return transcription.text


# OLED Face Functions
def draw_idle_face():
    with canvas(oled) as draw:
        draw.rectangle((30, 20, 40, 30), outline="white", fill="white")
        draw.rectangle((88, 20, 98, 30), outline="white", fill="white")
        draw.arc((40, 30, 88, 60), 0, 180, fill="white")


def draw_listening_face():
    with canvas(oled) as draw:
        draw.ellipse((25, 15, 45, 35), outline="white", fill="white")
        draw.ellipse((83, 15, 103, 35), outline="white", fill="white")
        draw.ellipse((58, 45, 70, 55), outline="white", fill="white")


def draw_thinking_face():
    with canvas(oled) as draw:
        draw.rectangle((30, 15, 40, 25), outline="white", fill="white")
        draw.rectangle((88, 15, 98, 25), outline="white", fill="white")
        draw.line((50, 50, 78, 50), fill="white", width=2)
        draw.ellipse((100, 10, 105, 15), outline="white")


def draw_talking_face():
    with canvas(oled) as draw:
        draw.rectangle((30, 20, 40, 30), outline="white", fill="white")
        draw.rectangle((88, 20, 98, 30), outline="white", fill="white")
        draw.ellipse((50, 45, 78, 60), outline="white", fill="white")


def draw_talking_mouth_closed():
    with canvas(oled) as draw:
        draw.rectangle((30, 20, 40, 30), outline="white", fill="white")
        draw.rectangle((88, 20, 98, 30), outline="white", fill="white")
        draw.line((50, 52, 78, 52), fill="white", width=2)


def draw_camera_face():
    with canvas(oled) as draw:
        draw.ellipse((25, 15, 45, 35), outline="white", fill="white")
        draw.ellipse((83, 15, 103, 35), outline="white", fill="white")
        draw.ellipse((30, 20, 40, 30), outline="black", fill="black")
        draw.ellipse((88, 20, 98, 30), outline="black", fill="black")
        draw.arc((40, 35, 88, 60), 0, 180, fill="white")


def draw_confused_face():
    with canvas(oled) as draw:
        draw.line((25, 25, 35, 20), fill="white", width=3)
        draw.line((93, 20, 103, 25), fill="white", width=3)
        draw.arc((45, 45, 65, 60), 180, 360, fill="white")
        draw.arc((63, 45, 83, 60), 0, 180, fill="white")


def draw_sleepy_face():
    with canvas(oled) as draw:
        draw.arc((25, 20, 45, 35), 180, 360, fill="white")
        draw.arc((83, 20, 103, 35), 180, 360, fill="white")
        draw.ellipse((50, 48, 78, 58), outline="white", fill="white")
        draw.text((105, 10), "z", fill="white")
        draw.text((110, 15), "Z", fill="white")


def draw_sad_face():
    with canvas(oled) as draw:
        draw.line((25, 25, 30, 20), fill="white", width=3)
        draw.line((35, 20, 40, 25), fill="white", width=3)
        draw.line((88, 25, 93, 20), fill="white", width=3)
        draw.line((98, 20, 103, 25), fill="white", width=3)
        draw.arc((40, 55, 88, 75), 180, 360, fill="white")


def animate_talking(duration=1.0):
    end_time = time.time() + duration
    mouth_open = True

    while time.time() < end_time:
        if mouth_open:
            draw_talking_face()
        else:
            draw_talking_mouth_closed()

        buzzer.start(35)
        freq = [500, 600, 700, 800, 650, 750][int(time.time() * 10) % 6]
        buzzer.ChangeFrequency(freq)
        time.sleep(0.08)
        buzzer.stop()

        mouth_open = not mouth_open
        time.sleep(0.08)


def tft_write_lines(lines, color='white', bg='black'):
    image = Image.new('RGB', (320, 240), color=bg)
    draw = ImageDraw.Draw(image)
    y = 10
    for line in lines[:8]:  # Up to 8 lines now
        draw.text((10, y), line[:40], fill=color, font=font)
        y += 28
    tft.display(image)


def record_audio(duration=5, sample_rate=16000):
    print(f"Recording for {duration} seconds...")
    audio = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype='int16',
        device='plughw:1'  # I2S mic ALSA device (verify with arecord -l)
    )
    sd.wait()
    return audio, sample_rate


# Main Loop
def main():
    global last_interaction_time, conversation_history

    print("Classroom Buddy starting up!")

    draw_idle_face()
    beep_startup()
    tft_write_lines([
        "  Classroom Buddy",
        "      ^_^",
        "",
        "Press button!"
    ])

    last_interaction_time = time.time()

    while True:
        try:
            # Wait for button (idle timeout is checked inside)
            press_duration = wait_for_button_press()
            last_interaction_time = time.time()
            beep_button()
            use_vision = press_duration >= 2.0

            if use_vision:
                print("Long press - VISION mode")
                draw_camera_face()
                tft_write_lines([
                    "  Classroom Buddy",
                    "     [O_O]",
                    "",
                    "  Taking photo..."
                ])
                time.sleep(0.5)
                beep_photo()
                photo_file = take_photo("photo.jpg")
                time.sleep(0.5)
            else:
                print("Short press - Voice mode")

            # LISTENING
            draw_listening_face()
            beep_listening()
            tft_write_lines([
                "  Classroom Buddy",
                "     (o.o)",
                "",
                "   Listening..."
            ])

            audio, sample_rate = record_audio(duration=5)
            wav.write("question.wav", sample_rate, audio)

            # THINKING
            draw_thinking_face()
            beep_thinking()
            tft_write_lines([
                "  Classroom Buddy",
                "     (@_@)",
                "",
                "   Thinking..."
            ])

            print("Transcribing...")
            question = transcribe_audio("question.wav").strip()
            print(f"\nStudent asked: {question}")

            if not question:
                tft_write_lines([
                    "  Classroom Buddy",
                    "     (o.o)?",
                    "",
                    " Didn't hear you!"
                ])
                draw_confused_face()
                beep_confused()
                time.sleep(2)
                draw_idle_face()
                time.sleep(1)
                continue

            # Check for voice commands before sending to LLM
            if check_voice_command(question):
                draw_idle_face()
                tft_write_lines([
                    "  Classroom Buddy",
                    "      ^_^",
                    "",
                    "Press button!"
                ])
                continue

            tft_write_lines([
                "You asked:",
                "",
                question[:40]
            ])
            time.sleep(2)

            print("Getting response...")

            if use_vision:
                # Vision - Claude
                with open(photo_file, "rb") as image_file:
                    image_data = base64.b64encode(image_file.read()).decode("utf-8")

                message = claude_client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=150,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": image_data
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": f"""{get_system_prompt()}
Look at this image and answer: {question}

Additional guidelines for vision:
- Describe what you see clearly
- Keep responses SHORT (2-3 sentences max)"""
                                }
                            ]
                        }
                    ]
                )
                response = message.content[0].text
            else:
                # Voice - Groq with history
                messages = [{"role": "system", "content": get_system_prompt()}]
                messages.extend(conversation_history)
                messages.append({"role": "user", "content": question})

                completion = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    max_tokens=150,
                    messages=messages
                )
                response = completion.choices[0].message.content

                # Add to history
                conversation_history.append({"role": "user", "content": question})
                conversation_history.append({"role": "assistant", "content": response})

                # Keep last 20 messages
                if len(conversation_history) > 20:
                    conversation_history = conversation_history[-20:]

            print(f"\nResponse: {response}\n")
            print(f"History: {len(conversation_history)} messages\n")

            # TALKING with animation
            words = response.split()
            lines = [""]
            for word in words:
                if len(lines[-1]) + len(word) + 1 <= 40:
                    lines[-1] += (" " if lines[-1] else "") + word
                else:
                    lines.append(word)

            for i in range(0, len(lines), 7):
                chunk = lines[i:i + 7]
                tft_write_lines(["Response:"] + chunk)
                animate_talking(duration=4.0)

            # Back to idle
            draw_idle_face()
            beep_happy()
            tft_write_lines([
                "  Classroom Buddy",
                "      ^_^",
                "",
                "Press button!"
            ])

        except KeyboardInterrupt:
            print("\nShutting down...")
            buzzer.stop()
            picam.stop()
            oled.clear()
            GPIO.cleanup()
            break
        except Exception as e:
            print(f"Error: {e}")
            draw_sad_face()
            beep_sad()
            tft_write_lines([
                "  Oops!",
                "  Something went",
                "  wrong. Try again!",
                ""
            ])
            time.sleep(3)
            draw_idle_face()


if __name__ == "__main__":
    try:
        main()
    finally:
        buzzer.stop()
        picam.stop()
        GPIO.cleanup()
