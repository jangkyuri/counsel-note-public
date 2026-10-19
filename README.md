# Counsel Note + HDHcounsel

학생 상담 녹음을 **이 PC에서** 한국어로 전사하고 시간·화자·단어 근거와
검수 기록을 남기는 Windows 중심 앱입니다. 별도 HDHcounsel 검수 화면은 Google 설정,
Codex 로그인, 유료 API가 필요 없습니다. 기존 `run_app.bat`의 ADMIN·Docs 흐름과
Intel GPU용 `GPU 터보 모드` 기본값은 유지합니다.

**원본:** [jangkyuri/counsel-note-public](https://github.com/jangkyuri/counsel-note-public),
commit `47da11d`. 원본 소스와 이력을 보존했습니다. 원본에는 명시적인 라이선스가
없으므로 임의로 MIT 등의 라이선스를 붙이지 않습니다. HDH 기능은
[HDHhhdhh/HDHcounsel](https://github.com/HDHhhdhh/HDHcounsel)의 `3c6c238`에서
가져왔으며 작성자와 이력을 보존했습니다. [원본 조사 기록](docs/UPSTREAM_REVIEW.md)의
비공개 게시·NVIDIA 환경 검증 설명은 해당 원본의 당시 기록입니다.

## 달라진 점

- CPU Whisper base/small/medium 및 OpenVINO turbo 외에 **WhisperX 3.8.6,
  large-v3, 한국어 wav2vec2 정렬, pyannote Community-1** 경로를 추가했습니다.
- NVIDIA CUDA 기본값은 FP16, batch 4입니다. 부족하면 batch 1 또는 CPU INT8로
  설정합니다. 단계마다 모델을 해제하고 작업이 끝나면 별도 프로세스를 종료합니다.
- 다운로드 전용 `prepare`와 오프라인 전사를 분리했습니다. 추론 중 모델 자동
  다운로드와 Python 소켓 연결을 차단하며 pyannote/HF 텔레메트리를 끕니다.
- `hdhcounsel/`은 설정, 도메인, 음성 변환, 추론, 저장, CLI, UI를 분리합니다.
  원문은 보존하고 화자 이름·검수 메모는 별도 원자적 JSON 파일에 저장합니다.
- 기존 `app.py`의 일괄 처리·Codex 요약·Google Docs·ADMIN은 보존했습니다.
  **이 legacy 화면은 외부 서비스와 통신하며 완전 로컬 앱이 아닙니다.**
  새 화면은 원문 검수·수동 상담 메모용이며 생성형 자동 요약을 하지 않습니다.

## Windows 설치

HDH 엔진은 Python **3.12 x64** 기준입니다. 기존 `.venv`를 덮어쓰지 말고
별도 `.venv-hdh`에 설치하세요. 아래 CUDA 설치는 NVIDIA GPU용입니다.
Intel GPU는 CUDA로 사용할 수 없으므로 기존 OpenVINO 모드를 유지하거나,
새 HDH 엔진에서 CPU/int8을 명시적으로 설정해야 합니다. 화자 분리 정확도와
한국어 인명은 사람이 확인해야 합니다.

```powershell
py -3.12 -I -m venv .venv-hdh
.\.venv-hdh\Scripts\python.exe -I -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv-hdh\Scripts\python.exe -I -m pip install -e ".[speech,dev,legacy]"
.\.venv-hdh\Scripts\python.exe -I -m pip check
```

FFmpeg 실행 파일은 설치된 `imageio-ffmpeg`의 번들을 기본 사용합니다. 이미 PATH에
FFmpeg가 있으면 그것을 우선합니다. TorchCodec/pyannote 환경에 따라 FFmpeg
4–7의 **공유 DLL**도 필요할 수 있습니다. CUDA는 PyTorch뿐 아니라 CTranslate2가
사용하는 cuBLAS/cuDNN 9 DLL도 확인해야 합니다. [운영 안내](docs/HDHCOUNSEL.md).

## 모델 준비: 음성 없이 온라인으로 한 번

기본 작업 폴더는 `%USERPROFILE%\.hdhcounsel`입니다. 다른 위치는
`HDHCOUNSEL_HOME` 환경변수로 설정합니다. 앱 코드 폴더에 실제 상담 자료를
넣지 마세요.

1. Hugging Face의 `pyannote/speaker-diarization-community-1` 모델 페이지에서
   조건에 동의합니다. 읽기 전용 토큰을 **로컬 셸의 `HF_TOKEN` 환경변수**에만
   설정합니다. 토큰을 설정 파일·명령 기록·Git에 넣지 않습니다.
2. 모델을 준비합니다. 토큰은 다운로드 접근에만 필요하며 음성을 전송하지 않습니다.

```powershell
.\.venv-hdh\Scripts\python.exe -I -m hdhcounsel.cli prepare
# 화자 분리 없이 먼저 점검하는 경우:
.\.venv-hdh\Scripts\python.exe -I -m hdhcounsel.cli prepare --no-diarization
```

모델 revision은 `models/manifest.json`에 기록합니다. 모델 파일은 Git에 올리지
않습니다. Community-1의 모델 라이선스는 CC-BY-4.0이며 모델 카드의 조건을
따릅니다. WhisperX 코드의 BSD-2-Clause 및 각 모델의 개별 조건도 확인하세요.

## 실행과 검수

```powershell
.\.venv-hdh\Scripts\python.exe -I -m hdhcounsel.cli ui
# 또는 run_hdhcounsel.bat
```

브라우저 주소: `http://127.0.0.1:8517`. 녹음 선택 → 로컬 전사 → 화자 이름
확인 → 상담 메모 작성 → 검수 기록 저장 순서입니다. 전사 원문은 자동 저장하고,
검수 메모는 저장 버튼을 눌러야 보존됩니다. 업로드한 음성 사본과 변환 WAV는
작업 뒤 삭제하며 원본 파일은 수정하지 않습니다.

```powershell
.\.venv-hdh\Scripts\python.exe -I -m hdhcounsel.cli transcribe "C:\recordings\sample.wav"
.\.venv-hdh\Scripts\python.exe -I -m hdhcounsel.cli transcribe "C:\recordings\sample.wav" --no-diarization --cpu
```

설정 예시는 `hdhcounsel.example.toml`입니다. 기본
`%USERPROFILE%\.hdhcounsel\config.toml`에 두거나
`hdhcounsel --config "C:\private\config.toml" ui`처럼 지정합니다.
SPEAKER 번호는 파일별 군집이며 실제 학생/상담자를 자동 식별하지 않습니다.
겹친 발화, 짧은 맞장구, 이름, 숫자는 원음을 대조하세요.

기존 앱에서 `HDH 로컬 화자 전사`를 선택할 때 별도 실행 환경을 사용하려면
로컬 설정의 `worker_python`에 `.venv-hdh\Scripts\python.exe`의 절대 경로를
지정합니다. 모델 준비 전에는 이 모드를 선택하지 마세요. 독립 검수 화면은
모델이 없으면 전사 시작을 비활성화하며 기존 상담 데이터에는 접근하지 않습니다.

## 검증

```powershell
.\.venv-hdh\Scripts\python.exe -I -m pytest
.\.venv-hdh\Scripts\python.exe -I -m ruff check hdhcounsel tests
```

실제 실행 증거와 미검증 항목은 [검증 결과](docs/VERIFICATION.md)를 참조하세요.
기존 운영 연동을 쓰려면 아래의 upstream 안내를 따르되 별도의 권한과 설정이
필요합니다. 기존 `run_app.bat`는 legacy 앱을 실행합니다.

---

# 원본 학원 개별 상담 정리 도구 안내 (legacy)

## 공개 소스 안내

2026-10-19 기준 Counsel Note 앱의 공개 소스입니다. 기존 운영 환경을 다른 Windows PC로 옮기는 담당자를 위한 코드 백업이며, 연동을 처음부터 구성하는 독립 설치 마법사는 아닙니다. 기존 비공개 저장소의 과거 커밋은 포함하지 않으며, 운영 계정과 Apps Script 프로젝트 정보는 제거했습니다.

**다운로드만으로 바로 실행되는 설치 패키지는 아닙니다.** 다른 PC에서는 다음 초기 설정이 필요합니다.

1. Python을 설치한 뒤 프로젝트 폴더에서 `install_first.bat`를 실행합니다. Python이 없으면 `install_python_first.bat`가 Python 3.12 설치를 안내합니다.
2. `install_codex_first.bat`로 Codex CLI를 준비한 뒤 새 PowerShell 창에서 `codex login`으로 로그인합니다. 이 설치 도우미는 필요 시 Node.js LTS 설치도 안내하며, Apps Script 코드 검사에도 Node.js가 필요합니다.
3. 권한 있는 담당자에게 다음 비공개 설정을 받아 로컬에 복원합니다. 이 파일들은 Git에 올리지 않습니다.
   - `data/students/google_docs_map.csv`: 학생과 Docs 연결 정보
   - `data/students/google_apps_script_webapp_url.txt`: 사용하는 Apps Script Web App URL
4. ADMIN 연동을 사용할 경우 해당 PC에서 별도로 로그인 및 연결을 설정합니다. 음성 인식 모델은 PC에 맞게 설치하거나 처음 실행할 때 내려받아야 하며, OpenVINO 선택 시 `requirements-openvino.txt`도 필요합니다.
5. `run_app.bat`로 실행합니다. 학생 파일 없이 먼저 앱 상태를 확인합니다.

위 비공개 설정 두 파일이 없으면 현재 앱은 시작 점검에서 중단됩니다. 운영 계정·Apps Script 프로젝트·기존 배포의 대조 정보도 운영 담당자에게 비공개로 확인하세요. 임의의 계정이나 배포를 대신 사용하지 않습니다.

학생 명단, 상담 녹음, 원문, 정리본, 저장 이력, 계정 정보, 모델 파일 및 설치 파일은 이 저장소에 포함되지 않습니다. 공개 소스를 받는 것만으로 ADMIN 또는 Google Docs에 저장되거나 Apps Script가 배포되지는 않습니다.

기본 실행 진입점은 `run_app.bat`입니다. `experiments/`와 `tools/`의 실험 자료는 기본 실행 경로가 아니며, 각 폴더의 별도 안내를 따릅니다.

현재 저장 완료·재시도·일괄 처리 정책은 [운영 안내](docs/OPERATIONS.md)를, 공개 범위는 [소스 백업 안내](docs/GITHUB_BACKUP.md)를 먼저 확인하세요. 아래에는 초기 버전의 사용 안내도 포함되어 있어 현재 화면 및 동작과 다를 수 있습니다.

## 초기 사용 안내

학생별 상담 녹음 파일에서 한국어 원문을 추출해 파일로 저장하고, `codex exec`로 상담 정리본을 생성해 클립보드에 복사하는 Streamlit 앱입니다.

정리본은 파일이나 DB에 저장하지 않습니다.

학생명은 녹음에서 추출하지 않고 화면 입력칸의 값을 기준으로 사용합니다.

## 설치 방법

Python 3.10 이상을 권장합니다.

처음 설치하는 조교 PC에서는 프로젝트 폴더에서 아래 파일을 한 번 실행합니다.

```text
install_first.bat
```

이 파일은 `.venv` 가상환경을 만들고 `requirements.txt`의 패키지를 설치합니다.

수동 설치가 필요하면 아래 명령을 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Codex CLI가 npm 전역 설치 경로의 `codex.cmd`로 실행 가능해야 합니다. Windows에서는 `%APPDATA%\npm\codex.cmd` 경로를 확인합니다.

## 백업 정리 원칙

수정 전 백업은 바탕화면의 `counsel-note_BACKUPS` 폴더에 `counsel-note_BACKUP_YYYYMMDD_HHMMSS` 형식으로 보관합니다.

백업은 최신 5개만 남기고, 오래된 백업은 영구 삭제하지 않고 휴지통으로 이동합니다. 이 정리는 `scripts/backup_project.py`에서 `send2trash`를 사용합니다.

## 실행 방법

평소에는 프로젝트 폴더에서 아래 파일을 실행합니다.

```text
run_app.bat
```

`.venv`가 없으면 `run_app.bat`가 `install_first.bat`를 먼저 실행하라고 안내합니다.

수동 실행이 필요하면 아래 명령을 사용합니다.

```powershell
streamlit run app.py --server.address localhost --server.port 8501
```

기본 실행 배치 파일도 `localhost` 전용으로 실행되도록 설정되어 있어 같은 PC에서만 접속합니다.

브라우저가 열리면 화면에서 학생명, 상담일, 녹음 파일을 입력합니다.

앱 시작 시 필요한 폴더는 자동으로 생성됩니다. `prompts/summary_ko.txt` 같은 필수 파일이 없으면 화면 상단에 경고가 표시되고, 정리본 생성 버튼은 실행되지 않습니다.

## 사용 방법

화면은 세 영역으로 구성됩니다.

- 왼쪽: 학생명, 작성자, 상담일, 처리 모드, 정리본 스타일
- 가운데: 녹음 파일 업로드, 실행 버튼
- 오른쪽: 처리 상태, 오늘 저장된 원문 목록

기본 사용 순서:

1. 왼쪽에서 학생명과 상담일을 입력합니다.
   - 학생명은 음성 인식 결과와 관계없이 입력값이 정답으로 사용됩니다.
2. 처리 모드를 선택합니다.
   - 빠른 모드: 빠르지만 일부 오인식 가능
   - 균형 모드: 기본 추천
   - 정확 모드: 상담 기록용 추천
   - 최고 정확 모드: 가장 정확하지만 느림
3. 정리본 스타일을 선택합니다.
   - 기본 상담 정리
   - 내부 기록용
   - 다음 상담 체크용
4. 가운데에서 `mp3`, `m4a`, `wav` 파일을 업로드합니다.
   - 녹음 파일명을 학생 이름으로 저장하면 학생명 칸에 자동 반영됩니다. 예: `장헌태.m4a` → `장헌태`
   - 파일명 끝의 `_숫자` suffix는 자동 제거됩니다. 예: `장헌태_6.m4a` → `장헌태`
   - 정리본 학생명과 원문 파일명은 자동 반영된 학생명을 기준으로 생성됩니다.
   - `최근 녹음 불러오기`와 `오늘 녹음 파일` 선택은 `개발자 진단 정보 표시`를 켰을 때 보이는 `보조 기능` 안에 있습니다.
5. `정리본 생성·복사` 버튼을 누릅니다.
   - 파일 업로드만으로는 자동 실행되지 않습니다.
6. 앱이 원문 추출, 원문 저장, 정리본 생성, 클립보드 복사를 한 번에 실행합니다.
7. 문서나 카톡에 `Ctrl+V`로 붙여넣습니다.
8. 클립보드 복사가 실패하면 화면의 textarea에서 직접 복사합니다.

처리 중에는 학생명, 상담일, 녹음 파일, 처리 모드, 정리본 스타일 등 입력값이 잠깁니다.

`Enter로 실행` 입력칸은 제거되었습니다. 일반 사용자는 기본 화면에서 `정리본 생성·복사`만 사용하면 됩니다.

학원 용어집은 정리본에 항상 반영됩니다. 고급 음성 인식 설정은 기본 화면에서 숨겨져 있으며, 사이드바의 `개발자 진단 정보 표시`를 켜면 확인하거나 변경할 수 있습니다. 기본값은 VAD OFF, 음성 인식 힌트 OFF입니다.

정리본 최상단에는 `[오늘 바로 확인할 것]`이 표시됩니다. `숙제`와 `요청사항`은 `[완료]`, `[부분 완료]`, `[미완료]`, `[진행 중]` 같은 상태 태그로 표시하고, `판정:`/`내용:` 라벨은 사용하지 않습니다. 진행 여부가 불명확한 내용은 `[확인 안 됨]` 태그 없이 짧게 적습니다. 관련 내용이 없으면 `없음`이나 `-` 같은 자리 채우기 문구를 쓰지 않고 비워 둡니다. `특이사항`과 `다음 수업 체크`는 짧은 bullet로 표시합니다. 별도의 학부모 전달 문구나 문자 문구는 생성하지 않습니다.

## 학생별 Google Docs 문서

`data/students/google_docs_map.csv`에 학생명과 Google Docs URL을 저장하면 앱에서 자동으로 읽고 현재 학생명 기준으로 문서를 찾습니다.

CSV 열은 `student_name`, `doc_url`, `doc_id`입니다. 학생명이 정확히 일치하면 기본 화면에는 `Google Docs: 등록됨`으로 표시됩니다.

매칭되는 문서가 있으면 기본 화면의 학생명 입력칸 아래에 `학생 Docs 열기` 버튼이 바로 표시됩니다. 별도의 매핑 불러오기 버튼을 누를 필요가 없습니다.

자세한 `doc_id`, `doc_url`, CSV 마지막 수정 시간, 매핑 다시 읽기 버튼은 사이드바의 `개발자 진단 정보 표시`를 켰을 때만 표시됩니다.

현재 기능은 학생별 문서 링크 표시와 열기만 지원합니다. Google Docs에 자동으로 쓰는 기능은 별도 기능이며, 이 매핑 표시 기능만으로는 문서 내용이 수정되지 않습니다.

### Google Docs에 정리본 추가

`개발자 진단 정보 표시`를 켠 뒤 `Google Docs 설정`에 Google Apps Script Web App URL을 저장할 수 있습니다.

저장 위치:

```text
data/students/google_apps_script_webapp_url.txt
```

정리본 생성 후 `정리본 확인 및 수정` 영역에서 `수정본을 Google Docs에 추가` 버튼을 누르면 현재 textarea의 수정본을 해당 학생 Google Docs 문서에 추가합니다.

`오늘 확인할 것만 복사` 버튼을 누르면 현재 textarea에서 `[오늘 바로 확인할 것]` 섹션만 클립보드에 복사합니다.

학생별 문서는 `data/students/google_docs_map.csv`의 `student_name`, `doc_url`, `doc_id` 매핑에서 찾습니다.

Google Docs 추가는 사용자가 버튼을 눌렀을 때만 실행됩니다. admin 페이지에는 접근하지 않고, admin 페이지 버튼 클릭이나 POST/PUT/DELETE 요청도 하지 않습니다.

같은 화면에서 같은 학생, 같은 상담일, 같은 수정본을 중복 추가하는 것은 막습니다. 정리본은 로컬 txt 파일이나 DB에 저장하지 않습니다.

Apps Script 복구용 코드는 `scripts/google_apps_script_doPost.js`에 보관합니다. 이 파일은 로컬 앱에서 실행되지 않으며, Apps Script 프로젝트가 삭제되거나 꼬였을 때 복사해서 다시 배포하기 위한 백업본입니다.

## 학원 용어집

`data/glossary/academy_terms.csv`에 학원에서 쓰는 교재명, 커리큘럼명, 시험명을 등록할 수 있습니다.

앱 화면에서 용어집 상태를 확인하거나 폴더를 열려면 사이드바의 `개발자 진단 정보 표시`를 켭니다. 용어집 반영은 항상 켜져 있으며 끄는 옵션은 제공하지 않습니다.

CSV 형식은 `category,term,aliases,note`입니다. `aliases`는 세미콜론 `;`으로 구분합니다.

예: `쎈`의 aliases에 `센;쌘;ssen`을 적어두면, 정리본 생성 시 원문에 비슷한 표현이 실제로 나온 경우 공식 term인 `쎈`으로 보정될 수 있습니다.

용어집은 정리본 표기 보정에만 사용됩니다. 원문 txt 파일은 자동 치환하지 않고, 원문에 없는 용어를 추측해서 넣지 않습니다.

원문이 비정상적으로 짧거나 내용이 빠진 것 같으면 `무음 구간 자동 제거 사용`을 끄고 `정확 모드` 또는 `최고 정확 모드`로 다시 추출해보세요. 기본값은 작은 목소리나 짧은 발화를 보존하기 위해 VAD를 끈 상태입니다.

## 음성 인식 힌트와 반복 원문

기본 설정에서는 faster-whisper의 `initial_prompt`를 사용하지 않습니다. 필요한 경우에만 `음성 인식 힌트 사용`을 켜면 짧은 힌트인 `한국어 학원 상담 녹음입니다.`가 전달됩니다.

원문에 `교재명, 단어 암기, 테스트, 수업 태도, 복습, 오답` 같은 힌트성 문구가 반복되면 initial_prompt 환각일 수 있습니다. 이 경우 `음성 인식 힌트 사용`을 끄고, `정확 모드` 이상으로 바꾼 뒤 VAD도 끈 상태에서 다시 시도하세요.

앱은 반복되거나 환각으로 의심되는 원문을 자동 감지하면 원문 저장, 정리본 생성, 클립보드 복사를 중단합니다. 이 경우 기존 원문 파일은 유지됩니다.

## 작업 복원

브라우저를 새로고침하거나 앱이 rerun되어도 입력하던 작업이 자동으로 복원됩니다.

임시 작업 상태는 아래 파일에 저장됩니다.

```text
data/drafts/current_draft.json
```

저장되는 내용은 학생명, 작성자, 상담일, 처리 모드, 정리본 스타일, 원문 텍스트, 원문 파일 경로, 업로드한 녹음 파일 복사본 경로, 마지막 진행 단계입니다.

업로드한 녹음 파일은 새로고침 뒤에도 다시 사용할 수 있도록 아래 폴더에 복사됩니다.

```text
data/uploads
```

정리본 본문은 임시 작업 파일, 파일, DB 어디에도 저장하지 않습니다.

작업을 지우려면 화면 상단의 `현재 작업 초기화` 버튼을 누릅니다. 이 버튼은 `data/drafts/current_draft.json`을 삭제하고 현재 draft에 연결된 업로드 복사본을 가능하면 함께 삭제합니다.

## 최근 녹음 불러오기

`최근 녹음 불러오기` 버튼은 아래 후보 폴더에서 가장 최근 수정된 `m4a`, `mp3`, `wav` 파일을 찾습니다.

- `~/Documents/Sound recordings`
- `~/OneDrive/Documents/Sound recordings`
- `~/Documents/소리 녹음`
- `~/OneDrive/Documents/소리 녹음`
- `~/문서/Sound recordings`
- `~/OneDrive/문서/Sound recordings`
- `~/문서/소리 녹음`
- `~/OneDrive/문서/소리 녹음`
- `~/Music/Sound recordings`
- `~/OneDrive/Music/Sound recordings`
- `~/Music/소리 녹음`
- `~/OneDrive/Music/소리 녹음`

찾은 파일명과 수정 시간이 화면에 표시됩니다. 업로드 파일이 있으면 업로드 파일을 우선 처리합니다.

Windows 파일 업로드 창의 시작 폴더는 브라우저/Windows가 관리하므로 앱에서 강제로 지정할 수 없습니다. `개발자 진단 정보 표시`를 켜면 `보조 기능`의 `오늘 녹음 파일` 목록에서 오늘 녹음된 파일을 바로 선택할 수 있습니다.

## 오래된 녹음 파일 자동 정리

앱은 실행 세션마다 한 번, 24시간이 지난 녹음 파일을 휴지통으로 이동합니다.

정리 대상은 아래로 제한됩니다.

- `data/uploads` 안의 앱 업로드 복사본
- Windows 녹음기 `Sound recordings` 또는 `소리 녹음` 후보 폴더 안의 음성 파일

정리 대상 확장자는 `m4a`, `mp3`, `wav`, `webm`, `aac`, `flac`입니다.

현재 선택된 녹음 파일이나 처리 중인 파일은 정리하지 않습니다. `data/transcripts`의 원문 txt 파일, 용어집, 학생별 Google Docs 매핑 파일은 정리 대상이 아닙니다.

파일은 영구 삭제하지 않고 `send2trash`를 사용해 휴지통으로 이동합니다. 휴지통 이동에 실패하면 해당 파일은 그대로 둡니다.

## codex exec 실행 옵션

앱은 정리본 생성 시 별도의 OpenAI API 호출 코드를 사용하지 않고 아래 옵션으로 `codex exec`를 subprocess 호출합니다.

```powershell
Get-Content -Raw -Encoding UTF8 data/tmp/codex_input.txt |
& "$env:APPDATA\npm\codex.cmd" exec --ephemeral --skip-git-repo-check "아래 표준입력의 상담 정보를 바탕으로 상담 정리본만 작성해."
```

상담 정리 프롬프트는 `prompts/summary_ko.txt`에서 수정할 수 있습니다.

## Codex CLI를 찾을 수 없을 때

앱 안의 `Codex CLI 상태 확인`을 열고 `Codex CLI 진단 실행`을 눌러 현재 실행 경로와 버전을 확인합니다.

## 앱 상태 점검

앱 실행 후 사이드바의 `개발자 진단 정보 표시`를 켠 뒤 `앱 상태 점검`을 열고 `상태 점검 실행`을 누르면 Python, Codex CLI, ffmpeg, 필수 폴더, Python 패키지 상태를 확인할 수 있습니다.

상태 점검은 읽기 전용이며 원문이나 정리본을 변경하지 않습니다. Codex 사용량을 소모하는 `codex exec`는 실행하지 않고, `codex.cmd --version`만 확인합니다.

## ffmpeg 관련 안내

`m4a` 또는 `mp3` 파일의 원문 추출이 실패하면 ffmpeg 설치가 필요할 수 있습니다. ffmpeg를 설치한 뒤에는 PowerShell과 앱을 다시 시작하세요. `wav` 파일은 상대적으로 문제가 적을 수 있습니다.

PowerShell에서는 아래 명령으로 설치 상태를 확인할 수 있습니다.

```powershell
where.exe codex.cmd
codex.cmd --version
npm.cmd prefix -g
```

`codex.cmd`가 없거나 버전 확인이 실패하면 아래 명령으로 다시 설치합니다.

```powershell
npm.cmd i -g @openai/codex
```

설치 후 앱을 다시 시작합니다. `run_app.bat`와 `start_app.bat`에는 `%APPDATA%\npm`과 `C:\Program Files\nodejs`가 PATH 앞쪽에 추가되어 있습니다.

WindowsApps의 `codex.EXE`가 먼저 잡히면 권한 오류가 날 수 있습니다. `%APPDATA%\npm\codex.cmd` 경로를 확인합니다. 정리본 생성은 PowerShell에서 성공한 방식과 같이 `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ...`로 실행하며, 상담 원문과 프롬프트는 `data/tmp/codex_input.txt` 임시 파일을 통해 표준입력으로 전달합니다. 정리본 생성 후 임시 입력 파일은 삭제됩니다.

## 저장 정책

- 저장함: 한국어 상담 원문 텍스트
- 기본 저장 위치: `data/transcripts/{상담일}/{학생명}_{상담일}_{HHMMSS}_{작성자}.txt`
- 저장 예: `data/transcripts/2026-05-06/김태준_2026-05-06_153245_오세빈.txt`
- 같은 날짜, 같은 학생이라도 저장 시각과 작성자를 파일명에 넣어 새 파일로 저장합니다.
- 동일 파일명이 이미 있으면 `_2`, `_3`처럼 번호를 붙여 덮어쓰기를 방지합니다.
- 원문 파일 생성은 덮어쓰기 방지 방식으로 처리합니다.
- `정리본 생성·복사` 버튼을 누르면 기존 원문 파일을 삭제하지 않고, 선택된 녹음 파일에서 원문을 새로 추출해 새 파일로 저장합니다.
- 같은 녹음 파일을 다시 넣어도 기존 원문을 재사용하지 않고 새로 추출합니다.
- 원문 파일에는 `[상담 정보]` 메타데이터와 `[음성 인식 원문]`이 함께 저장됩니다.
- 상담 정보의 학생명과 상담일은 화면 입력값을 기준으로 저장됩니다.
- 오른쪽 `오늘 저장된 원문` 목록에는 실제 오늘 날짜 폴더의 원문 파일만 표시됩니다.
- 날짜가 바뀌면 이전 날짜 원문은 화면 목록에서 사라지고, 새 날짜에 저장한 원문만 표시됩니다.
- 예전 방식의 `data/transcripts/{상담일}/{학생명}_{상담일}.txt`, `data/transcripts/{상담일}/{학생명}.txt`, `data/transcripts/{상담일}_{학생명}.txt` 파일도 목록에서 인식합니다.
- 업로드한 녹음 파일 복사본은 `data/uploads`에 보관되며 자동 삭제하지 않습니다.
- 원문 추출 실패 시 새 원문 파일을 만들지 않습니다.
- 정리본 생성 실패 시 새로 추출해 저장한 원문 파일은 유지됩니다.
- 저장하지 않음: 상담 정리본

오른쪽의 `원문 폴더 열기` 버튼을 누르면 선택한 상담일의 원문 폴더가 열립니다.

## 참고

처음 사용하는 faster-whisper 모델은 실행 중 모델 파일을 내려받을 수 있어 시간이 걸릴 수 있습니다. 이후에는 `st.cache_resource`로 같은 모델을 반복 로딩하지 않아 더 빠르게 처리됩니다.
Windows CPU 환경에서는 `compute_type="int8"`을 사용합니다.
