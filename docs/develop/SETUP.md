# 개발 환경 준비

소스에서 ArcadeClip을 실행하고 검사하는 방법입니다.

## 준비물

1. Python 3.11 이상을 설치하세요.
2. [uv](https://docs.astral.sh/uv/)를 설치하세요. 의존성 설치와 실행에 씁니다.
3. FFmpeg를 받으세요. `uv run python run/fetch_tools.py`가 잠금 파일에 적힌 빌드를 `tools/`에 내려받고 파일마다 SHA-256을 확인합니다. 공유 라이브러리 빌드라 `ffmpeg.exe`·`ffprobe.exe`와 `av*.dll`·`sw*.dll`이 같은 폴더에 있어야 합니다.

## 실행하기

```sh
uv sync --locked
uv run start.py
```

첫 실행에서는 데이터 폴더를 지정하고 곡 자켓 약 100 MB를 받습니다. 다운로드에 약 10분이 걸릴 수 있습니다.

진단 정보를 남기려면 `--debug`를 붙이세요. 데이터 폴더에 `debug.log`를 씁니다.

```sh
uv run start.py --debug
```

## 검사 실행하기

```sh
uv run --directory src python -m unittest discover -s tests
```

## 도구 해시 확인하기

`tools/`의 실행 파일은 [run/tools.lock.json](../../run/tools.lock.json)의 SHA-256과 일치해야 배포본을 만들 수 있습니다.

```sh
uv run python run/verify_tools.py tools
```

해시가 다르면 빌드가 중단됩니다. FFmpeg를 바꾸는 절차는 [Windows 배포본 만들기](BUILD.md)에 있습니다.

## 실행 환경

| 환경 | 상태 |
|---|---|
| Windows x64 | 개발·배포 대상. UI와 회귀 검사를 여기서 확인한다. |
| macOS Apple Silicon (14 이상) | 잠긴 의존성의 설치 계획만 확인. 실기 미검증. |
| Linux x86_64 · ARM64 | 잠긴 의존성의 설치 계획만 확인. 실기 미검증. Qt가 요구하는 시스템 라이브러리와 그래픽 환경이 따로 필요하다. |
| macOS Intel | 고정한 ONNX Runtime 1.30.0에 해당 wheel이 없어 설치되지 않는다. |
| Windows ARM64 | 고정 패키지 일부에 해당 wheel이 없다. |

설치 계획이 만들어진다는 것은 실행·재생·저장이 된다는 뜻이 아닙니다.

코드에 남아 있는 플랫폼 분기는 다음과 같습니다.

- `src/sources.py` — 실행 파일 확장자와 프로세스 취소 방식을 Windows/POSIX로 나눕니다.
- `src/export.py` — 파일 게시를 Windows rename과 POSIX hard link로 나눕니다. GPU 저장 후보는 NVENC·QSV·AMF입니다.
- `src/video_sampling.py` — 실험용 GPU 디코딩은 D3D11VA만 씁니다. 실패하면 CPU로 처리합니다.
- `run/build_windows.ps1`, `run/tools.lock.json`, `run/verify_tools.py` — 배포 자동화와 도구 해시는 Windows x64 기준입니다.

## 관련 문서

- [Windows 배포본 만들기](BUILD.md)
- [설명서 고치고 게시하기](MANUAL.md)
