# AWS 사용량 기반 배포 방향

## 현재 구현 범위

React 화면, 기존 FastAPI 연결, 로컬 대량 처리, AWS용 프론트 API 어댑터를 구현했습니다.
AWS 리소스·Lambda API·Batch 워커·S3 업로드 세션 발급 서버는 아직 구현·배포하지 않았습니다.
현재 앱을 실행하면 기본 local 모드로 기존 모델과 서버를 사용합니다.
`VITE_API_MODE=cloud`만 바꿔도 AWS에서 실행되는 상태는 아닙니다.

## 기본 배포안: React + S3 + API Gateway/Lambda + AWS Batch GPU

사진 몇천 장과 2GiB 업로드를 한 작업으로 처리하므로, 일반 웹 요청에 GPU 처리를 묶지 않습니다.
React 정적 파일은 S3/CloudFront에서 제공하고, 입력과 결과는 비공개 S3에 저장하며,
Batch 큐에 들어간 작업만 GPU 컨테이너에서 실행하는 방향입니다.

| 구성 | 역할 | 작업이 없을 때 |
|---|---|---|
| React 정적 화면 / CloudFront·S3 | 화면 제공 | 저장·요청·전송 사용량 과금 가능 |
| API Gateway / Lambda | 설정, 업로드 주소, 작업 접수·상태 조회 | 호출 중심 과금; 고정 용량 예약 없이 구성 |
| 비공개 S3 | 입력 사진·ZIP, 결과 ZIP, 작업 메타데이터 | 보관량·요청·전송 과금 |
| AWS Batch EC2 GPU 환경 | ZIP 해제, 검증, 기존 predictor/sorter 실행 | 최소 vCPU 0으로 설정하여 유휴 시 0대로 축소 |
| ECR / CloudWatch 등 | 워커 이미지·로그 | 이미지 보관·로그 등 비용 가능 |

GPU 인스턴스를 유지하는 상시 EC2 구성과 달리, 작업이 있을 때 Batch가 계산 자원을 확보하고
유휴 상태에는 0대로 줄이도록 구성합니다. GPU 커널이 실제 계산한 시간만 과금되는 것은 아닙니다.
서버 기동·이미지/모델 준비·작업 실행·종료 전 대기 시간도 인스턴스 실행료에 포함될 수 있습니다.
첫 작업은 자원 확보와 모델 로딩 때문에 기다릴 수 있으며, 대기 중에도 작업 상태를 조회할 수 있어야 합니다.

AWS Batch는 GPU 작업과 최소 vCPU 0을 지원합니다. 정확한 축소 시간·실제 비용은 배포 후 검증해야 합니다.
처음에는 안정적인 온디맨드 GPU로 기능을 검증하고, 재시도·중복 방지와 중단 복구를 구현한 뒤 Spot 도입을 검토합니다.
인스턴스 유형·리전·네트워크·최대 vCPU 값은 AWS 계정 쿼터와 실제 모델 메모리 측정 뒤 확정합니다.

## SageMaker 대신 Batch를 기본안으로 잡은 이유

SageMaker 비동기 추론도 유휴 시 0대로 줄일 수 있습니다. 다만 현재 공식 문서는 입력 1GB와 처리 최대 1시간을 명시합니다.
이번 프로젝트는 ZIP 2GiB 및 사진 수천 장을 받으므로, 이를 하나의 SageMaker 입력으로 그대로 전달하지 않습니다.
S3 경로만 전달하는 별도 핸들러·분할 처리를 설계하면 사용 가능하지만,
ZIP 해제부터 결과 생성까지 기존 파이썬 파일 처리 작업을 실행하는 Batch가 현재 작업 형태에 더 직접적이라고 판단했습니다.
이는 비용·속도를 측정한 최종 결론은 아니며, 실제 배포 전에 비교할 수 있습니다.

## 처리 순서

1. React가 API에서 제한값과 서비스 준비 상태를 조회합니다.
2. 사진·ZIP 파일명과 바이트 크기만 담아 업로드 세션을 요청합니다.
3. 서버가 고유 S3 키와 만료 시간 있는 PUT 주소를 발급합니다.
4. 브라우저가 사진 바이트를 S3로 직접 업로드합니다. AWS 자격증명을 브라우저에 배포하지 않습니다.
5. 업로드가 모두 성공하면 세션 ID로 분류 작업을 접수합니다.
6. 서버가 실제 S3 객체 크기와 세션 소유·만료·중복 접수를 검증하고 Batch 작업을 제출합니다.
7. GPU 워커가 입력 파일을 내려받아 기존 이미지 검증·predictor·sorter를 실행합니다.
8. 결과 ZIP과 작업 상태를 S3에 기록하고, 로컬 임시 사본을 정리한 뒤 워커 작업이 끝납니다.
9. React가 상태를 조회하고 완료되면 다운로드 링크를 제공합니다. 결과는 GPU 서버 종료 후에도 다운로드할 수 있어야 합니다.
10. 서비스 만료 정책에 맞춰 S3 파일을 삭제하고 Batch 계산 자원을 0대로 축소합니다.

GPU 워커의 로컬 파일과 메모리 딕셔너리는 영속 상태로 사용하지 않습니다.
초기에는 S3의 JSON으로 작업 메타데이터를 보관하는 방식을 검토하고, 일반 사용자 로그인·회원가입·관계형 DB는 추가하지 않습니다.
중복 접수·경합 처리와 동시 GPU 작업 1개 제한은 별도로 구현해야 합니다.
Batch의 최대 vCPU 설정만으로 모든 배포 구성에서 동시 실행 수가 정확히 보장된다고 가정하지 않습니다.

## React에 구현한 cloud API 계약

`frontend/src/api.js`의 cloud 모드는 아래 계약을 호출합니다. 이 API 서버는 아직 없습니다.

| 호출 | 입력·응답 |
|---|---|
| GET /api/status | local과 같은 model_ready, busy, message, limits |
| POST /api/upload-sessions | JSON files 배열 → id, uploads 배열 |
| S3 PUT | uploads의 URL로 해당 원본 File 업로드 |
| POST /api/jobs | JSON session_id → id, status |
| GET /api/jobs/{id} | 상태, counts, failed_count, error, expires_at, download_url |

업로드 세션 요청 예시:

```json
{
  "files": [
    {"name": "photos.zip", "size": 123456, "content_type": "application/zip"}
  ]
}
```

응답 모양 예시입니다. 다음 주소는 실제 발급된 주소가 아닌 형식 설명입니다.

```json
{
  "id": "opaque-session-id",
  "uploads": [
    {"method": "PUT", "url": "https://bucket.example/signed-upload", "headers": {}}
  ]
}
```

uploads는 요청 파일과 순서·개수가 일치해야 합니다. 서버가 발급한 서명에 포함된 헤더가 있으면
브라우저도 그 헤더를 그대로 보냅니다. 같은 세션 재접수는 같은 작업을 반환하거나 거부해야 하며,
같은 업로드로 GPU 작업이 여러 번 발생하지 않도록 구현해야 합니다.

cloud의 `model_ready`는 현재 GPU가 켜져 있다는 뜻으로 사용하지 않습니다.
관리자 모델과 작업 접수 구성이 유효하면 true를 반환하고, 작업별 provisioning 상태로 GPU 준비 상황을 안내합니다.
실제 배포에서 이 의미를 서버와 맞춰야 합니다.

지원 상태:

| 상태 | 화면 |
|---|---|
| queued | 분류 순서 대기 |
| provisioning | GPU 서버 준비 |
| processing | 사진 분류 |
| zipping | ZIP 생성 |
| completed | 개수와 다운로드 |
| failed | 실패 이유 |

진행률을 실제로 계산하지 않으면 퍼센트를 만들지 않습니다.
완료 응답의 download_url은 API 다운로드 경로 또는 HTTPS S3 서명 주소여야 합니다.
오래된 서명 주소가 결과 보관 기간보다 먼저 만료되는 문제를 피하려면,
API 다운로드 경로에서 만료 여부를 확인하고 짧은 서명 주소로 리다이렉트하는 방식을 우선 검토합니다.

## 유지해야 할 조건

- 기존 EfficientNetV2-L 체크포인트 계약과 predictor의 전처리를 그대로 사용합니다.
- 관리자가 모델을 S3의 비공개 관리 경로에 배치하며 사용자는 사진·ZIP만 올립니다.
- worker 시작 때 모델을 한 번 로드하고, 같은 작업의 사진마다 재사용합니다.
- 사진 수·개별 크기·해제 후 총량·픽셀·ZIP 항목·경로 검사를 워커에서도 수행합니다.
- 사전 신고한 파일 크기는 신뢰하지 않습니다. 작업 접수 때 S3 실제 크기를 확인하고 워커도 실제 바이트를 제한합니다.
- S3 서명 URL은 재사용·덮어쓰기 가능성을 고려해야 합니다. 업로드 확정 후 입력 객체 버전을 고정하거나 별도 불변 입력 키로 복사하여 처리 중 변경을 막는 방안을 구현해야 합니다.
- 미완료 업로드·실패 작업도 정리합니다. S3 Lifecycle은 보조 정리 수단이며 한 시간 단위의 정확한 삭제를 보장한다고 표현하지 않습니다.
- 다운로드 권한 만료와 파일 삭제는 별도입니다. 다운로드 시작 직후 객체를 지워도 안전하다고 가정하지 않습니다. 전송 보호·삭제 유예를 S3 기반 구조에 맞춰 구현·검증해야 합니다.
- AWS 키는 React 환경변수에 넣지 않습니다. 서버 IAM 역할에 필요한 S3 키 범위·Batch 제출 권한만 부여합니다.
- 대량 사진 업로드는 S3 직행으로 처리하며 Netlify·Lambda의 작은 요청 본문에 사진을 넣지 않습니다.
- 화면과 API, S3 CORS에서 배포한 프론트 출처를 허용합니다. 현재 로컬 모드는 같은 출처 또는 Vite 프록시로 동작합니다.
- 개인 사진·가중치·API 비밀값을 Git·공개 S3 버킷에 올리지 않습니다.

## 실제 배포 전에 할 작업

- AWS 계정·리전·GPU 쿼터·보관 공간·예산·공개 범위를 확인합니다.
- GPU 컨테이너를 만들고 CUDA·torch·torchvision·Python 호환성과 실제 모델 추론을 검증합니다.
- 세션 발급 API, 실제 크기 검사, 멱등 작업 제출, 워커, 영속 상태, 만료 정리를 구현합니다.
- S3 비공개 버킷·IAM·ECR·Batch·API·정적 호스팅을 IaC로 구성합니다.
- 최소 0대 축소와 다시 기동되는지, 유휴·실패 시 비용이 얼마나 남는지 확인합니다.
- GPU 동시 작업 1개 제한, 요청 제한, 작업 시간 제한, 예산 알림을 설정합니다.
- 실제 사진의 정상·손상·대용량·중복·만료 다운로드를 클라우드에서 검증합니다.
- 그 다음 cloud 모드로 React를 빌드하고 배포합니다.

## 참고한 공식 문서

- React + Vite: https://react.dev/learn/build-a-react-app-from-scratch
- AWS Batch GPU: https://docs.aws.amazon.com/batch/latest/userguide/gpu-jobs.html
- Batch compute environment: https://docs.aws.amazon.com/batch/latest/APIReference/API_CreateComputeEnvironment.html
- S3 서명 업로드: https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html
- SageMaker 비동기 추론: https://docs.aws.amazon.com/sagemaker/latest/dg/async-inference.html

확인일: 2026-11-04. 실제 계정의 사용 가능 인스턴스와 가격은 배포 때 다시 확인합니다.

## 진행률 API 계약 추가

로컬 서버는 실제 sorter 콜백으로 progress를 갱신합니다. AWS 배포 시에도 같은 필드를 작업 상태 응답에 포함해야 합니다.

- phase: preparing / extracting / validating / classifying / zipping / completed
- processed: 성공 또는 실패 처리를 마친 대상 수
- total: 전체 처리 대상 수. ZIP을 풀기 전에는 null 가능
- percent: classifying 단계부터 processed / total × 100. 알려지지 않으면 null

실패 보고서 항목도 처리 수에 포함합니다. 사진 처리 100%와 ZIP 생성 완료를 구분하고,
Batch 작업이 최종 ZIP과 상태를 저장한 후에만 completed를 반환해야 합니다.
React는 completed를 받으면 /result로 전환하여 폴더별 개수와 다운로드 여부 선택을 표시합니다.
현재 AWS 워커의 진행 저장·조회 API는 미구현이며 프론트만으로 진행률이 생기지 않습니다.
