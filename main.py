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

from faster_whisper import WhisperModel

from assistant import Assistant
from assistant.assistant import create_basic_llm

import colorama

import dotenv
dotenv.load_dotenv()

def load_default_bot():
    from langchain_core.runnables import RunnableLambda
    from langchain_ollama import ChatOllama
    from langchain_community.tools import BraveSearch, tool

    orig_model = ChatOllama(model="qwen3:8b", extract_reasoning=True) #llama3.1:8b-instruct-q6_K
    tools = []

    if "BRAVE_SEARCH_API_KEY" in os.environ:
        tools.append(BraveSearch())
    else:
        print("Brave Search API key not found in environment variables. Skipping BraveSearch tool.")
    
    @tool
    def get_time():
        """Get the current time in a human-readable format."""
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    tools.append(get_time)

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

        assistant = Assistant(llm=model, model=WhisperModel(args.model_size, device="auto", local_files_only=False), wake_words=args.wake_words.split(","), true_wake_word="hey frame", configuration={"session_id": "frame"})
        
        if args.save_audio:
            ext = os.path.splitext(args.save_audio)[1]
            if ext not in ['.wav']:
                args.save_audio += '.wav'

            audio_proc = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-hide_banner', '-f', 's16le', '-ar', '16000', '-ac', '1', '-i', '-', '-y', args.save_audio], stdin=subprocess.PIPE, bufsize=1024)
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
                    response = t.invoke(tool["args"])
                    if response is not None:
                        responses.append(response)
        partial_word = ""
        in_thinking = False
        async def speak_word(part):
            nonlocal partial_word, in_thinking
            if part is None:
                in_thinking = False
            else:
                if "<think>" in partial_word:
                    in_thinking = True
                if "</think>" in partial_word:
                    in_thinking = False
            if part is None:
                if partial_word:
                    print(f"{colorama.Fore.GREEN}{partial_word}{colorama.Style.RESET_ALL}\n", end='', flush=True)
                    if "</think>" in partial_word:
                        partial_word = partial_word[partial_word.index("</think>") + len("</think>"):]
                    espeak_process.stdin.write(partial_word + "\n")
                    espeak_process.stdin.flush()
                    await frame.send_message(MESSAGE_BASE + 6, partial_word.strip().encode('utf-8'))
                    partial_word = ""
            else:
                partial_word += part
                if any(c in partial_word for c in '.!?'):
                    words = partial_word.strip()
                    sents = re.split(r'(?<=[.!?])', words)
                    for sent in sents:
                        print(f"{colorama.Fore.GREEN}{sent}{colorama.Style.RESET_ALL} ", end='', flush=True)
                    idx = 0
                    if "</think>" in partial_word:
                        idx = partial_word.index("</think>") + len("</think>")
                    sents = re.split(r'(?<=[.!?])', words[idx:])
                    if not in_thinking:
                        for sent in sents:
                            await frame.send_message(MESSAGE_BASE + 6, sent.strip().encode('utf-8'))
                            espeak_process.stdin.write(sent + "\n")
                            espeak_process.stdin.flush()
                    partial_word = ""
        
        loop = asyncio.get_event_loop()
        assistant.on('tool', tool_call)
        assistant.on('assistant_speak_word', lambda word: loop.create_task(speak_word(word)))
        assistant.on('assistant_speak', lambda text: asyncio.run_coroutine_threadsafe(speak_word(None), loop))
        
        # await frame.send_lua("frame.display.text('Loading... ' .. frame.battery_level(), 1, 1);frame.display.show();print(0)", await_print=True)
        if args.resend:
            await resend(frame, data_received, print_response_handler, 'lua-repl.lua')
        
        async def sync_time():
            utc_offset = int(datetime.datetime.now().astimezone().utcoffset().total_seconds())
            timezone = f"{utc_offset // 3600:+d}:{round(utc_offset % 3600 // 60 / 15) * 15 % 60:02d}"
            await frame.send_message(MESSAGE_BASE + 1, str(datetime.datetime.now(datetime.UTC).timestamp()).encode() + b"\n" + timezone.encode())

        await sync_time()
        await frame.send_message(MESSAGE_BASE + 5, b'SEABLUE,Connected') # Clear any existing connection message

        try:
            while True:
                data = await aioconsole.ainput("> ")
                if not data.strip():
                    continue

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
                    await frame.send_message(MESSAGE_BASE + 5, b'SEABLUE,Time Resynced')
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
            await frame.send_message(MESSAGE_BASE + 5, b'RED,Disconnected') # Exiting
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
    parser.add_argument('--wake-words', default="hey frame,hey rain,hey brain,hey frank,hey fraim,hey graham", help="Comma-separated list of wake words to use")
    parser.add_argument('--device', default='auto', help="Device to use for Whisper model (auto, cpu, cuda, etc.)")
    args = parser.parse_args()
    asyncio.run(main(args, *load_default_bot()))
