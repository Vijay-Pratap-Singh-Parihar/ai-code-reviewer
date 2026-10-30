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

---

## 4. Keep half-finished downloads across restarts (`OLLAMA_NOPRUNE`)

**What happened.** The first download of `qwen3.5:9b` (6.6 GB) ran at 1–2 MB/s over an unstable connection and stopped several times on DNS failures and `503` errors from Ollama's registry. `ollama pull` resumes where it stopped, and it reached 2.5 GB. After the laptop restarted, the folder was empty again: **when the Ollama server starts it deletes downloads it considers unused, including unfinished ones** (its log shows `OLLAMA_NOPRUNE:false`).

**What was done.**

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_NOPRUNE", "1", "User")
```

then Ollama was restarted (`D:\Ollama\app\ollama app.exe`), and its log confirmed `OLLAMA_NOPRUNE:true`.

**Why.** On a connection that drops, a large download can take several sessions. With pruning off, whatever has been downloaded survives restarts and `ollama pull` continues from there.

**What it affects.** Ollama no longer cleans up blobs left behind by removed or abandoned models on its own. If you delete models later, free the space with `ollama rm <model>` (which removes its files) rather than relying on automatic cleanup. Nothing else changes.

**How to undo.** `[Environment]::SetEnvironmentVariable("OLLAMA_NOPRUNE", $null, "User")` and restart Ollama.

**Download command (with retries, because of the connection):**

```powershell
for ($i = 1; $i -le 300; $i++) {
  ollama pull qwen3.5:9b
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 30
}
```

If the line stays unreliable, downloading on a steadier network (college Wi-Fi, a phone hotspot) is faster. A finished `D:\Ollama\models` folder can also be copied from another machine.

**Note (2026-10-30).** Ollama updates itself in the background; it is now 0.40.2. The settings above (model folder, context length, no-prune) carried over unchanged.

## Update: Ollama 0.40.2 to 0.40.13 (2026-10-30)

Ollama updates itself, but only at its own pace. Here it was updated by hand to the newest release so the local model runs on the current engine.

```powershell
# 1. The pinned release installer, downloaded with resume (the line was unreliable)
curl.exe -L -C - -o "D:\Ollama\installer\OllamaSetup-0.40.13.exe" `
  https://github.com/ollama/ollama/releases/download/v0.40.13/OllamaSetup.exe
Get-AuthenticodeSignature "D:\Ollama\installer\OllamaSetup-0.40.13.exe"   # Valid, CN=Ollama Inc.

# 2. Quit Ollama (no model download running), then install over the same folder
Get-Process "ollama app","ollama" -ErrorAction SilentlyContinue | Stop-Process -Force
$env:OLLAMA_MODELS = "D:\Ollama\models"; $env:OLLAMA_CONTEXT_LENGTH = "32768"; $env:OLLAMA_NOPRUNE = "1"
Start-Process "D:\Ollama\installer\OllamaSetup-0.40.13.exe" -ArgumentList '/DIR="D:\Ollama\app"','/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES'
```

**Why.**
- It is a specific, versioned release from Ollama's GitHub, not the moving "latest" link, so the record says exactly what was installed.
- The signature check also proves the resumed download is complete: a truncated or altered file fails it.
- Installing into the same `/DIR` replaces the program in place, and the `$env:` lines give the first server the same settings as step 3.

**What it affects.**
- The program in `D:\Ollama\app` is replaced.
- Downloaded models, including partial downloads, and the three user variables are left alone.
- The installer opens the Ollama window when it finishes; closing the window is fine, and Ollama stays in the tray.
- A command that waits for the installer (`Start-Process -Wait`) keeps waiting until that window is closed.
- Another 1.7 GB on D: for the new installer. The old `OllamaSetup.exe` (0.40.0) can be deleted, or kept for a rollback.

**Result.**
- `ollama version is 0.40.13`.
- The server log shows `OLLAMA_MODELS:D:\Ollama\models`, `OLLAMA_CONTEXT_LENGTH:32768` and `OLLAMA_NOPRUNE:true`.
- The `qwen3.5:9b` download resumed from where it stopped.

**How to undo.** Run the older installer the same way (quit Ollama, same `/DIR` and switches). Models are unaffected.

## 5. Download the models and measure them on this laptop

```powershell
ollama pull qwen3.5:9b   # 6.6 GB, the first choice
ollama pull qwen3.5:4b   # 3.3 GB, added after measuring 9b
ollama list
```

**Why.** Qwen 3.5 is an open-weight model family that is strong at code and has an OpenAI-compatible API through Ollama, which is what revu's "OpenAI-compatible" provider talks to. 9b was tried first for quality. Then 4b was added, because the measurements below showed 9b does not fit this GPU.

**How it was measured.** Every model got the same prompt through Ollama's HTTP API: review a two-line diff and answer in JSON. The diff was `return sum(xs) / len(xs)`, which has an obvious divide-by-zero bug on an empty list. Speed comes from Ollama's own `eval_count` / `eval_duration`. GPU share comes from `GET /api/ps` (`size_vram`).

| Model | Context | Thinking | Load (cold) | Speed | Model in GPU | Found the bug? |
|---|---|---|---|---|---|---|
| qwen3.5:9b | 32k | off | 66 s | 4.6 tok/s | 1.7 of 7.4 GB | no |
| qwen3.5:9b | 8k | on | – | – | 1.7 of 6.4 GB | no answer within 15 min |
| qwen3.5:4b | 32k | off | 7 s | 10.3 tok/s | 1.9 of 4.9 GB | no |
| qwen3.5:4b | 8k | off | 7.5 s | 11.9 tok/s | 1.9 of 3.9 GB | no |
| qwen3.5:4b | 8k | on | – | 7.6 tok/s (2,110 tokens, 280 s) | 1.9 of 3.9 GB | **yes**, high severity, with the right fix |

**What it means.**
- The GTX 1650 has 4 GB of video memory, and only about 1.9 GB of a model fits next to Windows' own use. The rest runs on the CPU, so speed is set by how much of the model spills over.
- 4b is about 2.5× faster than 9b and loads ten times sooner. **qwen3.5:4b is the demo model on this laptop.**
- With thinking off, both sizes answered instantly but missed the bug. With thinking on, 4b found it but needed almost 5 minutes for two lines.
- This is one prompt, so it is not a benchmark. Quality is measured properly with `revu-bench`.
- For a live demo, use a very small PR and expect minutes per review.
- Real reviews and the benchmark use a hosted provider (Groq) or a machine with a larger GPU. Both connect through the same AI Providers screen.
- Ollama's OpenAI-compatible endpoint has thinking **on by default**: a one-word reply took 32 s on 9b.

**What it affects.** 9.9 GB on D: under `D:\Ollama\models`. A loaded model uses RAM and video memory until it has been idle for 5 minutes, then Ollama unloads it.

**How to undo.** `ollama rm qwen3.5:9b` (or `qwen3.5:4b`) frees its space. Deleting 9b is reasonable on this laptop, since 4b is the one in use.

<!-- Steps 6+ are appended as they are performed. -->
