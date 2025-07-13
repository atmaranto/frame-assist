import asyncio
import aioconsole
import tempfile
import os
import atexit

from frame_msg import FrameMsg

from PIL import Image
from io import BytesIO

import traceback

import subprocess
import re

import datetime

import sys
sys.path.append(os.path.join(os.environ.get("HOME", os.path.abspath('..')), "VirtualAssistant"))

from faster_whisper import WhisperModel

from assistant import Assistant
from assistant.assistant import create_basic_llm

import dotenv
dotenv.load_dotenv()

def load_default_bot():
    from langchain_core.runnables import RunnableLambda
    from langchain_ollama import ChatOllama
    from langchain_community.tools import BraveSearch

    orig_model = ChatOllama(model="qwen3:8b", extract_reasoning=True) #llama3.1:8b-instruct-q6_K
    tools = []

    if "BRAVE_SEARCH_API_KEY" in os.environ:
        tools.append(BraveSearch())
    else:
        print("Brave Search API key not found in environment variables. Skipping BraveSearch tool.")
    

    orig_model = orig_model.bind_tools(tools)

    # import code; code.interact(local=locals())

    model, chat_history = create_basic_llm(orig_model)

    model = RunnableLambda(lambda x: dict(
        system_message="You are a helpful agent named Frame that runs on a set of advanced smart glasses. You will respond in a cheeky but accurate way to user queries based on the provided context and history. Do not use parentheses in your responses or emotes; what you say will be displayed on the smart glasses screen and spoken aloud.",
        optional_user_prompt=[],
        **x)
    ) | model

    return model, tools, chat_history

MESSAGE_BASE = 0x30

async def resend(frame: FrameMsg, data_received, print_response_handler, local_path):
    await frame.send_break_signal()
    await asyncio.sleep(1)
    await frame.send_break_signal()
    await asyncio.sleep(1)
    await frame.print_short_text("Loading... ")
    # await frame.send_lua("frame.display.text('Loading... ' .. frame.battery_level(), 1, 1);frame.display.show();print(0)", await_print=True)
    await frame.upload_frame_app(local_filename=local_path, frame_filename='main.lua')
    await frame.upload_stdlua_libs(['data'], minified=True)
    frame.attach_print_response_handler(print_response_handler)
    await asyncio.sleep(1)
    await frame.start_frame_app(frame_app_name='main')
    await asyncio.sleep(1)

    print("Sent")

async def main(args, model=None, tools=None, chat_history=None):
    frame = FrameMsg()

    try:
        await frame.connect(initialize=False)

        data_received = asyncio.Event()
        async def print_response_handler(data):
            if isinstance(data, bytes):
                data = data.decode('utf-8', errors='replace')
            print(data, end='', flush=True)
        
        def data_received_handler(data):
            data = data[1:]
            if isinstance(data, bytes):
                data = data.decode('utf-8', errors='replace')
            elif not isinstance(data, str):
                data = bytes(data).decode('utf-8', errors='replace')
            print(data, end='', flush=True)
            data_received.set()
        
        frame.register_data_response_handler(None, [MESSAGE_BASE + 1], data_received_handler)

        image_data = []
        def image_data_end(data):
            print("Image data received, saving to file...")
            # Load into PIL
            image_bytes = b''.join(image_data)
            image = Image.open(BytesIO(image_bytes), formats=['JPEG']).convert('RGB')
            # Rotate the image 90 degrees counter-clockwise
            image = image.rotate(90, expand=False)
            # Save to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpeg") as f:
                image.save(f, format='JPEG')

            if os.name == 'nt':
                os.startfile(f.name)
            elif os.name == 'posix':
                os.system("xdg-open " + f.name)
            print(f"Image saved to {f.name}")
            image_data.clear()
        def image_data_append(data):
            image_data.append(bytes(data[1:]))
            print(f"Received {len(data)} bytes of image data, total {sum(len(d) for d in image_data)} bytes")

        assistant = Assistant(llm=model, model=WhisperModel(args.model_size, device="cpu", local_files_only=False), wake_words=["hey frame", "hey rain", "hey brain", "hey frank"], configuration={"session_id": "frame"})
        
        if args.save_audio:
            ext = os.path.splitext(args.save_audio)[1]
            if ext not in ['.wav']:
                args.save_audio += '.wav'

            audio_proc = subprocess.Popen(['ffmpeg', '-f', 's16le', '-ar', '16000', '-ac', '1', '-i', '-', '-y', args.save_audio], stdin=subprocess.PIPE, bufsize=1024)
            atexit.register(audio_proc.stdin.close, input=b'', timeout=5)
            audio_file = open(args.save_audio + ".s16le", 'wb')
        def mic_data_handler(data):
            assistant.feed(data[1:])

            if args.save_audio:
                audio_proc.stdin.write(data[1:])
                audio_proc.stdin.flush()

                audio_file.write(data[1:])
                audio_file.flush()

        assistant.on('wake_word_detected', lambda wake_word, transcription: print(f"Wake word detected: {wake_word}"))
        assistant.on('transcription_word', lambda transcription: print(f"Large transcription: {transcription}"))

        # assistant.on('audio_process', lambda audio: print(f"Processing large audio chunk of size {len(audio)}"))
        frame.register_data_response_handler(None, [MESSAGE_BASE + 2], image_data_append)
        frame.register_data_response_handler(None, [MESSAGE_BASE + 3], image_data_end)
        frame.register_data_response_handler(None, [MESSAGE_BASE + 4], mic_data_handler)

        if os.name == 'posix':
            espeak_process = subprocess.Popen(['espeak'], stdin=subprocess.PIPE, encoding='utf-8', bufsize=1)
        else:
            import win32com.client
            espeak_process = win32com.client.Dispatch("SAPI.SpVoice")
            class EspeakProcessWrapper:
                def __init__(self, sapi_voice):
                    self.sapi_voice = sapi_voice
                    self.stdin = self
                def write(self, text):
                    self.sapi_voice.Speak(text, 1)
                def flush(self):
                    pass
            espeak_process = EspeakProcessWrapper(espeak_process)

        frame.attach_print_response_handler(print_response_handler)

        def tool_call(tool, responses):
            for t in tools:
                if t.name == tool["name"]:
                    response = t(tool["args"])
                    if response is not None:
                        responses.append(response)
        partial_word = ""
        def speak_word(part):
            nonlocal partial_word
            if part is None:
                if partial_word:
                    espeak_process.stdin.write(partial_word + "\n")
                    espeak_process.stdin.flush()
                    partial_word = ""
            else:
                partial_word += part
                if any(c in partial_word for c in '.!?'):
                    words = partial_word.strip()
                    sents = re.split(r'(?<=[.!?])', words)
                    print(sents)
                    for sent in sents:
                        espeak_process.stdin.write(sent + "\n")
                        espeak_process.stdin.flush()
                    partial_word = ""
        
        assistant.on('tool', tool_call)
        assistant.on('assistant_speak_word', speak_word)
        assistant.on('assistant_speak', lambda text: speak_word(None))
        
        # await frame.send_lua("frame.display.text('Loading... ' .. frame.battery_level(), 1, 1);frame.display.show();print(0)", await_print=True)
        if args.resend:
            await resend(frame, data_received, print_response_handler, 'lua-repl.lua')
        
        async def sync_time():
            utc_offset = int(datetime.datetime.now(datetime.UTC).timestamp() - datetime.datetime.now().timestamp())
            timezone = f"{utc_offset // 3600:+d}:{round(utc_offset % 3600 // 60 / 15) * 15 % 60:02d}"
            await frame.send_message(MESSAGE_BASE + 1, str(datetime.datetime.now(datetime.UTC).timestamp()).encode() + b"\n" + timezone.encode())

        await sync_time()

        try:
            while True:
                data = await aioconsole.ainput("> ")
                # print(repr(data))
                if data == '.exit' or data == '.exit break':
                    if 'break' in data:
                        await frame.send_break_signal()
                    break
                elif data.startswith('.resend'):
                    parts = data.split()
                    if len(parts) > 1:
                        filename = parts[1]
                    else:
                        filename = 'lua-repl.lua'
                    await resend(frame, data_received, print_response_handler, filename)
                    continue
                elif data.startswith('.python '):
                    python_code = data[8:]
                    if python_code.strip():
                        try:
                            exec(python_code)
                        except Exception as e:
                            print(f"Error executing Python code: {e}")
                            traceback.print_exc()
                    continue
                elif data == '.reset':
                    await frame.send_break_signal()
                    await asyncio.sleep(1)
                    await frame.send_reset_signal()
                    continue
                elif data == '.resync':
                    await sync_time()
                    continue
                elif data.startswith('.'):
                    print(f"Unknown command: {data}")
                    print("Usage:")
                    print(".exit - Exit the REPL")
                    print(".resend [filename] - Resend the main Lua file and standard libraries")
                    print(".python <code> - Execute Python code")
                    print(".resync - Resync the Frame device time")
                    print(".reset - Reset the Frame device")
                    print("All other commands are sent as Lua code to the Frame device.")
                    continue
                if not data.strip(): continue

                data_received.clear()
                await frame.send_message(MESSAGE_BASE, data.encode())
                await data_received.wait()
                data_received.clear()
        finally:
            # await frame.send_break_signal()
            await frame.disconnect()

    except Exception as e:
        traceback.print_exc()
        return

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lua REPL for Frame device")
    parser.add_argument('--save-audio', default=None, help="Path to save audio data")
    parser.add_argument('--resend', action='store_true', help="Resend the main Lua file and standard libraries")
    parser.add_argument('--model-size', default='Systran/faster-distil-whisper-large-v2', help="Whisper model size to use for transcription")
    args = parser.parse_args()
    asyncio.run(main(args, *load_default_bot()))
