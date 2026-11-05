# 사진 정리함 — 웹 실행 안내

React + Vite로 한국어 화면을 만들고, FastAPI가 React 빌드 결과와 API를 함께 제공합니다. 모델 학습은 수행하지 않습니다.

## 적용 전 확인

- 저장소: https://github.com/sukwon2000k/photo-auto-sorter
- 확인 기준 커밋: `dfbcf3eacdeee928d35bf9a34107eb929f1190ff`
- `app/services/predictor.py`는 실제 코드를 읽고 **변경 없이** 포함했습니다.
- `sorter.py`의 분류·이름 충돌 처리·원본 복사·CSV·ZIP 생성을 재사용했습니다. 웹용 선택 인자 `initial_failures`, `source_names`, `allow_empty`, `on_progress`를 추가했습니다. 기존 CLI 기본 동작을 유지합니다.
- 사용자 Windows 파일과 GitHub 저장소는 수정하지 않았습니다. 이 ZIP은 적용할 소스 묶음입니다.
- 기존 프로젝트에 합치기 전 `git status`와 `git diff`로 로컬 변경을 확인하세요. 다른 채팅에서 predictor나 sorter를 변경했다면 무조건 덮어쓰지 말고 `web_changes.patch`와 비교해 변경 부분만 반영하세요. predictor는 변경이 없으므로 최신 로컬 파일을 유지해도 됩니다.
- 기존 학습 노트북·데이터 준비 코드·데이터셋은 수정하지 않았으며 묶음에 포함하지 않았습니다.

## 파일 구조와 역할

```text
photo-auto-sorter/
├── frontend/
│   ├── src/
│   │   ├── App.jsx             # React 상태·파일 선택·작업 조회
│   │   ├── AppShell.jsx        # 기능 우선 홈·사용법·진행·결과 선택 화면
│   │   ├── navigation.js       # 화면 URL과 브라우저 뒤로 가기
│   │   ├── api.js              # local / cloud 업로드·작업 API 어댑터
│   │   ├── files.js            # 파일 선택 제한과 요약
│   │   ├── main.jsx            # React 진입점
│   │   └── styles.css          # 한국어 반응형 디자인
│   ├── tests/                  # React 및 API 어댑터 검증
│   ├── package.json
│   ├── package-lock.json       # 실제 설치한 버전 고정
│   ├── vite.config.js          # FastAPI 개발 프록시
│   ├── .env.example            # 프론트 설정 참고
│   └── dist/                   # 빌드 결과; 제공 ZIP에 포함
├── app/
│   ├── main.py                 # /assets와 React 화면, 모델·작업·다운로드
│   ├── config.py               # 모델 경로와 서버 제한값
│   └── services/
│       ├── predictor.py        # 기존 모델 로더·전처리; 변경 없음
│       ├── sorter.py           # 기존 원본 복사·CSV·ZIP
│       ├── uploads.py          # 스트리밍 업로드 수신
│       └── web_jobs.py         # ZIP·사진 검증, sorter 연결
├── tests/                      # 백엔드 및 대량 ZIP 검증
├── requirements-web.txt
├── requirements-test.txt
├── .env.example
├── .gitignore
├── AWS_PLAN.md                 # AWS 사용량 기반 배포안과 미구현 범위
├── WEB_README.md
├── VALIDATION.md
└── web_changes.patch
```

실행 중 `.work/`가 자동 생성됩니다. 기존 학습 프로젝트 파일은 그대로 두세요.

## Windows PowerShell 설치·실행

VS Code에서 기존 CUDA용 PyTorch가 설치된 인터프리터를 선택하고 새 터미널을 여세요. 프로젝트 루트에서 실행합니다. React 개발·재빌드에는 Node.js 22.12 이상 또는 24 LTS가 필요합니다. 제공 ZIP에는 React 빌드 결과가 포함되어 있어 화면 수정 없이 실행할 때는 npm 명령을 생략할 수 있습니다.

```powershell
# 기존 torch/torchvision을 교체하지 않고 웹 패키지만 추가합니다.
python -m pip install -r requirements-web.txt

# React 소스를 수정했다면 화면을 재빌드합니다. 제공된 빌드를 쓰면 이 단계는 생략합니다.
Push-Location frontend
npm ci
npm run build
Pop-Location

# 생략해도 이 상대 경로가 기본값입니다.
$env:MODEL_PATH = "models/head_20261103_230028_536214/best_head.pth"

# GPU 메모리와 작업 상태를 하나의 프로세스에서 관리합니다.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

브라우저에서 http://127.0.0.1:8000 을 여세요. 종료는 `Ctrl+C`입니다.

- Python 3.14와 기존 torch 2.11.0+cu130 / torchvision 0.26.0+cu130 설치를 유지하세요.
- 모델이 없으면 torch를 가져오지 않으므로 웹 패키지만으로 화면을 실행할 수 있습니다.
- 모델 준비 실패의 상세 원인은 서버 로그에 기록하고 화면에는 일반적인 상태만 표시합니다.
- `--reload`는 모델 재로딩·결과 링크 폐기를 유발하므로 일반 실행에는 사용하지 마세요.
- 선택한 파일 목록은 새로 고치면 사라집니다. 접수한 작업 ID는 같은 탭의 sessionStorage에 저장해 상태·결과를 다시 조회합니다. 원본 사진은 브라우저 저장소에 저장하지 않습니다. 서버가 재시작되거나 결과가 만료되면 복원이 실패합니다.

## React 개발 모드

두 터미널을 사용합니다. 터미널 1에서는 프로젝트 루트에서 위의 FastAPI 실행 명령을 사용합니다.
터미널 2에서는 아래를 실행합니다.

```powershell
# frontend 폴더에서 React 개발 서버를 실행합니다.
cd frontend
npm ci
npm run dev
```

Vite가 출력한 로컬 주소를 여세요. 개발 중 /api는 FastAPI 127.0.0.1:8000으로 프록시합니다.
코드 수정 사항을 반영한 최종 화면은 `npm run build`로 만듭니다. 빌드 파일명은 해시가 붙어 바뀌므로 dist 폴더 전체를 함께 배포합니다.
기존 `app/static/`의 HTML·JS는 더 이상 사용하지 않습니다. 로컬에 옛 파일이 남아 있어도 새 서버는 그 경로를 제공하지 않습니다.

## 모델 배치·교체

1. 최종 모델을 `models/` 아래에 별도로 배치합니다. 일반 사용자의 웹 모델 업로드 기능은 없습니다.
2. 기존 경로를 사용하거나 아래와 같이 새 경로를 환경변수에 설정합니다.
3. 서버를 완전히 종료한 뒤 다시 실행합니다. 모델은 시작 때 한 번만 불러옵니다.
4. http://127.0.0.1:8000/api/status 에서 `model_ready: true`를 확인합니다.
5. 실제 사진으로 ZIP 결과와 분류 내용을 확인합니다.

```powershell
# 상대 경로는 터미널 위치가 아니라 프로젝트 루트를 기준으로 해석합니다.
$env:MODEL_PATH = "models/final/best_head.pth"
$env:MODEL_DEVICE = "auto"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Windows 절대 경로도 환경변수 값으로 사용할 수 있지만 소스에는 하드코딩하지 않습니다.
`.env.example`은 참고용입니다. 이름을 `.env`로 바꾸기만 해서는 적용되지 않습니다.

### 체크포인트 연결 계약

| 항목 | 필요한 값·조건 |
|---|---|
| architecture | `efficientnet_v2_l` |
| pretrained_weights | `IMAGENET1K_V1` |
| model_state_dict | 3개 출력 분류기를 포함한 모델 전체 가중치 |
| class_to_idx | `{"food": 0, "landscape": 1, "person": 2}` |
| image_size | 480 |
| mean / std | 각각 `[0.5, 0.5, 0.5]` |
| epoch | 기존 predictor가 읽는 정수 변환 가능한 값 |

기존 predictor가 torchvision 가중치의 평가 전처리를 만들고 크기·정규화 정보의 일치를 검사합니다. 웹에서 전처리·점수 임계값을 추가하지 않습니다. 가장 높은 점수의 클래스 하나를 그대로 사용합니다.

아키텍처·분류기 구조가 바뀌면 predictor의 모델 생성·가중치 로딩을, 클래스가 바뀌면 매핑 검증을 수정해야 합니다. 입력 크기·정규화·평가 전처리가 바뀌면 predictor의 전처리 구성·검증을 함께 맞춰야 합니다. 키가 바뀌면 해당 키를 읽는 부분도 수정해야 합니다. 형식이 다르면 로드를 실패 처리하며 호환성을 가정하지 않습니다.

## 서버 설정과 파일 정책

| 환경변수 | 기본값 | 역할 |
|---|---:|---|
| MAX_FILES | 5000 | ZIP 내부 포함 사진 수 상한; 업로드 입력 파일 수도 이 값 이하 |
| MAX_FILE_MB | 20 | 개별 크기 상한; 1 MB = 1,048,576바이트 |
| MAX_REQUEST_MB | 2048 | multipart 부가 데이터 포함 전체 요청 상한 |
| MAX_ARCHIVE_MB | 2048 | ZIP 파일 하나의 용량 상한 |
| MAX_EXPANDED_MB | 4096 | ZIP 해제 사진과 개별 사진의 실제 바이트 합계 상한 |
| MAX_ARCHIVE_ENTRIES | 20000 | 모든 ZIP의 폴더·파일 항목 합계 상한 |
| MAX_IMAGE_PIXELS | 40000000 | 이미지 픽셀 수 상한; Pillow 자체 보호도 유지 |
| RETENTION_SECONDS | 3600 | 완료부터 결과 ZIP·작업 상태 보관 기간 |
| CLEANUP_INTERVAL_SECONDS | 60 | 만료 작업 삭제 주기 |
| UPLOAD_TIMEOUT_SECONDS | 1800 | 요청 수신·파싱·저장 시간 제한 |
| WORK_DIR | .work | 전용 임시 폴더; 상대 경로는 프로젝트 기준 |
| MODEL_DEVICE | auto | auto, cpu, cuda |

예: `$env:MAX_FILES = "10000"`, `$env:RETENTION_SECONDS = "1800"` 설정 후 서버를 재시작하세요. 제한값은 화면에도 표시됩니다.

- ZIP 또는 개별 JPG/JPEG/JFIF, PNG, WEBP를 함께 올릴 수 있습니다. ZIP 내부 하위 폴더도 처리합니다. ZIP 내부 이미지는 브라우저에서 미리 풀지 않으므로 내부 장수와 실패 파일은 서버 처리 후 확인합니다. RAR·7z·암호 ZIP·분할 ZIP·중첩 ZIP·ZIP64 입력은 지원하지 않습니다. 일반 ZIP으로 나누어 올려 주세요.
- 사진을 60개씩 나누어 미리 보고 페이지를 바꾸면 이전 썸네일 URL을 해제합니다. ZIP은 ZIP 표시와 이름·용량만 보여줍니다.
- JPG/JPEG/JFIF, PNG, WEBP를 지원합니다. 확장자와 실제 형식을 검사하고 전체 디코딩을 수행합니다. 실제 형식이 이 세 종류이면 허용하며 확장자와 형식의 개별 대응을 강제하지는 않습니다.
- 개별 업로드 파일 수·사진/ZIP 개별 크기·전체 요청 상한을 넘으면 요청 전체를 거부합니다. ZIP 내부 사진 수·압축 해제 총량·항목 수 상한 초과는 접수 후 작업 실패로 이유를 안내합니다. ZIP 안의 개별 사진 용량 초과·손상·미지원 파일은 실패 보고서에 기록하고 정상 사진은 계속 처리합니다.
- 업로드 최대 파일 수 위반은 400, 용량 초과는 413입니다. 거부 후 작업 슬롯을 반환합니다.
- 전체 요청은 전송 부가 데이터도 포함하므로 사진 합계가 상한에 아주 가까우면 서버가 거부할 수 있습니다. Content-Length 없는 전송도 실제 바이트 수를 제한합니다.
- 사용자 경로를 그대로 사용하지 않습니다. 고유 하위 폴더와 정제된 마지막 파일명을 사용합니다. Windows 예약 이름·제어 문자·수식처럼 시작하는 이름도 정제합니다.
- 중복 이름은 대소문자를 무시해 `_2`, `_3` 등을 붙입니다. 정제에 따라 원래 파일명이 일부 달라질 수 있습니다.
- 사진을 재인코딩하지 않습니다. ZIP에는 `food/`, `landscape/`, `person/`, `classification_report.csv`만 포함합니다.
- 모든 사진이 손상돼도 빈 세 폴더와 실패 보고서를 제공합니다. GPU 실행·디스크 저장 등 작업 전체 오류는 실패 상태로 종료합니다.
- 요청 본문을 통째로 보관하거나 모든 multipart 파일을 메모리에 쌓지 않습니다. 파일별로 디스크에 수신하고 즉시 핸들을 닫습니다. ZIP도 한 항목씩 용량을 제한해 풉니다. ZIP의 경로를 그대로 추출하지 않으며 경로 탈출·심볼릭 링크를 제외합니다. ZIP 파일 목록 정보도 최대 32MiB로 제한합니다.
- 업로드 ZIP은 해제 후 삭제하고, 사진 원본과 분류 중간 사본은 처리 후 삭제해 결과 ZIP만 보관합니다. ZIP의 이미지가 아닌 파일은 실패로 기록하며, macOS의 __MACOSX 및 .DS_Store 메타데이터는 조용히 제외합니다.
- 만료 시 링크를 즉시 차단하고 다음 정리 주기(기본 최대 약 60초 뒤)에 파일을 지웁니다. 삭제 실패는 기록하고 재시도합니다.
- 만료 전에 시작한 다운로드는 전송 종료·오류까지 보호하고 이후 주기에 정리합니다.
- 재시작하면 이전 링크를 폐기하고 시작 시 잔여 `job_*` 폴더를 삭제합니다. 다른 자료가 없는 전용 WORK_DIR을 사용하세요.
- 서버가 중단된 동안에는 자동 정리도 멈춥니다. 다음 시작 시 잔여 파일을 지웁니다. 중단 중에도 정시 삭제가 필요하면 배포 서버에 별도 정리 작업이 필요합니다.
- 동일 WORK_DIR의 중복 서버는 OS 잠금으로 시작을 거부합니다. 다른 WORK_DIR로 서버를 여러 개 켜면 단일 GPU 제한을 우회하므로 그렇게 실행하지 마세요.

## API

| 메서드·경로 | 동작 |
|---|---|
| GET / | 웹 화면 |
| GET /api/status | 모델 준비, 사용 중 상태, 서버 제한값 |
| POST /api/jobs | multipart `files` 여러 개 → 202와 작업 ID |
| GET /api/jobs/{id} | processing / completed / failed와 개수 |
| GET /api/jobs/{id}/download | 유효한 완료 ZIP 다운로드 |

모델 미준비는 503, 다른 작업 접수·처리 중 또는 아직 다운로드 불가는 409입니다. 만료는 410, 정리 후나 서버 재시작 뒤에는 404입니다. 임의 분류·가짜 진행률은 제공하지 않습니다. 진행 API의 progress는 실제 처리 완료 수와 전체 수를 전달합니다.

작업 ID는 무작위 토큰입니다. 로그인 없는 이 구현에서는 링크를 아는 사람이 결과를 받을 수 있으므로 공유하지 마세요.

## 자동 검증

백엔드 화면 검사에는 React 빌드가 필요하므로 먼저 frontend에서 `npm ci`와 `npm run build`를 실행하세요.

```powershell
# 검증용 패키지도 torch/torchvision을 교체하지 않습니다.
python -m pip install -r requirements-test.txt
python -m pytest -q
```

React 검증은 frontend에서 다음과 같이 실행합니다.

```powershell
npm test
```

React는 jsdom 기반 DOM 테스트이며 실제 모델·AWS를 호출하지 않습니다. 테스트 안에서만 주입한 예측기로 웹·파일 흐름을 검사합니다. 운영 앱에 모의 분류 모드는 없습니다. 실제 검증 범위는 `VALIDATION.md`를 참고하세요.

### 사용자 PC에서 남은 확인

1. 모델 없이 실행 → 화면·썸네일·제거·초기화, 분류 버튼 비활성화 확인.
2. 모델 배치·설정·재시작 → 준비 여부 확인.
3. 정상 JPG/PNG/WEBP, 손상 사진, 같은 이름 사진과 하위 폴더가 담긴 ZIP을 함께 선택.
4. 업로드와 분류 상태 구분, 완료 개수, ZIP의 세 폴더·CSV 확인.
5. `Get-FileHash`로 원본과 추출 사진의 SHA256을 비교.
6. 다른 브라우저에서 동시 요청 → 한 작업만 접수하는지 확인.
7. 짧은 보관 시간으로 링크 만료·자동 삭제 확인. 만료 직전 시작한 큰 ZIP 다운로드 유지도 확인.
8. 모바일 브라우저에서 사진 선택·제거·다운로드 확인.

## 배포 공통 조건

- 실제 AWS 사용량 기반 배포 방향과 미구현 항목은 AWS_PLAN.md를 기준으로 봅니다.
- GPU 워커에 PyTorch와 체크포인트가 필요하며, CUDA·드라이버·Python 호환성은 실제 서버에서 확인해야 합니다.
- 사진·결과·가중치·비밀값을 공개 정적 경로나 Git에 넣지 않습니다.
- 로컬 FastAPI 작업 상태는 메모리 전용입니다. 이 서버를 그대로 여러 워커나 Lambda로 복제하지 않습니다.
- 이미 Git에서 추적 중인 파일은 .gitignore만으로 제거되지 않습니다. 기존 추적 상태는 별도 확인하세요.

## 몇천 장을 분류할 때

100장은 이전 기본 설정일 뿐 모델의 장수 제한이 아닙니다. 현재 기본값은 ZIP 내부를 포함해 5,000장입니다. 10,000장으로 바꾸려면 PowerShell에서 다음과 같이 설정하고 서버를 재시작하세요.

```powershell
# 장수와 용량 상한은 서로 독립적입니다. 실제 사진 용량에 맞춰 함께 조정합니다.
$env:MAX_FILES = "10000"
$env:MAX_ARCHIVE_ENTRIES = "40000"
$env:MAX_REQUEST_MB = "4096"
$env:MAX_ARCHIVE_MB = "4096"
$env:MAX_EXPANDED_MB = "8192"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

기본 전체 업로드는 2GiB이므로 예를 들어 사진 5,000장이 평균 3MiB라면 장수 제한 안이어도 용량을 넘습니다. ZIP은 사진 자체를 크게 줄여 주지 않을 수 있으므로 여러 작업으로 나누거나 디스크 여유에 맞춰 용량 상한을 높이세요. 압축 해제 8GiB로 늘리면 중간 파일과 결과용 공간도 그에 맞게 늘어납니다.

이미지는 한 장씩 검증·추론하고 전체 작업은 하나만 실행합니다. 장수를 늘려도 전부 GPU에 한꺼번에 올리지는 않습니다. 대신 처리 시간은 늘어납니다. 실제 EfficientNetV2-L의 몇천 장 소요 시간은 이 환경에서 측정하지 않았습니다. 대량 처리 중에는 컴퓨터·서버를 계속 켜두고 화면도 유지하세요.

## AWS 사용량 기반 배포를 위한 준비 상태

현재 실행 기본값은 local입니다. React API 계층에 추후 AWS의 S3 직접 업로드용 cloud 어댑터를 추가했습니다.
실제 AWS 백엔드와 자원은 아직 없으며 `VITE_API_MODE=cloud`를 켜도 자동 배포되지 않습니다.

향후 AWS API를 구현한 뒤에만 frontend/.env.local에 아래처럼 설정하고 재빌드합니다.
주소는 실제 배포한 API 주소로 바꾸세요.

```dotenv
VITE_API_MODE=cloud
VITE_API_BASE_URL=https://your-api.example.com
```

Vite는 이 프론트 환경파일을 읽습니다. 루트의 파이썬 환경변수와는 별도입니다.
`VITE_` 값은 브라우저 코드에 포함되므로 AWS 비밀키를 넣지 마세요.

기본 배포 방향은 React 정적 호스팅 + 비공개 S3 + API Gateway/Lambda + AWS Batch GPU입니다.
Batch 최소 vCPU 0으로 유휴 자원을 줄이되, 기동·대기·저장·전송 비용도 남을 수 있습니다.
현재 FastAPI를 통째로 Lambda에 올려 사진 2GiB를 받는 방식은 사용하지 않습니다.
결과 ZIP과 작업 상태는 GPU 종료 뒤에도 조회할 수 있도록 S3 등 외부 저장소로 분리해야 합니다.
상세 API 계약과 남은 구현은 `AWS_PLAN.md`에 정리했습니다. 실제 배포는 진행하지 않았습니다.

## 새 화면 흐름과 실제 진행률

- `/`: 상단에서 바로 사진·ZIP 선택과 분류 시작. 기능 위의 긴 설명을 제거했습니다.
- `/guide`: 사용방법 버튼으로 이동하는 별도 설명 페이지. 지원 형식·분류 기준·보관 정책을 확인합니다.
- `/processing`: 업로드·ZIP 해제·검증·사진 처리·ZIP 생성 상태를 표시합니다.
- `/result`: 분류 완료 후 자동 이동합니다. 풍경 및 기타·인물·음식 개수와 실패 수를 표시하고 다운로드 또는 지금은 받지 않기를 선택합니다.
- 나중에 받기를 선택해도 보관 기간 안에는 홈의 결과 다시 보기로 돌아갈 수 있습니다. 자동 다운로드는 하지 않습니다.

진행률은 서버가 예측·원본 복사·보고서 기록을 끝낸 개수 ÷ 전체 처리 대상 개수입니다.
손상·미지원 파일 등 보고서에 실패로 기록한 항목도 처리 대상 수에 포함합니다.
ZIP 해제 단계에서는 전체 수가 확정되지 않아 숫자 없는 준비 게이지를 표시합니다.
검증 단계도 분류 진행률로 부풀리지 않습니다. classifying 단계부터 실제 수치를 제공합니다.
사진 처리 100% 뒤 ZIP을 저장하는 동안에는 'ZIP 준비'를 표시하고,
ZIP이 완성돼 작업이 completed가 된 뒤에만 결과 선택 화면으로 이동합니다.

진행 API 예시:

```json
{
  "progress": {
    "phase": "classifying",
    "processed": 1250,
    "total": 5000,
    "percent": 25
  }
}
```

extracting·validating 단계의 percent는 null입니다. ZIP을 풀기 전 total도 null일 수 있습니다.
프론트는 이때 임의 퍼센트를 만들지 않습니다. 상태 조회 간격은 현재 2초이므로 짧은 작업은 중간 퍼센트를 건너뛰고 완료가 표시될 수 있습니다.

AWS cloud 어댑터도 같은 progress 계약을 읽습니다. 실제 AWS 워커에서 진행 이벤트를 영속 저장하고 API로 전달하는 구현은 아직 없습니다.
