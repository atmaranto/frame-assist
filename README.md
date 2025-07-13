# Project Description

This project provides a fully working, configurable "OS" suite for the Frame, including menus, settings, and a basic HUD.

# Installation

Installation should be fairly simple, provided you have an relatively recent (3.10+) version of Python installed.

Simply clone the git repository, create a venv or similar, and install the requirements. On Linux:
```bash
git clone https://github.com/atmaranto/frame-assist
cd frame-assist
python -m venv frame-assist-venv
source frame-assist-venv/bin/activate
pip install -r requirements.txt
```
On Windows:
```bat
git clone https://github.com/atmaranto/frame-assist
cd frame-assist
python -m venv frame-assist-venv
frame-assist-venv\Scripts\activate.bat
pip install -r requirements.txt
```

# Usage

## The Menu

Single tap and wait one half second to go to the menu. Choose your option by *tilting* your head right and left. Select by tapping.

The selected option will appear in YELLOW.


## The HUD
The current HUD has several options, all enabled by default:
```
N    10    20   30     # Compass
[|||||||||||     ] 95% # Battery
11:12:19 AM Friday     # Day/Time
TX: 10 RX: 50          # KB TX/RX
```

You can enable or disable HUD components in the menu.

### Microphone

By default, the microphone is ENABLED. All microphone activity will be sent to the accompanying program and transcribed. Language that SEEMS to begin with "hey frame" or a similar-sounding word will be responded to via the packaged `simple-virtual-assistant`.

### LICENSE

MIT License

```
Copyright 2025 Anthony Maranto

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```