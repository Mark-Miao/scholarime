# Scholarime Installation

Scholarime requires Python 3.10 or newer. Pillow renders the general CSV charts, NumPy and
Matplotlib render publication templates, PyMuPDF enables optional rendered-PDF collision audits,
`pywebview` provides the native review window, `openpyxl` handles Excel input/output, and the
remaining data/search/import/screening logic uses the Python standard library.

## Create an Isolated Environment

Run these commands from the repository root so the relative dependency path is correct. If the
skill has been copied elsewhere, run the equivalent commands from the skill directory and use
`install/dependencies.txt`.

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r scholarime\install\dependencies.txt
```

If PowerShell blocks activation for the current process, the user may run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Windows Command Prompt

```bat
py -3 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r scholarime\install\dependencies.txt
```

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r scholarime/install/dependencies.txt
```

Always invoke pip as `python -m pip` so it targets the active interpreter.

For the complete toolkit, install the package in editable mode with optional dependencies:

```bash
python -m pip install -e ".[all]"
```

Individual groups are available when a smaller installation is preferred: `charts`, `figures`,
`figure-pdf`, `ui`, `excel`, `symbolic`, `workspace`, `stats`, and `writing`. The legacy
`scholarime/install/dependencies.txt` contains the full set.

`pywebview` uses the operating system webview. Windows normally uses WebView2, macOS uses
WKWebView, and Linux requires a supported GTK/WebKit runtime supplied by the distribution. If the
native window cannot load, `reference_review.py` falls back to the system browser, but browser
security can prevent automatic tab closing.

PDF OCR additionally requires the system Poppler utilities, Tesseract, and the relevant Tesseract
language packs, such as `eng` or `chi_sim`. Install them through the platform package manager or
the official distributions. `pypdf` and `pdfplumber` handle normal text and table extraction
without OCR. DOCX generation requires `python-docx`; true CSL processing requires `citeproc-py`.

## Slow Downloads and PyPI Mirrors

Do not change the user's pip source configuration automatically. If installation is unusually
slow or repeatedly times out, stop and ask the user which option they want:

1. Huawei Cloud
2. Alibaba Cloud
3. USTC
4. Tsinghua
5. Keep the current source configuration

Wait for explicit consent before using a different mirror. If the user chooses to keep the
current configuration, make no change and do not retry through another source.

After the user selects a mirror, prefer a one-command override so their persistent configuration
remains untouched:

```bash
python -m pip install --index-url <mirror-url> -r scholarime/install/dependencies.txt
```

Mirror URLs:

- Huawei: `https://repo.huaweicloud.com/repository/pypi/simple/`
- Alibaba Cloud: `https://mirrors.aliyun.com/pypi/simple/`
- USTC: `https://mirrors.ustc.edu.cn/pypi/simple/`
- Tsinghua: `https://pypi.tuna.tsinghua.edu.cn/simple`

On PowerShell, use the same command and replace the dependency path with
`scholarime\install\dependencies.txt`. Do not add `--trusted-host` unless the user has separately
approved that security tradeoff.

If the user explicitly asks to make the mirror persistent, only then change pip configuration,
for example:

```bash
python -m pip config set global.index-url <mirror-url>
```

## Semantic Scholar API Configuration

After dependency installation, the agent must ask the user whether they have a Semantic Scholar
API key. Do not assume one exists, and do not send a key through shell arguments or logs.

If the user has a key, run the configuration helper from an interactive terminal:

```bash
python scholarime/install/configure_semantic_scholar.py
```

The helper asks the install question and reads the key with hidden input. The key is stored in the
user configuration path:

- Windows: `%APPDATA%\Scholarime\config.json`
- macOS: `~/Library/Application Support/Scholarime/config.json`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/scholarime/config.json`

File permissions are restricted where the platform supports POSIX modes. The
`SEMANTIC_SCHOLAR_API_KEY` environment variable overrides the saved value.

If the user has no key, the agent must tell them:

> Semantic Scholar 搜索暂不可用，因为尚未配置 API Key。

The open sources OpenAlex, Crossref, arXiv, and PubMed remain available. Semantic Scholar is
skipped by the search tool with the same warning. To remove a previously saved key:

```bash
python scholarime/install/configure_semantic_scholar.py --remove
```

## Fonts

The default font is Times New Roman. If it is missing, the chart tool checks common academic
replacements such as Liberation Serif, Nimbus Roman, DejaVu Serif, and Georgia.

When labels or titles contain Chinese, the tool also checks CJK serif fonts such as Noto Serif CJK
SC, Source Han Serif SC, SimSun, Songti SC, and STSong. It reports the font it actually used. No
font download is required, but the operating system must provide a usable font.

Use `--list-fonts` to inspect detected academic fonts or pass an explicit font family or font file
path with `--font`.

## Verify the Installation

From the repository root:

```bash
python scholarime/scripts/scholarly_chart.py --help
python scholarime/scripts/scholarly_data.py --help
python scholarime/scripts/symbolic_derive.py --help
python scholarime/scripts/academic_search.py --help
python scholarime/scripts/scholarly_verify.py --help
python scholarime/scripts/scholarly_figure.py --help
python scholarime/scripts/scholarly_screening.py --help
python -m scholarime --help
python -m scholarime workspace --help
python -m scholarime stats --help
python -m scholarime manuscript --help
python scholarime/scripts/reference_review.py --help
python scholarime/install/configure_semantic_scholar.py --help
python scholarime/scripts/scholarly_chart.py --list-palettes
python scholarime/scripts/scholarly_chart.py --list-fonts
python -m unittest discover -s scholarime/tests -v
```

## Optional Formal Verification Tools

SymPy is installed from the Python dependency file. Lean 4 and Coq are separate toolchains and
must be installed by the user outside this sandbox:

- Lean 4: install `elan`, `lean`, and `lake` from the official Lean toolchain, then pass an
  existing Lake project with `--project`. Mathlib projects should use the project's pinned
  toolchain and lock file.
- Coq: on Arch Linux, install the official prover and standard library with
  `sudo pacman -Syu rocq rocq-stdlib`. Recent versions provide the `rocq compile` command and may
  also provide the legacy `coqc` compatibility command. On other platforms use the package manager
  or an opam switch. Use `--project` when a `_CoqProject` or project-local load path is required.

Do not install or replace a user's Lean/Coq toolchain without consent; the downloads are large and
project-version mismatches can break existing work. Check availability with:

```bash
python scholarime/scripts/symbolic_derive.py doctor
python scholarime/scripts/symbolic_derive.py verify \
  scholarime/formal/examples/smoke.lean
python scholarime/scripts/symbolic_derive.py verify \
  scholarime/formal/examples/smoke.v --prover coq
```

APA 7 and GB/T 7714-2025 CSL assets are included under
`scholarime/assets/citation-styles/`. Keep their third-party license and source notice when
redistributing the skill.

For literature searches, `SCHOLARIME_CONTACT_EMAIL` improves OpenAlex/Crossref request
identification, and `NCBI_API_KEY` raises PubMed E-utilities limits. Do not put either value in a
committed configuration file.

Citation verification uses the bundled CiteVerify lookup engine and needs no additional Python
package. OpenAlex and Semantic Scholar cross-checks are optional and use `OPENALEX_KEY` and
`S2_API_KEY`; Scholarime's saved Semantic Scholar configuration is also accepted. Keep keys in
protected local files or environment variables and never pass them as shell arguments.

Complex figure templates require NumPy and Matplotlib from the `figures` group. Rendered-PDF
collision auditing additionally requires PyMuPDF from the optional `figure-pdf` group. The other
figure audits and systematic-review screening use lightweight or standard-library code.
