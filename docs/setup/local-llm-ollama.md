# Running a local LLM for revu with Ollama (Windows)

> **Status:** in progress. Steps 0–3 are done; model downloads, verification and connecting it to revu are added as they are performed.

This is a record of how Ollama was installed on the development laptop so revu can review code with a model that runs entirely on this machine. Each step says what was done, why, and what it affects. Use it to reproduce the setup on another machine or to undo it.

**Why a local model at all.** Enterprises asked for a way to use their own model instead of a commercial API (see `docs/research/`). A model served by Ollama shows exactly that: revu talks to it through the same OpenAI-compatible interface it would use for a company's vLLM server or fine-tuned model. No code leaves the laptop, and every review is free. The trade-off is speed and quality on this hardware.

---

## 0. The machine (checked before installing)

| Item | Value | What it means for local models |
|---|---|---|
| GPU | NVIDIA GeForce GTX 1650, 4 GB VRAM, compute capability 7.5, driver 617.42 | Supported by Ollama (needs CC 5.0+ and driver 550+). Only small parts of a model fit in 4 GB, so most layers run on the CPU. |
| CPU | Intel Core i5-9300H, 4 cores / 8 threads | Does most of the work. This is what limits speed. |
| RAM | 24 GB | Enough for a 20B mixture-of-experts model (~14 GB) next to Windows and Docker, but not much more. |
| Disk C: | ~7.7 GB free | Too little for models. Nothing large goes on C:. |
| Disk D: | ~212 GB free | Holds the program, the installer and all models. |
| Ollama already installed? | No (no program folder, no `~/.ollama`, port 11434 free) | Clean install, nothing to migrate. |

---

## 1. Point Ollama's model store at D: and raise the context window

**What was done** (Windows user environment variables, set before Ollama ever ran):

```powershell
New-Item -ItemType Directory -Force "D:\Ollama\models"
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\Ollama\models", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "32768", "User")
```

**Why.**
- `OLLAMA_MODELS`: Ollama stores downloaded models in `C:\Users\<you>\.ollama\models` by default. The two models used here are about 21 GB together and C: has under 8 GB free, so the first download would fill the system drive. Setting it before the first run means nothing is ever written to C: and nothing has to be moved later.
- `OLLAMA_CONTEXT_LENGTH`: on GPUs with less than 24 GB of memory Ollama's default context window is only **4,096 tokens**, and anything longer is **cut off silently**. A code review prompt (instructions + diff + surrounding code) is often 6,000–20,000 tokens. With the default, the model would review a truncated diff without any error. 32,768 tokens covers realistic pull requests. revu talks to Ollama through its OpenAI-compatible API, which cannot change the context per request, so it has to be set here.

**What it affects.**
- Only this Windows user. Programs started *after* this see the variables; terminals that were already open do not.
- A larger context uses more memory while a model is loaded (the KV cache grows with context), and long prompts take longer to process. If memory gets tight, 16384 is the fallback.
- Nothing else on the system reads these variables.

**How to undo.** `[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", $null, "User")` (same for `OLLAMA_CONTEXT_LENGTH`), then delete `D:\Ollama\models`.

---

## 2. Download the official installer to D:

**What was done.**

```powershell
Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile "D:\Ollama\installer\OllamaSetup.exe"
Get-AuthenticodeSignature "D:\Ollama\installer\OllamaSetup.exe"
```

**Why.** It comes straight from ollama.com rather than a mirror, and its digital signature is checked before it runs, so a tampered file would be caught. It is downloaded to D: (not the default Downloads folder on C:) because the installer bundles the CUDA libraries and is large.

**What it affects.** 1.5 GB of disk on D: (`D:\Ollama\installer\OllamaSetup.exe`). Nothing is installed yet. The file can be deleted after step 3; keep it if you want to reinstall offline.

**Result.** Signature status `Valid`, signed by `CN=Ollama Inc., Toronto, Ontario, CA`.

---

## 3. Install Ollama onto D:, silently

**What was done.**

```powershell
$env:OLLAMA_MODELS = "D:\Ollama\models"; $env:OLLAMA_CONTEXT_LENGTH = "32768"
Start-Process "D:\Ollama\installer\OllamaSetup.exe" -ArgumentList '/DIR="D:\Ollama\app"','/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES'
```

**Why.**
- `/DIR="D:\Ollama\app"`: the installer's default is `C:\Users\<you>\AppData\Local\Programs\Ollama`. The program is ~2.9 GB (mostly bundled CUDA/GPU libraries), which C: can't spare.
- `/VERYSILENT /NORESTART /SUPPRESSMSGBOXES`: a per-user install with no prompts and no reboot. No administrator rights are needed.
- The two `$env:` lines matter because the installer **starts Ollama itself** when it finishes, and that first server process inherits the installer's environment. The user variables from step 1 only reach programs started fresh from Windows, so without these lines the very first server could still have used C: and a 4k context.

**What it affects.**
- **Disk:** 2.85 GB in `D:\Ollama\app`. Nothing large on C:. Ollama keeps only small files (keys, logs) under `C:\Users\<you>\.ollama` and `%LOCALAPPDATA%\Ollama`.
- **Startup:** the installer registers *Ollama* to start at sign-in (tray icon). While idle it uses ~200 MB of RAM. Turn it off in *Settings → Apps → Startup* if you only want it on demand.
- **PATH:** `ollama` is added to the user PATH; new terminals can run `ollama ...` directly.
- **Network:** it listens on `127.0.0.1:11434` only, so it is **not reachable from other machines on your network**. Nothing was opened in the firewall.
- **GPU/CPU/RAM:** nothing is used until a model is loaded. A loaded model is unloaded after 5 minutes idle (`OLLAMA_KEEP_ALIVE`).

**Result.** `ollama version is 0.40.0`; both processes (`ollama.exe` server and `ollama app.exe` tray) run from `D:\Ollama\app`; no model folder was created on C:.

**How to undo.** *Settings → Apps → Installed apps → Ollama → Uninstall*, then delete `D:\Ollama` and the two user variables from step 1.

<!-- Steps 4+ are appended as they are performed. -->
